"""
DynamoDB permission lookup and access resolution.

Lambda has read-only access to the permissions table.
Only the EC2 control plane can write permissions.

Table schema:
  PK: client_id (String)
  Attributes:
    name: str
    access_rules: {folders: [str], files: [str]}
    deny_rules:   {folders: [str], files: [str]}  (optional)
"""

import os
from typing import Dict, List, Any, Optional
import boto3
from functools import lru_cache


_dynamodb = None
_table_name = None


def _get_table():
    global _dynamodb, _table_name
    if _dynamodb is None:
        region = os.environ.get("REGION", os.environ.get("AWS_REGION", "us-west-2"))
        _dynamodb = boto3.resource("dynamodb", region_name=region)
        _table_name = os.environ.get("PERMISSIONS_TABLE", "mcpify-permissions")
    return _dynamodb.Table(_table_name)


def get_permissions(client_id: str) -> Optional[Dict[str, Any]]:
    """Fetch permissions for a client_id from DynamoDB."""
    if not client_id:
        return None
    try:
        resp = _get_table().get_item(Key={"client_id": client_id})
        return resp.get("Item")
    except Exception as e:
        print(f"Error fetching permissions for {client_id}: {e}")
        return None


def is_path_allowed(path: str, permissions: Dict[str, Any]) -> bool:
    """
    Check if a document path is accessible given permissions.

    Resolution order:
    1. deny_rules checked first (explicit deny wins)
    2. access_rules.folders: ["*"] grants all
    3. Path's parent folder in access_rules.folders
    4. Exact path in access_rules.files
    """
    if not permissions:
        return False

    access = permissions.get("access_rules", {})
    deny = permissions.get("deny_rules", {})

    # Check explicit deny first
    if path in deny.get("files", []):
        return False
    parts = path.split("/")
    for i in range(len(parts)):
        folder = "/".join(parts[: i + 1])
        if folder in deny.get("folders", []):
            return False

    # Check allow
    allowed_folders = access.get("folders", [])
    if "*" in allowed_folders:
        return True

    # Folder-level: check if any parent folder is allowed
    for i in range(len(parts) - 1):
        folder = "/".join(parts[: i + 1]) if i > 0 else parts[0]
        if folder in allowed_folders:
            return True

    # File-level
    if path in access.get("files", []):
        return True

    return False


def build_opensearch_filter(permissions: Dict[str, Any]) -> Optional[Dict]:
    """
    Build an OpenSearch bool filter from permissions.
    Returns None if wildcard access (no filter needed).
    """
    if not permissions:
        return {"bool": {"must_not": [{"match_all": {}}]}}  # deny all

    access = permissions.get("access_rules", {})
    deny = permissions.get("deny_rules", {})
    folders = access.get("folders", [])
    files = access.get("files", [])

    # Wildcard = no allow filter needed
    is_wildcard = "*" in folders

    # Build allow clause
    should = []
    if not is_wildcard:
        if folders:
            should.append({"terms": {"category": folders}})
        if files:
            should.append({"terms": {"file_path": files}})
        if not should:
            return {"bool": {"must_not": [{"match_all": {}}]}}  # deny all

    # Build deny clause
    must_not = []
    if deny.get("files"):
        must_not.append({"terms": {"file_path": deny["files"]}})
    if deny.get("folders"):
        must_not.append({"terms": {"category": deny["folders"]}})

    if is_wildcard:
        if must_not:
            return {"bool": {"must_not": must_not}}
        return None  # no filter needed

    filt = {"bool": {"should": should, "minimum_should_match": 1}}
    if must_not:
        filt["bool"]["must_not"] = must_not
    return filt
