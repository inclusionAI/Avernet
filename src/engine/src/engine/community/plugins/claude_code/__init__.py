"""Concrete claude_code port implementation (shared profile-neutral transport).

Implements ``engine.community.plugin_api.claude_code.ClaudeCodePlugin`` over the vendored
claude_code Node relay client (``ws://127.0.0.1:18900``). A **pure leaf**:
imports ``engine.community.plugin_api`` + ``engine.community.kernel`` + ``engine.community.openclaw.protocol``
(shared handshake types) + stdlib — never ``engine.community.core`` or ``engine.community.api``.

This is the shared claude_code plugin implementation used by both the community
and (future) corp composition roots; it lives directly under ``plugins/`` so it
is not branded as a ``community/`` or ``prod/`` variant. The composition root
that assembles these impls into an engine lives OUTSIDE this leaf, at
``engines/claude_code_community/engine.py`` (mirroring ``engines/openclaw/engine.py``).
"""
