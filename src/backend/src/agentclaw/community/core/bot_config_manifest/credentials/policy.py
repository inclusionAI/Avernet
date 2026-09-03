"""Structured HTTPS prefix authorization for manifest source credentials."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


class PrefixAuthorizationError(ValueError):
    """The target is outside a credential's explicitly authorized prefixes."""


def _decoded_path(path: str) -> str:
    # Decode before normalising so encoded separators and dot segments cannot
    # bypass the boundary. Repeat until stable for doubly encoded input.
    for _ in range(3):
        decoded = unquote(path or "/")
        if decoded == path:
            break
        path = decoded
    parts: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    # 尾斜杠非语义:``/team/content`` 与 ``/team/content/`` 是同一前缀,
    # canonical 形态统一无尾斜杠;空 path 即 root("/")。
    return "/" + "/".join(parts)


def _origin(parts) -> tuple[str, str, int]:
    if not parts.scheme or not parts.hostname or parts.username or parts.password:
        raise ValueError("prefix must be an absolute HTTPS URL without userinfo")
    scheme = parts.scheme.lower()
    if scheme != "https":
        raise ValueError("prefix must use HTTPS")
    port = parts.port or 443
    return scheme, parts.hostname.lower().rstrip("."), port


@dataclass(frozen=True)
class CanonicalPrefix:
    scheme: str
    host: str
    port: int
    path: str

    @classmethod
    def parse(cls, value: str) -> "CanonicalPrefix":
        try:
            parts = urlsplit(value)
            scheme, host, port = _origin(parts)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(
                "allowed_prefixes must contain absolute HTTPS prefixes"
            ) from exc
        if parts.query or parts.fragment:
            raise ValueError("allowed prefix must not contain query or fragment")
        return cls(scheme, host, port, _decoded_path(parts.path))

    def allows(self, target: str) -> bool:
        try:
            parts = urlsplit(target)
            scheme, host, port = _origin(parts)
        except (ValueError, UnicodeError):
            return False
        if (scheme, host, port) != (self.scheme, self.host, self.port):
            return False
        target_path = _decoded_path(parts.path)
        prefix = self.path.rstrip("/") or "/"
        return (
            prefix == "/"
            or target_path == prefix
            or target_path.startswith(prefix + "/")
        )


def validate_prefixes(prefixes: list[str]) -> tuple[CanonicalPrefix, ...]:
    if not prefixes:
        raise ValueError(
            "allowed_prefixes must contain at least one absolute HTTPS prefix"
        )
    parsed = tuple(
        CanonicalPrefix.parse(item.strip())
        for item in prefixes
        if isinstance(item, str)
    )
    if len(parsed) != len(prefixes):
        raise ValueError("allowed_prefixes must contain only strings")
    return parsed


class PrefixAuthorizationPolicy:
    """W2 AuthorizationPolicy implementation bound to one credential."""

    def __init__(self, credential_name: str, prefixes: list[str]) -> None:
        self.credential_name = credential_name
        self._prefixes = validate_prefixes(prefixes)

    def reauthorize(self, url) -> None:
        if not any(prefix.allows(str(url)) for prefix in self._prefixes):
            raise PrefixAuthorizationError(
                f"credential {self.credential_name!r} is not authorized for this source"
            )
