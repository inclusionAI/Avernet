"""TC environment configuration around the shared model client."""

from app.infra.config.search_provider_settings import reranker_options
from search_core.providers.reranker import HttpReranker as SharedHttpReranker
from search_core.providers.reranker import requests


class HttpReranker(SharedHttpReranker):
    def __init__(self, model=None):
        super().__init__(**reranker_options(model))


def get_reranker(model: str | None = None):
    """Explicit request model wins; omitted model uses the TC process default."""
    return HttpReranker(model=model)
