"""Tests for ``DeviceCredentialsAdminsWriter``.

Real fakes only (no mocks — per repo convention). The writer is the shared seam
that seeds / syncs the ``ADMINS=`` line of a running container's
``/home/admin/.credentials``. For a service bot it must resolve the **online**
binding (``ext.binding.online`` via the publish record) and write via
``resolve_for_binding``; for bots without a publish record it falls back to
``resolve_for_bot`` (today's ARCA behavior). It read-modify-writes the existing
file (engine rejects ADMINS-only) and never creates a bare file.
"""
from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    CollaboratorRole,
)
from agentclaw.community.core.bot_collaborator.services.credentials_admins_writer import (
    _DEVICE_CREDENTIALS_PATH,
    DeviceCredentialsAdminsWriter,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    DeviceNotBoundError,
)
from agentclaw.community.core.devices.services.device_filesystem import (
    DeviceFileSystem,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeDeviceFileSystem:
    """In-memory dict-backed DeviceFileSystem. Records nothing but state."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self._files: dict[str, bytes] = dict(files or {})
        self.write_calls: list[tuple[str, bytes]] = []

    async def read_file(self, file_path: str, *, enforce_download_limit: bool = False) -> bytes | None:
        return self._files.get(file_path)

    async def write_file(self, file_path: str, content: bytes) -> None:
        self._files[file_path] = content
        self.write_calls.append((file_path, content))

    async def delete_file(self, file_path: str) -> bool:
        return self._files.pop(file_path, None) is not None

    async def delete_tree(self, dir_path: str) -> bool:
        return True

    async def list_dir(self, dir_path: str, *, recursive: bool = False) -> list[dict[str, Any]] | None:
        return []

    async def exists(self, path: str) -> bool:
        return path in self._files


class RaisingDeviceFileSystem(FakeDeviceFileSystem):
    """A fs whose write_file always raises — to test error propagation."""

    async def write_file(self, file_path: str, content: bytes) -> None:
        raise RuntimeError("write failed (fake)")


class FakeDispatcher:
    def __init__(self, fs: DeviceFileSystem) -> None:
        self._fs = fs
        self.dispatched: list[DeviceContext] = []

    def dispatch(self, ctx: DeviceContext) -> DeviceFileSystem:
        self.dispatched.append(ctx)
        return self._fs


class FakeResolver:
    """Records which entry (resolve_for_bot vs resolve_for_binding) was used."""

    def __init__(self, provider: str = "teclaw") -> None:
        self._provider = provider
        self.resolve_for_bot_calls: list[tuple[str, str]] = []
        self.resolve_for_binding_calls: list[tuple[int, str, str]] = []

    def resolve_for_bot(self, bot_id: str, user_id: str, *, device_uuid: str | None = None) -> DeviceContext:
        self.resolve_for_bot_calls.append((bot_id, user_id))
        return DeviceContext(
            provider=self._provider,  # type: ignore[arg-type]
            conn_info={},
            binding_id=0,
            bot_id=bot_id,
            user_id=user_id,
        )

    def resolve_for_binding(
        self, binding_id: int, operator_id: str, *, bot_id: str, device_uuid: str | None = None
    ) -> DeviceContext:
        self.resolve_for_binding_calls.append((binding_id, operator_id, bot_id))
        return DeviceContext(
            provider=self._provider,  # type: ignore[arg-type]
            conn_info={},
            binding_id=binding_id,
            bot_id=bot_id,
            user_id=operator_id,
        )


class FakeCollaboratorRepo:
    def __init__(self, admins: list[str]) -> None:
        self._admins = admins

    def list_by_bot(self, bot_id: str, owner_id: str, env: str, role: str | None = None) -> list[CollaboratorRecord]:
        return [
            CollaboratorRecord(
                id=i,
                bot_pk=1,
                bot_id=bot_id,
                owner_id=owner_id,
                user_id=uid,
                user_name=None,
                role=CollaboratorRole.ADMIN,
                operator_id=owner_id,
                env=env,
            )
            for i, uid in enumerate(self._admins)
        ]


class FakePublishRepo:
    def __init__(self, online_binding_id: int | None) -> None:
        self._online_binding_id = online_binding_id

    def get_latest_success_by_source_bot_id(self, source_bot_id: str, env: str) -> BotPublishRecord | None:
        if self._online_binding_id is None:
            return None
        return BotPublishRecord(
            id=1,
            source_bot_pk=1,
            source_bot_id=source_bot_id,
            publish_bot_id=f"{source_bot_id}pub1",
            name="n",
            description=None,
            owner_id="owner",
            owner_name=None,
            status=PublishStatus.SUCCESS,
            version=1,
            last_pub_id=0,
            env=env,
            ext={"binding": {"online": self._online_binding_id}},
            permission_owner="owner",
        )


def _make_writer(
    *,
    fs: DeviceFileSystem,
    admins: list[str] | None = None,
    online_binding_id: int | None = None,
    provider: str = "teclaw",
) -> tuple[DeviceCredentialsAdminsWriter, FakeResolver, FakeDispatcher]:
    resolver = FakeResolver(provider=provider)
    dispatcher = FakeDispatcher(fs)
    writer = DeviceCredentialsAdminsWriter(
        collaborator_repo=FakeCollaboratorRepo(admins=admins or []),
        bot_publish_repo=FakePublishRepo(online_binding_id=online_binding_id),
        resolver_provider=lambda: resolver,
        device_fs_dispatcher_provider=lambda: dispatcher,
    )
    return writer, resolver, dispatcher


def _credentials(token: str = "TOK", client_id: str = "CID", admins: str = "") -> bytes:
    return f"TOKEN={token}\nCLIENT_ID={client_id}\nADMINS={admins}\n".encode()


# ── seed_for_publish ─────────────────────────────────────────────────────────


def test_seed_for_publish_uses_resolve_for_binding_and_writes_admins():
    fs = FakeDeviceFileSystem({_DEVICE_CREDENTIALS_PATH: _credentials()})
    writer, resolver, dispatcher = _make_writer(fs=fs, admins=["u1", "u2"], online_binding_id=42)

    writer.seed_for_publish(binding_id=42, bot_id="bot-1", owner_id="owner-1")

    # online binding resolved via resolve_for_binding, NOT resolve_for_bot
    assert resolver.resolve_for_binding_calls == [(42, "owner-1", "bot-1")]
    assert resolver.resolve_for_bot_calls == []
    # only the ADMINS= line changed; TOKEN/CLIENT_ID preserved
    written = fs.write_calls[-1][1].decode()
    assert "TOKEN=TOK" in written
    assert "CLIENT_ID=CID" in written
    assert "ADMINS=u1,u2" in written


def test_seed_for_publish_empty_admins_clears_line_preserves_others():
    fs = FakeDeviceFileSystem({_DEVICE_CREDENTIALS_PATH: _credentials(admins="old")})
    writer, *_ = _make_writer(fs=fs, admins=[], online_binding_id=7)

    writer.seed_for_publish(binding_id=7, bot_id="bot-1", owner_id="o")

    written = fs.write_calls[-1][1].decode()
    assert "ADMINS=\n" in written
    assert "TOKEN=TOK" in written
    assert "CLIENT_ID=CID" in written


def test_seed_for_publish_missing_credentials_skips_no_create():
    fs = FakeDeviceFileSystem({})  # no .credentials present
    writer, *_ = _make_writer(fs=fs, admins=["u1"], online_binding_id=7)

    writer.seed_for_publish(binding_id=7, bot_id="bot-1", owner_id="o")

    # engine rejects ADMINS-only → never create a bare file
    assert fs.write_calls == []
    assert _DEVICE_CREDENTIALS_PATH not in fs._files


def test_seed_for_publish_write_failure_propagates():
    fs = RaisingDeviceFileSystem({_DEVICE_CREDENTIALS_PATH: _credentials()})
    writer, *_ = _make_writer(fs=fs, admins=["u1"], online_binding_id=7)

    # propagates so the publish handler can Retry
    with pytest.raises(RuntimeError, match="write failed"):
        writer.seed_for_publish(binding_id=7, bot_id="bot-1", owner_id="o")


# ── sync_on_change ───────────────────────────────────────────────────────────


def test_sync_on_change_service_bot_uses_online_binding():
    fs = FakeDeviceFileSystem({_DEVICE_CREDENTIALS_PATH: _credentials()})
    writer, resolver, dispatcher = _make_writer(fs=fs, admins=["u1"], online_binding_id=99)

    writer.sync_on_change(bot_id="bot-1", owner_id="owner-1", admins=["u1"])

    # online binding resolved via resolve_for_binding (service bot has a publish record)
    assert resolver.resolve_for_binding_calls == [(99, "owner-1", "bot-1")]
    assert resolver.resolve_for_bot_calls == []
    assert "ADMINS=u1" in fs.write_calls[-1][1].decode()


def test_sync_on_change_no_publish_record_falls_back_to_resolve_for_bot():
    fs = FakeDeviceFileSystem({_DEVICE_CREDENTIALS_PATH: _credentials()})
    writer, resolver, _ = _make_writer(fs=fs, online_binding_id=None, provider="baas")

    writer.sync_on_change(bot_id="bot-1", owner_id="owner-1", admins=["u1"])

    # ARCA/personal/desktop: no online binding → today's resolve_for_bot path
    assert resolver.resolve_for_bot_calls == [("bot-1", "owner-1")]
    assert resolver.resolve_for_binding_calls == []
    assert "ADMINS=u1" in fs.write_calls[-1][1].decode()


def test_sync_on_change_missing_ext_binding_online_falls_back():
    fs = FakeDeviceFileSystem({_DEVICE_CREDENTIALS_PATH: _credentials()})
    resolver = FakeResolver(provider="baas")
    dispatcher = FakeDispatcher(fs)
    # publish record present but ext has no online binding id
    class _NoOnlineRepo:
        def get_latest_success_by_source_bot_id(self, source_bot_id, env):
            return BotPublishRecord(
                id=1, source_bot_pk=1, source_bot_id=source_bot_id, publish_bot_id="p",
                name="n", description=None, owner_id="o", owner_name=None,
                status=PublishStatus.SUCCESS, version=1, last_pub_id=0, env=env,
                ext={"binding": {}}, permission_owner="o",
            )
    writer = DeviceCredentialsAdminsWriter(
        collaborator_repo=FakeCollaboratorRepo(["u1"]),
        bot_publish_repo=_NoOnlineRepo(),
        resolver_provider=lambda: resolver,
        device_fs_dispatcher_provider=lambda: dispatcher,
    )

    writer.sync_on_change(bot_id="bot-1", owner_id="owner-1", admins=["u1"])

    assert resolver.resolve_for_bot_calls == [("bot-1", "owner-1")]
    assert resolver.resolve_for_binding_calls == []


def test_sync_on_change_no_device_bound_swallows():
    fs = FakeDeviceFileSystem({_DEVICE_CREDENTIALS_PATH: _credentials()})
    resolver = FakeResolver(provider="teclaw")
    resolver.resolve_for_bot = lambda bot_id, user_id, *, device_uuid=None: (_ for _ in ()).throw(
        DeviceNotBoundError("no active binding")
    )
    # for the service-bot branch it uses resolve_for_binding; make that raise too:
    resolver.resolve_for_binding = lambda binding_id, operator_id, *, bot_id, device_uuid=None: (
        _ for _ in ()
    ).throw(DeviceNotBoundError("no active binding"))
    dispatcher = FakeDispatcher(fs)
    writer = DeviceCredentialsAdminsWriter(
        collaborator_repo=FakeCollaboratorRepo(["u1"]),
        bot_publish_repo=FakePublishRepo(online_binding_id=99),
        resolver_provider=lambda: resolver,
        device_fs_dispatcher_provider=lambda: dispatcher,
    )

    # must NOT raise — a collab change must never break the main flow
    writer.sync_on_change(bot_id="bot-1", owner_id="owner-1", admins=["u1"])
    assert fs.write_calls == []


def test_sync_on_change_write_failure_swallows():
    fs = RaisingDeviceFileSystem({_DEVICE_CREDENTIALS_PATH: _credentials()})
    writer, *_ = _make_writer(fs=fs, admins=["u1"], online_binding_id=99)

    # must NOT raise — collab change flow stays intact
    writer.sync_on_change(bot_id="bot-1", owner_id="owner-1", admins=["u1"])


def test_sync_on_change_empty_admins_clears_line():
    fs = FakeDeviceFileSystem({_DEVICE_CREDENTIALS_PATH: _credentials(admins="old")})
    writer, *_ = _make_writer(fs=fs, online_binding_id=99)

    writer.sync_on_change(bot_id="bot-1", owner_id="owner-1", admins=[])

    written = fs.write_calls[-1][1].decode()
    assert "ADMINS=\n" in written
    assert "TOKEN=TOK" in written


def test_seed_for_publish_no_device_bound_propagates():
    fs = FakeDeviceFileSystem({_DEVICE_CREDENTIALS_PATH: _credentials()})
    resolver = FakeResolver(provider="teclaw")
    resolver.resolve_for_binding = lambda binding_id, operator_id, *, bot_id, device_uuid=None: (
        _ for _ in ()
    ).throw(DeviceNotBoundError("no active binding"))
    dispatcher = FakeDispatcher(fs)
    writer = DeviceCredentialsAdminsWriter(
        collaborator_repo=FakeCollaboratorRepo(["u1"]),
        bot_publish_repo=FakePublishRepo(online_binding_id=99),
        resolver_provider=lambda: resolver,
        device_fs_dispatcher_provider=lambda: dispatcher,
    )

    # seed_for_publish propagates DeviceNotBound so the handler can decide (Complete)
    with pytest.raises(DeviceNotBoundError):
        writer.seed_for_publish(binding_id=99, bot_id="bot-1", owner_id="owner-1")
