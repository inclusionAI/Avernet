"""Resolve the versioned Backend mapping payload into Engine-local paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.community.core.skills.exceptions import (
    InvalidPoolMappingRequestError,
)
from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    MAPPING_CONTRACT_VERSION,
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
    if mapping_contract_version != MAPPING_CONTRACT_VERSION:
        raise InvalidPoolMappingRequestError(
            f"unsupported mapping contract: {mapping_contract_version}"
        )

    logical: list[LogicalSkillMapping] = []
    for raw_item in payload:
        item = _require_mapping_fields(
            raw_item,
            expected=_LOGICAL_V2_FIELDS,
            contract_name="logical",
        )
        corpus = item["corpus"]
        relative_path = item["relative_path"]
        link_name = item["link_name"]
        if (
            not isinstance(corpus, str)
            or not isinstance(relative_path, str)
            or not isinstance(link_name, str)
        ):
            raise InvalidPoolMappingRequestError(
                "logical mapping corpus, relative_path and link_name "
                "must be strings"
            )
        try:
            resolved_corpus = SkillCorpus(corpus)
        except ValueError as error:
            raise InvalidPoolMappingRequestError(
                f"unknown Skill corpus: {corpus!r}"
            ) from error
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
    try:
        resolved = resolve_skill_mappings(
            active_root=plan.active_root,
            local_root=local_root,
            repo_root=repo_root,
            mappings=logical,
        )
    except SkillLayoutResolutionError as error:
        raise InvalidPoolMappingRequestError(str(error)) from error
    return ResolvedMappingPayload(
        mappings=tuple(
            SkillMapping(source=str(mapping.source), target=str(mapping.target))
            for mapping in resolved
        ),
        resolved_locators=tuple(
            {
                "corpus": mapping.corpus.value,
                "relative_path": mapping.relative_path,
                "link_name": mapping.link_name,
                "resolved_locator": mapping.resolved_locator,
            }
            for mapping in resolved
        ),
    )


__all__ = ["ResolvedMappingPayload", "resolve_mapping_payload"]
