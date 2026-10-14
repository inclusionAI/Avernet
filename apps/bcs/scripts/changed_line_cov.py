"""Changed-line coverage helpers for BCS coverage reports.

Maps the lines a change adds under apps/bcs (``git diff -U0 <base>``) onto a
cargo-llvm-cov Cobertura report and counts how many of the *instrumentable*
changed lines were hit. A changed line counts only when the report lists it
(blank lines, comments and code not compiled into the measured binary are not
instrumentable and are left out of the denominator), matching the unit-test
gate in cov_gate.py.

Changed Rust files that the report does not list at all are classified rather
than silently dropped, so a reader can tell "not applicable" from "not run":

  excluded  - matches the report's --ignore-filename-regex (outside the scope
              the report measures, e.g. tool crates the E2E stack never runs)
  skipped   - matches an extra changed-line-only exclusion (e.g. memory store
              backends, compiled in but never selected by the SQLite E2E
              stack); applied even when the report lists the file
  test_code - test-only sources (tests/, benches/, examples/, *_tests.rs)
  unmapped  - any other Rust source missing from the report
"""

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

BCS_PREFIX = "apps/bcs/"
_HUNK = re.compile(r"\+(\d+)(?:,(\d+))?")
_TEST_PATH = re.compile(r"(^|/)(tests|benches|examples)/|_tests?\.rs$")
_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _decode_git_path(token):
    """Decode a path from a ``+++`` header: C-quoted, or raw with a trailing TAB."""
    if not (len(token) >= 2 and token.startswith('"') and token.endswith('"')):
        # Git appends a TAB to unquoted header paths that contain spaces.
        return token.rstrip("\t")
    body, out, i = token[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch != "\\" or i + 1 == len(body):
            out += ch.encode("utf-8")
            i += 1
        elif body[i + 1] in "01234567":
            out.append(int(body[i + 1:i + 4], 8))  # octal byte of a UTF-8 sequence
            i += 4
        else:
            out.append(_C_ESCAPES.get(body[i + 1], ord(body[i + 1])))
            i += 2
    return out.decode("utf-8", errors="surrogateescape")


@dataclass
class FileResult:
    path: str
    changed: int
    instrumentable: int
    covered: int


@dataclass
class ChangedLineResult:
    files: list = field(default_factory=list)  # FileResult, measured files only
    excluded: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    test_code: list = field(default_factory=list)
    unmapped: list = field(default_factory=list)

    @property
    def instrumentable(self):
        return sum(f.instrumentable for f in self.files)

    @property
    def covered(self):
        return sum(f.covered for f in self.files)

    @property
    def rate(self):
        total = self.instrumentable
        return (self.covered / total * 100.0) if total else None

    def to_dict(self):
        return {
            "instrumentable_changed_lines": self.instrumentable,
            "covered_changed_lines": self.covered,
            "rate": self.rate,
            "files": [
                {
                    "path": f.path,
                    "changed": f.changed,
                    "instrumentable": f.instrumentable,
                    "covered": f.covered,
                }
                for f in sorted(self.files, key=lambda f: f.path)
            ],
            "excluded_files": sorted(self.excluded),
            "skipped_files": sorted(self.skipped),
            "test_files": sorted(self.test_code),
            "unmapped_files": sorted(self.unmapped),
        }


def parse_diff(diff_text):
    """Return {path relative to apps/bcs: set(added line numbers)}."""
    changed = {}
    current = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = _decode_git_path(line[4:])
            if target.startswith("b/") and target[2:].startswith(BCS_PREFIX):
                current = target[2 + len(BCS_PREFIX):]
            else:
                current = None  # /dev/null (deletion) or outside apps/bcs
            continue
        if current is None or not line.startswith("@@ "):
            continue
        match = _HUNK.search(line)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        if count:
            changed.setdefault(current, set()).update(range(start, start + count))
    return changed


def git_changed_lines(repo_root, base_ref):
    diff = subprocess.run(
        ["git", "-C", repo_root, "diff", "--no-renames", "-U0", base_ref, "--", "apps/bcs"],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="surrogateescape",
    ).stdout
    return parse_diff(diff)


def _normalize(filename, sources, bcs_dir):
    """Map a Cobertura filename to a path relative to apps/bcs."""
    candidates = [filename] if os.path.isabs(filename) else (
        [os.path.join(s, filename) for s in sources] + [filename])
    bcs_abs = os.path.abspath(bcs_dir) if bcs_dir else None
    rel = filename
    for candidate in candidates:
        if bcs_abs and os.path.isabs(candidate):
            under = os.path.relpath(os.path.normpath(candidate), bcs_abs)
            if not under.startswith(".."):
                rel = under
                break
    rel = rel.replace(os.sep, "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.removeprefix(BCS_PREFIX)


def load_cobertura(path, bcs_dir=None):
    """Return {path relative to apps/bcs: {line number: hits}}."""
    root = ET.parse(path).getroot()
    sources = [s.text.strip() for s in root.iter("source") if s.text and s.text.strip()]
    coverage = {}
    for cls in root.iter("class"):
        filename = cls.get("filename")
        if not filename:
            continue
        lines = coverage.setdefault(_normalize(filename, sources, bcs_dir), {})
        for line in cls.iter("line"):
            number, hits = line.get("number"), line.get("hits")
            try:
                n, h = int(number), int(hits)
            except (TypeError, ValueError):
                continue
            lines[n] = max(lines.get(n, 0), h)
    return coverage


def measure(changed, coverage, ignore_regex=None, skip_regex=None):
    ignore = re.compile(ignore_regex) if ignore_regex else None
    skip = re.compile(skip_regex) if skip_regex else None
    result = ChangedLineResult()
    for path, lines in sorted(changed.items()):
        if skip and path.endswith(".rs") and skip.search(path) and not _TEST_PATH.search(path):
            result.skipped.append(path)  # checked first: these files are in the report
            continue
        hits = coverage.get(path)
        if hits is None:
            if not path.endswith(".rs"):
                continue  # configs, docs, scripts: not part of a Rust report
            if ignore and ignore.search(path):
                result.excluded.append(path)
            elif _TEST_PATH.search(path):
                result.test_code.append(path)
            else:
                result.unmapped.append(path)
            continue
        instrumentable = [n for n in lines if n in hits]
        result.files.append(FileResult(
            path=path,
            changed=len(lines),
            instrumentable=len(instrumentable),
            covered=sum(1 for n in instrumentable if hits[n] > 0),
        ))
    return result
