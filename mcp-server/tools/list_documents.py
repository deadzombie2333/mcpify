"""
Tool 3: List Documents — browse accessible documents from S3, filtered by permissions.
"""

import os
from typing import Dict, Any, Optional, List
import boto3

from auth.permissions import is_path_allowed


class ListDocuments:
    def __init__(self):
        self.region = os.environ.get("REGION", os.environ.get("AWS_REGION", "us-west-2"))
        self.bucket = os.environ.get("DOCS_BUCKET", "")
        self.s3 = boto3.client("s3", region_name=self.region)

    def list(
        self,
        permissions: Optional[Dict] = None,
        folder: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List documents the caller can access.
        Optionally filter to a specific folder.
        """
        try:
            file_types = os.environ.get("FILE_TYPES", "md,txt,json").split(",")
            base_prefix = os.environ.get("DOCS_PREFIX", "")
            if base_prefix and not base_prefix.endswith("/"):
                base_prefix += "/"
            prefix = folder if folder else base_prefix
            if prefix and not prefix.endswith("/"):
                prefix += "/"
            if folder and base_prefix and not prefix.startswith(base_prefix):
                prefix = base_prefix + prefix

            paginator = self.s3.get_paginator("list_objects_v2")
            params = {"Bucket": self.bucket}
            if prefix:
                params["Prefix"] = prefix

            docs = []
            folders_seen = set()

            for page in paginator.paginate(**params):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if not any(key.endswith(f".{ft}") for ft in file_types):
                        continue
                    if permissions and not is_path_allowed(key, permissions):
                        continue

                    parts = key.split("/")
                    if len(parts) > 1:
                        folders_seen.add(parts[0])

                    docs.append({
                        "path": key,
                        "folder": parts[0] if len(parts) > 1 else "",
                        "name": parts[-1],
                        "size_bytes": obj["Size"],
                        "last_modified": obj["LastModified"].isoformat(),
                    })

            return {
                "bucket": self.bucket,
                "total_documents": len(docs),
                "folders": sorted(folders_seen),
                "documents": docs,
            }
        except Exception as e:
            return {"error": str(e), "documents": []}
