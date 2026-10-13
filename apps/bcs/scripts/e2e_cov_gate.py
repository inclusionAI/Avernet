#!/usr/bin/env python3
"""BCS e2e coverage gate.

Reads the cargo-llvm-cov JSON summary (target/cov-e2e/summary.json, produced by
`cargo llvm-cov report --summary-only --json`) and enforces line + method
(function) coverage thresholds. Region coverage is reported but NOT gated (e2e
runtime coverage of regions runs low and noisy). Emits GitHub Actions
::notice::/::error:: workflow annotations when run under CI, else plain OK/FAIL
lines — mirroring the style of cov_gate.py (the unit-test gate).

Why separate from cov_gate.py: e2e coverage is RUNTIME coverage of the
instrumented bcs binary (only paths the e2e suite exercises), so there is no
junit pass-rate file (the e2e suite is bash); the metrics and thresholds differ
from the unit gate.

A threshold of 0 means "report only, never fail on this metric".

With --cobertura and --base-ref it also reports E2E changed-line coverage: the
share of instrumentable lines added under apps/bcs since the base ref that the
e2e run executed. This metric is report-only and never fails the gate, even
when it cannot be computed.

Usage:
  python3 scripts/e2e_cov_gate.py --summary <summary.json> \
      [--line-min 20] [--method-min 20] [--region-min 0] \
      [--cobertura <cobertura.xml> --base-ref <git_ref> \
       [--ignore-filename-regex <regex>] [--changed-json <out.json>]]
"""

import argparse
import json
import os
import subprocess
import sys

import changed_line_cov


def gh(msg):
    """Emit a GitHub Actions annotation in CI; else strip the prefix for local readability."""
    if os.environ.get("CI") == "true" and os.environ.get("GITHUB_ACTIONS") == "true":
        print(msg)
    else:
        if msg.startswith("::error::"):
            print("FAIL: " + msg[len("::error::"):])
        elif msg.startswith("::notice::"):
            print("OK:   " + msg[len("::notice::"):])
        elif msg.startswith("::warning::"):
            print("WARN: " + msg[len("::warning::"):])
        else:
            print(msg)


def parse_args():
    p = argparse.ArgumentParser(description="BCS e2e coverage gate")
    p.add_argument("--summary", required=True,
                   help="path to `cargo llvm-cov report --summary-only --json` output")
    p.add_argument("--line-min", type=float, default=0.0,
                   help="minimum line coverage %% (0 = report only)")
    p.add_argument("--method-min", type=float, default=0.0,
                   help="minimum method/function coverage %% (0 = report only)")
    p.add_argument("--region-min", type=float, default=0.0,
                   help="minimum region coverage %% (0 = report only)")
    p.add_argument("--cobertura",
                   help="e2e cobertura.xml; with --base-ref, report changed-line coverage")
    p.add_argument("--base-ref",
                   help="git ref the change is diffed against for changed-line coverage")
    p.add_argument("--ignore-filename-regex",
                   help="regex the report excluded from its scope (labels excluded files)")
    p.add_argument("--changed-json",
                   help="write per-file changed-line coverage details to this path")
    return p.parse_args()


def pct(covered, count):
    return (covered / count * 100.0) if count else 0.0


def report(label, covered, count, minimum):
    """Emit one metric's annotation. Returns True if OK (or report-only), False on breach."""
    if count == 0:
        # Branches are not instrumented in the e2e coverage build, so they show
        # up as count=0 — report as not-measured rather than a misleading 0%.
        gh("::notice::E2E %s coverage: not measured (0 total) — skipped" % label)
        return True
    p = pct(covered, count)
    if minimum and minimum > 0:
        if p < minimum:
            gh("::error::E2E %s coverage %.2f%% is below the required %.2f%%"
               % (label, p, minimum))
            return False
        gh("::notice::E2E %s coverage %.2f%% (requirement: >=%.2f%%) — OK"
           % (label, p, minimum))
        return True
    gh("::notice::E2E %s coverage %.2f%% (reporting only, no threshold)" % (label, p))
    return True


def write_changed_json(path, payload):
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except OSError as e:
        gh("::warning::cannot write %s: %s" % (path, e))


