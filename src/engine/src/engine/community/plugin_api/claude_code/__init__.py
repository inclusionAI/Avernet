"""ClaudeCode native port — the engine-owned ACL boundary.

``ClaudeCodePlugin`` is the single aggregate Protocol the claude_code engine
owns: its native operation surface, expressed in native shapes (dicts + kernel
frames, never core DTOs). The concrete impl lives in ``plugins/``; the
``core/adapters/claude_code/`` adapters translate the core ``*Service``
protocols to/from this port. This package imports only ``engine.community.kernel``
(+ stdlib/typing).

See ``specs/2026-07-01-engine-claude-code-acl-opensource/`` for the design notes.
"""
from engine.community.plugin_api.claude_code.plugin import ClaudeCodePlugin

__all__ = ["ClaudeCodePlugin"]
