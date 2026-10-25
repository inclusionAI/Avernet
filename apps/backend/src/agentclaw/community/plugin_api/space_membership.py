"""Provider port for a complete, immutable space-membership snapshot.

The caller owns ordered durable delivery. A successful apply must acknowledge
exactly this environment, space, revision and content, not merely accept a job.
"""

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@dataclass(frozen=True)
class SpaceMembershipSnapshot:
    environment: str
    space_id: int
    body: dict

    @property
    def revision(self) -> int:
        return self.body["revision"]

    def digest(self) -> str:
        data = {k: v for k, v in self.body.items() if k != "full_snapshot"}
        data["space_id"] = str(self.space_id)
        data["members"] = sorted(data["members"], key=lambda m: m["staff_no"])
        encoded = json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(encoded.encode()).hexdigest()


@runtime_checkable
class SpaceMembershipSink(Plugin, Protocol):
    def is_enabled(self) -> bool: ...
    def apply_snapshot(self, snapshot: SpaceMembershipSnapshot) -> None: ...
