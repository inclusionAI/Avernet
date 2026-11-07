# Preserve Bot MCP overrides across SkillSet lifecycle changes

Accepted design; implementation in progress.

A Bot MCP override is independent of its current supplier. SkillSet membership, activation, Default exclusion and capability reconciliation or compensation must not erase it. Direct explicit uninstall and Bot deletion retain their cleanup contract.

Manifest retains inline parent cleanup within its Direct-only scope: removing a managed MCP also removes its override. A retained MCP with no config declaration preserves its override; an explicit empty object clears it and a nonempty object replaces it. This intentionally changes the former bare-entry reset behavior. Genome absent override remains an explicit clear target, preserving complete-snapshot restoration.

The existing tables and public YAML shape suffice. Preserve does not write a stale snapshot back. This avoids lifecycle data loss without introducing a new SkillSet-level configuration snapshot or stronger cross-request consistency mechanism.

Contract and acceptance: [Spec](../../apps/backend/specs/2026-11-02-mcp-config-lifecycle/spec.md).
