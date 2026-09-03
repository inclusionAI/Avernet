"""``CliToolStore`` — the platform's copy of a bot's tool bytes (W9)."""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.cli_tools import (
    BOT_DATA_STORE,
    CliToolScope,
    CliToolStore,
    CliToolStoreError,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.store import (
    CLI_NS,
    ENGINE_LAYOUT_SEGMENT,
    MAX_NAME_LENGTH,
    checked_name,
)

from ._fakes import FakeCopyingObjectStorage, FakeObjectStorage

_SCOPE = CliToolScope(entity_type="staff", entity_id="u1", bot_id="bot7")
_BASE = "teclaw/dev/bolt_data"
_LIVE = f"{_BASE}/staff_u1/bot7_cli"
_BYTES = b"\x7fELF-ish payload"


def _store(oss=None):
    oss = oss or FakeObjectStorage()
    return CliToolStore(object_storage=oss, store_base=lambda: _BASE), oss


# ── keys ──────────────────────────────────────────────────────────────────


def test_put_writes_the_live_key_and_returns_what_the_row_records() -> None:
    store, oss = _store()
    stored = store.put(_SCOPE, name="mycli", data=_BYTES)
    assert stored.store_key == f"{_LIVE}/mycli"
    assert stored.ref_path == "staff_u1/bot7_cli/mycli"
    assert stored.store == BOT_DATA_STORE
    assert oss.objects[stored.store_key] == _BYTES


def test_the_live_prefix_is_not_a_publish_stage_prefix() -> None:
    """``_cli`` versus ``_{publish_id}_{stage}``: they can never collide, so a
    live tool is never mistaken for a snapshot of one."""
    store, _ = _store()
    live = store.store_key(_SCOPE, "mycli")
    staged = store.stage_store_key(
        _SCOPE, name="mycli", publish_id=9, stage="verify"
    )
    assert live != staged
    assert not staged.startswith(live.rsplit("/", 1)[0] + "/")


def test_the_staged_key_sits_in_the_layout_promotion_already_builds() -> None:
    store, _ = _store()
    key = store.stage_store_key(_SCOPE, name="mycli", publish_id=9, stage="verify")
    assert key == f"{_BASE}/staff_u1/bot7_9_verify/{ENGINE_LAYOUT_SEGMENT}/{CLI_NS}/mycli"


def test_the_ref_path_is_the_key_minus_the_store_base() -> None:
    """What a ``cliToolRef`` carries: the engine resolves it against the
    ``bot-data`` store's base, so the base must not be inside it."""
    store, _ = _store()
    stored = store.put(_SCOPE, name="mycli", data=_BYTES)
    assert stored.store_key == f"{_BASE}/{stored.ref_path}"
    assert _BASE not in stored.ref_path


def test_the_base_is_read_per_call_not_bound_once() -> None:
    """The thunk is the point: the base depends on the deployment env."""
    base = ["teclaw/dev/bolt_data"]
    store = CliToolStore(object_storage=FakeObjectStorage(), store_base=lambda: base[0])
    first = store.store_key(_SCOPE, "mycli")
    base[0] = "teclaw/prod/bolt_data"
    assert store.store_key(_SCOPE, "mycli") != first


def test_a_trailing_slash_on_the_base_does_not_double_up() -> None:
    store = CliToolStore(
        object_storage=FakeObjectStorage(), store_base=lambda: f"{_BASE}/"
    )
    assert store.store_key(_SCOPE, "mycli") == f"{_LIVE}/mycli"


# ── the name is one key segment ───────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["../escape", "a/b", "sub/dir/cli", ".hidden", "", "-leading", "a b", "a\\b"],
)
def test_a_name_that_is_not_one_key_segment_is_refused(name: str) -> None:
    """The store will not build a key it cannot vouch for: a name is the one
    field that promises not to be a path."""
    store, oss = _store()
    with pytest.raises(ValueError):
        store.put(_SCOPE, name=name, data=_BYTES)
    assert oss.puts == []


