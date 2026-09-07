#!/usr/bin/env python3
"""merge-config.py — deep-merge an image-rendered JSON config into a
persistent user copy.

Problem this solves: /home/admin is NAS-mounted and outlives image upgrades,
so a config generated on first boot (mount-wins, e.g. ~/.openclaw/openclaw.json
and ~/.claude/models.json) stays stale forever — image-side key additions and
value updates never reach running pods.

On every engine startup the freshly rendered image template (base) is merged
INTO the user file (target):

- dict + dict   → recursive merge: target-only keys survive, base-only keys
                  are added, conflicting leaves take the base (image) value,
                  so image updates heal the stale copy while user/deployment
                  customizations that the image does not define are kept.
- list + list   → when every element is an object with an "id" field, both
                  lists merge by id: base entries update matching target
                  entries (recursively) and new base entries are appended;
                  target-only entries stay in place. Lists that are not
                  id-objects are replaced by the base list wholesale.
- other / type mismatch → base value replaces the target value.
- --keys FILE   → optional allowlist file maintained alongside the image:
                  one key or dot-path per line ('#' comments, blank lines
                  ignored; the shipped files are docker/agent/openclaw.path
                  and docker/agent/claude_code.path). Only the listed
                  subtrees merge from the image side — everything else the
                  base carries behaves as user-owned and is never touched.
                  A '*' line merges the whole base (used for top-level
                  arrays like models.json, where entry-level id merging
                  already protects user entries). A path that misses in the
                  base is skipped with a warning; an allowlist that lists
                  nothing is a deliberate no-op. Without --keys the whole
                  base merges.
- UNSET guard   → base string values exactly equal to "UNSET" are dropped
                  before merging: that literal is the renderer's marker for
                  an un-injected env var and must never clobber a real value.

Idempotence: when nothing changes, the target file is not written (no mtime
churn on repeated boots). Effective changes are written atomically (temp
file + rename in the target directory), preserve the target's owner, group
and mode, and save a .bak snapshot of the pre-merge content next to it.

Exit codes: 0 merged or no-change, 2 usage error, 3 target unreadable or
invalid JSON (file untouched), 4 base unreadable or invalid JSON,
5 write failure, 6 keys file unreadable.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile

#: Renderer marker for "env var was not injected" — never let it clobber.
_UNSET = "UNSET"


def drop_unset(value):
    """Remove dict entries whose value is the literal "UNSET" marker."""
    if isinstance(value, dict):
        return {
            k: drop_unset(v)
            for k, v in value.items()
            if not (isinstance(v, str) and v == _UNSET)
        }
    if isinstance(value, list):
        return [drop_unset(v) for v in value]
    return value


def _mergeable_by_id(seq: list) -> bool:
    """True when every element is a dict carrying an "id" field."""
    return all(isinstance(e, dict) and "id" in e for e in seq)


def _get_path(node, dotted: str):
    """Walk a dot-separated path (e.g. "agents.defaults") through dicts.

    Returns (found, value). Any non-dict hop or missing key → not found. Path
    segments are literal keys — no wildcards, no index addressing.
    """
    cur = node
    for seg in dotted.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return False, None
        cur = cur[seg]
    return True, cur


def restrict_to_paths(base, paths: list[str]):
    """Trim the base to only the given dotted paths (the --keys allowlist).

    Builds a fresh tree containing just the whitelisted subtrees, so merges
    confine themselves to those paths; everything else the base carries is
    user-owned (the target keeps its value). A path that misses anywhere
    along its segments is skipped ENTIRELY — no partial skeleton is emitted,
    so a missing path can never inject empty objects into the target. An
    allowlist that matches nothing collapses the base to {}, a pure no-op
    merge. Requires a dict base; a non-dict base also yields {}.
    """
    if not isinstance(base, dict):
        return {}
    out: dict = {}
    for dotted in paths:
        found, val = _get_path(base, dotted)
        if not found:
            continue
        cur = out
        segs = dotted.split(".")
        for seg in segs[:-1]:
            cur = cur.setdefault(seg, {})
        cur[segs[-1]] = val
    return out


def load_keys(path: str) -> tuple[bool, list[str]]:
    """Parse a --keys allowlist file: one key or dot-path per line.

    '#' starts a comment (also allowed after a path on the same line),
    blank lines are ignored. Returns (whole, paths): whole is True when the
    file contains a '*' line — merge the entire base; paths lists the
    dot-paths otherwise. A file with only comments yields (False, []),
    a deliberate merge-nothing allowlist.
    """
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    paths: list[str] = []
    for ln in lines:
        ln = ln.split("#", 1)[0].strip()
        if not ln:
            continue
        if ln == "*":
            return True, []
        paths.append(ln)
    return False, paths


def deep_merge(base, target):
    """Merge base (image) into target (user); base wins conflicts."""
    if isinstance(base, dict) and isinstance(target, dict):
        out = dict(target)  # target order preserved; target-only keys kept
        for k, bv in base.items():
            out[k] = deep_merge(bv, target[k]) if k in target else bv
        return out
    if isinstance(base, list) and isinstance(target, list):
        if _mergeable_by_id(base) and _mergeable_by_id(target):
            base_by_id = {e["id"]: e for e in base}
            target_ids = set()
            out = []
            for t in target:  # target order preserved
                target_ids.add(t["id"])
                b = base_by_id.get(t["id"])
                out.append(deep_merge(b, t) if b is not None else t)
            for b in base:  # new image entries appended in template order
                if b["id"] not in target_ids:
                    out.append(b)
            return out
        return base
    return base


def _leaves(node, prefix=()):
    """Flatten a JSON tree into {path-tuple: scalar} for diff statistics."""
    out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(_leaves(v, prefix + (str(k),)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.update(_leaves(v, prefix + (str(i),)))
    else:
        out[prefix] = node
    return out


def _diff_stats(merged, target):
    m, t = _leaves(merged), _leaves(target)
    added = sum(1 for p in m if p not in t)
    changed = sum(1 for p in m if p in t and m[p] != t[p])
    return added, changed


def _atomic_write(path: str, text: str) -> None:
    """Write text to path preserving owner/group/mode; temp+rename atomic."""
    st = os.stat(path)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path) or ".", prefix=".merge-config-"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        try:
            os.chown(tmp, st.st_uid, st.st_gid)  # best-effort (NAS may refuse)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deep-merge a rendered image JSON template into a "
                    "persistent user copy (image keys land, user-only keys "
                    "survive)."
    )
    parser.add_argument("--base", required=True,
                        help="image-rendered template JSON (winning side)")
    parser.add_argument("--target", required=True,
                        help="persistent user file to merge into")
    parser.add_argument("--keys",
                        help="optional allowlist file: one key or dot-path "
                             "per line (see docker/agent/openclaw.path / "
                             "claude_code.path); only listed subtrees "
                             "merge, '*' merges the whole base. Omitted = "
                             "merge the whole base.")
    args = parser.parse_args()

    try:
        with open(args.base, "r", encoding="utf-8") as fh:
            base = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"merge-config: ERROR reading base {args.base}: {e}",
              file=sys.stderr)
        return 4
    try:
        with open(args.target, "r", encoding="utf-8") as fh:
            target_text = fh.read()
            target = json.loads(target_text)
    except (OSError, json.JSONDecodeError) as e:
        print(f"merge-config: ERROR reading target {args.target}: {e} "
              f"— target untouched, keeping it as-is", file=sys.stderr)
        return 3

    if args.keys:
        try:
            whole, paths = load_keys(args.keys)
        except OSError as e:
            print(f"merge-config: ERROR reading keys {args.keys}: {e}",
                  file=sys.stderr)
            return 6
        if not whole:
            # Warn on allowlist entries missing from the base — a typo or a
            # stale patch file should surface during startup, not silently
            # drop a key the operator expects to be merged.
            if isinstance(base, dict):
                missed = [p for p in paths if not _get_path(base, p)[0]]
            else:
                missed = list(paths)
            if missed:
                print(f"merge-config: WARNING: keys not in base, skipped: "
                      f"{', '.join(missed)}", file=sys.stderr)
            base = restrict_to_paths(base, paths)
    base = drop_unset(base)

    merged = deep_merge(base, target)

    if merged == target:
        print(f"merge-config: {args.target}: no changes")
        return 0

    added, changed = _diff_stats(merged, target)
    bak = args.target + ".bak"
    try:
        with open(bak, "w", encoding="utf-8") as fh:
            fh.write(target_text)
        _atomic_write(args.target, json.dumps(merged, indent=2) + "\n")
    except OSError as e:
        print(f"merge-config: ERROR writing {args.target}: {e}",
              file=sys.stderr)
        return 5
    print(f"merge-config: {args.target}: {added} key(s) added, "
          f"{changed} updated (backup: {bak})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
