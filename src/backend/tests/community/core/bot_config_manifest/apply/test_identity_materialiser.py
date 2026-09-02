"""Tests for the ``identity`` materialiser (W5).

What these pin, by the work item's acceptance criteria:

- entries materialise through ``IdentityService``'s write path, with the
  engine's legal set re-asked at apply time rather than trusted from the PUT;
- `source` and inline `content` both work, and inline carries no fetch fields;
- the area is the identity file set **minus the reserved names**, and both
  halves of that subtraction are pinned (never written, never removed);
- a fetch failure aborts the whole category before the first write —
  §3.2's all-or-nothing, by construction because fetch belongs to ``resolve``;
- convergence: re-applying the same document performs no write at all.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetcher,
)
from agentclaw.community.core.bot_config_manifest.apply.materialisers.identity import (
    IdentityMaterialiser,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
)

from ._fakes import (
    FakeCredentials,
    FakeGuardedFetcher,
    FakeIdentityService,
    FakeManifestContent,
    fetched_object,
    identity_rig,
    make_context,
    SOUL_BODY,
    SOUL_URL,
)


def _run(coro):
    """Materialisers are async; each call gets its own loop."""
    return asyncio.run(coro)


def _ctx(**kwargs):
    # openclaw: the wide legal set — claude_code's is a single file and would
    # turn every RULES/SOUL test into a legality test.
    kwargs.setdefault("engine_type", "openclaw")
    kwargs.setdefault("owner_id", "u_owner")
    return make_context(**kwargs)


async def _apply(materialiser, ctx, entries):
    """resolve → plan → write, the engine's three calls in order."""
    resolved = await materialiser.resolve(ctx, entries)
    if not resolved.ok:
        return resolved, None, None
    plan = await materialiser.plan(ctx, resolved.intents)
    written = await materialiser.write(ctx, plan)
    return resolved, plan, written


DECLARED = {"type": "SOUL.md", "source": SOUL_URL}


# ── resolve: entries, both source forms ─────────────────────────────────────


def test_an_inline_content_entry_materialises():
    materialiser, identity, _, _ = identity_rig()
    result, plan, written = _run(
        _apply(materialiser, _ctx(), [{"type": "RULES.md", "content": "# rules"}])
    )
    assert result.ok
    assert [e.outcome.value for e in written] == ["created"]
    assert identity.writes == [
        {
            "file_type": "RULES.md",
            "content": "# rules",
            "operator": "u_actor",
            "entity_type": "staff",
            "entity_id": "u_owner",
            "bot_id": "b_1",
        }
    ]


def test_a_sourced_entry_fetches_then_writes():
    materialiser, identity, fetcher, _ = identity_rig()
    result, plan, written = _run(_apply(materialiser, _ctx(), [DECLARED]))
    assert result.ok
    assert [e.outcome.value for e in written] == ["created"]
    assert fetcher.requests[0].url == SOUL_URL
    assert identity.writes[0]["file_type"] == "SOUL.md"
    assert identity.writes[0]["content"] == SOUL_BODY.decode("utf-8")


def test_placeholder_substitution_reaches_the_identity_fetch():
    materialiser, identity, fetcher, _ = identity_rig()
    substituted = "https://content.example/identity/dev/soul.md"
    fetcher.responses[substituted] = fetcher.responses.pop(SOUL_URL)

    result, _, _ = _run(
        _apply(
            materialiser,
            _ctx(),
            [
                {
                    "type": "SOUL.md",
                    "source": "https://content.example/identity/${BOT_ENV}/soul.md",
                }
            ],
        )
    )
    assert result.ok
    assert fetcher.requests[0].url == substituted


def test_a_non_utf8_identity_source_fails_the_entry():
    materialiser, identity, fetcher, _ = identity_rig()
    binary = bytes([0xFF, 0xFE, 0x00, 0x01])
    fetcher.responses[SOUL_URL] = fetched_object(binary, url=SOUL_URL)

    resolved = _run(materialiser.resolve(_ctx(), [DECLARED]))
    assert not resolved.ok
    assert "UTF-8" in resolved.failures[0].reason
    assert identity.writes == []


# ── resolve: legality re-asked, reserved names, duplicates ──────────────────


def test_the_engines_legal_set_is_reasked_at_apply_time():
    # A bot's engine can change after the document was accepted; applying a
    # RULES.md to a claude_code bot must fail the entry, not silently skip.
    materialiser, identity, _, _ = identity_rig()
    resolved = _run(
        materialiser.resolve(_ctx(engine_type="claude_code"), [DECLARED])
    )
    assert not resolved.ok
    assert "SOUL.md" in resolved.failures[0].reason
    assert identity.writes == []


