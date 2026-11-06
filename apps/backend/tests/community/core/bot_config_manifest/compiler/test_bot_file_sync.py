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
    session, _, _ = rig(sources={"from_bot": {"protocol": "bot", "bot_id": "src_1"}})
    declared = compile_bot_file_sync(
        session,
        [{"path": "workspace/", "from": "from_bot", "excludes": ["secrets/", "large.bin"]}],
    )
    assert declared is not None
    (entry,) = declared.entries
    assert entry == DesiredBotFileSync(
        source_bot_id="src_1",
        path="workspace/",
        excludes=("secrets/", "large.bin"),
    )
    DesiredState(bot_file_sync=declared)  # the model accepts what the compiler emits


def test_a_mixed_list_lands_only_the_bot_source_in_this_declaration() -> None:
    # A resources list with both a stored-content entry and a bot-source entry
    # walks both compilers; each owns only its own entry.
    session, _, _ = rig(sources={"from_bot": {"protocol": "bot", "bot_id": "s"}})
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

