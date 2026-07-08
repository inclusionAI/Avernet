"""ClaudeCodePlugin — the aggregate native port Protocol for the claude_code engine.

The facade composes one per-domain Protocol per service (``ClaudeCodeChatPort``,
``ClaudeCodeSessionPort``, …), each defined in a sibling module. One concrete
impl in ``plugins/`` satisfies the whole facade (sharing a relay client + token
pool); each ``core/adapters/claude_code/`` adapter receives that impl typed as
its domain port.

Conventions (see ``specs/2026-07-01-engine-claude-code-acl-opensource/``):
- every method takes ``token: str | None = None`` (never the core ``AuthContext``)
- native returns are dicts / ``kernel.frames`` types, never core DTOs
- capability-gated ops are still declared here; the impl satisfies by raising
  ``CapabilityNotSupportedError`` and the adapter's guard is the clean home for
  the not-supported decision
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.plugin_api.claude_code.chat import ClaudeCodeChatPort
from engine.community.plugin_api.claude_code.commands import ClaudeCodeCommandsPort
from engine.community.plugin_api.claude_code.cron import ClaudeCodeCronPort
from engine.community.plugin_api.claude_code.file import ClaudeCodeFilePort
from engine.community.plugin_api.claude_code.mcp import ClaudeCodeMcpPort
from engine.community.plugin_api.claude_code.models_port import ClaudeCodeModelsPort
from engine.community.plugin_api.claude_code.relay import ClaudeCodeRelayPort
from engine.community.plugin_api.claude_code.session import ClaudeCodeSessionPort
from engine.community.plugin_api.claude_code.skills import ClaudeCodeSkillsPort


@runtime_checkable
class ClaudeCodePlugin(
    ClaudeCodeChatPort,
    ClaudeCodeSessionPort,
    ClaudeCodeMcpPort,
    ClaudeCodeSkillsPort,
    ClaudeCodeCronPort,
    ClaudeCodeModelsPort,
    ClaudeCodeFilePort,
    ClaudeCodeCommandsPort,
    ClaudeCodeRelayPort,
    Protocol,
):
    """Aggregate claude_code native port.

    Grows one per-domain Protocol at a time as vertical slices land. Each slice
    adds ``class ClaudeCode<Domain>Port(Protocol): ...`` in a sibling module and
    appends it to this facade's base list. Keep domain method names
    domain-prefixed (``session_*``, ``chat_*``, …) so the composed facade never
    silently unifies two same-named methods.

    NOTE: ``runtime_checkable`` only checks method *names*, not signatures (and
    an empty facade matches everything). Do not use ``isinstance`` as a
    wiring/conformance guard — rely on the static type checker against the
    ``@provider`` return type (and the F6 conformance tests) instead.

    Composed domain ports: ClaudeCodeChatPort, ClaudeCodeSessionPort, ClaudeCodeMcpPort, ClaudeCodeSkillsPort, ClaudeCodeCronPort, ClaudeCodeModelsPort, ClaudeCodeFilePort, ClaudeCodeCommandsPort, ClaudeCodeRelayPort.
    """

    ...
