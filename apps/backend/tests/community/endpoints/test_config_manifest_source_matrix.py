"""Matrix conformance: the surface and the table agree, cell for cell.

Acceptance criterion 8, and the guard against the whole class of drift that
produced defects D1–D5. Every one of them was the same failure: the answer to
"does this category work with this source" lived in more than one place, so the
places disagreed and shipped anyway.

So this file does not test rules. It drives a **real `PUT`** through the
assembled public app for every cell of the cartesian product and asserts the
observed outcome equals :data:`MATRIX` — and, where the cell is closed, that the
message the caller reads is the cell's own string rather than a paraphrase. A
future change that opens a combination in code without opening it in the table
(or the reverse) fails here.
"""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
)
from agentclaw.community.core.bot_config_manifest.support_matrix import (
    MATRIX,
    SourceKind,
    refusal_for,
    supports,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)

_OWNER = "matrix-owner"
_BOT_ID = "matrix-bot"
_KEY = "config-manifest-framework-signing-key-at-least-32-bytes"
_DIGEST = "sha256:" + "0" * 64
_REPO = "https://code.example.com/team/content.git"
#: An object store address is two structured fields, not a URL — the source
#: names the bucket and the key, the credential names the endpoint.
_BUCKET = "cdn-assets"
_OBJECT_KEY = "pkg.zip"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": _OWNER, "username": f"{_OWNER}@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_PATH = f"/openapi/v1/bots/{_BOT_ID}/config-manifest"


