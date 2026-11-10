"""Load legacy TC provider settings at construction, outside shared execution."""

import os


def qdrant_path(path):
    return path or os.getenv("QDRANT_LOCAL_PATH", "./qdrant_storage")


def embedding_options(max_retries, base_delay_ms, request_interval_ms):
    return dict(
        max_retries=int(os.environ.get("EMBEDDING_MAX_RETRIES", max_retries)),
        base_delay_ms=int(os.environ.get("EMBEDDING_RETRY_BACKOFF_SECONDS", "5"))
        * 1000,
        request_interval_ms=int(
            os.environ.get("EMBEDDING_REQUEST_INTERVAL_MS", request_interval_ms)
        ),
    )


def reranker_options(model):
    return dict(
        base_url=os.getenv("RERANKER_BASE_URL", os.getenv("EMBEDDING_BASE_URL", "")),
        api_key=os.getenv("RERANKER_API_KEY", os.getenv("EMBEDDING_AUTH_TOKEN", "")),
        model=model
        if model is not None
        else os.getenv("RERANKER_MODEL", "Qwen3-Reranker-4B"),
        timeout=int(os.getenv("RERANKER_TIMEOUT", "30")),
    )
