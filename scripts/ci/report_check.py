#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RED = "\033[0;31m"
NC = "\033[0m"


def parse_junit(path: Path) -> tuple[int, int, int]:
    root = ET.parse(path).getroot()
    nodes = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(int(node.attrib.get("tests", "0")) for node in nodes)
    failures = sum(int(node.attrib.get("failures", "0")) for node in nodes)
    errors = sum(int(node.attrib.get("errors", "0")) for node in nodes)
    return tests, failures, errors


def parse_coverage(path: Path) -> tuple[float, dict[str, dict[int, int]]]:
    root = ET.parse(path).getroot()
    line_rate = float(root.attrib.get("line-rate", "0")) * 100
    hits_by_file: dict[str, dict[int, int]] = {}
    for cls in root.findall(".//class"):
        filename = cls.attrib.get("filename", "")
        if not filename:
            continue
        line_hits: dict[int, int] = {}
        for line in cls.findall("./lines/line"):
            line_hits[int(line.attrib["number"])] = int(line.attrib.get("hits", "0"))
        hits_by_file[filename] = line_hits
    return line_rate, hits_by_file


def repository_relative_path(path: Path, repository_root: Path) -> Path:
    try:
        return path.resolve().relative_to(repository_root.resolve())
    except ValueError:
        return path


def find_repository_root(path: Path) -> Path:
    current = path.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError(f"could not find repository root from {path}")


def clean_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_INDEX_FILE"):
        environment.pop(name, None)
    return environment


def changed_lines(base: str, head: str, source_root: Path) -> dict[str, set[int]]:
    repository_root = find_repository_root(source_root)
    source_pathspec = repository_relative_path(source_root, repository_root)
    diff = subprocess.run(
        ["git", "diff", "--unified=0", base, head, "--", str(source_pathspec)],
        check=True,
        cwd=repository_root,
        env=clean_git_environment(),
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    current_file: str | None = None
    result: dict[str, set[int]] = {}
    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line.removeprefix("+++ b/")
            result.setdefault(current_file, set())
            continue
        if not raw_line.startswith("@@") or current_file is None:
            continue
        plus_part = raw_line.split(" +", 1)[1].split(" ", 1)[0]
        if "," in plus_part:
            start_text, count_text = plus_part.split(",", 1)
            start, count = int(start_text), int(count_text)
        else:
            start, count = int(plus_part), 1
        if count > 0:
            result[current_file].update(range(start, start + count))
    return result


def coverage_candidates(
    diff_file: str, source_root: Path, repository_root: Path
) -> list[str]:
    path = Path(diff_file)
    candidates = [diff_file]
    try:
        source_root_relative = repository_relative_path(source_root, repository_root)
        candidates.append(str(path.relative_to(source_root_relative)))
    except ValueError:
        pass
    return candidates


def find_coverage_hits(
    diff_file: str,
    coverage_hits: dict[str, dict[int, int]],
    source_root: Path,
    repository_root: Path,
) -> dict[int, int] | None:
    candidates = coverage_candidates(diff_file, source_root, repository_root)
    for candidate in candidates:
        for name, hits in coverage_hits.items():
            if name == candidate or name.endswith("/" + candidate):
                return hits

    # Some coverage producers report only basenames. Keep that compatibility
    # only when the basename identifies exactly one file in the report.
    basename = Path(diff_file).name
    basename_matches = [
        hits for name, hits in coverage_hits.items() if Path(name).name == basename
    ]
    if len(basename_matches) == 1:
        return basename_matches[0]
    return None


def check_change_coverage(
    coverage_hits: dict[str, dict[int, int]],
    changed: dict[str, set[int]],
    source_root: Path,
    minimum: float,
) -> None:
    repository_root = find_repository_root(source_root)
    total = 0
    covered = 0
    uncovered_by_file: dict[str, list[int]] = {}
    for diff_file, lines in changed.items():
        matched_hits = find_coverage_hits(
            diff_file, coverage_hits, source_root, repository_root
        )
        if matched_hits is None:
            continue
        uncovered: list[int] = []
        for line in lines:
            if line in matched_hits:
                total += 1
                if matched_hits[line] > 0:
                    covered += 1
                else:
                    uncovered.append(line)
        if uncovered:
            uncovered_by_file[diff_file] = sorted(uncovered)

    if total == 0:
        print(
            "change line coverage: no changed executable lines found; treated as pass"
        )
        return

    rate = covered / total * 100
    print(
        f"change line coverage: {rate:.2f}% ({covered}/{total}, required >= {minimum:.2f}%)"
    )
    if rate + 1e-9 < minimum:
        print(
            f"{RED}FAILED: changed-line coverage {rate:.2f}% "
            f"is below minimum {minimum:.2f}%{NC}",
            file=sys.stderr,
        )
        print("uncovered changed executable lines:")
        for filename, lines in uncovered_by_file.items():
            print(f"  {filename}: {','.join(str(line) for line in lines)}")
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--min-case-pass-rate", type=float, default=100)
    parser.add_argument("--min-line-coverage", type=float)
    parser.add_argument("--min-change-line-coverage", type=float)
    args = parser.parse_args()

    tests, failures, errors = parse_junit(args.junit)
    passed = tests - failures - errors
    case_rate = 100.0 if tests == 0 else passed / tests * 100
    print(
        f"case pass rate: {case_rate:.2f}% ({passed}/{tests}, required >= {args.min_case_pass_rate:.2f}%)"
    )
    if case_rate + 1e-9 < args.min_case_pass_rate:
        print(
            f"{RED}FAILED: case pass rate {case_rate:.2f}% "
            f"is below minimum {args.min_case_pass_rate:.2f}%{NC}",
            file=sys.stderr,
        )
        return 1

    line_rate, hits = parse_coverage(args.coverage)
    if args.min_line_coverage is not None:
        print(
            f"line coverage: {line_rate:.2f}% (required >= {args.min_line_coverage:.2f}%)"
        )
        if line_rate + 1e-9 < args.min_line_coverage:
            print(
                f"{RED}FAILED: line coverage {line_rate:.2f}% "
                f"is below minimum {args.min_line_coverage:.2f}%{NC}",
                file=sys.stderr,
            )
            return 1

    if args.min_change_line_coverage is not None and args.base:
        changed = changed_lines(args.base, args.head, args.source_root)
        check_change_coverage(
            hits, changed, args.source_root, args.min_change_line_coverage
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
