#!/usr/bin/env python3
"""Report and gate bcs-cli leaf-command coverage from E2E invocations.

The command inventory is discovered from the built CLI's recursive `--help`
output, so adding a new Clap command automatically creates a coverage gap.
`help` pseudo-commands and visible aliases are intentionally excluded.
"""

import argparse
import os
import re
import subprocess
import sys


def in_ci():
    return os.environ.get("CI") == "true" and os.environ.get("GITHUB_ACTIONS") == "true"


def annotate(kind, message):
    if in_ci():
        print("::%s::%s" % (kind, message))


def child_commands(cli, prefix):
    result = subprocess.run(
        [cli, *prefix, "--help"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "failed to inspect `%s --help`: %s"
            % (" ".join([cli, *prefix]), result.stderr.strip())
        )

    commands = []
    in_commands = False
    for line in result.stdout.splitlines():
        if line == "Commands:":
            in_commands = True
            continue
        if not in_commands:
            continue
        if not line.strip():
            break
        match = re.match(r"^  ([a-z][a-z0-9-]*)(?:\s|$)", line)
        if match and match.group(1) != "help":
            commands.append(match.group(1))
    return commands


def leaf_commands(cli):
    leaves = []

    def visit(prefix):
        children = child_commands(cli, prefix)
        if not children:
            leaves.append(" ".join(prefix))
            return
        for child in children:
            visit([*prefix, child])

    for command in child_commands(cli, []):
        visit([command])
    return sorted(leaves)


def main():
    parser = argparse.ArgumentParser(description="BCS CLI E2E command coverage")
    parser.add_argument("--cli", required=True, help="path to the built bcs-cli binary")
    parser.add_argument("--log", required=True, help="newline-delimited invoked command paths")
    parser.add_argument("--out-txt", help="write the full coverage report to this path")
    parser.add_argument("--min", type=float, default=0.0, help="minimum coverage %%")
    args = parser.parse_args()

    if args.min < 0 or args.min > 100:
        parser.error("--min must be between 0 and 100")

    try:
        expected = leaf_commands(args.cli)
    except (OSError, RuntimeError) as error:
        print("ERROR: %s" % error, file=sys.stderr)
        sys.exit(1)
    if not expected:
        print("ERROR: discovered 0 bcs-cli leaf commands", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.log, encoding="utf-8", errors="replace") as handle:
            invocations = [line.strip() for line in handle if line.strip()]
    except OSError as error:
        print("ERROR: cannot read CLI coverage log %s: %s" % (args.log, error), file=sys.stderr)
        sys.exit(1)

    expected_set = set(expected)
    hit_set = set(invocations) & expected_set
    covered = sorted(hit_set)
    uncovered = sorted(expected_set - hit_set)
    unknown = sorted(set(invocations) - expected_set)
    percentage = len(covered) / len(expected) * 100.0

    summary = [
        "bcs-cli leaf command coverage: %d / %d (%.1f%%)"
        % (len(covered), len(expected), percentage),
        "raw invocations: %d" % len(invocations),
    ]
    report = [*summary, "", "Covered commands (%d):" % len(covered)]
    report.extend("  ✓ %s" % command for command in covered)
    report.append("")
    report.append("Uncovered commands (%d):" % len(uncovered))
    report.extend("  ✗ %s" % command for command in uncovered)
    if unknown:
        report.append("")
        report.append("Unknown logged command paths (%d):" % len(unknown))
        report.extend("  ? %s" % command for command in unknown)
    report_text = "\n".join(report) + "\n"

    if args.out_txt:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_txt)), exist_ok=True)
        with open(args.out_txt, "w", encoding="utf-8") as handle:
            handle.write(report_text)
    print("\n".join(summary))

    if percentage + 1e-9 < args.min:
        message = (
            "bcs-cli command coverage %.1f%% (%d / %d) is below the required %.1f%%"
            % (percentage, len(covered), len(expected), args.min)
        )
        annotate("error", message)
        if not in_ci():
            print("FAIL: " + message, file=sys.stderr)
        sys.exit(1)

    annotate(
        "notice",
        "bcs-cli command coverage gate passed: %.1f%% (%d / %d)"
        % (percentage, len(covered), len(expected)),
    )


if __name__ == "__main__":
    main()