@pytest.mark.parametrize("name", ["mycli", "my-cli", "my_cli", "my.cli", "cli2", "9lives"])
def test_an_ordinary_command_name_is_accepted(name: str) -> None:
    assert checked_name(name) == name


def test_a_name_longer_than_the_column_is_refused() -> None:
    """The prefixes are bounded and this is the one segment a caller chooses,
    so it is also what keeps a key inside the object store's key cap."""
    store, oss = _store()
    with pytest.raises(ValueError) as excinfo:
        store.put(_SCOPE, name="a" * (MAX_NAME_LENGTH + 1), data=_BYTES)
    assert str(MAX_NAME_LENGTH) in str(excinfo.value)
    assert oss.puts == []
    assert checked_name("a" * MAX_NAME_LENGTH)


def test_the_stage_key_refuses_the_same_names() -> None:
    store, _ = _store()
    with pytest.raises(ValueError):
        store.stage_store_key(_SCOPE, name="../escape", publish_id=9, stage="verify")


# ── failures raise ────────────────────────────────────────────────────────


def test_a_put_that_did_not_land_raises_rather_than_returning_a_key() -> None:
    """A row recorded for bytes that are not there is the failure this
    prevents: the artifact would reference an object nothing wrote."""
    store, _ = _store(FakeObjectStorage(fail_puts=True))
    with pytest.raises(CliToolStoreError) as excinfo:
        store.put(_SCOPE, name="mycli", data=_BYTES)
    assert "mycli" in str(excinfo.value)


def test_a_delete_that_did_not_land_raises_with_the_object_still_there() -> None:
    store, oss = _store(FakeObjectStorage(fail_deletes=True))
    stored = store.put(_SCOPE, name="mycli", data=_BYTES)
    with pytest.raises(CliToolStoreError):
        store.delete(key=stored.store_key)
    assert stored.store_key in oss.objects


def test_delete_addresses_the_recorded_key_not_a_recomputed_one() -> None:
    """A tool written under an earlier store base is still removable."""
    store, oss = _store()
    oss.objects["teclaw/OLD/bolt_data/staff_u1/bot7_cli/mycli"] = _BYTES
    store.delete(key="teclaw/OLD/bolt_data/staff_u1/bot7_cli/mycli")
    assert oss.deletes == ["teclaw/OLD/bolt_data/staff_u1/bot7_cli/mycli"]
    assert oss.objects == {}


def test_delete_issues_the_call_without_a_prior_existence_check() -> None:
    """A pre-check would fold a transient listing failure into "not there"."""
    store, oss = _store()
    store.delete(key=f"{_LIVE}/never-written")
    assert oss.deletes == [f"{_LIVE}/never-written"]
    assert oss.reads == []


# ── staging a copy ────────────────────────────────────────────────────────


def test_copy_to_stage_uses_the_server_side_copy_when_the_store_has_one() -> None:
    """The bytes must not travel through the backend: a tool can be 200 MiB
    and a promotion copies every one of a bot's."""
    oss = FakeCopyingObjectStorage()
    store, _ = _store(oss)
    source = store.put(_SCOPE, name="mycli", data=_BYTES)
    staged = store.copy_to_stage(
        _SCOPE, name="mycli", source_key=source.store_key, publish_id=9, stage="verify"
    )
    assert oss.copies == [(source.store_key, staged.store_key)]
    assert oss.reads == []
    assert oss.objects[staged.store_key] == _BYTES


def test_copy_to_stage_falls_back_to_read_and_write_without_the_capability() -> None:
    """An overlay that has not shipped ``copy_object`` must still promote."""
    oss = FakeObjectStorage()
    store, _ = _store(oss)
    source = store.put(_SCOPE, name="mycli", data=_BYTES)
    staged = store.copy_to_stage(
        _SCOPE, name="mycli", source_key=source.store_key, publish_id=9, stage="verify"
    )
    assert oss.reads == [source.store_key]
    assert oss.objects[staged.store_key] == _BYTES


