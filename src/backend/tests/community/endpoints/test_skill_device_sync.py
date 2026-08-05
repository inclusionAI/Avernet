"""Golden-master device-push tests for the skill-center entrypoints (Task 14a).

Pins the **device sync_symlinks calls** the skill entrypoints make. Task
14a relocates the push from the adapter layer into the service layer; the
contract is ARCA byte-equivalence, so these record the calls against
current code and must pass **unchanged** after the refactor.

The device boundary is the **real** local plugin (``LocalDeviceSyncPlugin``
carrying the ``MockSeam``). Phase 2 Task 6 收口后 ``DeviceSyncPluginSupplier``
已删,改注入 ``DeviceSyncDispatcher``。本测试通过 monkey-patch dispatcher.dispatch
让其返回一个 recording ``LocalDeviceSyncPlugin``(skills_dir=None → noop),
再读其 ``calls``。The core symlink orchestration runs for real.

``activate``/``deactivate`` first run a real symlink op on the *local*
filesystem (genuinely reachable in local mode), so the seed prepares the
per-bot workspace dir.

Coverage of the call chain:
- happy (openclaw): a git skill resolves to ``/home/admin/.openclaw/...``.
- happy (claude_code): same skill, different engine → ``/home/admin/.claude/...``
  (exercises the engine→container-path branch in get_symlink_mappings).
- error: activate with an invalid skill path → 400, no push.
- happy (deactivate): removes the on-disk link, re-pushes remaining set.
- error: deactivate a skill with no on-disk entry → 400.
"""
from __future__ import annotations

import shutil

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.skill_center.services.repositories import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
from agentclaw.community.core.devices.services.local_device_filesystem import LocalDeviceFileSystem
from agentclaw.community.plugins.local.device_sync import LocalDeviceSyncPlugin
from tests.community.factories.access import make_staff_user
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test
from tests.community.framework.device_seams import (
    install_fake_filesystem_dispatcher,
    install_fake_resolver,
    install_fake_sync_dispatcher,
)


_OWNER = "u_owner"
_GIT_PATH = "git://business/foo"

# Per-engine container-view base dir (ENGINE_SKILLS_DIR_MAP in
# get_symlink_mappings). The git skill resolves to <base>/skills-repo/business/foo
# linked at <base>/foo.
_ENGINE_BASE = {
    "openclaw": "/home/admin/.openclaw/workspace/skills",
    "claude_code": "/home/admin/.claude/skills",
}


def _expected_symlink(engine: str) -> dict:
    base = _ENGINE_BASE[engine]
    return {
        "source": f"{base}/skills-repo/business/foo",
        "target": f"{base}/foo",
        "skill_uuid": None,
        "version": None,
    }


def _record_for_bot(world) -> LocalDeviceSyncPlugin:
    """Patch ``DeviceSyncDispatcher.dispatch``, ``DeviceFilesystemDispatcher.
    dispatch``/``for_bot``, and ``DeviceContextResolver.resolve_for_bot`` so
    the activate/deactivate paths can drive a stable, recording local plugin
    without a real device binding.

    The test bots are inserted via the repo without a real binding, so the
    real ``resolve_for_bot`` would raise ``DeviceNotBoundError``; the patch
    returns a fake ``DeviceContext`` (provider="baas") and records the
    (bot_id, user_id) for the assertions to check. The fs dispatcher is
    routed to a real ``LocalDeviceFileSystem`` so the on-disk symlink ops
    that ``skill_service.deactivate_skill`` makes work normally.
    """
    rec = LocalDeviceSyncPlugin(skills_dir=None)  # real impl, noop + records
    local_fs = LocalDeviceFileSystem()  # real pathlib-backed fs

    world._resolve_calls = []
    install_fake_resolver(world, record_calls_to=world._resolve_calls)
    install_fake_sync_dispatcher(world, plugin=rec)
    # device_fs_factory in SkillService is fs_dispatcher.for_bot; patch
    # both entry points to the same local fs.
    install_fake_filesystem_dispatcher(
        world, fs=local_fs, dispatcher_cls=DeviceFilesystemDispatcher
    )
    world._rec_plugin = rec
    return rec


