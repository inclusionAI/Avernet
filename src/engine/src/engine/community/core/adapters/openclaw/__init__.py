"""OpenClaw ACL adapters.

One adapter per core `*Service` protocol OpenClaw supports. Each implements the
core protocol by delegating to an injected `OpenClawPlugin`, converting core
DTOs ↔ the port's native dict/frame shapes and guarding capability. Imports
`engine.community.core` + `engine.community.plugin_api` + `engine.community.kernel` only — never
`engine.plugins` (the DI module is the only site that touches the concrete impl).

Populated one vertical slice at a time in Groups C (gateway) and D (local-infra).
"""
