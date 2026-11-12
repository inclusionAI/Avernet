"""Trusted, environment-scoped task asset discovery backed by system-config."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

CATEGORY = "task"
KEY = "panel"


def _canonical(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold().strip()
    return "".join(char for char in normalized if not unicodedata.combining(char))


def is_panel_config(category: str, key: str) -> bool:
    """Include aliases that case/width/accent-insensitive DB collations equate."""
    return _canonical(category) == CATEGORY and _canonical(key) == KEY


@dataclass(frozen=True)
class PanelBundle:
    name: str
    url: str


@dataclass(frozen=True)
class PanelManifestConfig:
    schema_version: int
    bundles: tuple[PanelBundle, ...]

    @classmethod
    def from_value(cls, value: object) -> PanelManifestConfig:
        if not isinstance(value, dict) or set(value) != {"schema_version", "bundles"}:
            raise ValueError("Expected schema_version and bundles")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("Unsupported manifest schema")
        bundles = value["bundles"]
        if not isinstance(bundles, list) or len(bundles) != 1:
            raise ValueError("Expected exactly one taskPanel bundle")
        bundle = bundles[0]
        if not isinstance(bundle, dict) or set(bundle) != {"name", "type", "url"}:
            raise ValueError("Invalid task panel bundle")
        if bundle["name"] != "taskPanel" or bundle["type"] != "url":
            raise ValueError("Only taskPanel URL bundles are supported")
        url = bundle["url"]
        if not isinstance(url, str) or url != url.strip() or any(char.isspace() for char in url):
            raise ValueError("Invalid bundle URL")
        try:
            parsed = urlsplit(url)
            # Accessing port also rejects malformed/out-of-range ports.
            parsed.port
        except ValueError:
            raise ValueError("Invalid bundle URL") from None
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.path.endswith("/index.umd.js")
                or "\\" in url or "/latest/" in parsed.path):
            raise ValueError("Expected a fixed HTTPS index.umd.js URL")
        if parsed.hostname in {"cdn.jsdelivr.net", "unpkg.com"} and not re.search(
            r"@avernet-assets/task-panel@\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?/dist/index\.umd\.js$", parsed.path
        ):
            raise ValueError("npm CDN bundles must pin an exact task-panel version")
        return cls(1, (PanelBundle("taskPanel", url),))

    def to_value(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "bundles": [
            {"name": bundle.name, "type": "url", "url": bundle.url}
            for bundle in self.bundles
        ]}


@dataclass(frozen=True)
class TaskPanelManifest:
    schema_version: int
    env: str
    bundles: tuple[PanelBundle, ...]


@runtime_checkable
class TaskPanelManifestServiceProtocol(Protocol):
    def get_manifest(self, *, env: str) -> TaskPanelManifest: ...

    def set_manifest(self, *, value: dict[str, object], env: str, operator: str) -> TaskPanelManifest:
        """Create or fully replace the current environment's validated manifest.

        The caller supplies the authenticated actor for audit fields.
        Invalid input raises ValueError;
        storage failure raises InternalError, never a successful response.
        """
        ...
