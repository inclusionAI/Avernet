"""Resolve the versioned Backend mapping payload into Engine-local paths."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from engine.community.core.skills.exceptions import (
    InvalidPoolMappingRequestError,
)
from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    MAPPING_CONTRACT_VERSION,
    MAPPING_V3_CONTRACT_VERSION,
    MAPPING_V4_CONTRACT_VERSION,
    LayoutIdentity,
    LogicalSkillMapping,
    RuntimeLayoutContext,
    SkillCorpus,
    SkillLayoutResolutionError,
    resolve_filesystem_skill_layout,
    resolve_skill_mappings,
)
from engine.community.plugins.skills_pool.layout_activation import (
    MappingSourceLayout,
    SkillMapping,
)

_LEGACY_PHYSICAL_FIELDS = frozenset({"source", "target"})
_LOGICAL_V2_FIELDS = frozenset({"corpus", "relative_path", "link_name"})
_LOGICAL_V3_CENTER_FIELDS = frozenset(
    {"corpus", "skill_uuid", "sc_version_number", "link_name"}
)


@dataclass(frozen=True, slots=True)
class ResolvedMappingPayload:
    mappings: tuple[SkillMapping, ...]
    resolved_locators: tuple[dict[str, str], ...] = ()


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
        MAPPING_V4_CONTRACT_VERSION,
    }:
        raise InvalidPoolMappingRequestError(
            f"unsupported mapping contract: {mapping_contract_version}"
        )

    logical: list[LogicalSkillMapping] = []
    for raw_item in payload:
        if not isinstance(raw_item, dict):
            raise InvalidPoolMappingRequestError(
                "each logical mapping must be an object"
            )
        fields = frozenset(raw_item)
        is_center = fields == _LOGICAL_V3_CENTER_FIELDS
        if is_center and mapping_contract_version not in {
            MAPPING_V3_CONTRACT_VERSION,
            MAPPING_V4_CONTRACT_VERSION,
        }:
            raise InvalidPoolMappingRequestError(
                "v2 logical mapping must contain exactly corpus, link_name, relative_path"
            )
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
            logical.append(
                LogicalSkillMapping(
                    corpus=resolved_corpus,
                    relative_path=None,
                    link_name=link_name,
                    skill_uuid=skill_uuid,
                    sc_version_number=sc_version_number,
                )
            )
            continue
        relative_path = item["relative_path"]
        if resolved_corpus is SkillCorpus.CENTER or not isinstance(relative_path, str):
            raise InvalidPoolMappingRequestError(
                "logical mapping corpus, relative_path and link_name must be strings"
            )
        logical.append(
            LogicalSkillMapping(
                corpus=resolved_corpus,
                relative_path=relative_path,
                link_name=link_name,
            )
        )

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


__all__ = ["ResolvedMappingPayload", "resolve_mapping_payload"]
