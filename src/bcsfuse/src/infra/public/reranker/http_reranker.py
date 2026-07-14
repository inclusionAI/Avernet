"""
HTTP Reranker - OSS Wrapper

Wraps existing HTTP reranker for OSS compatibility.
"""
from src.infra.reranker.http_reranker import HttpReranker as _HttpReranker


class HttpReranker(_HttpReranker):
    """
    HTTP Reranker for OSS.

    This is a thin wrapper around the existing HTTP reranker
    to maintain consistent naming and future extensibility.

    Supports bge-reranker-v2-m3 and Qwen3-Reranker-4B models.
    Requires RERANKER_BASE_URL and RERANKER_API_KEY environment variables.
    """

    pass