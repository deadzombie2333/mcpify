"""
Tool 1: Document Search — semantic search via OpenSearch, filtered by permissions.
"""

import json
import os
from typing import Dict, Any, Optional, List
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth


class DocumentSearch:
    def __init__(self):
        self.region = os.environ.get("REGION", os.environ.get("AWS_REGION", "us-west-2"))
        self.index_name = os.environ.get("OPENSEARCH_INDEX", "mcpify-docs")
        endpoint = os.environ.get("OPENSEARCH_ENDPOINT", "")

        self.bedrock = boto3.client("bedrock-runtime", region_name=self.region)

        creds = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(creds, self.region, "aoss")
        self.os_client = OpenSearch(
            hosts=[{"host": endpoint, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=30,
        )

    def get_embedding(self, text: str) -> List[float]:
        model = os.environ.get("EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
        dims = int(os.environ.get("EMBEDDING_DIMS", "1024"))
        body = json.dumps({"inputText": text[:8000], "dimensions": dims, "normalize": True})
        resp = self.bedrock.invoke_model(
            modelId=model, body=body, contentType="application/json", accept="application/json"
        )
        return json.loads(resp["body"].read())["embedding"]

    def search(
        self,
        query: str,
        permissions_filter: Optional[Dict] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        Semantic search filtered by caller's permissions.
        permissions_filter is built by auth.build_opensearch_filter().
        """
        try:
            vec = self.get_embedding(query)
            knn = {"knn": {"embedding": {"vector": vec, "k": top_k}}}

            if permissions_filter:
                body = {
                    "size": top_k,
                    "query": {"bool": {"must": [knn], "filter": [permissions_filter]}},
                }
            else:
                body = {"size": top_k, "query": knn}

            body["_source"] = [
                "file_path", "category", "doc_name", "section",
                "section_hierarchy", "content", "chunk_index", "total_chunks",
            ]

            results = self.os_client.search(index=self.index_name, body=body)

            hits = []
            for h in results["hits"]["hits"]:
                s = h["_source"]
                hits.append({
                    "score": h["_score"],
                    "doc_name": s.get("doc_name"),
                    "category": s.get("category"),
                    "file_path": s.get("file_path"),
                    "section": s.get("section"),
                    "content": s.get("content"),
                    "chunk": f"{s.get('chunk_index', 0)+1}/{s.get('total_chunks', 1)}",
                })

            return {
                "query": query,
                "total": results["hits"]["total"]["value"],
                "results": hits,
            }
        except Exception as e:
            return {"error": str(e), "query": query, "results": []}
