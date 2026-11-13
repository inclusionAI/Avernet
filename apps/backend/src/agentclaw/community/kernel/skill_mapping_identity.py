"""Canonical identity of a Skill's runtime mapping source."""

from pathlib import PurePosixPath


def source_tail(git_path: str, prefix: str) -> PurePosixPath:
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


def mapping_source(
    git_path: str, skill_uuid: str | None, version: str | None
) -> tuple[str, PurePosixPath | None, str]:
    if git_path.startswith("local://"):
        relative = PurePosixPath(source_tail(git_path, "local://").name)
        return "local", relative, f"local:{relative.as_posix()}"
    if git_path.startswith("git://"):
        relative = source_tail(git_path, "git://")
        return "repo", relative, f"repo:{relative.as_posix()}"
    if git_path.startswith("center://"):
        if not skill_uuid or not version:
            raise ValueError("center mapping requires structured identity")
        return "center", None, f"center:{skill_uuid}:{version}"
    raise ValueError(f"unsupported skill source: {git_path}")