def _seed_bot_with_active_git_skill(world, *, bot_id: str, engine: str) -> None:
    """user → bot(active_engine) → active skill set → one git skill."""
    make_staff_user(world, user_id=_OWNER)

    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": f"Bot {bot_id}",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": _OWNER,
            "entity_type": "user",
            "creator_id": _OWNER,
            "active_engine": engine,
        }
    )

    skill = world.get(SkillRepository).create(
        {
            "name": "foo",
            "description": "golden-master git skill",
            "git_path": _GIT_PATH,
            "category": "business",
            "tags": ["test"],
            "input_schema": "{}",
            "output_schema": "{}",
            "is_public": True,
            "is_builtin": False,
            "user_id": _OWNER,
            "bolt_id": bot_id,
            "status": "PUBLISHED",
            "version": 1,
            "skill_uuid": "foo-uuid",
            "source_type": "git",
            "category_path": "/business",
            "package_url": "",
            "zip_url": "",
        }
    )

    ss_repo = world.get(SkillSetRepository)
    skill_set = ss_repo.create(
        {
            "name": "Default Set",
            "description": "golden-master active set",
            "user_id": _OWNER,
            "bolt_id": bot_id,
            "is_default": False,
            "is_builtin": False,
            "is_active": 1,
            "engine_type": engine,
        }
    )
    ss_repo.add_skill_to_set(str(skill_set["id"]), str(skill["id"]))


def _clean_workspace_skills_dir(world, *, bot_id: str, engine: str):
    """Return the per-bot active skills dir, freshly created (cleared first
    so the test is idempotent across runs — a leftover ``foo`` symlink
    would send ``activate_skill`` down the device-delete branch).
    """
    pf = world.get(WorkspacePathFactory)
    skills_dir = pf.get_bot_skills_dir(_OWNER, bot_id, engine, "staff")
    shutil.rmtree(skills_dir, ignore_errors=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir


def _make_activate_seed(*, bot_id: str, engine: str, record: bool):
    def _seed(world) -> None:
        _seed_bot_with_active_git_skill(world, bot_id=bot_id, engine=engine)
        _clean_workspace_skills_dir(world, bot_id=bot_id, engine=engine)
        if record:
            _record_for_bot(world)

    return _seed


def _make_assert_pushed(*, bot_id: str, engine: str):
    def _assert(response, world) -> None:
        assert response.json().get("success") is True, response.json()
        resolve_calls = world._resolve_calls
        assert len(resolve_calls) == 1, resolve_calls
        assert resolve_calls[0][0] == bot_id
        sym = world._rec_plugin.calls_to("sync_symlinks")
        assert len(sym) == 1, world._rec_plugin.calls
        # The production code's LOCAL override only converts openclaw
        # paths; other engines keep container-view paths unchanged.
        if engine == "openclaw":
            pf = world.get(WorkspacePathFactory)
            skills_dir = pf.get_bot_skills_dir(_OWNER, bot_id, engine, "staff")
        else:
            skills_dir = _ENGINE_BASE[engine]
        expected = {
            "source": f"{skills_dir}/skills-repo/business/foo",
            "target": f"{skills_dir}/foo",
            "skill_uuid": None,
            "version": None,
        }
        assert sym[0].args[0] == [expected]

    return _assert


# ---- activate: happy (openclaw) ----
@endpoint_test(
    method="POST",
    path="/api/skills/{skill_id}/activate",
    scenario="happy_git_skill_openclaw_pushes_symlink",
    input=CaseInput(
        path_params={"skill_id": "foo"},
        query_params={"bot_id": "bot_skill_oc", "entity_id": _OWNER},
        headers={"x-user-id": _OWNER},
        json_body={"source_path": _GIT_PATH, "relative_path": _GIT_PATH},
    ),
    seed=_make_activate_seed(bot_id="bot_skill_oc", engine="openclaw", record=True),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_make_assert_pushed(bot_id="bot_skill_oc", engine="openclaw"),),
)
def activate_openclaw_pushes_symlink():
    """Activate (openclaw): pushes the active-set symlink in openclaw paths."""


# ---- activate: happy (claude_code) — different engine → different paths ----
@endpoint_test(
    method="POST",
    path="/api/skills/{skill_id}/activate",
    scenario="happy_git_skill_claude_code_pushes_symlink",
    input=CaseInput(
        path_params={"skill_id": "foo"},
        query_params={"bot_id": "bot_skill_cc", "entity_id": _OWNER},
        headers={"x-user-id": _OWNER},
        json_body={"source_path": _GIT_PATH, "relative_path": _GIT_PATH},
    ),
    seed=_make_activate_seed(bot_id="bot_skill_cc", engine="claude_code", record=True),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_make_assert_pushed(bot_id="bot_skill_cc", engine="claude_code"),),
)
def activate_claude_code_pushes_symlink():
    """Activate (claude_code): same skill resolves to .claude container paths."""