def report_changed_lines(args):
    """Report E2E changed-line coverage. Report-only: never affects the exit code.

    Any previous --changed-json output is removed first, so a failed or skipped
    calculation never leaves an older run's result looking current.
    """
    if args.changed_json:
        try:
            os.remove(args.changed_json)
        except FileNotFoundError:
            pass
        except OSError as e:
            gh("::warning::cannot remove stale %s: %s" % (args.changed_json, e))
    if not args.cobertura or not args.base_ref:
        gh("::notice::E2E changed-line coverage: no base ref provided — skipped")
        write_changed_json(args.changed_json, {
            "status": "unavailable", "reason": "no base ref provided",
            "base_ref": args.base_ref})
        return
    bcs_dir = os.getcwd()
    try:
        repo_root = subprocess.run(
            ["git", "-C", bcs_dir, "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        changed = changed_line_cov.git_changed_lines(repo_root, args.base_ref)
        coverage = changed_line_cov.load_cobertura(args.cobertura, bcs_dir)
    except (OSError, subprocess.CalledProcessError, changed_line_cov.ET.ParseError) as e:
        gh("::warning::E2E changed-line coverage could not be computed: %s" % e)
        write_changed_json(args.changed_json, {
            "status": "unavailable", "reason": str(e), "base_ref": args.base_ref})
        return
    result = changed_line_cov.measure(changed, coverage, args.ignore_filename_regex)

    rate = result.rate
    if rate is None:
        gh("::notice::E2E changed-line coverage: no instrumentable changed lines in e2e scope "
           "— not applicable (reporting only, no threshold)")
    else:
        gh("::notice::E2E changed-line coverage %.2f%% (%d / %d changed lines; reporting only, "
           "no threshold)" % (rate, result.covered, result.instrumentable))
        ranked = sorted((f for f in result.files if f.instrumentable),
                        key=lambda f: (f.covered - f.instrumentable, f.path))
        for f in ranked[:20]:
            gh("    %s: %d / %d changed lines hit" % (f.path, f.covered, f.instrumentable))
    if result.excluded:
        gh("    %d changed file(s) outside the e2e report scope (not counted)"
           % len(result.excluded))
    if result.test_code:
        gh("    %d changed test-only file(s) (not counted)" % len(result.test_code))
    if result.unmapped:
        gh("::warning::%d changed Rust file(s) missing from the e2e report (not counted): %s"
           % (len(result.unmapped), ", ".join(result.unmapped[:10])))

    write_changed_json(args.changed_json, {
        "status": "ok" if rate is not None else "not_applicable",
        "base_ref": args.base_ref, **result.to_dict()})


def main():
    args = parse_args()
    fail = False

    try:
        with open(args.summary, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except OSError as e:
        gh("::error::coverage summary not found at %s: %s" % (args.summary, e))
        sys.exit(1)
    except json.JSONDecodeError as e:
        gh("::error::coverage summary is not valid JSON: %s" % e)
        sys.exit(1)

    try:
        totals = data["data"][0]["totals"]
    except (KeyError, IndexError, TypeError):
        gh("::error::coverage summary has unexpected structure (no data[0].totals)")
        sys.exit(1)

    def grp(name):
        d = totals.get(name) or {}
        return int(d.get("covered", 0) or 0), int(d.get("count", 0) or 0)

    l_c, l_t = grp("lines")
    m_c, m_t = grp("functions")   # "method" coverage
    r_c, r_t = grp("regions")
    b_c, b_t = grp("branches")

    gh("")
    gh("== e2e coverage gate ==")
    if not report("line", l_c, l_t, args.line_min):
        fail = True
    if not report("method", m_c, m_t, args.method_min):
        fail = True
    # Region: report-only by design (e2e runtime region coverage is low & noisy).
    report("region", r_c, r_t, args.region_min)
    # Branches: the e2e instrumented build (-Cinstrument-coverage) does not emit
    # branch coverage, so count is 0 -> reported as not measured.
    report("branch", b_c, b_t, 0.0)
    report_changed_lines(args)

    if fail:
        gh("::error::E2E coverage gate FAILED — metric(s) above below threshold.")
        sys.exit(1)
    gh("::notice::E2E coverage gate PASSED.")
    sys.exit(0)


if __name__ == "__main__":
    main()