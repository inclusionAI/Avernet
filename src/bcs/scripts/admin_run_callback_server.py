#!/usr/bin/env python3
"""Local receiver for organization admin-run callbacks.

This utility intentionally uses only Python standard library modules.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Mapping
from typing import Any


JsonObject = dict[str, Any]


def now_ms() -> int:
    return int(time.time() * 1000)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        str(name): "<redacted>"
        if str(name).lower() == "authorization"
        else str(value)
        for name, value in headers.items()
    }


class CallbackStore:
    """Thread-safe in-memory callback capture store."""

    def __init__(self) -> None:
        self._records: list[JsonObject] = []
        self._seen_counts: dict[str, int] = {}
        self._lock = threading.RLock()

    def record(
        self,
        headers: Mapping[str, str],
        body: JsonObject,
        method: str = "POST",
        path: str = "/callback",
    ) -> JsonObject:
        run_id_value = body.get("run_id")
        run_id = str(run_id_value) if run_id_value is not None else ""
        with self._lock:
            seen_count = self._seen_counts.get(run_id, 0) if run_id else 0
            if run_id:
                self._seen_counts[run_id] = seen_count + 1
            record: JsonObject = {
                "received_at": now_ms(),
                "method": method,
                "path": path,
                "headers": redact_headers(headers),
                "body": copy.deepcopy(body),
                "run_id": run_id,
                "duplicate": seen_count > 0,
            }
            self._records.append(record)
            return copy.deepcopy(record)

    def snapshot(self) -> JsonObject:
        with self._lock:
            return {
                "callbacks": copy.deepcopy(self._records),
                "duplicate_counts": {
                    run_id: count - 1
                    for run_id, count in self._seen_counts.items()
                    if count > 1
                },
            }

    def for_run(self, run_id: str) -> list[JsonObject]:
        with self._lock:
            return copy.deepcopy(
                [record for record in self._records if record["run_id"] == run_id]
            )

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._seen_counts.clear()
