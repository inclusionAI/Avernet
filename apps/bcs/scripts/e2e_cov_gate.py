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
junit pass-rate file (the e2e suite is bash) and no changed-line concept; the
metrics and thresholds differ from the unit gate.

A threshold of 0 means "report only, never fail on this metric".

Usage:
  python3 scripts/e2e_cov_gate.py --summary <summary.json> \
      [--line-min 20] [--method-min 20] [--region-min 0]
"""

import argparse
import json
import os
import sys


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

    if fail:
        gh("::error::E2E coverage gate FAILED — metric(s) above below threshold.")
        sys.exit(1)
    gh("::notice::E2E coverage gate PASSED.")
    sys.exit(0)


if __name__ == "__main__":
    main()