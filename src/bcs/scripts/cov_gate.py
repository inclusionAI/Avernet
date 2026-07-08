#!/usr/bin/env python3
"""BCS coverage gate: shared by GitHub CI (unit-tests.yml) and local pre-push.

Enforces three thresholds against the testresult/ produced by ci_test.sh
--coverage:
  1. Test pass rate  == 100%
  2. Overall line coverage > 70%
  3. Changed-line coverage >= 80%  (instrumentable changed BCS lines vs base ref)

When run inside GitHub Actions (CI=true), prints ::error::/::notice:: workflow
commands so failures annotate the job. Elsewhere, prints plain FAIL/OK lines
and exits non-zero on any failure.

Usage:
  python3 scripts/cov_gate.py --base-ref <git_ref> --bcs-dir <bcs_checkout_root>
"""

import argparse
import os
import re
import subprocess
import sys


def gh(msg):
    """Emit a GitHub Actions annotation when running in CI; else plain text."""
    if os.environ.get("CI") == "true" and os.environ.get("GITHUB_ACTIONS") == "true":
        print(msg)
    else:
        # Strip the "::error::"/"::notice::" prefix for local readability.
        if msg.startswith("::error::"):
            print("FAIL: " + msg[len("::error::"):])
        elif msg.startswith("::notice::"):
            print("OK:   " + msg[len("::notice::"):])
        else:
            print(msg)


def parse_args():
    p = argparse.ArgumentParser(description="BCS coverage gate")
    p.add_argument("--base-ref", required=True,
                   help="git ref to diff against for changed-line coverage")
    p.add_argument("--bcs-dir", required=True,
                   help="bcs checkout root (contains testresult/ + is under repo root)")
    p.add_argument("--pass-rate-min", type=float, default=100.0)
    p.add_argument("--overall-line-min", type=float, default=70.0)
    p.add_argument("--changed-line-min", type=float, default=80.0)
    return p.parse_args()


