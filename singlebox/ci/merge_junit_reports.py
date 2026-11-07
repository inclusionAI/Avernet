#!/usr/bin/env python3
"""Merge pytest JUnit XML reports without dropping individual testcases."""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _number(value: str | None, *, integer: bool = False) -> float | int:
    if value is None:
        return 0 if integer else 0.0
    try:
        return int(value) if integer else float(value)
    except ValueError as exc:
        raise ValueError(f"invalid JUnit numeric attribute: {value!r}") from exc


def merge(output: Path, inputs: list[Path]) -> None:
    if not inputs:
        raise ValueError("at least one JUnit input is required")

    roots: list[ET.Element] = []
    for path in inputs:
        if not path.is_file():
            raise FileNotFoundError(path)
        root = ET.parse(path).getroot()
        if root.tag != "testsuites":
            raise ValueError(f"{path}: expected testsuites root")
        roots.append(root)

    root = ET.Element("testsuites", dict(roots[0].attrib))
    suites = []
    for source_root in roots:
        suites.extend(source_root.findall("testsuite"))
    if not suites:
        raise ValueError("JUnit inputs contain no testsuite elements")

    merged_suite = ET.SubElement(root, "testsuite", dict(suites[0].attrib))
    total_tests = total_errors = total_failures = total_skipped = 0
    total_time = 0.0
    for suite in suites:
        total_tests += _number(suite.get("tests"), integer=True)
        total_errors += _number(suite.get("errors"), integer=True)
        total_failures += _number(suite.get("failures"), integer=True)
        total_skipped += _number(suite.get("skipped"), integer=True)
        total_time += _number(suite.get("time"))
        for testcase in suite.findall("testcase"):
            merged_suite.append(testcase)

    merged_suite.set("tests", str(total_tests))
    merged_suite.set("errors", str(total_errors))
    merged_suite.set("failures", str(total_failures))
    merged_suite.set("skipped", str(total_skipped))
    merged_suite.set("time", str(total_time))
    root.set("tests", str(total_tests))
    root.set("errors", str(total_errors))
    root.set("failures", str(total_failures))
    root.set("skipped", str(total_skipped))
    root.set("time", str(total_time))

    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(f"usage: {argv[0]} OUTPUT INPUT...", file=sys.stderr)
        return 2
    try:
        merge(Path(argv[1]), [Path(value) for value in argv[2:]])
    except (ET.ParseError, OSError, ValueError) as exc:
        print(f"merge junit reports failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
