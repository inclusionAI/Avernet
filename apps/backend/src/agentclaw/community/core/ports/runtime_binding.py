"""The Runtime identity carried from publication to a later capability write."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeBindingTarget:
    bot_id: str
    owner_id: str
    entity_id: str
    binding_id: int | None
    device_id: str | None
