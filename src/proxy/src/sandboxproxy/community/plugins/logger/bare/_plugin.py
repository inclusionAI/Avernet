"""Bare logger plugin — stdlib logging, no sidecar."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any


class BareLoggerPlugin:
    """Logger plugin backed by stdlib ``logging`` (configured once)."""

    _configured = False

    def configure(
        self,
        *,
        log_level: str = "INFO",
        log_dir: str = "",
        app_name: str = "sandboxproxy",
        trace_log_dir: str = "",
    ) -> None:
        if BareLoggerPlugin._configured:
            return
        level = getattr(logging, log_level.upper(), logging.INFO)
        handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            handlers.append(
                logging.FileHandler(os.path.join(log_dir, f"{app_name}.log"))
            )
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            handlers=handlers,
        )
        BareLoggerPlugin._configured = True

    def get_logger(self, name: str) -> Any:
        return logging.getLogger(name)
