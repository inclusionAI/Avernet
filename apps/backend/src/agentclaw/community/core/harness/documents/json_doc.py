"""JsonDocument — structured JSON parsing and merge-patch support.

Zero external dependencies; uses only stdlib.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class JsonDocument:
    """Structured JSON document supporting RFC 7396 merge-patch semantics."""

    meta: dict[str, str] = field(default_factory=dict)
    value: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""

    @classmethod
    def parse(cls, raw_text: str, bot_id: str = "", file_type: str = "") -> JsonDocument:
        """Parse raw JSON text."""
        checksum = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        value = json.loads(raw_text)
        return cls(
            meta={
                "file_type": file_type,
                "bot_id": bot_id,
                "version": "1.0",
                "checksum": checksum,
            },
            value=value if isinstance(value, dict) else {"_value": value},
            raw_text=raw_text,
        )

    def serialize(self, indent: int = 2) -> str:
        return json.dumps(self.value, indent=indent, ensure_ascii=False) + "\n"

    def get(self, key: str, default: Any = None) -> Any:
        return self.value.get(key, default)

    def set(self, key: str, val: Any) -> None:
        self.value[key] = val
        self.raw_text = self.serialize()

    def delete(self, key: str) -> bool:
        if key in self.value:
            del self.value[key]
            self.raw_text = self.serialize()
            return True
        return False

    def merge_patch(self, patch: dict[str, Any]) -> None:
        """Apply RFC 7396 merge-patch inplace."""
        self.value = self._merge_patch(self.value, patch)
        self.raw_text = self.serialize()

    @staticmethod
    def _merge_patch(target: Any, patch: Any) -> Any:
        if not isinstance(patch, dict):
            return deepcopy(patch)
        if not isinstance(target, dict):
            target = {}
        result = deepcopy(target)
        for key, val in patch.items():
            if val is None:
                result.pop(key, None)
            else:
                result[key] = JsonDocument._merge_patch(result.get(key), val)
        return result

    def compute_diff(self, other: JsonDocument) -> str:
        """Return a simple key-level diff summary (not unified-diff)."""
        diffs: list[str] = []
        all_keys = set(self.value.keys()) | set(other.value.keys())
        for key in sorted(all_keys):
            a = json.dumps(self.value.get(key), sort_keys=True)
            b = json.dumps(other.value.get(key), sort_keys=True)
            if a != b:
                diffs.append(f"- {key}: {a}\n+ {key}: {b}")
        return "\n".join(diffs) if diffs else ""