def test_a_reserved_name_is_refused_even_though_the_validator_also_refuses_it():
    # The belt exists because the engines' legal sets *contain* both reserved
    # files: a document that reaches a materialiser without the validator in
    # its history (a hand-built apply in W8) must still be refused — the
    # guarantee "never written, never removed" cannot depend on another layer
    # remembering to check.
    materialiser, identity, _, _ = identity_rig()
    resolved = _run(
        materialiser.resolve(
            _ctx(),
            [
                {"type": "MEMORY.md", "content": "x"},
                {"type": "IDENTITY.md", "content": "y"},
            ],
        )
    )
    assert not resolved.ok
    assert {f.identity for f in resolved.failures} == {"MEMORY.md", "IDENTITY.md"}
    assert identity.writes == []


def test_the_same_file_type_declared_twice_is_refused():
    materialiser, _, _, _ = identity_rig()
    resolved = _run(
        materialiser.resolve(
            _ctx(),
            [
                {"type": "RULES.md", "content": "a"},
                {"type": "RULES.md", "content": "b"},
            ],
        )
    )
    assert not resolved.ok
    assert resolved.failures[0].identity == "RULES.md"
    assert "declared more than once" in resolved.failures[0].reason


def test_an_entry_without_either_source_form_is_refused():
    materialiser, _, _, _ = identity_rig()
    resolved = _run(materialiser.resolve(_ctx(), [{"type": "RULES.md"}]))
    assert not resolved.ok
    assert "source" in resolved.failures[0].reason


# ── all-or-nothing: fetch failures abort before the first write ────────────


def test_one_failed_fetch_aborts_the_whole_category_no_writes():
    identity = FakeIdentityService()
    ok_url = "https://content.example/identity/rules.md"
    failing = FakeGuardedFetcher(
        responses={ok_url: fetched_object(b"# rules", url=ok_url)},
        failures={SOUL_URL: FetchFailedError("source answered 404")},
    )
    materialiser = IdentityMaterialiser(
        identity, EntryFetcher(failing, FakeManifestContent(), FakeCredentials())
    )

    resolved = _run(
        materialiser.resolve(
            _ctx(),
            [
                {"type": "RULES.md", "source": ok_url},
                {"type": "SOUL.md", "source": SOUL_URL},
            ],
        )
    )
    assert not resolved.ok
    assert "source answered 404" in resolved.failures[0].reason
    # Refused in resolve ⇒ no plan, no write — the all-or-nothing property
    # holds by construction, not by a mid-write undo.
    assert identity.writes == []
    assert identity.listed == 0


def test_keep_last_reuses_the_platform_copy_when_the_source_is_down():
    identity = FakeIdentityService()
    content = FakeManifestContent()
    # A prior apply fetched and filed the bytes; since then, the source died.
    content.store(
        fetched_object(SOUL_BODY, url=SOUL_URL, content_type="text/markdown"),
        scope=None,
        source_url=SOUL_URL,
    )
    failing = FakeGuardedFetcher(
        failures={SOUL_URL: FetchFailedError("source transport failed")}
    )
    materialiser = IdentityMaterialiser(
        identity, EntryFetcher(failing, content, FakeCredentials())
    )

    result, plan, written = _run(
        _apply(
            materialiser,
            _ctx(),
            [
                {
                    "type": "SOUL.md",
                    "source": SOUL_URL,
                    "on_fetch_failure": "keep_last",
                },
                {"type": "RULES.md", "content": "# rules"},
            ],
        )
    )
    assert result.ok
    assert sorted(w["file_type"] for w in identity.writes) == ["RULES.md", "SOUL.md"]
    assert identity.files["SOUL.md"] == SOUL_BODY.decode("utf-8")


def test_a_declared_digest_that_the_source_no_longer_matches_fails():
    materialiser, identity, fetcher, _ = identity_rig()
    other = "sha256:" + "1" * 64
    resolved = _run(
        materialiser.resolve(
            _ctx(), [{"type": "SOUL.md", "source": SOUL_URL, "digest": other}]
        )
    )
    assert not resolved.ok  # the pin disagrees with what is actually served
    assert "digest mismatch" in resolved.failures[0].reason
    assert identity.writes == []


# ── plan/write: the area, the removals, the reserved subtraction ───────────


def test_the_second_apply_of_an_unchanged_document_writes_nothing():
    materialiser, identity, _, _ = identity_rig()
    manifest_entries = [DECLARED, {"type": "RULES.md", "content": "# rules"}]

    _run(_apply(materialiser, _ctx(), manifest_entries))
    assert len(identity.writes) == 2

    _, _, second = _run(_apply(materialiser, _ctx(), manifest_entries))
    assert [e.outcome.value for e in second] == ["unchanged", "unchanged"]
    assert len(identity.writes) == 2  # zero further writes — convergence


