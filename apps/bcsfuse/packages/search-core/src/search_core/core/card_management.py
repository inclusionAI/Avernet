"""Shared source management; the application's index runtime supplies status."""

from abc import ABC, abstractmethod
from copy import deepcopy

from search_core.plugin_api.card_store import CardStore


def merge_fields(current, changes):
    """Objects merge recursively; arrays/scalars/null replace the supplied field."""
    merged = deepcopy(current)
    for key, value in changes.items():
        merged[key] = (
            merge_fields(merged[key], value)
            if isinstance(merged.get(key), dict) and isinstance(value, dict)
            else deepcopy(value)
        )
    return merged


class CardManagementService(ABC):
    def __init__(self, repository: CardStore):
        self.repository = repository

    def upsert(self, agent_id, fields, resource_type, search_config, *, labels=None):
        return self.repository.upsert(
            agent_id, fields, resource_type, search_config, labels=labels
        )

    def remove(self, agent_id):
        self.repository.remove(agent_id)

    def read(self, agent_id):
        row = self.repository.read_card(agent_id)
        if row is None:
            raise KeyError(agent_id)
        return row

    @abstractmethod
    def status(self, agent_id: str) -> dict:
        """Report index state from the runtime that owns processing and application."""
