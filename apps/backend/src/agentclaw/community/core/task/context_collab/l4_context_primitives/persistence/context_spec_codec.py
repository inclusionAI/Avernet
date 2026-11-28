"""Typed codec for the real ``ac_context_collab_collet.context_spec`` column.

The deployed table has one JSON storage column, not the later split columns
(``scope`` / ``collet_metadata`` / ``resources`` / ``creation_facts`` /
``root_task_ref``). This module keeps model and SQL shapes aligned: all L4
Collet-owned references are encoded inside ``context_spec`` and decoded through
explicit typed constructors. Historic unversioned ``context_spec`` objects are
accepted only with the documented legacy mapping and are never rewritten on GET.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet_metadata import (
    ColletMetadata,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabColletModel,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.resource_binding import (
    ResourceBinding,
)

FORMAT = "context-collab.collet@2"
_FORMAT_KEY = "storage_format"
_REQUIRED_KEYS = frozenset(
    {
        _FORMAT_KEY,
        "scope",
        "collet_metadata",
        "resources",
        "creation_facts",
        "root_task_ref",
    }
)


@dataclass(frozen=True)
class StoredColletContextSpec:
    """Typed view of the complete Collet JSON envelope.

    ``creation_facts`` remains the same dictionary consumed by L4/L6 record
    decoders. ``root_task_ref`` refers to independent Task storage when that
    migration has been deployed; taskless Collets and product Collets use None.
    """

    scope: SpaceRef | None
    collet_metadata: ColletMetadata | None
    resources: tuple[ResourceBinding, ...]
    creation_facts: dict
    root_task_ref: str | None

    def with_creation_facts(self, creation_facts: dict) -> "StoredColletContextSpec":
        if not isinstance(creation_facts, dict):
            raise ContextCollabValidationError(
                "creation facts inside context_spec must be an object"
            )
        return replace(self, creation_facts=dict(creation_facts))

    def with_root_task_ref(
        self, root_task_ref: str | None
    ) -> "StoredColletContextSpec":
        if root_task_ref is not None and (
            not isinstance(root_task_ref, str) or not root_task_ref.strip()
        ):
            raise ContextCollabValidationError(
                "root_task_ref inside context_spec must be a non-empty string or null"
            )
        return replace(self, root_task_ref=root_task_ref)

    def with_collet_metadata(
        self, collet_metadata: ColletMetadata | None
    ) -> "StoredColletContextSpec":
        if collet_metadata is not None and not isinstance(
            collet_metadata, ColletMetadata
        ):
            raise ContextCollabValidationError(
                "collet_metadata inside context_spec requires a typed ColletMetadata"
            )
        return replace(self, collet_metadata=collet_metadata)


def parse_collet_context_spec(
    row: ContextCollabColletModel,
) -> StoredColletContextSpec:
    """Decode a physical row without inventing missing SQL columns."""
    try:
        data = json.loads(row.context_spec)
    except (TypeError, json.JSONDecodeError) as error:
        raise ContextCollabValidationError(
            f"collaboration context {row.collab_context_id!r} has invalid "
            f"context_spec JSON: {error}"
        ) from error
    if not isinstance(data, dict):
        raise ContextCollabValidationError(
            f"collaboration context {row.collab_context_id!r} has invalid "
            "context_spec: expected a JSON object"
        )
    try:
        return _parse_data(data)
    except ContextCollabValidationError as error:
        raise ContextCollabValidationError(
            f"collaboration context {row.collab_context_id!r} has invalid "
            f"context_spec: {error}"
        ) from error


def encode_collet_context_spec(
    spec: StoredColletContextSpec,
) -> str:
    """Serialize only real physical storage; no split-column assumptions."""
    data = {
        _FORMAT_KEY: FORMAT,
        "scope": spec.scope.to_dict() if spec.scope is not None else None,
        "collet_metadata": (
            spec.collet_metadata.to_dict() if spec.collet_metadata else None
        ),
        "resources": [resource.to_dict() for resource in spec.resources],
        "creation_facts": spec.creation_facts,
        "root_task_ref": spec.root_task_ref,
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def stored_collet_context_spec(
    *,
    scope: SpaceRef | None,
    collet_metadata: ColletMetadata | None,
    resources: tuple[ResourceBinding, ...],
    creation_facts: dict,
    root_task_ref: str | None,
) -> StoredColletContextSpec:
    """Construct a strictly typed envelope before writing or replacing it."""
    return StoredColletContextSpec(
        scope=scope,
        collet_metadata=collet_metadata,
        resources=resources,
        creation_facts=creation_facts,
        root_task_ref=root_task_ref,
    )


def _parse_data(data: dict) -> StoredColletContextSpec:
    if data.get(_FORMAT_KEY) == FORMAT:
        return _parse_version_2(data)
    # Historic ContextSpec rows did not contain the split-column values. Match
    # the documented migration mapping exactly: space becomes Scope and the
    # remaining fields (minus the duplicated principal) become creation facts.
    legacy_facts = dict(data)
    raw_scope = legacy_facts.pop("space", None)
    legacy_facts.pop("principal_ref", None)
    return stored_collet_context_spec(
        scope=SpaceRef.from_dict(raw_scope) if raw_scope is not None else None,
        collet_metadata=None,
        resources=(),
        creation_facts=legacy_facts,
        root_task_ref=None,
    )


def _parse_version_2(data: dict) -> StoredColletContextSpec:
    if set(data) != _REQUIRED_KEYS:
        raise ContextCollabValidationError(
            f"version-2 context_spec keys must exactly match {sorted(_REQUIRED_KEYS)}"
        )
    raw_scope = data["scope"]
    raw_metadata = data["collet_metadata"]
    raw_resources = data["resources"]
    raw_facts = data["creation_facts"]
    raw_root = data["root_task_ref"]
    if raw_scope is not None and not isinstance(raw_scope, dict):
        raise ContextCollabValidationError("scope must be an object or null")
    if raw_metadata is not None and not isinstance(raw_metadata, dict):
        raise ContextCollabValidationError("collet_metadata must be an object or null")
    if isinstance(raw_metadata, dict) and "schema_version" in raw_metadata:
        raw_metadata = dict(raw_metadata)
        if raw_metadata.pop("schema_version") != "1.1":
            raise ContextCollabValidationError(
                "unsupported legacy Collet schema_version"
            )
    elif raw_metadata is not None:
        raw_metadata = dict(raw_metadata)
    if not isinstance(raw_resources, list):
        raise ContextCollabValidationError("resources must be a list")
    if not isinstance(raw_facts, dict):
        raise ContextCollabValidationError("creation_facts must be an object")
    if raw_root is not None and (not isinstance(raw_root, str) or not raw_root.strip()):
        raise ContextCollabValidationError(
            "root_task_ref must be a non-empty string or null"
        )
    return stored_collet_context_spec(
        scope=SpaceRef.from_dict(raw_scope) if raw_scope is not None else None,
        collet_metadata=ColletMetadata.from_dict(raw_metadata)
        if raw_metadata is not None
        else None,
        resources=tuple(ResourceBinding.from_dict(item) for item in raw_resources),
        creation_facts=raw_facts,
        root_task_ref=raw_root,
    )
