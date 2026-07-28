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


def local_locators_from_evidence(
    assets: list[RegisteredSkillAsset],
    names: list[str],
    evidence: dict[str, object] | None,
) -> dict[int, str]:
    raw_locators = (
        evidence.get("local_locators")
        if isinstance(evidence, dict)
        else None
    )
    if not isinstance(raw_locators, dict) or set(raw_locators) != set(names):
        raise ValueError(
            "Engine locator evidence does not match registered local Skills"
        )
    locators: dict[int, str] = {}
    for asset, name in zip(assets, names, strict=True):
        locator = raw_locators.get(name)
        if not isinstance(locator, str) or not locator.startswith("local:///"):
            raise ValueError(
                f"Engine returned an invalid local locator for {name}"
            )
        path = PurePosixPath(locator[len("local://") :])
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != locator[len("local://") :]
        ):
            raise ValueError(
                f"Engine returned an invalid local locator for {name}"
            )
        locators[asset.skill_id] = locator
    return locators


__all__ = [
    "build_logical_skill_mappings",
    "local_locators_from_evidence",
    "local_skill_name",
]