def test_copy_to_stage_copies_from_the_recorded_key() -> None:
    """Promotion passes the row's ``oss_key``, so an object written under an
    earlier store base still promotes rather than staging nothing."""
    oss = FakeCopyingObjectStorage()
    store, _ = _store(oss)
    old_key = "teclaw/OLD/bolt_data/staff_u1/bot7_cli/mycli"
    oss.objects[old_key] = _BYTES
    staged = store.copy_to_stage(
        _SCOPE, name="mycli", source_key=old_key, publish_id=9, stage="verify"
    )
    assert oss.copies == [(old_key, staged.store_key)]
    assert oss.objects[staged.store_key] == _BYTES


def test_a_failed_server_side_copy_raises() -> None:
    oss = FakeCopyingObjectStorage(fail_copies=True)
    store, _ = _store(oss)
    source = store.put(_SCOPE, name="mycli", data=_BYTES)
    with pytest.raises(CliToolStoreError):
        store.copy_to_stage(
            _SCOPE, name="mycli", source_key=source.store_key,
            publish_id=9, stage="verify",
        )


def test_an_unreadable_source_raises_rather_than_staging_an_empty_tool() -> None:
    """The read path folds absent and unreadable into ``None``; staging either
    as an empty object would put a broken tool in a published artifact."""
    store, oss = _store()
    with pytest.raises(CliToolStoreError) as excinfo:
        store.copy_to_stage(
            _SCOPE, name="mycli", source_key=f"{_LIVE}/mycli",
            publish_id=9, stage="verify",
        )
    assert "nothing staged" in str(excinfo.value)
    assert oss.puts == []


def test_a_failed_staged_put_raises_on_the_fallback_path() -> None:
    oss = FakeObjectStorage()
    store, _ = _store(oss)
    source = store.put(_SCOPE, name="mycli", data=_BYTES)
    oss.fail_puts = True
    with pytest.raises(CliToolStoreError):
        store.copy_to_stage(
            _SCOPE, name="mycli", source_key=source.store_key,
            publish_id=9, stage="verify",
        )


def test_draft_and_verify_snapshots_do_not_share_an_object() -> None:
    """Republishing a draft must not change what a published bot runs."""
    oss = FakeCopyingObjectStorage()
    store, _ = _store(oss)
    source = store.put(_SCOPE, name="mycli", data=_BYTES)
    draft = store.copy_to_stage(
        _SCOPE, name="mycli", source_key=source.store_key, publish_id=9, stage="draft"
    )
    verify = store.copy_to_stage(
        _SCOPE, name="mycli", source_key=source.store_key, publish_id=9, stage="verify"
    )
    assert draft.store_key != verify.store_key
    assert {draft.store_key, verify.store_key} <= set(oss.objects)


def test_two_bots_never_share_a_key() -> None:
    store, _ = _store()
    other = CliToolScope(entity_type="staff", entity_id="u1", bot_id="bot8")
    assert store.store_key(_SCOPE, "mycli") != store.store_key(other, "mycli")


def test_the_context_addresses_the_store_by_its_own_coordinates() -> None:
    """One identity built at the top of an operation, handed down unchanged —
    so no layer re-derives which bot's prefix it is writing into."""
    from agentclaw.community.core.bot_config_manifest.cli_tools import CliToolContext

    ctx = CliToolContext(
        bot_id="bot7", owner_id="u1", actor_id="u2", entity_id="u1",
        env="dev", engine_type="openclaw",
    )
    store, _ = _store()
    assert ctx.scope == _SCOPE
    assert store.store_key(ctx.scope, "mycli") == f"{_LIVE}/mycli"


def test_two_entities_never_share_a_key() -> None:
    store, _ = _store()
    other = CliToolScope(entity_type="team", entity_id="u1", bot_id="bot7")
    assert store.store_key(_SCOPE, "mycli") != store.store_key(other, "mycli")
