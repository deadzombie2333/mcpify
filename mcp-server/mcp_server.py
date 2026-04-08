"""
mcpify MCP Server — generic document knowledge base.

Exposes 3 tools:
  1. document_search  — semantic search via OpenSearch
  2. document_assistant — LLM Q&A from S3 docs
  3. list_documents — browse accessible documents

All tools are permission-filtered per caller's token.
"""

from typing import Optional, Dict, Any
from fastmcp import FastMCP

from tools import DocumentSearch, DocumentAssistant, ListDocuments

mcp = FastMCP("mcpify")

# Lazy-init tools (initialized on first call in Lambda)
_search = None
_assistant = None
_list_docs = None


def _get_search():
    global _search
    if _search is None:
        _search = DocumentSearch()
    return _search


def _get_assistant():
    global _assistant
    if _assistant is None:
        _assistant = DocumentAssistant()
    return _assistant


def _get_list():
    global _list_docs
    if _list_docs is None:
        _list_docs = ListDocuments()
    return _list_docs


@mcp.tool()
def document_search(query: str, top_k: int = 5) -> Dict[str, Any]:
    """
    Semantic search across your knowledge base.

    Finds the most relevant document sections using vector similarity.
    Results are filtered to only documents you have access to.

    Args:
        query: Natural language search query
        top_k: Number of results to return (default: 5)
    """
    # permissions_filter injected by lambda_handler before calling
    return _get_search().search(query=query, top_k=top_k)


@mcp.tool()
def document_assistant(question: str) -> Dict[str, Any]:
    """
    Ask a question and get an answer based on your accessible documents.

    Reads documents you have access to and uses an LLM to answer.
    Only uses information from your authorized documents.

    Args:
        question: Your question in natural language
    """
    return _get_assistant().ask(question=question)


@mcp.tool()
def list_documents(folder: Optional[str] = None) -> Dict[str, Any]:
    """
    Browse documents you have access to.

    Lists all accessible documents with metadata. Optionally filter by folder.

    Args:
        folder: Optional folder name to filter (e.g. "api-docs")
    """
    return _get_list().list(folder=folder)


if __name__ == "__main__":
    mcp.run()
