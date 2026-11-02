"""Transport-independent query normalization shared by search and cache keys."""

from __future__ import annotations

import re
import unicodedata

_PUNCTUATION = str.maketrans(
    {
        "，": ", ",
        "、": ", ",
        "；": "; ",
        "：": ": ",
        "！": "! ",
        "？": "? ",
        "（": " ( ",
        "）": ") ",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "。": ". ",
        "！": "! ",
    }
)
_WHITESPACE = re.compile(r"\s+")


def normalize_query_text(value: str) -> str:
    """Return a stable retrieval and cache representation.

    The transformation is grammatical rather than semantic: validate input,
    apply NFKC, map common CJK punctuation to its ASCII equivalent, collapse
    whitespace, and case-fold. Identifier separators used by profile keys are
    intentionally preserved.
    """

    if not isinstance(value, str):
        raise ValueError("query must be a string")
    normalized = unicodedata.normalize("NFKC", value).translate(_PUNCTUATION)
    return _WHITESPACE.sub(" ", normalized).strip().casefold()


__all__ = ["normalize_query_text"]
