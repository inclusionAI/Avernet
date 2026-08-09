#!/usr/bin/env python3
"""Discover the exact non-beta OpenClaw versions covered by the plugin floor."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REPAIR_SUFFIX_RE = re.compile(r"^\d{4}\.\d+\.\d+(?:-\d+)?$")
UNSTABLE_RE = re.compile(r"(?:^|[-.])(alpha|beta|rc)(?:[.-]|$)", re.IGNORECASE)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def plugin_package_path(repo_root: Path) -> Path:
    return repo_root / "src/bcs/crates/plugins/openclaw-channel-bcn/package.json"


def numeric_core(version: str) -> tuple[int, int, int]:
    core = version.split("-", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"unsupported OpenClaw version: {version}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def version_key(version: str) -> tuple[int, int, int, int]:
    core = numeric_core(version)
    _, _, suffix = version.partition("-")
    repair = int(suffix) if suffix.isdigit() else 0
    return (*core, repair)


def plugin_api_floor(package: dict[str, Any]) -> str:
    compat = package.get("openclaw", {}).get("compat", {}).get("pluginApi")
    peer = package.get("peerDependencies", {}).get("openclaw")
    declared = compat or peer
    if not isinstance(declared, str):
        raise ValueError("plugin package does not declare an OpenClaw compatibility floor")
    match = re.search(r"\d{4}\.\d+\.\d+(?:-\d+)?", declared)
    if not match:
        raise ValueError(f"cannot parse OpenClaw compatibility floor: {declared}")
    return match.group(0)


def select_versions(
    versions: list[str],
    *,
    floor: str,
    latest: str,
) -> list[str]:
    floor_version = version_key(floor)
    latest_version = version_key(latest)
    selected: list[str] = []
    for version in versions:
        if UNSTABLE_RE.search(version):
            continue
        if not REPAIR_SUFFIX_RE.fullmatch(version):
            continue
        candidate = version_key(version)
        if floor_version <= candidate <= latest_version:
            selected.append(version)
    if latest not in selected and not UNSTABLE_RE.search(latest):
        selected.append(latest)
    return sorted(dict.fromkeys(selected), key=version_key)


def npm_json(*args: str) -> Any:
    command = ["npm", "view", "openclaw", *args, "--json"]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"npm returned invalid JSON for {' '.join(command)}") from error


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def discover(
    *,
    package_file: Path,
    versions_file: Path | None = None,
    dist_tags_file: Path | None = None,
) -> dict[str, Any]:
    package = load_json(package_file)
    floor = plugin_api_floor(package)
    versions = load_json(versions_file) if versions_file else npm_json("versions")
    dist_tags = load_json(dist_tags_file) if dist_tags_file else npm_json("dist-tags")
    if not isinstance(versions, list) or not all(isinstance(item, str) for item in versions):
        raise ValueError("OpenClaw versions payload must be a string array")
    if not isinstance(dist_tags, dict) or not isinstance(dist_tags.get("latest"), str):
        raise ValueError("OpenClaw dist-tags payload must contain latest")

    latest = dist_tags["latest"]
    if UNSTABLE_RE.search(latest) or not REPAIR_SUFFIX_RE.fullmatch(latest):
        raise ValueError(f"OpenClaw latest dist-tag is not a stable release: {latest}")
    selected = select_versions(versions, floor=floor, latest=latest)
    return {
        "package": "openclaw",
        "plugin_package": package.get("name"),
        "plugin_version": package.get("version"),
        "floor": floor,
        "latest": latest,
        "dist_tags": dist_tags,
        "count": len(selected),
        "versions": selected,
        "matrix": {"include": [{"openclaw": version} for version in selected]},
        "filter": {
            "excluded_labels": ["alpha", "beta", "rc"],
            "repair_suffixes_included": True,
        },
    }


def parse_args() -> argparse.Namespace:
    repo_root = repository_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-file", type=Path, default=plugin_package_path(repo_root))
    parser.add_argument("--versions-file", type=Path)
    parser.add_argument("--dist-tags-file", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = discover(
            package_file=args.package_file,
            versions_file=args.versions_file,
            dist_tags_file=args.dist_tags_file,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"openclaw version discovery failed: {error}", file=sys.stderr)
        return 1

    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