def test_declaring_one_file_removes_the_others_but_not_the_reserved():
    materialiser, identity, _, _ = identity_rig(
        files={"RULES.md": "old", "SOUL.md": "old", "MEMORY.md": "engine state"}
    )
    _, plan, written = _run(
        _apply(materialiser, _ctx(), [{"type": "RULES.md", "content": "new"}])
    )
    assert plan.removals == ("SOUL.md",)
    # The removal is an empty write — the domain's absent≡empty contract.
    assert [w["file_type"] for w in identity.writes] == ["RULES.md", "SOUL.md"]
    assert identity.writes[1]["content"] == ""
    # MEMORY.md is engine state: untouched by the write AND absent from the
    # removals — the reserved subtraction's removal half.
    assert identity.write_count(file_type="MEMORY.md") == 0


def test_declared_empty_empties_the_area_but_not_the_reserved_names():
    materialiser, identity, _, _ = identity_rig(
        files={"RULES.md": "a", "SOUL.md": "b", "IDENTITY.md": "engine"}
    )
    _, plan, written = _run(_apply(materialiser, _ctx(), []))
    assert plan.removals == ("RULES.md", "SOUL.md")
    assert [w["file_type"] for w in identity.writes] == ["RULES.md", "SOUL.md"]
    assert identity.write_count(file_type="IDENTITY.md") == 0
    assert identity.files["IDENTITY.md"] == "engine"


def test_a_changed_file_writes_the_new_body_as_an_update():
    materialiser, identity, _, _ = identity_rig(files={"RULES.md": "old"})
    _, _, written = _run(
        _apply(materialiser, _ctx(), [{"type": "RULES.md", "content": "new"}])
    )
    assert [e.outcome.value for e in written] == ["updated"]
    assert identity.writes[0]["content"] == "new"


# ── structural: the module's reach ─────────────────────────────────────────


def test_the_identity_materialiser_cannot_reach_restart_or_device_internals():
    """AST import guard, mcp-precedent shape: this module writes through one
    service and must not grow a side door into lifecycle or device plumbing —
    the tempting bug is "make it take effect now" by restarting the bot."""
    import agentclaw.community.core.bot_config_manifest.apply.materialisers.identity
    import agentclaw.community

    module_file = Path(
        agentclaw.community.core.bot_config_manifest.apply.materialisers.identity.__file__
    )
    tree = ast.parse(module_file.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {
        "agentclaw.community.core.bot.service",
        "agentclaw.community.core.services.bot_publish_service",
        "agentclaw.community.core.bot_service",
    }
    assert not (imported & forbidden), sorted(imported & forbidden)
    for module in imported:
        assert "restart" not in module, module
        assert "device" not in module, module


def test_an_omitted_on_fetch_failure_defaults_to_keep_last():
    """The schema doc's default is ``keep_last`` (§2): an entry that omits
    the field must behave like one that wrote it — the minimal, conforming
    declaration reuses the platform's own copy when the source is down."""
    identity = FakeIdentityService()
    content = FakeManifestContent()
    content.store(
        fetched_object(SOUL_BODY, url=SOUL_URL, content_type="text/markdown"),
        scope=None,
        source_url=SOUL_URL,
    )
    failing = FakeGuardedFetcher(
        failures={SOUL_URL: FetchFailedError("source transport failed")}
    )
    materialiser = IdentityMaterialiser(
        identity, EntryFetcher(failing, content, FakeCredentials())
    )

    result, _, written = _run(
        _apply(materialiser, _ctx(), [DECLARED])  # no on_fetch_failure key
    )
    assert result.ok
    assert identity.files["SOUL.md"] == SOUL_BODY.decode("utf-8")


def test_the_receipts_link_the_apply_and_the_entry():
    """The linkage the W11 columns exist for: a fetch filed from an apply
    carries the apply key and the entry's identity, so "what did apply X
    fetch" and "what was fetched for this entry" are reads, not guesses."""
    materialiser, identity, fetcher, content = identity_rig()
    ctx = _ctx(apply_id="apply-4")
    _run(_apply(materialiser, ctx, [DECLARED]))
    call = content.store_calls[0]
    assert call["apply_id"] == "apply-4"
    assert call["category"] == "identity"
    assert call["entry_identity"] == "SOUL.md"
    # A context with no apply id (a dry run's shape) files NULL linkage.
    _run(_apply(materialiser, _ctx(), [DECLARED]))
    assert all(call["apply_id"] is not None for call in content.store_calls[:1])
    assert content.store_calls[-1]["apply_id"] is None
