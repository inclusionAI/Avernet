"""Local claude_code test double.

In-memory / no-network implementation of the ``ClaudeCodePlugin`` aggregate
port. ``plugins/local/`` is for deterministic tests/contracts only — it never
opens a relay connection or spawns a Node subprocess.

Note: only ``LocalClaudeCodePluginImpl`` is exported. The real impl
``ClaudeCodePluginImpl`` lives in ``plugins/claude_code/`` (profile-neutral
shared); re-exporting it here under the local alias would shadow the real impl
and was removed.
"""
from engine.community.local.claude_code.plugin_impl import LocalClaudeCodePluginImpl

__all__ = ["LocalClaudeCodePluginImpl"]