# ---- activate: error (invalid skill path → activate returns False → 400) ----
@endpoint_test(
    method="POST",
    path="/api/skills/{skill_id}/activate",
    scenario="error_activate_invalid_skill_path_400",
    input=CaseInput(
        path_params={"skill_id": "foo"},
        query_params={"bot_id": "bot_skill_err", "entity_id": _OWNER},
        headers={"x-user-id": _OWNER},
        # local:// must be absolute; this raises ValueError in
        # parse_skill_path → activate_skill returns False → handler 400.
        json_body={"source_path": "local://oops", "relative_path": "local://oops"},
    ),
    seed=_make_activate_seed(bot_id="bot_skill_err", engine="openclaw", record=False),
    expect=ExpectError(status=400),
)
def activate_invalid_path_returns_400():
    """Activate with an invalid skill path → 400, no push."""


# ---- deactivate: happy ----
def _seed_deactivate_happy(world) -> None:
    _seed_bot_with_active_git_skill(world, bot_id="bot_skill_deact", engine="openclaw")
    skills_dir = _clean_workspace_skills_dir(
        world, bot_id="bot_skill_deact", engine="openclaw"
    )
    link = skills_dir / "foo"
    if not link.exists() and not link.is_symlink():
        link.symlink_to("skills-repo/business/foo")
    _record_for_bot(world)


def _assert_deactivate_pushed(response, world) -> None:
    assert response.json().get("success") is True, response.json()
    sym = world._rec_plugin.calls_to("sync_symlinks")
    # DB membership is unchanged by an on-disk deactivate, so the merged
    # active set still resolves to the one git symlink.
    assert len(sym) == 1, world._rec_plugin.calls
    # Compute expected paths from the actual runtime environment
    pf = world.get(WorkspacePathFactory)
    skills_dir = pf.get_bot_skills_dir(_OWNER, "bot_skill_deact", "openclaw", "staff")
    expected = {
        "source": f"{skills_dir}/skills-repo/business/foo",
        "target": f"{skills_dir}/foo",
        "skill_uuid": None,
        "version": None,
    }
    assert sym[0].args[0] == [expected]


@endpoint_test(
    method="POST",
    path="/api/skills/{skill_id}/deactivate",
    scenario="happy_deactivate_repushes_remaining",
    input=CaseInput(
        path_params={"skill_id": "foo"},
        query_params={"bot_id": "bot_skill_deact", "entity_id": _OWNER},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_deactivate_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_deactivate_pushed,),
)
def deactivate_repushes_remaining_symlinks():
    """Deactivate removes the on-disk link, re-pushes the merged set."""


# ---- deactivate: missing on-disk entry is idempotent ----
def _seed_deactivate_missing(world) -> None:
    _seed_bot_with_active_git_skill(world, bot_id="bot_skill_deact_err", engine="openclaw")
    _clean_workspace_skills_dir(world, bot_id="bot_skill_deact_err", engine="openclaw")
    _record_for_bot(world)
    # Deliberately do NOT create the on-disk 'foo' link.


@endpoint_test(
    method="POST",
    path="/api/skills/{skill_id}/deactivate",
    scenario="deactivate_missing_entry_is_idempotent",
    input=CaseInput(
        path_params={"skill_id": "foo"},
        query_params={"bot_id": "bot_skill_deact_err", "entity_id": _OWNER},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_deactivate_missing,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def deactivate_missing_entry_is_idempotent():
    """A missing on-disk entry is already deactivated and still converges runtime state."""


# ---- deactivate: reserved entry ----
@endpoint_test(
    method="POST",
    path="/api/skills/{skill_id}/deactivate",
    scenario="deactivate_reserved_entry_returns_error",
    input=CaseInput(
        path_params={"skill_id": "skills-local"},
        query_params={"bot_id": "bot_skill_deact_err", "entity_id": _OWNER},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_deactivate_missing,
    expect=ExpectError(status=400),
)
def deactivate_reserved_entry_returns_error():
    """Deactivate must not remove the protected local-package directory."""