def _seed(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    world.get(BotRepository).insert(
        {
            "bot_id": _BOT_ID,
            "bot_name": "Matrix Bot",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "status": "ACTIVE",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
    )


# ── documents, one per cell ─────────────────────────────────────────────────
#
# Each is the *most likely to be accepted* spelling for its cell: every field
# the category needs, and nothing the cell's own verdict is not about. A
# document that failed for an unrelated reason would make a closed cell look
# closed for the wrong reason, and — worse — an open one look broken.

#: The categories whose entries have a source axis at all. ``mcp`` and
#: ``engine_config`` do not, and are covered separately below: their cells are
#: closed for a reason the *entry vocabulary* enforces before a source is ever
#: read, so a document trying to give them one is refused earlier and would
#: never carry the cell's message.
_FETCHING = (
    ManifestCategory.IDENTITY,
    ManifestCategory.RESOURCES,
    ManifestCategory.SKILLS,
    ManifestCategory.CLI_TOOLS,
)

_ENTITY_FIELDS = {
    ManifestCategory.IDENTITY: "      type: SOUL.md\n",
    ManifestCategory.RESOURCES: "      path: data/a.md\n",
    ManifestCategory.SKILLS: "      name: qc\n",
    ManifestCategory.CLI_TOOLS: "      name: rg\n",
}


def _document(category: ManifestCategory, kind: SourceKind) -> str:
    """A minimal, otherwise-valid document exercising exactly one cell."""
    entry = _ENTITY_FIELDS[category]
    if kind is SourceKind.CONTENT:
        entry += "      content: |\n        # body\n"
        return f"schema_version: 1\nmanifest:\n  {category.value}:\n    - {entry[6:]}"

    if kind is SourceKind.GIT:
        source = (
            "sources:\n"
            "  src:\n"
            "    protocol: git\n"
            f"    url: {_REPO}\n"
            "    ref: v1.2.0\n"
            "    subpath: pkg\n"
        )
    else:
        source = (
            "sources:\n"
            "  src:\n"
            "    protocol: oss\n"
            f"    bucket: {_BUCKET}\n"
            f"    key: {_OBJECT_KEY}\n"
            "    auth: oss-cred\n"
        )
    entry += "      from: src\n"
    # ``oss`` needs the pin wherever the platform distributes executable
    # content, and a skills entry needs to say how the package travels. Both
    # are the cell's own neighbouring rules, not its verdict — supplying them
    # is what keeps this test about the cell.
    if kind is SourceKind.OSS:
        if category in (ManifestCategory.SKILLS, ManifestCategory.CLI_TOOLS):
            entry += f'      digest: "{_DIGEST}"\n'
        if category is ManifestCategory.SKILLS:
            entry += "      unpack: zip\n"
    return (
        f"schema_version: 1\n{source}manifest:\n  {category.value}:\n"
        f"    - {entry[6:]}"
    )


def _put(client: TestClient, document: str):
    return client.put(
        _PATH, params=_QUERY, headers=_HEADERS, json={"document": document}
    )


@pytest.mark.parametrize(
    ("category", "kind"),
    [(c, k) for c in _FETCHING for k in SourceKind],
    ids=lambda v: v.value,
)
def test_the_put_surface_matches_the_matrix_cell(
    app_with_testing_modules, world, category, kind
):
    """Accepted ⇔ the cell says supported, and a refusal is the cell's words."""
    _seed(world)
    client = TestClient(app_with_testing_modules)
    response = _put(client, _document(category, kind))

    if supports(category, kind):
        assert response.status_code == 200, response.json()
        return

    assert response.status_code == 422, response.json()
    refusal = refusal_for(category, kind)
    assert refusal is not None
    violations = response.json()["data"]["violations"]
    assert any(
        v["code"] == refusal.code and v["message"] == refusal.reason
        for v in violations
    ), violations


@pytest.mark.parametrize(
    "category", [ManifestCategory.MCP, ManifestCategory.ENGINE_CONFIG]
)
def test_the_categories_with_no_source_axis_are_closed_in_every_cell(category):
    """Both are closed for all three protocols, and the table says why.

    They are checked here rather than through a ``PUT`` because their entry
    vocabulary refuses a source *field* before any source is read — an mcp
    entry takes only ``server_code``, and ``engine_config`` is not a list of
    entries at all. Driving a document would therefore assert the earlier
    refusal, not the cell. What matters for conformance is that the table and
    the vocabulary agree that the combination cannot be written, and the two
    tests below prove the vocabulary half.
    """
    for kind in SourceKind:
        assert not supports(category, kind)
        assert refusal_for(category, kind).reason


def test_an_mcp_entry_cannot_be_given_a_source(app_with_testing_modules, world):
    _seed(world)
    client = TestClient(app_with_testing_modules)
    response = _put(
        client,
        "schema_version: 1\n"
        "manifest:\n"
        "  mcp:\n"
        "    - server_code: filesystem\n"
        "      source:\n"
        "        protocol: oss\n"
        f"        bucket: {_BUCKET}\n"
        f"        key: {_OBJECT_KEY}\n"
        "        auth: oss-cred\n",
    )
    assert response.status_code == 422
    codes = {v["code"] for v in response.json()["data"]["violations"]}
    assert codes == {"unknown_field"}


def test_engine_config_is_refused_as_a_whole_category(
    app_with_testing_modules, world
):
    _seed(world)
    client = TestClient(app_with_testing_modules)
    response = _put(
        client,
        "schema_version: 1\nmanifest:\n  engine_config:\n    config: {}\n",
    )
    assert response.status_code == 422
    codes = {v["code"] for v in response.json()["data"]["violations"]}
    assert "unsupported_category" in codes


def test_the_capabilities_endpoint_publishes_the_same_table(
    app_with_testing_modules, world
):
    """The read path and the write path answer from one object.

    ``GET …/capabilities`` exists so a caller can decide what to write *before*
    writing it. If its cells could disagree with the validator's, it would be
    worse than absent — it would be authoritative and wrong.
    """
    _seed(world)
    client = TestClient(app_with_testing_modules)
    response = client.get(
        f"{_PATH}/capabilities", params=_QUERY, headers=_HEADERS
    )
    assert response.status_code == 200
    published = {
        (cell["category"], cell["protocol"]): cell
        for cell in response.json()["data"]["source_matrix"]
    }
    assert len(published) == len(MATRIX)
    for (category, kind), refusal in MATRIX.items():
        cell = published[(category.value, kind.value)]
        assert cell["supported"] is (refusal is None), cell
        if refusal is not None:
            assert cell["reason"] == refusal.reason


def test_the_old_source_spelling_is_refused_and_names_its_replacement(
    app_with_testing_modules, world
):
    """Acceptance criterion 7. No silent acceptance and no dual road: a stored
    document written the old way is refused, and the refusal is the shortest
    path to a working one — it names the two lines that replace it."""
    _seed(world)
    client = TestClient(app_with_testing_modules)
    response = _put(
        client,
        "schema_version: 1\n"
        "sources:\n"
        "  src:\n"
        f"    git: {_REPO}\n"
        "    ref: v1.2.0\n"
        "manifest:\n"
        "  identity:\n"
        "    - type: SOUL.md\n"
        "      from: src\n",
    )
    assert response.status_code == 422
    violations = response.json()["data"]["violations"]
    assert any(v["code"] == "missing_protocol" for v in violations), violations
    assert any(
        "'protocol: git'" in v["message"] and "'url: <url>'" in v["message"]
        for v in violations
    ), violations


def test_unpack_on_a_git_source_is_refused_at_put(
    app_with_testing_modules, world
):
    """Acceptance criterion 4: refused, naming the field and the protocol —
    never silently ignored, which is the failure mode the matrix exists to
    remove."""
    _seed(world)
    client = TestClient(app_with_testing_modules)
    for field, value in (("unpack", "zip"), ("strip_components", "1")):
        response = _put(
            client,
            "schema_version: 1\n"
            "sources:\n"
            "  src:\n"
            "    protocol: git\n"
            f"    url: {_REPO}\n"
            "    ref: v1.2.0\n"
            "manifest:\n"
            "  cli_tools:\n"
            "    - name: rg\n"
            "      from: src\n"
            "      subpath: bin/rg\n"
            f"      {field}: {value}\n",
        )
        assert response.status_code == 422, field
        violations = response.json()["data"]["violations"]
        assert any(
            v["code"] == "archive_field_on_source"
            and field in v["message"]
            and "git source" in v["message"]
            for v in violations
        ), violations


def test_cli_tools_over_a_named_git_source_needs_no_digest(
    app_with_testing_modules, world
):
    """Acceptance criterion 5 — defect D5, at the surface.

    The rule used to read the source *form*, so reaching a git source by name
    classified as ``named`` and was charged the object-store pin: a digest that
    Appendix C separately calls meaningless on git bytes, and that failed at
    apply anyway. Keyed on the protocol, inline and by-name are the same source.
    """
    _seed(world)
    client = TestClient(app_with_testing_modules)
    response = _put(
        client,
        "schema_version: 1\n"
        "sources:\n"
        "  tools:\n"
        "    protocol: git\n"
        "    url: https://code.example.com/team/tools.git\n"
        "    ref: v1.0.0\n"
        "manifest:\n"
        "  cli_tools:\n"
        "    - name: rg\n"
        "      from: tools\n"
        "      subpath: bin/rg\n",
    )
    assert response.status_code == 200, response.json()


def test_cli_tools_over_oss_still_requires_a_digest(
    app_with_testing_modules, world
):
    """The other half of the same rule: the pin is mandatory where it means
    something. Loosening git must not loosen the object store."""
    _seed(world)
    client = TestClient(app_with_testing_modules)
    response = _put(
        client,
        "schema_version: 1\n"
        "sources:\n"
        "  artifacts:\n"
        "    protocol: oss\n"
        f"    bucket: {_BUCKET}\n"
        f"    key: {_OBJECT_KEY}\n"
        "    auth: oss-cred\n"
        "manifest:\n"
        "  cli_tools:\n"
        "    - name: rg\n"
        "      from: artifacts\n",
    )
    assert response.status_code == 422
    codes = {v["code"] for v in response.json()["data"]["violations"]}
    assert "missing_digest" in codes


# ── the published example document (defect D2) ──────────────────────────────


def _example_document() -> str:
    """The manifest out of ``docs/bot-config-manifest/examples.zh-CN.md`` §1.2.

    Read from the file rather than copied here, which is the entire point: a
    copy would be a second document that agrees with the doc only until someone
    edits one of them. Defect D2 was exactly that gap — the published example
    returned 422, and nothing in the suite could notice.
    """
    import re
    from pathlib import Path

    doc = (
        Path(__file__).parents[3]
        / "docs"
        / "bot-config-manifest"
        / "examples.zh-CN.md"
    ).read_text(encoding="utf-8")
    block = re.search(r"### 1\.2 [^\n]*\n\n```yaml\n(.*?)```", doc, re.S)
    assert block, "examples.zh-CN.md §1.2 no longer carries a yaml block"
    # The doc elides digests for readability ("sha256:3e7a…"); the validator
    # rightly refuses a truncated one. Only the elision is filled in — every
    # other character is the document as published.
    return re.sub(r"sha256:[0-9a-f]*…", "sha256:" + "0" * 64, block.group(1))


def test_the_published_example_document_is_accepted(
    app_with_testing_modules, world
):
    """Defect D2, closed and kept closed.

    The example carried `resources` entries with `from:` that the surface
    refused, plus a caveat header that was stale in the other direction — it
    listed constructs as unavailable that had been open for two waves. A
    document a reader copies out of the manual has to work; this asserts it
    against the real validator on every run.
    """
    _seed(world)
    client = TestClient(app_with_testing_modules)
    response = _put(client, _example_document())

    # The document deliberately shows the *complete* v1 shape, which includes
    # ``engine_config`` — the one construct its own caveat header still names
    # as unopened, and the one this change leaves out of scope. So the exact
    # assertion is: that is the only thing wrong with it. Anything else in
    # these violations is the doc and the code disagreeing again.
    violations = (
        [] if response.status_code == 200
        else response.json().get("data", {}).get("violations", [])
    )
    unexpected = [v for v in violations if v["location"] != "manifest.engine_config"]
    assert not unexpected, "the published example is refused:\n" + "\n".join(
        f"  {v['location']}: {v['code']} — {v['message']}" for v in unexpected
    )

    # And with that one section removed — which is what a reader following the
    # caveat header would write today — it is accepted outright.
    import re

    without = re.sub(
        r"\n  engine_config:.*?\n(?=  \w)", "\n", _example_document(), flags=re.S
    )
    assert "engine_config" not in without
    assert _put(client, without).status_code == 200, _put(client, without).json()


def test_the_example_document_exercises_both_protocols(
    app_with_testing_modules, world
):
    """And it is worth accepting only if it still shows what it claims to.

    A document trimmed down to whatever passes would satisfy the test above
    while teaching nothing, so the shape is asserted too: both protocols, a
    resources entry over git in each of its two forms, and no leftover of the
    old spelling anywhere in the file.
    """
    from pathlib import Path

    document = _example_document()
    assert "protocol: git" in document
    assert "protocol: oss" in document
    # A file entry and a directory entry, both from the git source.
    assert "path: data/faq.csv" in document
    assert "path: data/kb/" in document

    whole_doc = (
        Path(__file__).parents[3]
        / "docs"
        / "bot-config-manifest"
        / "examples.zh-CN.md"
    ).read_text(encoding="utf-8")
    assert "\n    git: http" not in whole_doc, "old source spelling survives"
