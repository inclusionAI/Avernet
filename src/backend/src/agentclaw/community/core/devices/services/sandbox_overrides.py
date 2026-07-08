"""SandboxOverrides value object for template_config sandbox override parameters.

Extracts image/command/envs/resource_spec from template_config, validates them,
merges envs over platform defaults, and produces kwargs for Arca SDK's
create_sync_sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agentclaw.community.kernel.device_dto import ResourceSpecification


class InvalidSandboxOverridesError(ValueError):
    """template_config sandbox override validation failed."""


@dataclass(frozen=True)
class SandboxOverrides:
    """Immutable value object representing sandbox override parameters.

    template_config keys use snake_case:
        image         -> image
        command       -> command
        envs          -> envs
        resource_spec -> resource_spec
    """

    image: Optional[str] = None
    command: Optional[str] = None
    envs: dict[str, str] = field(default_factory=dict)
    resource_spec: Optional[ResourceSpecification] = None

    @classmethod
    def from_template_config(cls, tc: Optional[dict]) -> "SandboxOverrides":
        """Extract override fields from template_config dict. Unknown fields ignored.

        None/empty dict -> empty instance (is_empty() == True).

        Backward compatibility: if ``command`` is a list, elements are joined
        with spaces to produce a single string (Arca SDK accepts ``Optional[str]``).
        """
        if not tc:
            return cls()

        image = tc.get("image")

        raw_command = tc.get("command")
        command: Optional[str] = None
        if raw_command is not None:
            if isinstance(raw_command, list):
                command = " ".join(str(c) for c in raw_command)
            else:
                command = str(raw_command)

        raw_envs = tc.get("envs")
        envs: dict[str, str] = {}
        if raw_envs and isinstance(raw_envs, dict):
            envs = {str(k): str(v) for k, v in raw_envs.items()}

        resource_spec: Optional[ResourceSpecification] = None
        raw_spec = tc.get("resource_spec")
        if raw_spec is not None and isinstance(raw_spec, dict):
            try:
                spec_kwargs: dict = {
                    "cpu": int(raw_spec["cpu"]),
                    "memory": int(raw_spec["memory"]),
                }
                if "disk" in raw_spec and raw_spec["disk"] is not None:
                    spec_kwargs["disk"] = int(raw_spec["disk"])
                resource_spec = ResourceSpecification(**spec_kwargs)
            except (KeyError, ValueError, TypeError):
                resource_spec = None  # type: ignore[assignment]

        return cls(image=image, command=command, envs=envs, resource_spec=resource_spec)

    def is_empty(self) -> bool:
        """All None / empty envs -> True. Caller uses this to skip override logic."""
        return (
            self.image is None
            and self.command is None
            and not self.envs
            and self.resource_spec is None
        )

    def validate(self) -> None:
        """Validate provided fields. Raise InvalidSandboxOverridesError on failure."""
        if self.image is not None and (not isinstance(self.image, str) or not self.image.strip()):
            raise InvalidSandboxOverridesError("image must be non-empty string")

        if self.command is not None:
            if not isinstance(self.command, str) or not self.command.strip():
                raise InvalidSandboxOverridesError("command must be non-empty string")

        if self.envs and isinstance(self.envs, dict):
            for k, v in self.envs.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise InvalidSandboxOverridesError(
                        f"envs must be dict of string→string, got key={k!r} value={v!r}"
                    )

        if self.resource_spec is not None:
            if not isinstance(self.resource_spec, ResourceSpecification):
                raise InvalidSandboxOverridesError(
                    "resource_spec must be a ResourceSpecification object"
                )
            if not isinstance(self.resource_spec.cpu, int) or self.resource_spec.cpu <= 0:
                raise InvalidSandboxOverridesError(
                    f"resource_spec.cpu must be a positive integer, got: {self.resource_spec.cpu!r}"
                )
            if not isinstance(self.resource_spec.memory, int) or self.resource_spec.memory <= 0:
                raise InvalidSandboxOverridesError(
                    f"resource_spec.memory must be a positive integer, got: {self.resource_spec.memory!r}"
                )

    def merged_envs(self, base_envs: dict[str, str]) -> dict[str, str]:
        """Merge: {**base_envs, **self.envs}. User values override platform defaults."""
        if not self.envs:
            return dict(base_envs)
        return {**base_envs, **self.envs}

    def to_create_kwargs(self) -> dict:
        """Return non-None fields as kwargs dict for **-unpacking into create_sync_sandbox.

        Keys are snake_case matching Arca SDK: image, command, resource_spec.
        envs NOT included here -- use merged_envs() separately.
        """
        kwargs: dict = {}
        if self.image is not None:
            kwargs["image"] = self.image
        if self.command is not None:
            kwargs["command"] = self.command
        if self.resource_spec is not None:
            kwargs["resource_spec"] = self.resource_spec
        return kwargs
