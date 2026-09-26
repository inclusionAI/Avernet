"""Concrete OpenClaw port implementation (community transport).

Implements `engine.community.plugin_api.openclaw.OpenClawPlugin` over the real OpenClaw
gateway client + `TokenClientPool` (gateway services) and the local OS (mcporter
subprocess, PTY, workspace FS, …). A **pure leaf**: imports
`engine.community.plugin_api` + `engine.community.kernel` + the top-level `engine.community.openclaw` gateway
client + `engine.community.shared` + `engine.community.config` + stdlib — never `engine.community.core` or
`engine.community.api`.

Populated one vertical slice at a time in Groups C (gateway) and D (local-infra).
In F2 the assembled `OpenClawEngine` (`engines/openclaw/engine.py`) is the
composition root that imports this impl directly; the idiomatic
`engine.community.di.modules.openclaw_module` injector wiring lands in F5.
"""
