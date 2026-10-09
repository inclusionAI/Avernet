"""Native model conventions for the claude_code port.

The claude_code gateway (vendored Node relay) speaks dict-shaped JSON frames,
so the port's native shapes are plain dicts / lists + ``engine.community.kernel.frames``
types — NOT core DTOs (the leaf rule forbids ``plugins -> core``) and NOT
bespoke native dataclasses (which would merely re-describe the wire dicts).
The ``core/adapters/claude_code/`` adapters own all dict<->DTO marshalling;
that is where the anti-corruption translation lives.

Currently no bespoke native types are needed — every port method uses
primitive dicts / lists / bools / ``kernel.frames`` types directly. This module
exists as the placeholder for future native DTOs if the port grows them.
"""
from __future__ import annotations

__all__: list[str] = []
