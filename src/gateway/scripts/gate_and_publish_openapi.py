"""Gate a candidate published OpenAPI against the current one, then publish.

Run in backend release CI, after ``openapi_v1.dump.dump_openapi`` produces the
candidate description:

    # 1. backend venv — produce the candidate public description
    python -m agentclaw.community.adapters.http.openapi_v1.dump /tmp/candidate.json

    # 2. gateway venv — gate for backward-compat, then publish on pass
    python scripts/gate_and_publish_openapi.py \
        configs/schemas/bots.openapi.json /tmp/candidate.json

The gate compares the candidate against the currently-published artifact and
**fails the release** (exit 1) on any backward-incompatible change, unless
``--allow-breaking`` is passed for an explicitly-coordinated change (record the
reason in the PR). On success the candidate becomes the new published artifact
(the committed single-box file); a distributed deploy adds an object-store
upload step here — the gate logic is identical.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from gateway.community.core.forwarding import Breaking, check_compatible


def gate(published: Path, candidate: Path, *, allow_breaking: bool) -> list[Breaking]:
    """Return the breaking changes; raise ``SystemExit(1)`` if they block publish."""
    candidate_doc = json.loads(candidate.read_text(encoding="utf-8"))
    published_doc = (
        json.loads(published.read_text(encoding="utf-8"))
        if published.exists()
        else {"paths": {}}
    )
    breaks = check_compatible(published_doc, candidate_doc)
    if breaks and not allow_breaking:
        print(f"BREAKING: {len(breaks)} backward-incompatible change(s):")
        for b in breaks:
            print(f"  [{b.kind}] {b.where} {b.detail}".rstrip())
        print(
            "\nRefusing to publish. Cut a new major version, or pass "
            "--allow-breaking for a coordinated change."
        )
        raise SystemExit(1)
    return breaks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("published", type=Path, help="current published artifact")
    parser.add_argument("candidate", type=Path, help="newly-dumped description")
    parser.add_argument("--allow-breaking", action="store_true")
    args = parser.parse_args(argv)

    breaks = gate(args.published, args.candidate, allow_breaking=args.allow_breaking)
    shutil.copyfile(args.candidate, args.published)
    note = f"{len(breaks)} allowed breaking" if breaks else "compatible"
    print(f"published {args.candidate} -> {args.published} ({note})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
