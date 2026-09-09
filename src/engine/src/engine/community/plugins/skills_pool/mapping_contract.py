"""Resolve the versioned Backend mapping payload into Engine-local paths."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from engine.community.core.skills.exceptions import (
    InvalidPoolMappingRequestError,
)
from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    MAPPING_CONTRACT_VERSION,
    MAPPING_V3_CONTRACT_VERSION,
    LayoutIdentity,
    LogicalSkillMapping,
    RuntimeLayoutContext,
    SkillCorpus,
    SkillLayoutResolutionError,
    resolve_filesystem_skill_layout,
    resolve_skill_mappings,
)
from engine.community.kernel.center_content import (
    CenterContentAdapter,
    CenterContentPackage,
    CenterContentPendingPackage,
    CenterContentPreparationStatus,
)
from engine.community.plugins.skills_pool.center_content import parse_center_content
from engine.community.plugins.skills_pool.layout_activation import (
    MappingApplyMode,
    MappingApplyResult,
    MappingItemResult,
    MappingProjectionStatus,
    MappingSourceLayout,
    SkillMapping,
    publish_pool_mappings,
)

_LEGACY_PHYSICAL_FIELDS = frozenset({"source", "target"})
_LOGICAL_V2_FIELDS = frozenset({"corpus", "relative_path", "link_name"})
_LOGICAL_V3_CENTER_FIELDS = frozenset(
    {"corpus", "skill_uuid", "sc_version_number", "link_name"}
)
_APPLY_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_APPLY_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True, slots=True)
class ResolvedMappingPayload:
    mappings: tuple[SkillMapping, ...]
    resolved_locators: tuple[dict[str, str], ...] = ()


def _logical_key(item: dict[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in item.items()))


def _deduplicate_logical_payload(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise InvalidPoolMappingRequestError("mappings must be an array")
    result: list[dict[str, object]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for raw in payload:
        if not isinstance(raw, dict):
            raise InvalidPoolMappingRequestError("each logical mapping must be an object")
        key = _logical_key(raw)
        if key not in seen:
            seen.add(key)
            result.append(raw)
    return result


def _aggregate_logical_results(items: Sequence[MappingItemResult]) -> tuple[MappingItemResult, ...]:
    """One logical outcome may cover several current/historical active roots."""

    severity = {
        MappingProjectionStatus.CONVERGED: 0,
        MappingProjectionStatus.PENDING: 1,
        MappingProjectionStatus.DEGRADED: 2,
    }
    grouped: dict[tuple[str, tuple[tuple[str, str], ...]], MappingItemResult] = {}
    for item in items:
        assert item.mapping is not None
        key = (item.action, _logical_key(item.mapping))
        previous = grouped.get(key)
        if previous is None:
            grouped[key] = item
            continue
        worst = item if severity[item.status] > severity[previous.status] else previous
        grouped[key] = replace(worst, retryable=previous.retryable or item.retryable)
    return tuple(grouped.values())


def apply_logical_mapping_payload(
    *,
    engine: str,
    source_layout: MappingSourceLayout,
    mappings_payload: object,
    retired_payload: object,
    additional_retirement_roots: Sequence[Path] = (),
    home: Path = Path("/home/admin"),
    center_content_payload: object | None = None,
    content_adapter: CenterContentAdapter,
) -> MappingApplyResult:
    """Validate, inspect Center, and BEST_EFFORT apply one logical snapshot."""

    # Validate before taking a runtime lock so malformed requests have no
    # filesystem side effects. The locked implementation validates again at
    # its own trust boundary and serializes a slow download with a later
    # retirement, preventing the completed download from relinking afterward.
    preview_mappings = _deduplicate_logical_payload(mappings_payload)
    preview_retired = _deduplicate_logical_payload(retired_payload)
    preview_contract = (
        MAPPING_V3_CONTRACT_VERSION
        if any(
            item.get("corpus") == "center"
            for item in [*preview_mappings, *preview_retired]
        )
        else MAPPING_CONTRACT_VERSION
    )
    resolve_mapping_payload(
        engine=engine,
        source_layout=source_layout,
        payload=preview_mappings,
        mapping_contract_version=preview_contract,
        home=home,
    )
    resolve_mapping_payload(
        engine=engine,
        source_layout=source_layout,
        payload=preview_retired,
        mapping_contract_version=preview_contract,
        additional_retirement_roots=additional_retirement_roots,
        home=home,
    )
    lock_key = (str(Path(home).absolute()), engine)
    with _APPLY_LOCKS_GUARD:
        apply_lock = _APPLY_LOCKS.setdefault(lock_key, threading.Lock())
    with apply_lock:
        return _apply_logical_mapping_payload_locked(
            engine=engine,
            source_layout=source_layout,
            mappings_payload=mappings_payload,
            retired_payload=retired_payload,
            additional_retirement_roots=additional_retirement_roots,
            home=home,
            center_content_payload=center_content_payload,
            content_adapter=content_adapter,
        )


def _apply_logical_mapping_payload_locked(
    *,
    engine: str,
    source_layout: MappingSourceLayout,
    mappings_payload: object,
    retired_payload: object,
    additional_retirement_roots: Sequence[Path] = (),
    home: Path = Path("/home/admin"),
    center_content_payload: object | None = None,
    content_adapter: CenterContentAdapter,
) -> MappingApplyResult:
    """Apply one validated snapshot while holding the per-runtime lock."""

    mappings = _deduplicate_logical_payload(mappings_payload)
    retired = _deduplicate_logical_payload(retired_payload)
    all_mappings = [*mappings, *retired]
    contract = (
        MAPPING_V3_CONTRACT_VERSION
        if any(item.get("corpus") == "center" for item in all_mappings)
        else MAPPING_CONTRACT_VERSION
    )
    desired_resolved = resolve_mapping_payload(
        engine=engine,
        source_layout=source_layout,
        payload=mappings,
        mapping_contract_version=contract,
        home=home,
    )
    retired_resolved = resolve_mapping_payload(
        engine=engine,
        source_layout=source_layout,
        payload=retired,
        mapping_contract_version=contract,
        additional_retirement_roots=additional_retirement_roots,
        home=home,
    )
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity(engine_type=engine, layout_contract_version=LAYOUT_CONTRACT_VERSION),
        RuntimeLayoutContext(home=home),
    )
    pending_center_names: set[str] = set()
    pending_center_keys: set[tuple[tuple[str, str], ...]] = set()
    center_failures: list[MappingItemResult] = []
    center_items = [item for item in mappings if item.get("corpus") == "center"]
    center_evidence: dict[str, object] | None = None
    if center_items:
        adapter = content_adapter
        contract_version: int | None = None
        packages: Mapping[tuple[str, str], CenterContentPackage] = {}
        if center_content_payload is not None:
            try:
                contract_version, packages = parse_center_content(center_content_payload)
            except ValueError as error:
                raise InvalidPoolMappingRequestError(str(error)) from error
        if adapter.mode == "DOWNLOAD" and contract_version is None:
            packages = {}
            contract_version = 1
        desired_keys = {
            (str(item["skill_uuid"]), str(item["sc_version_number"]))
            for item in center_items
        }
        if set(packages) - desired_keys:
            raise InvalidPoolMappingRequestError(
                "center_content contains a package outside desired Center mappings"
            )
        counters = {"ready": 0, "pending": 0, "unavailable": 0}
        preparations: dict[
            tuple[str, str], tuple[MappingProjectionStatus, str | None, bool]
        ] = {}
        for item in center_items:
            identity = (str(item["skill_uuid"]), str(item["sc_version_number"]))
            if identity not in preparations:
                package = packages.get(identity)
                if adapter.mode == "MOUNT" and center_content_payload is not None:
                    prepared_status = MappingProjectionStatus.DEGRADED
                    failure_code = "CENTER_CONTENT_MODE_MISMATCH"
                    retryable = False
                    counters["unavailable"] += 1
                elif adapter.mode == "DOWNLOAD" and package is None:
                    prepared_status = MappingProjectionStatus.DEGRADED
                    failure_code = "CENTER_CONTENT_DESCRIPTOR_MISSING"
                    retryable = False
                    counters["unavailable"] += 1
                else:
                    package = package or CenterContentPendingPackage(
                        skill_uuid=identity[0],
                        sc_version_number=identity[1],
                    )
                    prepared = adapter.prepare(
                        center_root=layout.pool_center, package=package
                    )
                    prepared_status = {
                        CenterContentPreparationStatus.READY: MappingProjectionStatus.CONVERGED,
                        CenterContentPreparationStatus.PENDING: MappingProjectionStatus.PENDING,
                        CenterContentPreparationStatus.UNAVAILABLE: MappingProjectionStatus.DEGRADED,
                    }[prepared.status]
                    failure_code = prepared.code
                    retryable = prepared.retryable
                    if prepared.status is CenterContentPreparationStatus.READY:
                        counters["ready"] += 1
                    elif prepared.status is CenterContentPreparationStatus.PENDING:
                        counters["pending"] += 1
                    else:
                        counters["unavailable"] += 1
                preparations[identity] = (
                    prepared_status,
                    failure_code,
                    retryable,
                )
            prepared_status, failure_code, retryable = preparations[identity]
            if prepared_status is not MappingProjectionStatus.CONVERGED:
                link_name = str(item["link_name"])
                pending_center_names.add(link_name)
                pending_center_keys.add(_logical_key(item))
                center_failures.append(
                    MappingItemResult(
                        target="",
                        source=None,
                        status=prepared_status,
                        code=failure_code,
                        retryable=retryable,
                        action="APPLY",
                        mapping={key: str(value) for key, value in item.items()},
                    )
                )
        if adapter.mode == "DOWNLOAD" or center_content_payload is not None:
            center_evidence = {
                "contract_version": contract_version or 1,
                "mode": adapter.mode,
                **counters,
                "packages": [
                    {
                        "skill_uuid": identity[0],
                        "sc_version_number": identity[1],
                        "status": (
                            "READY"
                            if status is MappingProjectionStatus.CONVERGED
                            else "PENDING"
                            if status is MappingProjectionStatus.PENDING
                            else "UNAVAILABLE"
                        ),
                    }
                    for identity, (status, _code, _retryable) in preparations.items()
                ],
            }
    desired_pairs = [
        (raw, physical)
        for raw, physical in zip(mappings, desired_resolved.mappings, strict=True)
        if _logical_key(raw) not in pending_center_keys
    ]
    retired_pairs = [
        (retired[index % len(retired)], physical)
        for index, physical in enumerate(retired_resolved.mappings)
        if retired
        and str(retired[index % len(retired)]["link_name"])
        not in pending_center_names
    ]
    published = publish_pool_mappings(
        mappings=[physical for _, physical in desired_pairs],
        retired_mappings=[physical for _, physical in retired_pairs],
        home=home,
        engine=engine,
        source_layout=source_layout,
        additional_retirement_roots=additional_retirement_roots,
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )
    desired_by_location = {
        (physical.target, physical.source): {
            key: str(value) for key, value in raw.items()
        }
        for raw, physical in desired_pairs
    }
    retired_by_location = {
        (physical.target, physical.source): {
            key: str(value) for key, value in raw.items()
        }
        for raw, physical in retired_pairs
    }
    logical_items: list[MappingItemResult] = [*center_failures]
    runtime_issues: list[MappingItemResult] = []
    desired_by_name = {str(raw["link_name"]): raw for raw, _ in desired_pairs}
    for item in published.items:
        logical = (
            retired_by_location.get((item.target, item.source or ""))
            if item.action == "RETIRE"
            else desired_by_location.get((item.target, item.source or ""))
        )
        action = item.action
        if logical is not None and action == "RETIRE":
            replacement = desired_by_name.get(logical["link_name"])
            if replacement is not None:
                # The replacement owns retirement at *all* active roots, not
                # just the canonical target skipped by the physical helper.
                logical = {key: str(value) for key, value in replacement.items()}
                action = "APPLY"
        enriched = MappingItemResult(
            target=item.target,
            source=item.source,
            status=item.status,
            code=item.code,
            retryable=item.retryable,
            action=action if logical is not None else "RUNTIME",
            mapping=logical,
        )
        (logical_items if logical is not None else runtime_issues).append(enriched)
    statuses = [item.status for item in [*logical_items, *runtime_issues]]
    status = (
        MappingProjectionStatus.DEGRADED
        if MappingProjectionStatus.DEGRADED in statuses
        else MappingProjectionStatus.PENDING
        if MappingProjectionStatus.PENDING in statuses
        else MappingProjectionStatus.CONVERGED
    )
    logical_results = _aggregate_logical_results(logical_items)
    evidence = {**published.evidence, "center_pending": len(center_failures)}
    if center_evidence is not None:
        evidence["center_content"] = center_evidence
    if len(logical_results) < len(logical_items):
        # Keep all per-directory diagnostics when aggregation removes items;
        # do not duplicate the normal single-root response payload.
        evidence["physical_items"] = [item.to_data() for item in published.items]
    return MappingApplyResult(
        status=status,
        items=logical_results,
        issues=tuple(runtime_issues),
        evidence=evidence,
    )


async def apply_logical_mapping_request(
    *,
    params: dict[str, object],
    engine: str,
    additional_retirement_roots: Sequence[Path] = (),
    content_adapter: CenterContentAdapter,
) -> dict[str, object]:
    """Run the shared filesystem contract without blocking the HTTP event loop."""

    result = await asyncio.to_thread(
        apply_logical_mapping_payload,
        engine=engine,
        source_layout=MappingSourceLayout(
            str(params.get("source_layout", MappingSourceLayout.POOL.value))
        ),
        mappings_payload=params.get("mappings", []),
        retired_payload=params.get("retired_mappings", []),
        center_content_payload=params.get("center_content"),
        additional_retirement_roots=additional_retirement_roots,
        content_adapter=content_adapter,
    )
    return result.to_data()


def _require_mapping_fields(
    item: object,
    *,
    expected: frozenset[str],
    contract_name: str,
) -> dict[str, object]:
    if not isinstance(item, dict):
        raise InvalidPoolMappingRequestError(
            f"each {contract_name} mapping must be an object"
        )
    if frozenset(item) != expected:
        raise InvalidPoolMappingRequestError(
            f"{contract_name} mapping must contain exactly "
            f"{', '.join(sorted(expected))}"
        )
    return item


def resolve_mapping_payload(
    *,
    engine: str,
    source_layout: MappingSourceLayout,
    payload: object,
    mapping_contract_version: str | None = None,
    additional_retirement_roots: Sequence[Path] = (),
    home: Path = Path("/home/admin"),
) -> ResolvedMappingPayload:
    """Resolve logical v2 or legacy unversioned physical mappings.

    Version and shape validation finishes before any Engine filesystem
    publication can begin. A versioned request is exclusively logical; only
    an unversioned request may use the legacy physical pair.
    """

    if not isinstance(payload, list):
        raise InvalidPoolMappingRequestError("mappings must be an array")
    if mapping_contract_version is None:
        physical: list[SkillMapping] = []
        for raw_item in payload:
            item = _require_mapping_fields(
                raw_item,
                expected=_LEGACY_PHYSICAL_FIELDS,
                contract_name="legacy",
            )
            source = item["source"]
            target = item["target"]
            if not isinstance(source, str) or not isinstance(target, str):
                raise InvalidPoolMappingRequestError(
                    "legacy mapping source and target must be strings"
                )
            physical.append(SkillMapping(source=source, target=target))
        return ResolvedMappingPayload(tuple(physical))
    if mapping_contract_version not in {
        MAPPING_CONTRACT_VERSION,
        MAPPING_V3_CONTRACT_VERSION,
    }:
        raise InvalidPoolMappingRequestError(
            f"unsupported mapping contract: {mapping_contract_version}"
        )

    logical: list[LogicalSkillMapping] = []
    for raw_item in payload:
        if not isinstance(raw_item, dict):
            raise InvalidPoolMappingRequestError("each logical mapping must be an object")
        fields = frozenset(raw_item)
        is_center = fields == _LOGICAL_V3_CENTER_FIELDS
        if is_center and mapping_contract_version != MAPPING_V3_CONTRACT_VERSION:
            raise InvalidPoolMappingRequestError("v2 logical mapping must contain exactly corpus, link_name, relative_path")
        item = _require_mapping_fields(
            raw_item,
            expected=_LOGICAL_V3_CENTER_FIELDS if is_center else _LOGICAL_V2_FIELDS,
            contract_name="logical",
        )
        corpus = item["corpus"]
        link_name = item["link_name"]
        if not isinstance(corpus, str) or not isinstance(link_name, str):
            raise InvalidPoolMappingRequestError(
                "logical mapping corpus and link_name must be strings"
            )
        try:
            resolved_corpus = SkillCorpus(corpus)
        except ValueError as error:
            raise InvalidPoolMappingRequestError(
                f"unknown Skill corpus: {corpus!r}"
            ) from error
        if is_center:
            skill_uuid = item["skill_uuid"]
            sc_version_number = item["sc_version_number"]
            if (
                resolved_corpus is not SkillCorpus.CENTER
                or not isinstance(skill_uuid, str)
                or not isinstance(sc_version_number, str)
            ):
                raise InvalidPoolMappingRequestError(
                    "center mapping requires structured skill_uuid and sc_version_number"
                )
            logical.append(LogicalSkillMapping(
                corpus=resolved_corpus,
                relative_path=None,
                link_name=link_name,
                skill_uuid=skill_uuid,
                sc_version_number=sc_version_number,
            ))
            continue
        relative_path = item["relative_path"]
        if (
            resolved_corpus is SkillCorpus.CENTER
            or not isinstance(relative_path, str)
        ):
            raise InvalidPoolMappingRequestError(
                "logical mapping corpus, relative_path and link_name must be strings"
            )
        logical.append(LogicalSkillMapping(
            corpus=resolved_corpus,
            relative_path=relative_path,
            link_name=link_name,
        ))

    plan = resolve_filesystem_skill_layout(
        LayoutIdentity(
            engine_type=engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
        ),
        RuntimeLayoutContext(home=home),
    )
    local_root = (
        plan.pool_local
        if source_layout is MappingSourceLayout.POOL
        else plan.legacy_local
    )
    repo_root = (
        plan.pool_repo
        if source_layout is MappingSourceLayout.POOL
        else plan.legacy_repo
    )
    active_roots = [plan.active_root, *additional_retirement_roots]
    try:
        resolved = [
            mapping
            for active_root in active_roots
            for mapping in resolve_skill_mappings(
                active_root=active_root,
                local_root=local_root,
                repo_root=repo_root,
                center_root=plan.pool_center,
                mappings=logical,
            )
        ]
    except SkillLayoutResolutionError as error:
        raise InvalidPoolMappingRequestError(str(error)) from error
    return ResolvedMappingPayload(
        mappings=tuple(
            SkillMapping(source=str(mapping.source), target=str(mapping.target))
            for mapping in resolved
        ),
        resolved_locators=tuple(
            {
                "corpus": "center",
                "skill_uuid": logical[index % len(logical)].skill_uuid or "",
                "sc_version_number": (
                    logical[index % len(logical)].sc_version_number or ""
                ),
                "link_name": mapping.link_name,
                "resolved_locator": mapping.resolved_locator,
            }
            if mapping.corpus is SkillCorpus.CENTER
            else {
                "corpus": mapping.corpus.value,
                "relative_path": mapping.relative_path,
                "link_name": mapping.link_name,
                "resolved_locator": mapping.resolved_locator,
            }
            for index, mapping in enumerate(resolved)
        ),
    )


__all__ = [
    "ResolvedMappingPayload",
    "apply_logical_mapping_payload",
    "apply_logical_mapping_request",
    "resolve_mapping_payload",
]
