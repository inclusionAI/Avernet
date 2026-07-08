"""Core adapters — the Anti-Corruption Layer (ACL).

Each ``<engine>/`` subpackage implements the granular core ``*Service``
protocols (``engine.community.core.<domain>.protocol``) for one engine by
converting service DTOs to/from that engine's native shape and delegating
to its ``engine.community.plugin_api`` port. Adapters also guard capability: a
capability exists for an engine only if its adapter wires a native method
for it.

Adapters import the port *abstraction* from ``engine.community.plugin_api`` — never
the concrete impl from ``engine.plugins`` (``engine.community.di`` injects that).
So ``engine.community.core`` never imports ``engine.plugins``.

Empty skeleton in F1; adapters land starting F2.
"""
