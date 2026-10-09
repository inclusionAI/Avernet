"""OpenClawPlugin — the aggregate native port Protocol for the OpenClaw engine.

The facade composes one per-domain Protocol per service (`OpenClawSessionPort`,
`OpenClawChatPort`, …), each defined in a sibling module and mixed into this
facade's bases as the corresponding Group C/D vertical slice lands. One concrete
impl in `plugins/community/openclaw/` satisfies the whole facade (sharing a gateway
client + token pool); each `core/adapters/openclaw/` adapter receives that impl
typed as its domain port.

Conventions (see `specs/2026-05-31-engine-arch-f2-openclaw-acl/port-design-notes.md`):
- every method takes `token: str | None = None` (never the core `AuthContext`)
- native returns are dicts / `kernel.frames` types, never core DTOs
- capability-gated ops are still declared here; the impl satisfies by raising
  `CapabilityNotSupportedError` and the adapter's guard is the clean home for the
  not-supported decision
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.plugin_api.openclaw.approval import OpenClawApprovalPort
from engine.community.plugin_api.openclaw.chat import OpenClawChatPort
from engine.community.plugin_api.openclaw.cron import OpenClawCronPort
from engine.community.plugin_api.openclaw.default_config import OpenClawDefaultConfigPort
from engine.community.plugin_api.openclaw.file import OpenClawFilePort
from engine.community.plugin_api.openclaw.mcp import OpenClawMcpPort
from engine.community.plugin_api.openclaw.models_port import OpenClawModelsPort
from engine.community.plugin_api.openclaw.node import OpenClawNodePort
from engine.community.plugin_api.openclaw.relay import OpenClawRelayPort
from engine.community.plugin_api.openclaw.session import OpenClawSessionPort
from engine.community.plugin_api.openclaw.skills import OpenClawSkillsPort
from engine.community.plugin_api.openclaw.web_shell import OpenClawWebShellPort


@runtime_checkable
class OpenClawPlugin(
    OpenClawNodePort,
    OpenClawRelayPort,
    OpenClawApprovalPort,
    OpenClawModelsPort,
    OpenClawCronPort,
    OpenClawChatPort,
    OpenClawSessionPort,
    OpenClawFilePort,
    OpenClawDefaultConfigPort,
    OpenClawMcpPort,
    OpenClawSkillsPort,
    OpenClawWebShellPort,
    Protocol,
):
    """Aggregate OpenClaw native port.

    Grows one per-domain Protocol at a time as Group C (gateway services) and
    Group D (local-infra) vertical slices land. Each slice adds
    `class OpenClaw<Domain>Port(Protocol): ...` in a sibling module and appends it
    to this facade's base list. Keep domain method names domain-prefixed
    (`session_*`, `chat_*`, …) so the composed facade never silently unifies two
    same-named methods.

    NOTE: `runtime_checkable` only checks method *names*, not signatures (and an
    empty facade matches everything). Do not use `isinstance` as a
    wiring/conformance guard — rely on the static type checker against the
    `@provider` return type (and the F6 conformance tests) instead.

    Composed domain ports: OpenClawNodePort, OpenClawRelayPort, OpenClawApprovalPort, OpenClawModelsPort, OpenClawCronPort, OpenClawChatPort, OpenClawSessionPort, OpenClawFilePort, OpenClawDefaultConfigPort, OpenClawMcpPort, OpenClawSkillsPort, OpenClawWebShellPort.
    """

    ...
