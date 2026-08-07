#!/usr/bin/env python3
"""Discover and execute the complete OpenClaw compatibility matrix."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from discover_versions import REPAIR_SUFFIX_RE, discover, repository_root
from report import write_reports


def version_slug(version: str) -> str:
    return version.replace("/", "_")


def run_version(
    *,
    version: str,
    plugin_dir: Path,
    results_dir: Path,
    npm_cache: Path,
    timeout_seconds: int,
    skip_typecheck: bool,
    skip_runtime: bool,
) -> tuple[str, int]:
    output_dir = results_dir / version_slug(version)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).with_name("run_one.py")),
        "--version",
        version,
        "--plugin-dir",
        str(plugin_dir),
        "--output-dir",
        str(output_dir),
        "--npm-cache",
        str(npm_cache),
        "--timeout-seconds",
        str(timeout_seconds),
        "--skip-plugin-build",
    ]
    if skip_typecheck:
        command.append("--skip-typecheck")
    if skip_runtime:
        command.append("--skip-runtime")
    with (output_dir / "runner.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    return version, completed.returncode


def ensure_plugin_ready(plugin_dir: Path, logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    required_tools = [plugin_dir / "node_modules/.bin/tsc", plugin_dir / "node_modules/.bin/tshy"]
    if not all(tool.is_file() for tool in required_tools):
        with (logs_dir / "plugin-install.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                ["npm", "install", "--package-lock=false", "--no-audit", "--no-fund"],
                cwd=plugin_dir,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError("plugin npm install failed")
    with (logs_dir / "plugin-build.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            ["npm", "run", "build"],
            cwd=plugin_dir,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError("plugin build failed")


def selected_versions(args: argparse.Namespace, discovery: dict[str, Any]) -> list[str]:
    versions = list(dict.fromkeys(args.version)) if args.version else list(discovery["versions"])
    invalid = [version for version in versions if not REPAIR_SUFFIX_RE.fullmatch(version)]
    if invalid:
        raise ValueError(f"only exact non-beta OpenClaw versions are supported: {', '.join(invalid)}")
    return versions


def remove_existing_file(path: Path) -> None:
    if path.is_file() or path.is_symlink():
        path.unlink()


def clear_selected_results(results_dir: Path, versions: list[str]) -> None:
    for version in versions:
        remove_existing_file(results_dir / version_slug(version) / "result.json")


def parse_args() -> argparse.Namespace:
    repo_root = repository_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-dir", type=Path, default=repo_root / "src/bcs/crates/plugins/openclaw-channel-bcn")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "scripts/.dependencies/compat/openclaw",
    )
    parser.add_argument(
        "--npm-cache",
        type=Path,
        default=repo_root / "scripts/.dependencies/cache/openclaw-npm",
    )
    parser.add_argument("--version", action="append", help="run an exact version; repeat as needed")
    parser.add_argument("--max-workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--versions-file", type=Path)
    parser.add_argument("--dist-tags-file", type=Path)
    parser.add_argument("--skip-typecheck", action="store_true")
    parser.add_argument("--skip-runtime", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results_dir = args.output_dir / "results"
    reports_dir = args.output_dir / "reports"
    discovery_file = args.output_dir / "discovery.json"
    npm_cache = args.npm_cache
    discovery_is_current = False

    try:
        remove_existing_file(discovery_file)
        discovery = discover(
            package_file=args.plugin_dir.resolve() / "package.json",
            versions_file=args.versions_file,
            dist_tags_file=args.dist_tags_file,
        )
        versions = selected_versions(args, discovery)
        if not versions:
            raise ValueError("no OpenClaw versions were selected")
        if args.max_workers < 1:
            raise ValueError("--max-workers must be at least 1")
        discovery = dict(discovery)
        discovery["versions"] = versions
        discovery["count"] = len(versions)
        discovery["matrix"] = {"include": [{"openclaw": version} for version in versions]}
        discovery_file.write_text(json.dumps(discovery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        discovery_is_current = True
        clear_selected_results(results_dir, versions)
        ensure_plugin_ready(args.plugin_dir.resolve(), args.output_dir / "setup-logs")
    except Exception as error:
        print(f"OpenClaw compatibility setup failed: {error}", file=sys.stderr)
        try:
            if not discovery_is_current:
                discovery_file.write_text(
                    json.dumps(
                        {
                            "floor": None,
                            "latest": None,
                            "versions": [],
                            "count": 0,
                            "matrix": {"include": []},
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            with tempfile.TemporaryDirectory(prefix="avernet-openclaw-empty-results-") as temporary:
                write_reports(
                    results_dir=Path(temporary),
                    output_dir=reports_dir,
                    discovery_file=discovery_file,
                    setup_error=str(error),
                )
        except Exception as report_error:
            print(f"OpenClaw setup failure report could not be written: {report_error}", file=sys.stderr)
        return 2

    print(f"Testing {len(versions)} OpenClaw version(s) with {args.max_workers} worker(s)")
    exit_codes: dict[str, int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(
                run_version,
                version=version,
                plugin_dir=args.plugin_dir.resolve(),
                results_dir=results_dir,
                npm_cache=npm_cache,
                timeout_seconds=args.timeout_seconds,
                skip_typecheck=args.skip_typecheck,
                skip_runtime=args.skip_runtime,
            ): version
            for version in versions
        }
        for future in concurrent.futures.as_completed(futures):
            version = futures[future]
            try:
                resolved_version, exit_code = future.result()
            except Exception as error:
                print(f"{version}: runner failed: {error}", file=sys.stderr)
                exit_codes[version] = 2
            else:
                exit_codes[resolved_version] = exit_code
                print(f"{resolved_version}: {'pass' if exit_code == 0 else 'fail'}")

    summary = write_reports(
        results_dir=results_dir,
        output_dir=reports_dir,
        discovery_file=discovery_file,
    )
    print((reports_dir / "summary.md").read_text(encoding="utf-8"))
    return 0 if summary["compatible"] and all(code == 0 for code in exit_codes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
