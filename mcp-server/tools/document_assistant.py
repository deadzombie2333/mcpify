"""
Tool 2: Document Assistant — LLM-powered Q&A reading from S3, filtered by permissions.
"""

import json
import os
from typing import Dict, Any, Optional, List
import boto3

from auth.permissions import is_path_allowed


class DocumentAssistant:
    def __init__(self):
        self.region = os.environ.get("REGION", os.environ.get("AWS_REGION", "us-west-2"))
        self.bucket = os.environ.get("DOCS_BUCKET", "")
        self.model_id = os.environ.get("BEDROCK_MODEL", "us.amazon.nova-2-lite-v1:0")
        self.bedrock = boto3.client("bedrock-runtime", region_name=self.region)
        self.s3 = boto3.client("s3", region_name=self.region)

    def _list_doc_keys(self) -> List[str]:
        """List all document keys in the S3 bucket."""
        keys = []
        file_types = os.environ.get("FILE_TYPES", "md,txt,json").split(",")
        prefix = os.environ.get("DOCS_PREFIX", "")
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        paginator = self.s3.get_paginator("list_objects_v2")
        params = {"Bucket": self.bucket}
        if prefix:
            params["Prefix"] = prefix
        for page in paginator.paginate(**params):
            for obj in page.get("Contents", []):
                if any(obj["Key"].endswith(f".{ft}") for ft in file_types):
                    keys.append(obj["Key"])
        return keys

    def _read_s3(self, key: str) -> str:
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read().decode("utf-8")
        except Exception:
            return ""

    def ask(
        self,
        question: str,
        permissions: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Answer a question using only documents the caller can access.
        Reads allowed docs from S3, feeds to Bedrock LLM.
        """
        try:
            # Get allowed docs
            all_keys = self._list_doc_keys()
            allowed_keys = [
                k for k in all_keys if is_path_allowed(k, permissions)
            ] if permissions else []

            if not allowed_keys:
                return {"answer": "No accessible documents found for your query.", "sources": []}

            # Read docs (cap at ~50k chars to fit context window)
            context_parts = []
            total_chars = 0
            used_keys = []
            for key in allowed_keys:
                if total_chars > 50000:
                    break
                content = self._read_s3(key)
                if content:
                    context_parts.append(f"--- {key} ---\n{content}")
                    total_chars += len(content)
                    used_keys.append(key)

            context = "\n\n".join(context_parts)

            prompt = (
                f"You are a helpful assistant. Answer the question based ONLY on the provided documents. "
                f"If the answer is not in the documents, say so.\n\n"
                f"Documents:\n{context}\n\n"
                f"Question: {question}"
            )

            resp = self.bedrock.converse(
                modelId=self.model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 4000, "temperature": 0.1},
            )

            answer = resp["output"]["message"]["content"][0]["text"]
            return {"answer": answer, "sources": used_keys}

        except Exception as e:
            return {"error": str(e), "answer": "", "sources": []}
