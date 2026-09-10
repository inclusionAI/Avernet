"""Contract for exact Skill Center packages delivered to filesystem Engines."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol, TypeAlias, runtime_checkable

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersionIdentity,
)


class CenterContentState(StrEnum):
    READY = "READY"
    PENDING = "PENDING"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class CenterContentReadyPackage:
    identity: CanonicalCenterVersionIdentity
    package_sha256: str
    package_size: int
    signed_url: str
    expires_at: str
    state: ClassVar[CenterContentState] = CenterContentState.READY

    def to_wire(self) -> dict[str, object]:
        return {
            "skill_uuid": self.identity.skill_uuid,
            "sc_version_number": self.identity.sc_version_number,
            "state": self.state.value,
            "package_sha256": self.package_sha256,
            "package_size": self.package_size,
            "signed_url": self.signed_url,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, slots=True)
class CenterContentPendingPackage:
    identity: CanonicalCenterVersionIdentity
    state: ClassVar[CenterContentState] = CenterContentState.PENDING

    def to_wire(self) -> dict[str, object]:
        return {
            "skill_uuid": self.identity.skill_uuid,
            "sc_version_number": self.identity.sc_version_number,
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class CenterContentUnavailablePackage:
    identity: CanonicalCenterVersionIdentity
    code: str
    retryable: bool
    state: ClassVar[CenterContentState] = CenterContentState.UNAVAILABLE

    def to_wire(self) -> dict[str, object]:
        return {
            "skill_uuid": self.identity.skill_uuid,
            "sc_version_number": self.identity.sc_version_number,
            "state": self.state.value,
            "code": self.code,
            "retryable": self.retryable,
        }


CenterContentPackage: TypeAlias = (
    CenterContentReadyPackage
    | CenterContentPendingPackage
    | CenterContentUnavailablePackage
)


@dataclass(frozen=True, slots=True)
class CenterContentRequest:
    packages: tuple[CenterContentPackage, ...]
    contract_version: int = 1

    def to_wire(self) -> dict[str, object]:
        if self.contract_version != 1:
            raise ValueError("unsupported Center content contract")
        return {
            "contract_version": self.contract_version,
            "packages": [package.to_wire() for package in self.packages],
        }


@runtime_checkable
class CenterContentDistribution(Protocol):
    """Light lookup and explicit heavy prepare for one immutable exact version."""

    def lookup(
        self, identity: CanonicalCenterVersionIdentity
    ) -> CenterContentPackage: ...

    def prepare(
        self, identity: CanonicalCenterVersionIdentity
    ) -> CenterContentPackage: ...


@runtime_checkable
class CenterContentURLSigner(Protocol):
    """Sign exact package keys for the network used by Desktop runtimes."""

    def sign_url(self, key: str, expires: int) -> str: ...


__all__ = [
    "CenterContentDistribution",
    "CenterContentPackage",
    "CenterContentPendingPackage",
    "CenterContentReadyPackage",
    "CenterContentRequest",
    "CenterContentState",
    "CenterContentUnavailablePackage",
    "CenterContentURLSigner",
]
