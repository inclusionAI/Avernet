"""Native model conventions for the OpenClaw port.

The OpenClaw gateway speaks dict-shaped JSON frames, so the port's native shapes
are plain dicts / lists + `engine.community.kernel.frames` types — NOT core DTOs (the leaf
rule forbids `plugins → core`) and NOT bespoke native dataclasses (which would
merely re-describe the wire dicts). The `core/adapters/openclaw/` adapters own
all dict↔DTO marshalling; that is where the anti-corruption translation lives.

`EngineToken` is the per-call routing key the port uses in place of the core
`AuthContext`: the adapter extracts `auth.token` and passes it down, so neither
`plugin_api` nor `plugins` ever imports `engine.community.core.engine.context.AuthContext`.
"""
from __future__ import annotations

from typing import TypeAlias

# Per-call routing key. Adapters pass `auth.token`; the port impl uses it for
# TokenClientPool routing. Never the core AuthContext (leaf rule).
EngineToken: TypeAlias = str | None

__all__ = ["EngineToken"]
