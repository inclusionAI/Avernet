"""Shared HTTP contract primitives for the gateway's public API.

The response envelope, pagination controls, and small shared payloads that
every `/openapi/v1` route reuses. These are *contract definitions* consumed by
OpenAPI generation; the gateway forwards downstream responses verbatim at
runtime and does not construct an envelope itself.
"""

from .envelope import (
    CODE_ACCEPTED,
    CODE_CREATED,
    CODE_NO_CONTENT,
    CODE_OK,
    Deleted,
    Envelope,
    NameCheck,
    Page,
)
from .pagination import PageParams, PageParamsDep
from .security import user_principal

__all__ = [
    "CODE_ACCEPTED",
    "CODE_CREATED",
    "CODE_NO_CONTENT",
    "CODE_OK",
    "Deleted",
    "Envelope",
    "NameCheck",
    "Page",
    "PageParams",
    "PageParamsDep",
    "user_principal",
]
