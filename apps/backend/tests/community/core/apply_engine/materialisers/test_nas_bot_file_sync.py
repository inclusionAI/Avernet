"""``NasBotFileSyncPort``: rsync argv, paths, and every failure branch.

A workspace sync is path-to-path over ``sudo rsync``; the one thing these tests
must prove is that the port builds the right argv (the system baseline excludes
from the build plan, the caller excludes anchored to the root, ``--delete
--delete-excluded --chown=1000:1000``, source→target at the engine root), the
right paths (the source and target NAS dirs joined to the engine root name),
and that every refusal is a composed reason rather than a raise or raw stderr.
The repository, the sandbox registry, the path factory and the command runner
are all fakes: no real subprocess, no real NAS.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.core.apply_engine.materialisers.nas_bot_file_sync import (
    NasBotFileSyncPort,
)
from agentclaw.community.core.ports.bot_file_sync_port import BotFileSyncTarget
from agentclaw.community.core.workspace.engine_sandbox import (
    EngineBuildPlan,
    EngineSandboxProvider,
    EngineSandboxRegistry,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory


# A minimal openclaw-shaped build plan — only the two fields the port reads.
def _build_plan():
    return EngineBuildPlan(
        engine_type="openclaw",
        source_root_name=".openclaw",
        migration_subpath="openclaw",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=["workspace/sessions/", ".git/"],
    )


class _Provider(EngineSandboxProvider):
    def __init__(self, plan):
        self._plan = plan

    def get_base_path(self):
        return "/home/admin/.openclaw"

    def get_build_plan(self, *, bot=None, build_rsync_excludes_append=None):
        return self._plan

    def default_excludes(self):
        return []


class _Registry(EngineSandboxRegistry):
    def __init__(self, plan):
        self._plan = plan

    def resolve(self, engine_type):
        return _Provider(self._plan)


def _factory(nas_root: Path) -> WorkspacePathFactory:
    """A path factory whose ``get_bot_nas_dir`` returns nas_root/<storage_id>."""
    skill_repo_sync = MagicMock()
    fake = WorkspacePathFactory(skill_repo_sync=skill_repo_sync)
    # Override the one method the port uses so it does not touch the real NAS
    # root (/home/admin/.merge_nas) or the process env's storage id.
    storage_id = "dev_staff_owner_openclaw_src_1"

    def get_bot_nas_dir(entity_id, bot_id, engine_type, entity_type="staff"):
        return nas_root / storage_id

    fake.get_bot_nas_dir = get_bot_nas_dir
    return fake


def _run(port, **kwargs):
    import asyncio

    return asyncio.run(port.sync(**kwargs))


def _ok(port, *, source_over=None, excludes=(), target_engine="openclaw"):
    import asyncio

    target = BotFileSyncTarget(
        bot_id="b_dst",
        owner_id="o",
        entity_id="owner",
        env="dev",
        engine_type=target_engine,
        entity_type="staff",
    )
    return asyncio.run(
        port.sync(target=target, source_bot_id="src_1", path="workspace/", excludes=excludes)
    )


_MISSING = object()


def _port(nas_root, run_command, *, source=_MISSING, plan=None):
    if plan is None:
        plan = _build_plan()
    bot_repo = MagicMock()
    if source is _MISSING:
        source = {
            "bot_id": "src_1",
            "active_engine": "openclaw",
            "entity_id": "owner",
            "entity_type": "staff",
            "env": "dev",
        }
    bot_repo.get_by_id.return_value = source
    return NasBotFileSyncPort(
        bot_repo=bot_repo,
        sandbox_registry=_Registry(plan),
        path_factory=_factory(nas_root),
        run_command=run_command,
        env="dev",
    )


def _recording_runner(argv_seen=None, *, raises=None, returncode=0):
    def runner(*, cmd, command_name, error_message, timeout_seconds=None):
        if argv_seen is not None:
            argv_seen.append(cmd)
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return runner


# ── argv + paths ────────────────────────────────────────────────────────────


def test_a_sync_builds_the_rsync_argv_at_the_engine_root(tmp_path):
    """The argv carries the system baseline excludes off the build plan, the
    caller excludes anchored with a leading ``/``, ``--delete
    --delete-excluded``, and ``--chown=1000:1000``; the source and target are
    both the NAS dir joined to the engine root name (``.openclaw``)."""
    seen = []
    port = _port(tmp_path, _recording_runner(seen), source={
        "bot_id": "src_1", "active_engine": "openclaw", "entity_id": "owner",
        "entity_type": "staff", "env": "dev",
    })
    # The source workspace must exist (the port checks it before the rsync).
    storage_id_dir = tmp_path / "dev_staff_owner_openclaw_src_1"
    (storage_id_dir / ".openclaw").mkdir(parents=True)

    result = _ok(port, excludes=("secrets", "large/tmp"))

    assert result.ok is True
    (argv,) = seen
    assert argv[:3] == ["sudo", "rsync", "-av"]
    assert "--delete" in argv and "--delete-excluded" in argv
    assert "--chown=1000:1000" in argv
    assert "--no-devices" in argv and "--no-specials" in argv
    # System baseline excludes, un-anchored (rsync's choice for build_ignore).
    assert "--exclude=workspace/sessions/" in argv
    assert "--exclude=.git/" in argv
    # Caller excludes, anchored to the source root.
    assert "--exclude=/secrets" in argv
    assert "--exclude=/large/tmp" in argv
    # Source→target at the engine root, trailing slashes (tree scope).
    assert argv[-2] == f"{storage_id_dir / '.openclaw'}/"
    assert argv[-1].endswith("/.openclaw/")


def test_a_sync_with_no_caller_excludes_omits_the_anchored_excludes(tmp_path):
    seen = []
    port = _port(tmp_path, _recording_runner(seen))
    storage_id_dir = tmp_path / "dev_staff_owner_openclaw_src_1"
    (storage_id_dir / ".openclaw").mkdir(parents=True)

    _ok(port, excludes=())
    (argv,) = seen
    assert not any(a.startswith("--exclude=/") for a in argv)


# ── refusals ────────────────────────────────────────────────────────────────


def test_an_engine_the_sandbox_registry_cannot_resolve_is_refused(tmp_path):
    """The port never names an engine family itself; the registry's resolving
    to it is what says an engine runs NAS-rsync. An engine the registry cannot
    resolve raises at build-plan time, and the port composes the failure
    rather than letting a raw traceback through. No rsync is attempted."""
    seen = []

    class _UnresolvableRegistry:
        def resolve(self, engine_type):
            raise KeyError(engine_type)

    port = NasBotFileSyncPort(
        bot_repo=MagicMock(get_by_id=MagicMock(return_value={
            "bot_id": "src_1", "active_engine": "openclaw", "entity_id": "owner",
            "entity_type": "staff", "env": "dev",
        })),
        sandbox_registry=_UnresolvableRegistry(),
        path_factory=_factory(tmp_path),
        run_command=_recording_runner(seen),
        env="dev",
    )
    result = _ok(port)  # engine openclaw → registry raises → composed reason
    assert result.ok is False
    assert result.reason == "bot workspace sync failed"
    assert seen == []


def test_a_missing_source_bot_is_refused_without_rsync(tmp_path):
    seen = []
    port = _port(tmp_path, _recording_runner(seen), source=None)
    result = _ok(port)
    assert result.ok is False
    assert "source bot not found" in result.reason
    assert seen == []


def test_a_raising_source_lookup_is_a_composed_reason_and_logs_the_traceback(tmp_path, monkeypatch):
    """``get_by_id`` raising (DB/MOSN transient, session failure) used to unwind
    straight into ``BotFileSyncMaterialiser.write``'s catch — surfaced as the
    catch-all ``"bot file sync failed"`` with no per-branch log. Now it is
    wrapped here: the report gets the composed ``"bot workspace sync failed"``
    and the traceback lands on the log under ``bot_file_sync.source_lookup_failed``,
    so the same PARTIAL is traceable."""
    seen = []
    port = _port(tmp_path, _recording_runner(seen))
    port._bot_repo.get_by_id.side_effect = RuntimeError("session expired: connection lost")

    logged: list[tuple[str, tuple]] = []

    class _SpyLogger:
        def exception(self, msg, *args, **_):
            logged.append((msg, args))

    import agentclaw.community.core.apply_engine.materialisers.nas_bot_file_sync as mod

    monkeypatch.setattr(mod, "logger", _SpyLogger())

    result = _ok(port)

    assert result.ok is False
    assert result.reason == "bot workspace sync failed"
    assert seen == []  # never reached rsync
    assert any("bot_file_sync.source_lookup_failed" in m for m, _ in logged)



def test_a_cross_engine_source_and_destination_is_refused(tmp_path):
    seen = []
    port = _port(tmp_path, _recording_runner(seen), source={
        "bot_id": "src_1", "active_engine": "aicoding", "entity_id": "owner",
        "entity_type": "staff", "env": "dev",
    })
    result = _ok(port, target_engine="openclaw")
    assert result.ok is False
    assert "cross-engine" in result.reason
    assert seen == []


def test_a_cross_env_source_is_refused(tmp_path):
    seen = []
    port = _port(tmp_path, _recording_runner(seen), source={
        "bot_id": "src_1", "active_engine": "openclaw", "entity_id": "owner",
        "entity_type": "staff", "env": "pre",  # source in env 'pre', process is 'dev'
    })
    result = _ok(port)  # target.env = 'dev'
    assert result.ok is False
    assert "cross-env" in result.reason
    assert seen == []


def test_a_missing_source_workspace_is_refused_without_rsync(tmp_path):
    seen = []
    # The NAS dir is never created, so source_path.exists() is False.
    port = _port(tmp_path, _recording_runner(seen))
    result = _ok(port)
    assert result.ok is False
    assert "source workspace not found" in result.reason
    assert seen == []


def test_an_mkdir_failure_is_a_composed_reason(tmp_path):
    seen = []
    # Force target_path.mkdir to raise: make the *resolvable* target render a
    # path whose parent (the storage_id dir) is a regular file. The source path
    # resolves to a different storage id on disk, and exists; the target path
    # is get_bot_nas_dir(...) / ".openclaw" — with the parent a file, mkdir
    # raises NotADirectoryError, which the port composes.
    storage_dir = tmp_path / "real_storage"
    (storage_dir / ".openclaw").mkdir(parents=True)
    port = _port(tmp_path / "elsewhere", _recording_runner(seen), source={
        "bot_id": "src_1", "active_engine": "openclaw", "entity_id": "owner",
        "entity_type": "staff", "env": "dev",
    })
    # Make the source workspace exist (the port's existence check passes).
    # The fake factory always returns the same storage id, so source and target
    # share it; mkdir is the call we want to fail. Plant a *file* at the
    # resolved dir to break the .openclaw subdir mkdir.
    resolved = storage_dir  # both source and target resolve here via the fake
    # Recreate the source workspace and then make the engine root a *file*
    # so the target's mkdir(parents=True) on a path under a file fails.
    # Easiest: a separate factory that returns a path whose .openclaw parent
    # is the source workspace itself, and the port already mkdir'd it. To keep
    # this deterministic, point the factory at a dir whose `.openclaw` is a file.
    file_root = tmp_path / "file_storage"
    file_root.mkdir()
    (file_root / ".openclaw").write_text("not a dir")  # a file, not a directory

    port._path_factory.get_bot_nas_dir = lambda *a, **k: file_root
    # But the source existence check runs first on the same path and sees a
    # file (not a dir) — the port's `source_path.exists()` returns True for a
    # file. To exercise mkdir-failure distinctly, route source to a real dir
    # and target to the file_root. Override path_factory per-call by splitting.
    real_source_root = tmp_path / "src_storage"
    (real_source_root / ".openclaw").mkdir(parents=True)

    calls = {"n": 0}

    def split_factory(entity_id, bot_id, engine_type, entity_type="staff"):
        # First (and only source) call resolves to the real source root.
        calls["n"] += 1
        return real_source_root if calls["n"] == 1 else file_root

    bot_repo = MagicMock(get_by_id=MagicMock(return_value={
        "bot_id": "src_1", "active_engine": "openclaw", "entity_id": "owner",
        "entity_type": "staff", "env": "dev",
    }))
    port2 = NasBotFileSyncPort(
        bot_repo=bot_repo,
        sandbox_registry=_Registry(_build_plan()),
        path_factory=SimpleNamespace(get_bot_nas_dir=split_factory),
        run_command=_recording_runner(seen),
        env="dev",
    )
    import asyncio

    target = BotFileSyncTarget(
        bot_id="b_dst", owner_id="o", entity_id="owner", env="dev",
        engine_type="openclaw", entity_type="staff",
    )
    result = asyncio.run(port2.sync(
        target=target, source_bot_id="src_1", path="workspace/", excludes=()
    ))
    assert result.ok is False
    assert result.reason == "bot workspace sync failed"
    assert seen == []


def test_a_raising_run_command_is_a_composed_reason_never_raw_text(tmp_path):
    seen = []
    port = _port(tmp_path, _recording_runner(
        seen, raises=subprocess.CalledProcessError(1, ["sudo", "rsync"], "permission denied: /x")
    ))
    storage_id_dir = tmp_path / "dev_staff_owner_openclaw_src_1"
    (storage_id_dir / ".openclaw").mkdir(parents=True)

    result = _ok(port)
    assert result.ok is False
    assert result.reason == "bot workspace sync failed"
    # The raw exception text must not reach the reason.
    assert "permission denied" not in result.reason
    (argv,) = seen
    assert argv[:2] == ["sudo", "rsync"]


def test_a_raising_build_plan_resolution_is_a_composed_reason(tmp_path):
    seen = []
    # A registry whose provider raises on get_build_plan.
    class _BrokenRegistry:
        def resolve(self, engine_type):
            class _P:
                def get_build_plan(self, *, bot=None, build_rsync_excludes_append=None):
                    raise RuntimeError("sandbox missing")
            return _P()
    port = NasBotFileSyncPort(
        bot_repo=MagicMock(get_by_id=MagicMock(return_value={
            "bot_id": "src_1", "active_engine": "openclaw", "entity_id": "owner",
            "entity_type": "staff", "env": "dev",
        })),
        sandbox_registry=_BrokenRegistry(),
        path_factory=_factory(tmp_path),
        run_command=_recording_runner(seen),
        env="dev",
    )
    result = _ok(port)
    assert result.ok is False
    assert result.reason == "bot workspace sync failed"
    assert seen == []
