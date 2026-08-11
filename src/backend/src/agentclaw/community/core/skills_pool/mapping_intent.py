"""Build logical mapping intent and validate Engine resolution evidence."""

from __future__ import annotations

from pathlib import PurePosixPath

from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
    RegisteredSkillAsset,
)


def _source_tail(git_path: str, prefix: str) -> PurePosixPath:
    raw = git_path[len(prefix) :]
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.strip() != raw
        or (prefix == "git://" and path.is_absolute())
        or path.name in {"", ".", ".."}
        or ".." in path.parts
        or path.as_posix() != raw
    ):
        raise ValueError(f"invalid skill locator: {git_path}")
    return path


def local_skill_name(asset: RegisteredSkillAsset) -> str:
    if not asset.git_path.startswith("local://"):
        raise ValueError(f"skill {asset.skill_id} is not local")
    return _source_tail(asset.git_path, "local://").name


def build_logical_skill_mappings(
    assets: list[RegisteredSkillAsset],
) -> list[PoolSkillMapping]:
    mappings: list[PoolSkillMapping] = []
    targets: dict[str, str] = {}
    for asset in assets:
        if asset.git_path.startswith("local://"):
            relative = PurePosixPath(local_skill_name(asset))
            corpus = "local"
        elif asset.git_path.startswith("git://"):
            relative = _source_tail(asset.git_path, "git://")
            corpus = "repo"
        else:
            continue
        link_name = relative.name
        identity = f"{corpus}:{relative.as_posix()}"
        if targets.get(link_name) == identity:
            continue
        if link_name in targets:
            raise ValueError(f"duplicate managed target: {link_name}")
        targets[link_name] = identity
        mappings.append(
            PoolSkillMapping(
                corpus=corpus,
                relative_path=relative.as_posix(),
                link_name=link_name,
            )
        )
    return mappings


def retired_logical_skill_mappings(
    previous: list[PoolSkillMapping],
    current: list[PoolSkillMapping],
) -> list[PoolSkillMapping]:
    """Return exact previous identities no longer selected by product state."""

    current_by_target = {mapping.link_name: mapping for mapping in current}
    return [
        mapping
        for mapping in previous
        if current_by_target.get(mapping.link_name) != mapping
    ]


def merge_retired_logical_skill_mappings(
    *groups: list[PoolSkillMapping],
    current: list[PoolSkillMapping],
) -> list[PoolSkillMapping]:
    """Keep durable retirements unless the exact identity became active again."""

    active = set(current)
    merged: list[PoolSkillMapping] = []
    seen: set[PoolSkillMapping] = set()
    for group in groups:
        for mapping in group:
            if mapping in active or mapping in seen:
                continue
            seen.add(mapping)
            merged.append(mapping)
    return merged


def logical_skill_mappings_from_evidence(
    evidence: dict[str, object] | None,
) -> list[PoolSkillMapping]:
    """Parse durable retirement candidates without trusting stored JSON."""

    if not isinstance(evidence, dict) or "retired_mappings" not in evidence:
        return []
    raw_mappings = evidence["retired_mappings"]
    if not isinstance(raw_mappings, list):
        raise ValueError("invalid retired mapping evidence")
    parsed: list[PoolSkillMapping] = []
    targets: dict[str, PoolSkillMapping] = {}
    for raw in raw_mappings:
        if not isinstance(raw, dict) or set(raw) != {
            "corpus",
            "relative_path",
            "link_name",
        }:
            raise ValueError("invalid retired mapping evidence")
        corpus = raw.get("corpus")
        relative_path = raw.get("relative_path")
        link_name = raw.get("link_name")
        if (
            corpus not in {"local", "repo"}
            or not isinstance(relative_path, str)
            or not isinstance(link_name, str)
        ):
            raise ValueError("invalid retired mapping evidence")
        path = PurePosixPath(relative_path)
        if (
            not relative_path
            or relative_path.strip() != relative_path
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != relative_path
            or path.name != link_name
        ):
            raise ValueError("invalid retired mapping evidence")
        mapping = PoolSkillMapping(
            corpus=corpus,
            relative_path=relative_path,
            link_name=link_name,
        )
        if link_name in targets and targets[link_name] != mapping:
            raise ValueError("ambiguous retired mapping evidence")
        targets[link_name] = mapping
        if mapping not in parsed:
            parsed.append(mapping)
    return parsed


def local_locators_from_evidence(
    assets: list[RegisteredSkillAsset],
    names: list[str],
    evidence: dict[str, object] | None,
) -> dict[int, str]:
    raw_locators = (
        evidence.get("local_locators") if isinstance(evidence, dict) else None
    )
    if not isinstance(raw_locators, dict) or set(raw_locators) != set(names):
        raise ValueError(
            "Engine locator evidence does not match registered local Skills"
        )
    locators: dict[int, str] = {}
    for asset, name in zip(assets, names, strict=True):
        locator = raw_locators.get(name)
        if not isinstance(locator, str) or not locator.startswith("local:///"):
            raise ValueError(f"Engine returned an invalid local locator for {name}")
        path = PurePosixPath(locator[len("local://") :])
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != locator[len("local://") :]
        ):
            raise ValueError(f"Engine returned an invalid local locator for {name}")
        locators[asset.skill_id] = locator
    return locators


__all__ = [
    "build_logical_skill_mappings",
    "logical_skill_mappings_from_evidence",
    "local_locators_from_evidence",
    "local_skill_name",
    "merge_retired_logical_skill_mappings",
    "retired_logical_skill_mappings",
]