def main():
    args = parse_args()
    fail = False

    junit_path = os.path.join(args.bcs_dir, "testresult", "junit.xml")
    cobertura_path = os.path.join(args.bcs_dir, "testresult", "cobertura.xml")

    # ---- 1) Test pass rate ----
    try:
        with open(junit_path, encoding="utf-8", errors="replace") as f:
            j = f.read()
    except OSError as e:
        gh("::error::junit.xml not found at %s: %s" % (junit_path, e))
        sys.exit(1)
    m = re.search(r'<testsuites\b[^>]*>', j) or re.search(r'<testsuite\b[^>]*>', j)
    if not m:
        gh("::error::could not parse <testsuite(s)> header from junit.xml")
        sys.exit(1)
    tag = m.group(0)

    def attr(name, default="0"):
        mm = re.search(r'%s="(\d+)"' % name, tag)
        return int(mm.group(1)) if mm else int(default)

    tests = attr("tests"); failures = attr("failures")
    errors = attr("errors"); skipped = attr("skipped")
    passed = tests - failures - errors - skipped
    pass_rate = (passed / tests * 100.0) if tests else 0.0
    gh("[pass-rate] tests=%d passed=%d failures=%d errors=%d skipped=%d -> %.2f%%"
       % (tests, passed, failures, errors, skipped, pass_rate))
    if failures != 0 or errors != 0 or pass_rate < args.pass_rate_min:
        gh("::error::Pass rate %.2f%% is below the required %.2f%% (failures=%d errors=%d)"
            % (pass_rate, args.pass_rate_min, failures, errors))
        fail = True
    else:
        gh("::notice::Pass rate %.2f%% (requirement: %.2f%%) — OK"
           % (pass_rate, args.pass_rate_min))

    # ---- 2) Overall line coverage ----
    try:
        with open(cobertura_path, encoding="utf-8", errors="replace") as f:
            c = f.read()
    except OSError as e:
        gh("::error::cobertura.xml not found at %s: %s" % (cobertura_path, e))
        sys.exit(1)
    rm = re.search(r'<coverage\b[^>]*line-rate="([0-9.]+)"', c)
    overall = float(rm.group(1)) if rm else 0.0
    pct = overall * 100.0
    gh("[overall-line-cov] line-rate=%.6f -> %.2f%%" % (overall, pct))
    if pct <= args.overall_line_min:
        gh("::error::Overall line coverage %.2f%% is not > %.2f%%"
           % (pct, args.overall_line_min))
        fail = True
    else:
        gh("::notice::Overall line coverage %.2f%% (requirement: >%.2f%%) — OK"
           % (pct, args.overall_line_min))

    # ---- 3) Changed-line coverage ----
    # Parse per-class file coverage: filename -> {line_number: hits}
    coverage = {}
    for cls in re.findall(r'<class\b[^>]*>.*?</class>', c, flags=re.S):
        fm = re.search(r'filename="([^"]*)"', cls)
        if not fm:
            continue
        fname = fm.group(1)
        lines = {}
        for lm in re.finditer(r'<line\b[^>]*number="(\d+)"[^>]*hits="(\d+)"', cls):
            lines[int(lm.group(1))] = int(lm.group(2))
        for lm in re.finditer(r'<line\b[^>]*hits="(\d+)"[^>]*number="(\d+)"', cls):
            lines[int(lm.group(2))] = int(lm.group(1))
        coverage[fname] = lines

    # repo root = parent that contains .git; bcs-dir lives under it.
    root = subprocess.run(["git", "-C", args.bcs_dir, "rev-parse", "--show-toplevel"],
                          check=True, capture_output=True, text=True).stdout.strip()
    try:
        diff = subprocess.run(
            ["git", "-C", root, "diff", "--no-renames", "-U0",
             f"{args.base_ref}", "--", "src/bcs"],
            check=True, capture_output=True, text=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        gh("::error::git diff failed: %s" % e)
        fail = True
        diff = ""

    changed = {}  # relpath_under_bcs -> set(line numbers)
    cur_path = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            p = line[6:]
            if p.startswith("src/bcs/"):
                cur_path = p[len("src/bcs/"):]
            else:
                cur_path = None
            continue
        if line.startswith("@@ ") and cur_path is not None:
            hm = re.search(r'\+(\d+)(?:,(\d+))?', line)
            if not hm:
                continue
            start = int(hm.group(1))
            count = int(hm.group(2)) if hm.group(2) else 1
            changed.setdefault(cur_path, set()).update(range(start, start + count))

    total_changed = 0
    total_changed_covered = 0
    per_file = []
    for fname, lines in changed.items():
        cov = coverage.get(fname, {})
        for ln in lines:
            # Only count source lines coverage marked as valid instrumentable
            # (present in the cobertura <line> list). Non-instrumentable
            # additions (blank, comments) are excluded.
            if ln not in cov:
                continue
            total_changed += 1
            if cov[ln] > 0:
                total_changed_covered += 1
        per_file.append((fname, len(lines)))

    if total_changed == 0:
        gh("[changed-line-cov] no instrumentable changed source lines mapped; gate not applicable")
    else:
        chg_rate = total_changed_covered / total_changed * 100.0
        gh("[changed-line-cov] changed-instrumentable-lines=%d covered=%d -> %.2f%%"
           % (total_changed, total_changed_covered, chg_rate))
        for fname, n in per_file[:20]:
            gh("    %s: %d changed line(s)" % (fname, n))
        if chg_rate < args.changed_line_min:
            gh("::error::Changed-line coverage %.2f%% is below the required %.2f%%"
               % (chg_rate, args.changed_line_min))
            fail = True
        else:
            gh("::notice::Changed-line coverage %.2f%% (requirement: >=%.2f%%) — OK"
               % (chg_rate, args.changed_line_min))

    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()