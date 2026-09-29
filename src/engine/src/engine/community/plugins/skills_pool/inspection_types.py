"""Value types shared by Skills Pool runtime inspections."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RuntimeLayoutInspectionStatus(str, Enum):
    READY = "READY"
    NOT_CAPABLE = "NOT_CAPABLE"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    INVALID = "INVALID"


@dataclass(frozen=True)
class RuntimeLayoutInspection:
    status: RuntimeLayoutInspectionStatus
    engine: str
    layout_contract_version: str
    preparation_id: str | None
    evidence: dict[str, Any]

    def to_data(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "engine": self.engine,
            "layout_contract_version": self.layout_contract_version,
            "preparation_id": self.preparation_id,
            "evidence": self.evidence,
        }


__all__ = ["RuntimeLayoutInspection", "RuntimeLayoutInspectionStatus"]
