"""Neutral device data carriers — the vendor-agnostic shapes the device layer
exchanges across the four layers.

These replace the ``arca.model.sandbox`` value objects that previously leaked
the AntGroup container-runtime SDK into ``core``/``api``. They are pure data
(kernel discipline: stdlib only, no business logic, field names ARE the API).

Who shares them:

- ``core`` constructs them (BaaS bot-create payload, shell-exec result,
  template-config resource overrides);
- ``plugins/prod`` serializes them to the BaaS JSON wire shape and, at the ARCA
  SandboxFactory boundary only, converts them to the ``arca.model.sandbox`` SDK
  types;
- ``api`` exposes them as Protocol signature types.

The JSON wire shape the BaaS endpoints expect is preserved by keeping the field
names identical to the SDK's — the consumers that hand-build the create-bot
payload read ``.cpu`` / ``.memory`` / ``.disk`` and each rule's ``.domains`` /
``.action`` / ``.header_name`` / ``.value`` / ``.placeholder`` directly, so this
is a like-for-like swap with no behavior change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandResult:
    """Result of a shell command executed inside a bot device/sandbox.

    Field set mirrors the former ``arca.model.sandbox.CommandResult`` carrier so
    the exec-shell call sites (BaaS + ARCA) need no behavioral change.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    elapsed_time: float = 0.0
    status: str = ""
    error: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "elapsed_time": self.elapsed_time,
            "status": self.status,
            "error": self.error,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandResult":
        return cls(
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            exit_code=int(data.get("exit_code", 0) or 0),
            elapsed_time=float(data.get("elapsed_time", 0) or 0),
            status=str(data.get("status") or ""),
            error=data.get("error"),
            extra={str(k): str(v) for k, v in (data.get("extra") or {}).items()},
        )


@dataclass(frozen=True)
class ResourceSpecification:
    """CPU / memory / disk request for a bot device/sandbox.

    Serialized into the BaaS create-bot payload as ``{"cpu", "memory"[, "disk"]}``
    (``disk`` omitted when ``None``) — matches the prior hand-built shape.
    """

    cpu: int
    memory: int
    disk: int | None = None

    def to_dict(self) -> dict[str, Any]:
        spec: dict[str, Any] = {"cpu": self.cpu, "memory": self.memory}
        if self.disk is not None:
            spec["disk"] = self.disk
        return spec


@dataclass(frozen=True)
class HeaderOperationRule:
    """A single outbound-header mutation applied to a bot's egress traffic.

    ``action`` ∈ {"set", "replace"}; ``placeholder`` is only meaningful for
    ``replace`` (the substring replaced by ``value``).
    """

    domains: list[str]
    action: str
    header_name: str
    value: str
    placeholder: str | None = None
    separator: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "domains": list(self.domains),
            "action": self.action,
            "header_name": self.header_name,
            "value": self.value,
            "placeholder": self.placeholder,
            "separator": self.separator,
        }


@dataclass(frozen=True)
class OutBoundOperationRule:
    """The full set of outbound-header rules for a bot device/sandbox.

    ``frozen=True`` only freezes attribute rebinding; the rule collection is
    coerced to a ``tuple`` in ``__post_init__`` so the value object is genuinely
    shallow-immutable (no post-construction ``.append``). Callers may pass a
    ``list`` or any iterable.
    """

    header_operation_rules: tuple[HeaderOperationRule, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "header_operation_rules", tuple(self.header_operation_rules)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "header_operation_rules": [
                rule.to_dict() for rule in self.header_operation_rules
            ]
        }


@dataclass(frozen=True)
class SandboxInfo:
    """Neutral result of a sandbox create / info query.

    The vendor-agnostic shape a ``SandboxRuntimeClient`` returns — the device
    orchestration (``core``) reads these fields and never the SDK's own info
    object. ``status`` / ``ttl_in_minutes`` default to empty/zero for the create
    path (which only needs ``sandbox_id``).
    """

    sandbox_id: str
    status: str = ""
    ttl_in_minutes: int = 0


@dataclass(frozen=True)
class ProxyConnection:
    """Neutral proxy connection coordinates for a running sandbox.

    ``target`` is the runtime-specific routing string and ``token`` the signed
    proxy-pass credential; how they're built (ARCA target format + Mist-signed
    JWT) is the ``SandboxRuntimeClient`` impl's concern, not ``core``'s.
    """

    target: str
    token: str


@dataclass(frozen=True)
class ProxyRequest:
    """A ready-to-send proxied HTTP request to a sandbox-internal service.

    ``url`` is the full proxy URL (base + routing + api path) and ``headers``
    carries the signed proxy-pass credential. Consumers issue the HTTP/WS call;
    how the URL + headers are built (ARCA proxypass + Mist token) is the
    ``SandboxRuntimeClient`` impl's concern, not ``core``'s.
    """

    url: str
    headers: dict[str, str]
