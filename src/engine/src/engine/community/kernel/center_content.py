"""Transport-neutral exact Center content values shared across Engine layers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Protocol, TypeAlias, runtime_checkable


class CenterContentState(StrEnum):
    READY = "READY"
    PENDING = "PENDING"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class CenterContentReadyPackage:
    skill_uuid: str
    sc_version_number: str
    package_sha256: str
    package_size: int
    signed_url: str
    expires_at: str
    state: ClassVar[CenterContentState] = CenterContentState.READY

    def to_data(self) -> dict[str, object]:
        return {
            "skill_uuid": self.skill_uuid,
            "sc_version_number": self.sc_version_number,
            "state": self.state.value,
            "package_sha256": self.package_sha256,
            "package_size": self.package_size,
            "signed_url": self.signed_url,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, slots=True)
class CenterContentPendingPackage:
    skill_uuid: str
    sc_version_number: str
    state: ClassVar[CenterContentState] = CenterContentState.PENDING

    def to_data(self) -> dict[str, object]:
        return {
            "skill_uuid": self.skill_uuid,
            "sc_version_number": self.sc_version_number,
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class CenterContentUnavailablePackage:
    skill_uuid: str
    sc_version_number: str
    code: str
    retryable: bool
    state: ClassVar[CenterContentState] = CenterContentState.UNAVAILABLE

    def to_data(self) -> dict[str, object]:
        return {
            "skill_uuid": self.skill_uuid,
            "sc_version_number": self.sc_version_number,
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

    def to_data(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "packages": [package.to_data() for package in self.packages],
        }


class CenterContentPreparationStatus(StrEnum):
    READY = "READY"
    PENDING = "PENDING"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class CenterContentPreparation:
    status: CenterContentPreparationStatus
    code: str | None = None
    retryable: bool = False


@runtime_checkable
class CenterContentAdapter(Protocol):
    mode: str

    def prepare(
        self,
        *,
        center_root: Path,
        package: CenterContentPackage,
    ) -> CenterContentPreparation: ...


__all__ = [
    "CenterContentAdapter",
    "CenterContentPackage",
    "CenterContentPendingPackage",
    "CenterContentPreparation",
    "CenterContentPreparationStatus",
    "CenterContentRequest",
    "CenterContentReadyPackage",
    "CenterContentState",
    "CenterContentUnavailablePackage",
]
