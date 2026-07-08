"""Runtime mode — the only place env is read to decide wiring.

``RuntimeConfig`` is built once by the composition root and passed to
``build_injector``. It is informational to the container itself: the caller
reads it to decide whether to layer ``Testing*`` override modules via
``extra_modules``. Business code in ``core``/``api`` never sees it — Rule 14
(no scattered ``if is_local_mode()``).

Mirror of ``src/backend/src/agentclaw/di/runtime_mode.py`` (engine has a single
mode dimension for now; a database dimension can be added later if needed).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from engine.community.di.profile import EngineProfile


class RuntimeMode(Enum):
    LOCAL = "local"
    PROD = "prod"

    @classmethod
    def detect(cls) -> "RuntimeMode":
        """LOCAL when ``RUNTIME_MODE=local`` (or ``SERVER_ENV`` is unset/dev),
        else PROD. Read only here."""
        runtime = os.getenv("RUNTIME_MODE", "").strip().lower()
        if runtime == "local":
            return cls.LOCAL
        if runtime in {"prod", "pre"}:
            return cls.PROD
        # Fall back to SERVER_ENV: anything other than an explicit prod/pre is
        # treated as local for wiring purposes.
        server_env = os.getenv("SERVER_ENV", "").strip().lower()
        return cls.PROD if server_env in {"prod", "pre"} else cls.LOCAL

    @property
    def is_local(self) -> bool:
        return self is RuntimeMode.LOCAL

    @property
    def is_prod(self) -> bool:
        return self is RuntimeMode.PROD


@dataclass(frozen=True)
class RuntimeConfig:
    runtime: RuntimeMode
    profile: EngineProfile = EngineProfile.COMMUNITY

    @classmethod
    def detect(cls) -> "RuntimeConfig":
        return cls(
            runtime=RuntimeMode.detect(),
            profile=EngineProfile.detect(),
        )
