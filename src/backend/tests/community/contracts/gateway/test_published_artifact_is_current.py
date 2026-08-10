"""The gateway's pinned description must match the surface this backend serves.

``src/gateway/configs/schemas/bots.openapi.json`` is a build output, and it is
what the gateway generates its served doc from — callers read *it*, not this
app's live ``/openapi.json``. Nothing regenerated it automatically, so a change
to a router or a schema model shipped green while the published description
still described the previous surface.

That is not a hypothetical: a whole pass of request-body examples and field
descriptions landed with every test passing, and the doc external callers read
did not change by one byte. The failure is silent by construction — the source
is right, the tests are right, and only the artifact is wrong.

So the artifact is checked here, against a fresh dump, the same way any other
generated file is guarded against drifting from its source.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dump_openapi import build_public_openapi

#: The published artifact, relative to this file. ``parents[5]`` is ``src/``.
_ARTIFACT = (
    Path(__file__).resolve().parents[5]
    / "gateway"
    / "configs"
    / "schemas"
    / "bots.openapi.json"
)

#: What to run when this fails. Quoted verbatim into the failure message so the
#: fix does not have to be looked up.
_REGENERATE = """
    cd src/backend && DEPLOY_PROFILE=community \\
        python scripts/dump_openapi.py /tmp/candidate.json
    cd src/gateway && python scripts/gate_and_publish_openapi.py \\
        configs/schemas/bots.openapi.json /tmp/candidate.json

(or src/gateway/scripts/dump_and_publish.sh, which runs both ends)
"""


def _diff_summary(published: dict, fresh: dict) -> list[str]:
    """Where the two descriptions disagree, in terms a reader can act on."""
    problems: list[str] = []

    pub_paths, new_paths = set(published.get("paths", {})), set(fresh.get("paths", {}))
    for path in sorted(new_paths - pub_paths):
        problems.append(f"path missing from the artifact: {path}")
    for path in sorted(pub_paths - new_paths):
        problems.append(f"path in the artifact but no longer served: {path}")
    for path in sorted(pub_paths & new_paths):
        if published["paths"][path] != fresh["paths"][path]:
            problems.append(f"path differs: {path}")

    pub_schemas = (published.get("components") or {}).get("schemas") or {}
    new_schemas = (fresh.get("components") or {}).get("schemas") or {}
    for name in sorted(set(new_schemas) - set(pub_schemas)):
        problems.append(f"schema missing from the artifact: {name}")
    for name in sorted(set(pub_schemas) - set(new_schemas)):
        problems.append(f"schema in the artifact but no longer defined: {name}")
    for name in sorted(set(pub_schemas) & set(new_schemas)):
        if pub_schemas[name] != new_schemas[name]:
            problems.append(f"schema differs: {name}")
    return problems


def test_published_gateway_artifact_matches_this_surface() -> None:
    """Regenerate the artifact in the same commit that changes the surface."""
    if not _ARTIFACT.exists():
        # The community distribution ships without the gateway tree; there is no
        # artifact to hold to the source, and its absence is not a failure.
        pytest.skip(f"no published artifact at {_ARTIFACT}")

    published = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    fresh = build_public_openapi()

    problems = _diff_summary(published, fresh)
    if not problems and published != fresh:
        # Equal paths and schemas but unequal documents — a top-level key such as
        # `info` or `openapi` moved. Named separately because the per-path diff
        # above would report nothing and the failure would read as a phantom.
        differing = sorted(
            key
            for key in set(published) | set(fresh)
            if published.get(key) != fresh.get(key)
        )
        problems.append(f"top-level keys differ: {', '.join(differing)}")

    assert not problems, (
        "The gateway's published description is out of date — the doc callers "
        "read does not match the surface this backend serves.\n\n  "
        + "\n  ".join(problems[:40])
        + (f"\n  …and {len(problems) - 40} more" if len(problems) > 40 else "")
        + f"\n\nRegenerate it:\n{_REGENERATE}"
    )
