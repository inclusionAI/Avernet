"""Private receipt for a whole Manifest artifact delivery."""
from dataclasses import dataclass
from enum import StrEnum


class ManifestDeliveryStatus(StrEnum):
    CONFIRMED = "confirmed"
    NOT_BOUND = "not_bound"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ManifestDeliveryReceipt:
    status: ManifestDeliveryStatus = ManifestDeliveryStatus.UNKNOWN
    # None means the transport did not confirm the artifact's MCP contents.
    delivered_mcp_codes: frozenset[str] | None = None
    note: str | None = None
