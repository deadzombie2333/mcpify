"""
S3 Document Embedder — reads docs from S3, chunks, embeds via Bedrock, indexes to OpenSearch.

Supports incremental updates via content hash comparison.
"""

import json
import os
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth


class S3Embedder:
    def __init__(self, config: Dict[str, Any]):
        self.region = config.get("region", "us-west-2")
        self.bucket = config["s3_bucket"]
        self.s3_prefix = config.get("s3_prefix", "")
        self.index_name = f"{config['project_name']}-docs"
        self.file_types = config.get("file_types", ["md", "txt", "json"])
        self.chunk_max = config.get("chunk_max_size", 3000)
        self.embed_model = config.get("embedding_model_id", "amazon.titan-embed-text-v2:0")
        self.embed_dims = config.get("embedding_dimensions", 1024)

        self.s3 = boto3.client("s3", region_name=self.region)
        self.bedrock = boto3.client("bedrock-runtime", region_name=self.region)

        # OpenSearch
        endpoint = os.environ.get("OPENSEARCH_ENDPOINT", "")
        if not endpoint:
            raise ValueError("OPENSEARCH_ENDPOINT env var required")
        creds = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(creds, self.region, "aoss")
        self.os_client = OpenSearch(
            hosts=[{"host": endpoint, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=300,
        )

    def create_index(self):
        """Create OpenSearch index with vector mapping."""
        if self.os_client.indices.exists(index=self.index_name):
            print(f"Index '{self.index_name}' already exists")
            return
        body = {
            "settings": {"index": {"knn": True, "knn.algo_param.ef_search": 512}},
            "mappings": {
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "doc_id": {"type": "keyword"},
                    "category": {"type": "keyword"},
                    "doc_name": {"type": "text"},
                    "file_path": {"type": "keyword"},
                    "content": {"type": "text"},
                    "content_hash": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},
                    "total_chunks": {"type": "integer"},
                    "section": {"type": "text"},
                    "section_hierarchy": {"type": "keyword"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": self.embed_dims,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "nmslib",
                            "parameters": {"ef_construction": 512, "m": 16},
                        },
                    },
                    "indexed_at": {"type": "date"},
                }
            },
        }
        self.os_client.indices.create(index=self.index_name, body=body)
        print(f"Created index '{self.index_name}'")

    def list_s3_docs(self) -> List[str]:
        """List all document keys in S3 matching configured file types."""
        keys = []
        paginator = self.s3.get_paginator("list_objects_v2")
        params = {"Bucket": self.bucket}
        if self.s3_prefix:
            params["Prefix"] = self.s3_prefix if self.s3_prefix.endswith("/") else self.s3_prefix + "/"
        for page in paginator.paginate(**params):
            for obj in page.get("Contents", []):
                if any(obj["Key"].endswith(f".{ft}") for ft in self.file_types):
                    keys.append(obj["Key"])
        return keys

    def read_s3(self, key: str) -> str:
        """Read document from S3. Uses OCR for PDF/DOCX."""
        ext = key.rsplit(".", 1)[-1].lower()
        raw = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

        if ext == "pdf":
            return self._ocr_pdf(raw, key)
        elif ext == "docx":
            return self._ocr_docx(raw, key)
        else:
            return raw.decode("utf-8")

    def _ocr_pdf(self, raw_bytes: bytes, key: str) -> str:
        """Convert PDF pages to images, OCR each via Bedrock Nova vision."""
        import tempfile
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(raw_bytes, dpi=200)
        print(f"    PDF {key}: {len(images)} pages")
        pages = []
        for i, img in enumerate(images):
            text = self._ocr_image(img, f"{key} page {i+1}")
            if text:
                pages.append(text)
        return "\n\n".join(pages)

    def _ocr_docx(self, raw_bytes: bytes, key: str) -> str:
        """Convert DOCX to PDF via LibreOffice, then OCR."""
        import tempfile
        import subprocess
        from pdf2image import convert_from_path

        with tempfile.TemporaryDirectory() as tmpdir:
            docx_path = os.path.join(tmpdir, "input.docx")
            with open(docx_path, "wb") as f:
                f.write(raw_bytes)

            # Convert DOCX → PDF via LibreOffice headless
            subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", tmpdir, docx_path],
                capture_output=True, timeout=120,
            )
            pdf_path = os.path.join(tmpdir, "input.pdf")
            if not os.path.exists(pdf_path):
                print(f"    ⚠️  LibreOffice conversion failed for {key}, trying direct text extraction")
                return self._docx_fallback_text(raw_bytes)

            images = convert_from_path(pdf_path, dpi=200)
            print(f"    DOCX {key}: {len(images)} pages")
            pages = []
            for i, img in enumerate(images):
                text = self._ocr_image(img, f"{key} page {i+1}")
                if text:
                    pages.append(text)
            return "\n\n".join(pages)

    def _docx_fallback_text(self, raw_bytes: bytes) -> str:
        """Fallback: extract text directly from DOCX if LibreOffice unavailable."""
        try:
            import docx
            import io
            doc = docx.Document(io.BytesIO(raw_bytes))
            return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            return ""

    def _ocr_image(self, pil_image, label: str = "") -> str:
        """Send a PIL image to Bedrock Nova vision for text extraction."""
        import io
        import base64

        # Convert PIL image to JPEG bytes
        buf = io.BytesIO()
        pil_image.save(buf, format="JPEG", quality=85)
        img_bytes = buf.getvalue()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")

        ocr_model = os.environ.get("BEDROCK_MODEL", "us.amazon.nova-2-lite-v1:0")
        try:
            resp = self.bedrock.converse(
                modelId=ocr_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"image": {"format": "jpeg", "source": {"bytes": img_bytes}}},
                        {"text": "Extract ALL text from this document image. Preserve the structure, headings, lists, and tables. Output only the extracted text, nothing else."},
                    ],
                }],
                inferenceConfig={"maxTokens": 8000, "temperature": 0},
            )
            return resp["output"]["message"]["content"][0]["text"]
        except Exception as e:
            print(f"    ⚠️  OCR failed for {label}: {e}")
            return ""

    def get_embedding(self, text: str) -> List[float]:
        body = json.dumps({
            "inputText": text[:8000],
            "dimensions": self.embed_dims,
            "normalize": True,
        })
        resp = self.bedrock.invoke_model(
            modelId=self.embed_model, body=body,
            contentType="application/json", accept="application/json",
        )
        return json.loads(resp["body"].read())["embedding"]

    def chunk_document(self, content: str) -> List[Dict[str, Any]]:
        """
        Split document by paragraphs and sentences, respecting markdown headers.
        
        Strategy:
        1. Split by markdown headers (sections)
        2. Within each section, split by double-newline (paragraphs)
        3. If a paragraph exceeds chunk_max, split by sentence (. ! ?)
        4. Merge small consecutive paragraphs into one chunk up to chunk_max
        """
        import re

        # Split into sections by markdown headers
        sections = []
        current_title = "Introduction"
        current_lines = []
        hierarchy = []

        for line in content.split("\n"):
            if line.strip().startswith("#"):
                if current_lines:
                    sections.append((current_title, hierarchy.copy(), "\n".join(current_lines).strip()))
                level = len(line) - len(line.lstrip("#"))
                current_title = line.strip("#").strip()
                hierarchy = hierarchy[: level - 1] + [current_title]
                current_lines = []
            else:
                current_lines.append(line)
        if current_lines:
            sections.append((current_title, hierarchy.copy(), "\n".join(current_lines).strip()))

        # For each section, split into paragraph-based chunks
        chunks = []
        for title, hier, text in sections:
            if not text:
                continue
            paragraphs = re.split(r"\n\s*\n", text)
            current_chunk = ""

            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue

                # If single paragraph exceeds max, split by sentence
                if len(para) > self.chunk_max:
                    # Flush current
                    if current_chunk:
                        chunks.append({"content": current_chunk.strip(), "section": title, "hierarchy": hier})
                        current_chunk = ""
                    # Split by sentence boundaries
                    sentences = re.split(r"(?<=[.!?])\s+", para)
                    sent_chunk = ""
                    for sent in sentences:
                        if len(sent_chunk) + len(sent) + 1 > self.chunk_max and sent_chunk:
                            chunks.append({"content": sent_chunk.strip(), "section": title, "hierarchy": hier})
                            sent_chunk = sent
                        else:
                            sent_chunk = f"{sent_chunk} {sent}".strip() if sent_chunk else sent
                    if sent_chunk:
                        chunks.append({"content": sent_chunk.strip(), "section": title, "hierarchy": hier})
                # Merge paragraphs up to chunk_max
                elif len(current_chunk) + len(para) + 2 > self.chunk_max:
                    if current_chunk:
                        chunks.append({"content": current_chunk.strip(), "section": title, "hierarchy": hier})
                    current_chunk = para
                else:
                    current_chunk = f"{current_chunk}\n\n{para}".strip() if current_chunk else para

            if current_chunk:
                chunks.append({"content": current_chunk.strip(), "section": title, "hierarchy": hier})

        return chunks if chunks else [{"content": content.strip(), "section": "Document", "hierarchy": []}]

    def is_already_indexed(self, doc_id: str, content_hash: str) -> bool:
        """Check if document with same content hash is already indexed."""
        try:
            resp = self.os_client.search(
                index=self.index_name,
                body={"query": {"bool": {"must": [
                    {"term": {"doc_id": doc_id}},
                    {"term": {"content_hash": content_hash}},
                ]}}, "size": 1},
            )
            return resp["hits"]["total"]["value"] > 0
        except Exception:
            return False

    def embed_document(self, key: str) -> int:
        """Embed a single S3 document. Returns number of chunks indexed."""
        content = self.read_s3(key)
        if not content.strip():
            return 0

        doc_id = hashlib.md5(key.encode()).hexdigest()
        content_hash = hashlib.md5(content.encode()).hexdigest()

        if self.is_already_indexed(doc_id, content_hash):
            print(f"  Skip (unchanged): {key}")
            return 0

        # Content changed — delete old chunks first
        self._delete_doc(doc_id)

        parts = key.split("/")
        category = parts[0] if len(parts) > 1 else ""
        doc_name = parts[-1].rsplit(".", 1)[0]
        chunks = self.chunk_document(content)

        for i, chunk in enumerate(chunks):
            vec = self.get_embedding(chunk["content"])
            doc = {
                "chunk_id": f"{doc_id}_{i}",
                "doc_id": doc_id,
                "category": category,
                "doc_name": doc_name,
                "file_path": key,
                "content": chunk["content"],
                "content_hash": content_hash,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "section": chunk["section"],
                "section_hierarchy": chunk["hierarchy"],
                "embedding": vec,
                "indexed_at": datetime.utcnow().isoformat(),
            }
            self.os_client.index(index=self.index_name, body=doc)

        print(f"  Indexed: {key} ({len(chunks)} chunks)")
        return len(chunks)

    def _delete_doc(self, doc_id: str):
        """Delete all chunks for a doc_id from the index."""
        try:
            self.os_client.delete_by_query(
                index=self.index_name,
                body={"query": {"term": {"doc_id": doc_id}}},
            )
        except Exception:
            pass

    def _get_indexed_file_paths(self) -> Dict[str, str]:
        """Get all file_path→doc_id currently in the index."""
        indexed = {}
        try:
            resp = self.os_client.search(
                index=self.index_name,
                body={"size": 0, "aggs": {"paths": {"terms": {"field": "file_path", "size": 10000},
                    "aggs": {"did": {"terms": {"field": "doc_id", "size": 1}}}}}},
            )
            for bucket in resp["aggregations"]["paths"]["buckets"]:
                path = bucket["key"]
                doc_id = bucket["did"]["buckets"][0]["key"] if bucket["did"]["buckets"] else ""
                indexed[path] = doc_id
        except Exception:
            pass
        return indexed

    def sync(self):
        """
        Full sync: compare S3 state vs index state.
        - New/changed files → embed
        - Deleted files → remove from index
        - Unchanged files → skip
        """
        print(f"Bucket: {self.bucket}")
        print(f"Index: {self.index_name}")
        self.create_index()

        s3_keys = set(self.list_s3_docs())
        indexed_paths = self._get_indexed_file_paths()
        indexed_keys = set(indexed_paths.keys())

        # Remove docs that no longer exist in S3
        removed = indexed_keys - s3_keys
        for key in removed:
            doc_id = indexed_paths[key]
            self._delete_doc(doc_id)
            print(f"  Removed (deleted from S3): {key}")

        # Embed new or changed docs
        total = 0
        for key in sorted(s3_keys):
            total += self.embed_document(key)

        print(f"\nSync complete: {len(s3_keys)} in S3, {total} chunks indexed, {len(removed)} removed.")
