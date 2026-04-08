"""
Lambda handler for mcpify MCP Server.

Flow:
  1. Extract client_id from JWT (Gateway already validated signature)
  2. Lookup permissions in DynamoDB (read-only)
  3. Build permission filter
  4. Route to the appropriate tool with permissions injected
"""

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))

from auth.jwt_utils import get_client_id
from auth.permissions import get_permissions, build_opensearch_filter
from tools import DocumentSearch, DocumentAssistant, ListDocuments

# Init tools once (reused across invocations)
_search = None
_assistant = None
_list_docs = None


def _init_tools():
    global _search, _assistant, _list_docs
    if _search is None:
        try:
            _search = DocumentSearch()
        except Exception as e:
            print(f"Warning: DocumentSearch init failed: {e}")
    if _assistant is None:
        try:
            _assistant = DocumentAssistant()
        except Exception as e:
            print(f"Warning: DocumentAssistant init failed: {e}")
    if _list_docs is None:
        try:
            _list_docs = ListDocuments()
        except Exception as e:
            print(f"Warning: ListDocuments init failed: {e}")


def handler(event, context):
    """Lambda entry point invoked by AgentCore Gateway."""
    try:
        print(f"Event: {json.dumps(event, default=str)}")
        _init_tools()

        # 1. Auth: extract client_id and get permissions
        client_id = get_client_id(event)
        if not client_id:
            return {"error": "No client_id found in request"}

        permissions = get_permissions(client_id)
        if not permissions:
            return {"error": f"No permissions found for client {client_id}"}

        # 2. Parse tool arguments from event
        args = event if isinstance(event, dict) else json.loads(event)

        # 3. Route to tool based on arguments
        if "query" in args:
            if not _search:
                return {"error": "Search not available — OPENSEARCH_ENDPOINT not configured"}
            perm_filter = build_opensearch_filter(permissions)
            return _search.search(
                query=args["query"],
                permissions_filter=perm_filter,
                top_k=args.get("top_k", 5),
            )

        if "question" in args:
            if not _assistant:
                return {"error": "Assistant not available — DOCS_BUCKET not configured"}
            return _assistant.ask(
                question=args["question"],
                permissions=permissions,
            )

        if "list_documents" in args or (not args.get("query") and not args.get("question")):
            if not _list_docs:
                return {"error": "List not available — DOCS_BUCKET not configured"}
            return _list_docs.list(
                permissions=permissions,
                folder=args.get("folder"),
            )

        return {
            "error": f"Cannot determine tool from arguments: {list(args.keys())}",
            "available_tools": ["document_search", "document_assistant", "list_documents"],
        }

    except Exception as e:
        print(f"Error: {e}\n{traceback.format_exc()}")
        return {"error": str(e)}
