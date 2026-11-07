"""Merge caller fields using internal CAS; callers never manage revisions."""

from uuid import uuid4

from search_core.api.cards import CatalogConflict
from search_core.catalog.cards import (
    CardSnapshot,
    SearchConfig,
    business_card,
    is_label_filter,
)
from search_core.core.card_management import merge_fields


class CardWrites:
    snapshot_type = CardSnapshot

    def validate_labels(self, kind, labels, *, creating, patch):
        """Hosts may require business-specific labels on managed writes."""

    def upsert(self, agent_id, fields, resource_type, search_config, *, labels=None):
        # Retry a bounded number of internal races, remerging onto the latest
        # snapshot each time so concurrent edits to different fields survive.
        for _ in range(8):
            current = self.get(agent_id)
            kind = resource_type or (current.card.resource_type if current else "agent")
            if current and kind != current.card.resource_type:
                raise CatalogConflict("resource type cannot change for an existing ID")
            active = current is not None and not current.card.deleted
            original = business_card(current.card) if active else {}
            config = current.card.search_config if current else SearchConfig()
            config = SearchConfig.model_validate(
                merge_fields(config.model_dump(mode="json"), search_config)
            )
            data = merge_fields(original, fields)
            if not data:
                raise ValueError("card required for creation")
            merged = dict(current.card.labels) if active else {}
            for key, values in (labels or {}).items():
                if (
                    not is_label_filter("labels." + key)
                    or not isinstance(values, list)
                    or any(not isinstance(v, str) or not v.strip() for v in values)
                ):
                    raise ValueError("invalid label key or value")
                if values:
                    merged[key] = list(dict.fromkeys(values))
                else:
                    merged.pop(key, None)
            self.validate_labels(kind, merged, creating=not active, patch=labels)
            if (
                active
                and data == original
                and config == current.card.search_config
                and merged == current.card.labels
            ):
                return current
            revision = current.revision if current else 0
            snapshot = self.snapshot_type(
                agent_id=agent_id,
                resource_type=kind,
                # Legacy storage/artifact metadata only; not an upstream version.
                source_version=uuid4().hex,
                source_card=data,
                labels=merged,
                search_config=config,
            )
            try:
                return self.save(snapshot, revision)
            except CatalogConflict:
                continue
        raise RuntimeError("Card write contention; retry request")

    def remove(self, agent_id):
        for _ in range(8):
            current = self.get(agent_id)
            if current is None or current.card.deleted:
                return
            try:
                self.delete(agent_id, current.revision)
                return
            except CatalogConflict:
                continue
        raise RuntimeError("Card deletion contention; retry request")
