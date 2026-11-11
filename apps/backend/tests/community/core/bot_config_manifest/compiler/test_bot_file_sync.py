"""``compile_bot_file_sync``: ``protocol: bot`` resources entries → ``DesiredBotFileSync``.

A bot-source entry sits under ``manifest.resources`` but reclassifies to the
``BOT_FILE_SYNC`` section: this compiler collects it, ``compile_resources``
skips it. The entry's ``path`` (a literal the copy serializer writes) and
``excludes`` ride through; ``source_bot_id`` comes from the named source.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    DesiredBotFileSync,
    DesiredState,
)
from agentclaw.community.core.bot_config_manifest.compiler.entries.bot_file_sync import (
    compile_bot_file_sync,
)
from tests.community.core.bot_config_manifest.compiler._rig import rig


def test_returns_none_when_no_resources_declared() -> None:
    session, _, _ = rig()
    assert compile_bot_file_sync(session, None) is None
    assert compile_bot_file_sync(session, []) is None


def test_returns_none_when_entries_are_stored_content_only() -> None:
    # A git/oss/content resources list has no bot-source entry → section undeclared.
    session, _, _ = rig()
    assert compile_bot_file_sync(
        session,
        [{"path": "data/faq.csv", "content": "hello\n"}],
    ) is None


def test_collects_a_bot_source_entry_with_path_and_excludes() -> None:
    session, _, _ = rig(sources={"from_bot": {"protocol": "bot", "bot_id": "src_1", "owner_id": "u1"}})
    declared = compile_bot_file_sync(
        session,
        [{"path": "workspace/", "from": "from_bot", "excludes": ["secrets/", "large.bin"]}],
    )
    assert declared is not None
    (entry,) = declared.entries
    assert entry == DesiredBotFileSync(
        source_bot_id="src_1",
        source_owner_id="u1",
        path="workspace/",
        excludes=("secrets/", "large.bin"),
    )
    DesiredState(bot_file_sync=declared)  # the model accepts what the compiler emits


def test_a_mixed_list_lands_only_the_bot_source_in_this_declaration() -> None:
    # A resources list with both a stored-content entry and a bot-source entry
    # walks both compilers; each owns only its own entry.
    session, _, _ = rig(sources={"from_bot": {"protocol": "bot", "bot_id": "s", "owner_id": "u1"}})
    declared = compile_bot_file_sync(
        session,
        [
            {"path": "data/faq.csv", "content": "x\n"},
            {"path": "workspace/", "from": "from_bot"},
        ],
    )
    assert declared is not None
    (entry,) = declared.entries
    assert entry.source_bot_id == "s"
    assert entry.path == "workspace/"


def test_a_non_mapping_entry_is_reported_as_a_failure_skipped() -> None:
    # A malformed entry never reaches DesiredBotFileSync; the compiler carries
    # the misuse as an EntryFailure rather than fetching from it.
    session, _, _ = rig(sources={"from_bot": {"protocol": "bot", "bot_id": "s", "owner_id": "u1"}})
    declared = compile_bot_file_sync(session, ["not-a-mapping"])
    assert declared is not None
    assert declared.entries == ()
    (failure,) = declared.failures
    assert "a resources entry must be a mapping" in failure.reason


def test_a_bot_source_entry_without_a_path_is_reported() -> None:
    session, _, _ = rig(sources={"from_bot": {"protocol": "bot", "bot_id": "s", "owner_id": "u1"}})
    declared = compile_bot_file_sync(session, [{"from": "from_bot"}])
    assert declared is not None
    assert declared.entries == ()
    (failure,) = declared.failures
    assert "a resources entry must declare a 'path'" in failure.reason


def test_a_bot_source_entry_whose_path_refuses_locally_is_reported() -> None:
    # ``resource_path_refusal`` rejects an absolute/escape path; the compiler
    # surfaces the refusal as an entry failure on that path.
    session, _, _ = rig(sources={"from_bot": {"protocol": "bot", "bot_id": "s", "owner_id": "u1"}})
    declared = compile_bot_file_sync(session, [{"path": "/abs/path", "from": "from_bot"}])
    assert declared is not None
    assert declared.entries == ()
    assert declared.failures and declared.failures[0].identity == "/abs/path"


def test_a_bot_source_entry_referencing_an_undeclared_name_is_skipped_not_failed() -> None:
    # ``from: absent`` is not under ``sources:``, so ``declared_protocol`` reads
    # it as ``None`` and the entry is skipped whole — the undeclared-name
    # refusal belongs to the resources validator / schema, not this compiler,
    # which only reclassifies entries it can already see are bot-sourced.
    session, _, _ = rig(sources={"from_bot": {"protocol": "bot", "bot_id": "s", "owner_id": "u1"}})
    assert compile_bot_file_sync(
        session, [{"path": "workspace/", "from": "absent_source"}]
    ) is None


def test_an_inline_bot_source_carries_its_bot_id_straight_through() -> None:
    # An inline ``source: {protocol: bot, bot_id: …}`` parses the same way as a
    # named one; the compiler reads the bot_id off the parsed decl.
    session, _, _ = rig()
    declared = compile_bot_file_sync(
        session,
        [{"path": "workspace/", "source": {"protocol": "bot", "bot_id": "inline_1", "owner_id": "u1"}}],
    )
    assert declared is not None
    (entry,) = declared.entries
    assert entry.source_bot_id == "inline_1"

