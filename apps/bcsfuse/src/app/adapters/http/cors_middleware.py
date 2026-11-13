"""CORS middleware that accepts a list of origin patterns."""

import re

from fastapi.middleware.cors import CORSMiddleware


class RegexCORSMiddleware(CORSMiddleware):
    """Match configured patterns without mutating the shared allowlist."""

    def __init__(self, app, allow_origin_regex: list[str] | None = None, **kwargs):
        self._compiled_patterns = [
            re.compile(".*" if pattern == "*" else pattern)
            for pattern in (allow_origin_regex or [])
        ]
        super().__init__(app, **kwargs)

    def is_allowed_origin(self, origin: str) -> bool:
        return super().is_allowed_origin(origin) or any(
            pattern.fullmatch(origin) for pattern in self._compiled_patterns
        )
