"""ClaudeCodePluginImpl — thin composition of the per-domain mixins.

One impl object satisfies the whole ``ClaudeCodePlugin`` facade, sharing a
single ``ClaudeCodeRelayClient`` (the relay is single-tenant per process, so
unlike openclaw there is no token pool here — ``token`` is accepted on every
port method for interface parity but client selection is always the single
connection).

Domain methods are split into per-domain mixin files under
``plugins/claude_code/``; each mixin is a plain class (no base, no
Protocol) Relay-backed mixins use ``ClaudeCodePortBase``; file I/O and runtime layout
operations execute locally in the Engine filesystem.

``ClaudeCodePluginImpl`` explicitly inherits ``ClaudeCodePlugin`` so static
type-checkers verify full facade conformance. Importing ``plugin_api`` from
``plugins`` is the allowed DIP edge (leaf → abstraction).
"""
from __future__ import annotations

from pathlib import Path

from engine.community.plugin_api.claude_code.plugin import ClaudeCodePlugin
from engine.community.plugins.claude_code._base import ClaudeCodePortBase, ClaudeCodeRelayClient
from engine.community.plugins.claude_code._chat import _ChatPortMixin
from engine.community.plugins.claude_code._commands import _CommandsPortMixin
from engine.community.plugins.claude_code._cron import _CronPortMixin
from engine.community.plugins.claude_code._file import _FilePortMixin
from engine.community.plugins.claude_code._mcp import _McpPortMixin
from engine.community.plugins.claude_code._models import _ModelsPortMixin
from engine.community.plugins.claude_code._relay import _RelayPortMixin
from engine.community.plugins.claude_code._session import _SessionPortMixin
from engine.community.plugins.claude_code._skills import _SkillsPortMixin


class ClaudeCodePluginImpl(
    _ChatPortMixin,
    _SessionPortMixin,
    _McpPortMixin,
    _SkillsPortMixin,
    _CronPortMixin,
    _ModelsPortMixin,
    _FilePortMixin,
    _CommandsPortMixin,
    _RelayPortMixin,
    ClaudeCodePortBase,
    ClaudeCodePlugin,
):
    """Claude Code capabilities: local files/layout, relay-backed chat.

    ``client`` is optional — tests inject a fake; production lazily connects a
    fresh ``ClaudeCodeRelayClient`` on first use.
    """

    def __init__(self, client: ClaudeCodeRelayClient | None = None, *, file_roots: tuple[Path, ...] = (), workspace: Path | None = None) -> None:
        super().__init__(client=client)
        self._file_roots = tuple(root.resolve() for root in file_roots)
        self._file_workspace = workspace


__all__ = ["ClaudeCodePluginImpl"]
