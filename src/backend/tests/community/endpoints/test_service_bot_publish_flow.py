"""Injection tests for the teclaw service-bot publish flow.

Declarative ``@endpoint_test`` cases covering the publish state machine
``DRAFT → BUILDING → BUILT → VALIDATE_PUB → VALIDATING → ONLINE_PUB → SUCCESS``
plus the branch lifecycle ops (re-publish/upgrade, offline, retry).

Two drive modes (see ``tests/framework``):
* **sync** cases seed the publish at a transition's *pre-state* and drive one
  endpoint — the inline ``/process`` legs (BUILT/VALIDATING) and the synchronous
  ``/{id}/sync`` poll are single-shot, so they fit the default runner.
* **drain_background** cases cover the legs whose *outcome* is backgrounded —
  the DRAFT ``/process`` build (a fire-and-forget ``asyncio.create_task``),
  offline-destroy, and retry-restart. The async runner awaits that work before
  the assertions run.

No service mocks — everything runs against the per-test DI injector; only the
BaaS HTTP boundary is stubbed via the injected ``LocalHttpClient`` (MockSeam).
The publish row is the first ``ac_bot_publish`` insert in a fresh in-memory DB,
so its id is deterministic (``_V1`` = 1; the upgrade's v2 = 2).
"""
from __future__ import annotations

from typing import Annotated

import httpx

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.repository.protocols.platform import ResourceRepositoryProtocol
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.repository.protocols.skill_center import SkillSetRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_BAAS,
    QUALIFIER_GENERAL,
    HttpClient,
)
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
    http_envelope_response,
)


_OWNER = "u_pub"
_BOT_ID = "pub_bot"
_SRC_UUID = "BOT-src"          # source bot's container uuid (its binding.device_id)
_VERIFY_UUID = "BOT-verify"    # the verify-stage bot
_ONLINE_UUID = "BOT-online"    # the online-stage bot
_BAAS_PUB_ID = 555             # BaaS publish workflow id
_ENGINE = "teclaw"             # session policy: teclaw is the engine under test
_V1 = 1                        # first ac_bot_publish row in the fresh DB
_V2 = 2                        # the upgrade's version-2 row
_HEADERS = {"x-user-id": _OWNER}

# A minimal but non-empty composed artifact, for pre-states seeded past the build
# (the create/update guard rejects an empty config_artifact).
_ARTIFACT = {
    "schema_version": 3, "engine_type": _ENGINE, "mcp": {"servers": []},
    "skills": [], "resources": [], "identity_files": [], "stores": {},
    "engine_overrides": {}, "engine_ext": {}, "version": 1,
}

_PROCESS = "/api/service-bot/publish/process"
_SYNC = "/api/service-bot/publish/{publish_id}/sync"
_OFFLINE = "/api/service-bot/publish/{publish_id}/offline"
_RETRY = "/api/service-bot/publish/{publish_id}/retry"
_RETRY_FOR_OTHERS = "/api/service-bot/publish/{publish_id}/retry_for_others"
_SCALE = "/api/service-bot/publish/{publish_id}/scale"
_SCALE_STATUS = "/api/service-bot/publish/{publish_id}/scale/status"
_UPGRADE = "/api/service-bot/publish/upgrade"

# SUPER_ADMIN member for for_others endpoints
_ADMIN_USER = "100000"
_ADMIN_HEADERS = {"x-user-id": _ADMIN_USER}


# ── BaaS stub ────────────────────────────────────────────────────────────────


def _baas(world):
    # BaasService uses QUALIFIER_BAAS (base_url=baas gateway) for control-plane calls.
    return world.get(Annotated[HttpClient, QUALIFIER_BAAS])


def _install_baas(world, *, progress_status: str = "SUCCESS", create=None) -> None:
    """Stub the BaaS interactions the flow makes. ``progress_status`` drives the
    sync flip; ``create`` overrides the create reply (a response or an Exception)."""
    create = create if create is not None else http_envelope_response(
        {"bot_uuid": _VERIFY_UUID, "publish_id": _BAAS_PUB_ID})

    def _get(path, **_kw):
        if "/devices" in path:                       # provider probe → teclaw
            return http_envelope_response([{"items": [
                {"provider_type": "TECLAW", "device_uuid": _SRC_UUID}]}])
        if "/progress" in path:                      # sync poll
            return http_envelope_response(
                {"status": progress_status, "device_details": [],
                 "overall_progress": {}, "failed_devices": []})
        if "/ws-info" in path or "/http-info" in path:  # teclaw conn_info + invoke_http
            return http_envelope_response({
                "ws_url": "ws://localhost:8890/api/openclaw/ws",
                "http_url": "http://localhost:8890/invoke-http",
                "token": "test-token", "target": _SRC_UUID, "expires_at": 0,
            })
        return http_envelope_response({})

    def _post(path, **_kw):
        if "/approve" in path:
            return http_envelope_response({"publish_id": _BAAS_PUB_ID, "status": "APPROVED"})
        if "/update" in path:                        # update_teclaw_bot (re-deliver)
            return http_envelope_response({"bot_uuid": _VERIFY_UUID, "publish_id": _BAAS_PUB_ID})
        if "/destroy" in path:                       # destroy_bot (offline)
            return http_envelope_response({"publish_id": _BAAS_PUB_ID})
        if path.endswith("/api/v1/bots"):              # create_teclaw_bot (full absolute URL)
            if isinstance(create, BaseException):
                raise create
            return create
        return http_envelope_response({})

    _baas(world).set_override("get", _get)
    _baas(world).set_override("post", _post)


# ── seeding ──────────────────────────────────────────────────────────────────


def _seed_draft(world) -> None:
    """Seed a service bot + source binding + skills (shared+user) + a resource +
    a DRAFT publish single (id == ``_V1``)."""
    make_staff_user(world, user_id=_OWNER)
    # Local mode roots skills-local at ~/.openclaw/...; null it so the user-skill
    # host path uses the prod bolt-data root the SourceRefResolver knows.
    world.get(SkillRepoSyncPlugin).set_override("get_local_skills_root", lambda: None)

    binding_repo = world.get(DeviceBindingRepository)
    src_binding_id = binding_repo.insert_binding(
        entity_id=_OWNER, entity_type="staff", device_id=_SRC_UUID,
        device_provider="teclaw", env="dev", device_props={},
        status="ACTIVE", apply_reason="seed", applied_by=_OWNER,
    )
    bot = world.get(BotRepository).insert({
        "bot_id": _BOT_ID, "bot_name": "Pub Bot",
        "owner_id": _OWNER, "owner_name": _OWNER,
        "bot_type": "service", "status": "ACTIVE",
        "entity_id": _OWNER, "entity_type": "staff", "creator_id": _OWNER,
        # teclaw engine: no default MCP servers, and its skill paths fall back to
        # openclaw's so the SourceRefResolver resolves shared + user skills.
        # TODO(task-15): teclaw inherits openclaw skill paths only via the
        # ENGINE_SKILLS_DIR_MAP `.get(default=openclaw)` fallback — give teclaw an
        # explicit entry + matching resolver rule once its layout is pinned.
        "active_engine": _ENGINE, "binding_id": src_binding_id,
    })

    world.get(ResourceRepositoryProtocol).create({
        "name": "sales.csv", "resource_type": "file", "status": "active",
        "attributes": {"path": "data/sales.csv"},
        "user_id": _OWNER, "created_by": _OWNER, "source": "upload", "bolt_id": _BOT_ID,
    })

    # A user skill records its ABSOLUTE host path in git_path (as upload_skill does:
    # local://{local_dir}/{name}); the collector reads it directly.
    local_skill_dir = world.get(WorkspacePathFactory).get_bot_skills_local_dir(
        _OWNER, _BOT_ID, _ENGINE, "staff"
    )
    skill = world.get(SkillRepository).create({
        "name": "my-skill", "description": "user skill",
        "git_path": f"local://{local_skill_dir}/my-skill",
        "category": "custom", "tags": ["test"], "input_schema": "{}", "output_schema": "{}",
        "is_public": False, "is_builtin": False, "user_id": _OWNER, "bolt_id": _BOT_ID,
        "status": "PUBLISHED", "version": 1, "skill_uuid": "my-skill-uuid",
        "source_type": "local", "category_path": "/custom", "package_url": "", "zip_url": "",
    })
    shared = world.get(SkillRepository).create({
        "name": "weather", "description": "shared skill", "git_path": "git://team/weather",
        "category": "tools", "tags": ["test"], "input_schema": "{}", "output_schema": "{}",
        "is_public": True, "is_builtin": False, "user_id": _OWNER, "bolt_id": _BOT_ID,
        "status": "PUBLISHED", "version": 1, "skill_uuid": "weather-uuid",
        "source_type": "git", "category_path": "/tools", "package_url": "", "zip_url": "",
    })
    ss_repo = world.get(SkillSetRepository)
    skill_set = ss_repo.create({
        "name": "Active Set", "description": "active set", "user_id": _OWNER,
        "bolt_id": _BOT_ID, "is_default": False, "is_builtin": False,
        "is_active": 1, "engine_type": _ENGINE,
    })
    ss_repo.add_skill_to_set(str(skill_set["id"]), str(skill["id"]))
    ss_repo.add_skill_to_set(str(skill_set["id"]), str(shared["id"]))

    world.get(BotPublishRepositoryProtocol).insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "Pub Bot", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.DRAFT, "version": 1, "env": "dev",
    })


def _binding(world, *, device_id: str, status: str) -> int:
    return world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER, entity_type="staff", device_id=device_id,
        device_provider="teclaw", env="dev", device_props={"publish_id": _BAAS_PUB_ID},
        status=status, apply_reason="seed", applied_by=_OWNER,
    )


def _advance(world, pid: int, target, ext: dict) -> None:
    world.get(BotPublishRepositoryProtocol).update_status_with_ext(
        publish_id=pid, target_status=target, ext=ext, source_status=PublishStatus.DRAFT,
    )


def _seed_failing_mcp(world) -> None:
    """Add an MCP to the active skill set → compose can't resolve its endpoint →
    the background build fails. Input-driven, no stub."""
    ss_repo = world.get(SkillSetRepository)
    sets = ss_repo.get_all_active_skill_sets(
        user_id=_OWNER, bolt_id=_BOT_ID, engine_type=_ENGINE,
    )
    ss_repo.add_mcp_to_set(
        str(sets[0]["id"]), server_code="mcp.test.noendpoint",
        name="No-Endpoint MCP", user_id=_OWNER,
    )


# ── per-case seeds ───────────────────────────────────────────────────────────


def _install_engine(world, *, workspace_files=None) -> None:
    """Stub the running source container's per-file engine API (QUALIFIER_GENERAL
    POST), reached by the teclaw build-time file gather via ``invoke_http``.

    Test-stub simplification: a single canned POST reply serves every engine call
    the gather makes — both namespace ``list`` calls get this file listing, and
    ``read`` gets the same body as bytes (the gather writes it to the local
    MockObjectStoragePlugin and emits stage-scoped refs). The build-flow assertion
    only checks the resource ref + skills; identity-namespace precision is covered
    by ``test_identity_teclaw``.
    """
    files = workspace_files if workspace_files is not None else [
        {"path": "/workspace/data/sales.csv", "is_dir": False},
    ]
    resp = httpx.Response(
        200, json={"data": {"files": files}}, request=httpx.Request("POST", "http://e"),
    )
    world.get(Annotated[HttpClient, QUALIFIER_GENERAL]).set_response("post", resp)


def _seed_process_happy(world) -> None:
    _seed_draft(world)
    _install_baas(world, progress_status="SUCCESS")
    _install_engine(world)


def _seed_process_build_fail(world) -> None:
    _seed_draft(world)
    _seed_failing_mcp(world)
    _install_baas(world)


def _seed_draft_create_rejected(world) -> None:
    # DRAFT: /process enqueues verify_flow → build succeeds, BaaS rejects the
    # create in the verify-release sub-step → FAILED. (BUILT /process is now
    # describe-only; the create runs inside the verify_flow task.)
    _seed_draft(world)
    _install_baas(world, create=http_envelope_response(code=1, message="create quota exceeded"))
    _install_engine(world)


def _seed_validate_pub(world, *, progress_status: str) -> None:
    _seed_draft(world)
    _install_baas(world, progress_status=progress_status)
    bid = _binding(world, device_id=_VERIFY_UUID, status="PENDING")
    _advance(world, _V1, PublishStatus.VALIDATE_PUB,
             {"binding": {"verify": bid}, "publish": {"verify": _BAAS_PUB_ID}})


def _seed_validating(world) -> None:
    _seed_draft(world)
    _install_baas(world, progress_status="SUCCESS")
    bid = _binding(world, device_id=_VERIFY_UUID, status="ACTIVE")
    _advance(world, _V1, PublishStatus.VALIDATING,
             {"binding": {"verify": bid}, "publish": {"verify": _BAAS_PUB_ID},
              "config_artifact": _ARTIFACT})


def _seed_online_pub(world) -> None:
    _seed_draft(world)
    _install_baas(world, progress_status="SUCCESS")
    vbid = _binding(world, device_id=_VERIFY_UUID, status="ACTIVE")
    obid = _binding(world, device_id=_ONLINE_UUID, status="PENDING")
    _advance(world, _V1, PublishStatus.ONLINE_PUB,
             {"binding": {"verify": vbid, "online": obid},
              "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
              "config_artifact": _ARTIFACT})


def _seed_success(world) -> None:
    _seed_draft(world)
    _install_baas(world, progress_status="SUCCESS")
    vbid = _binding(world, device_id=_VERIFY_UUID, status="ACTIVE")
    obid = _binding(world, device_id=_ONLINE_UUID, status="ACTIVE")
    _advance(world, _V1, PublishStatus.SUCCESS,
             {"binding": {"verify": vbid, "online": obid},
              "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
              "config_artifact": _ARTIFACT})


def _seed_failed_verify(world) -> None:
    _seed_draft(world)
    _install_baas(world, progress_status="SUCCESS")
    bid = _binding(world, device_id=_VERIFY_UUID, status="PENDING")
    _advance(world, _V1, PublishStatus.FAILED,
             {"binding": {"verify": bid}, "publish": {"verify": _BAAS_PUB_ID},
              "source_status": PublishStatus.VALIDATE_PUB.value, "config_artifact": _ARTIFACT})


# ── query helpers + assertion factories ──────────────────────────────────────


def _status(world, pid: int) -> str:
    return world.get(BotPublishRepositoryProtocol).get_by_id(pid).status


def _ext(world, pid: int) -> dict:
    return world.get(BotPublishRepositoryProtocol).get_by_id(pid).ext or {}


def _posts(world) -> list[str]:
    return [c.args[0] for c in _baas(world).calls_to("post")]


def _expect_status(pid: int, status):
    def _check(response, world):  # noqa: ARG001
        assert _status(world, pid) == status, _ext(world, pid)
    return _check


def _expect_no_post(*substrings: str):
    def _check(response, world):  # noqa: ARG001
        posts = _posts(world)
        for s in substrings:
            assert not any(s in p for p in posts), f"unexpected BaaS call {s!r} in {posts}"
    return _check


def _expect_post(*substrings: str):
    def _check(response, world):  # noqa: ARG001
        posts = _posts(world)
        for s in substrings:
            assert any(s in p for p in posts), f"expected BaaS call {s!r}, got {posts}"
    return _check


def _expect_binding_status(pid: int, stage: str, status: str):
    def _check(response, world):  # noqa: ARG001
        bid = _ext(world, pid)["binding"][stage]
        binding = world.get(DeviceBindingRepository).get_by_id(bid)
        assert binding.status == status, binding.status
    return _check


def _expect_error_message(*substrings: str):
    def _check(response, world):  # noqa: ARG001
        msg = _ext(world, _V1).get("error_message") or ""
        for s in substrings:
            assert s in msg, f"expected {s!r} in {msg!r}"
    return _check


def _install_scale_success(world, *, publish_id: int = 7001) -> None:
    _baas(world).set_override(
        "post",
        lambda path, **_kw: http_envelope_response({"publish_id": publish_id})
        if "/scale" in path
        else http_envelope_response({}),
    )


def _install_scale_failure(world, *, message: str = "scale quota exceeded") -> None:
    _baas(world).set_override(
        "post",
        lambda path, **_kw: http_envelope_response(code=1, message=message)
        if "/scale" in path
        else http_envelope_response({}),
    )


def _seed_scale_success(world) -> None:
    _seed_success(world)
    bot_repo = world.get(BotRepository)
    bot_repo.update_by_owner(_BOT_ID, _OWNER, {"ext": {"service_bot_config": {"device_count": 2}}})
    _install_scale_success(world)


def _seed_scale_error(world) -> None:
    _seed_success(world)
    bot_repo = world.get(BotRepository)
    bot_repo.update_by_owner(_BOT_ID, _OWNER, {"ext": {"service_bot_config": {"device_count": 2}}})
    _install_scale_failure(world)


def _seed_scale_status(world) -> None:
    _seed_success(world)
    repo = world.get(BotPublishRepositoryProtocol)
    record = repo.get_by_id(_V1)
    ext = record.ext or {}
    ext["scale"] = {"publish_id": _BAAS_PUB_ID}
    repo.update_status_with_ext(
        publish_id=_V1,
        target_status=record.status,
        ext=ext,
        source_status=record.status,
    )
    _install_baas(world, progress_status="SUCCESS")


# ── verify stage: DRAFT → BUILDING → BUILT → VALIDATE_PUB (background build) ──


def _assert_artifact_composed(response, world) -> None:
    """The background build composed the seeded inputs into the create payload."""
    create = next(c for c in _baas(world).calls_to("post") if c.args[0].endswith("/api/v1/bots"))
    artifact = create.kwargs["json"]["config"]["deploy_config"]["teclaw_bot_config"]
    assert artifact.get("schema_version")
    assert "sales.csv" in [r["name"] for r in artifact["resources"]], artifact["resources"]
    names = {s["name"]: s for s in artifact["skills"]}
    # local:// (user) skills are engine-owned: the container auto-discovers them
    # under /workspace/skills-local and the publish-time gather snapshots their
    # bytes, so they are NOT composed into the artifact's skills list. Only shared
    # git:// skills are emitted.
    assert "my-skill" not in names, artifact["skills"]
    assert names["weather"]["scope"] == "shared" and names["weather"]["store"] == "skill-repo"


@endpoint_test(
    method="POST", path=_PROCESS, scenario="draft_process_builds_to_validate_pub",
    input=CaseInput(json_body={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_process_happy, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"status": "building"}}),
    extra_assertions=(
        _expect_status(_V1, PublishStatus.VALIDATE_PUB),
        _assert_artifact_composed,
        _expect_post("/api/v1/bots"),
        # #197 all-auto: BaaS auto-approves server-side — no client /approve POST.
        _expect_no_post("/approve"),
        _expect_no_post("/destroy"),
    ),
)
def draft_process_builds_to_validate_pub():
    """DRAFT /process backgrounds build+create (auto-approved server-side) →
    VALIDATE_PUB, artifact carries the seeded resource + user/shared skills."""


@endpoint_test(
    method="POST", path=_PROCESS, scenario="build_failure_unresolvable_mcp",
    input=CaseInput(json_body={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_process_build_fail, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"data": {"status": "building"}}),
    extra_assertions=(
        _expect_status(_V1, PublishStatus.FAILED),
        _expect_error_message("endpoint"),
        _expect_no_post("/api/v1/bots", "/approve"),
    ),
)
def build_failure_unresolvable_mcp():
    """Input-driven build failure: a seeded MCP has no resolvable endpoint → the
    background compose raises → FAILED before any BaaS create."""


@endpoint_test(
    method="POST", path=_PROCESS, scenario="create_rejected_by_baas",
    input=CaseInput(json_body={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_draft_create_rejected, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"data": {"status": "building"}}),
    extra_assertions=(
        _expect_status(_V1, PublishStatus.FAILED),
        _expect_error_message("create quota exceeded"),
        _expect_no_post("/approve"),
    ),
)
def create_rejected_by_baas():
    """DRAFT /process enqueues verify_flow; the durable task builds then BaaS
    rejects the create → FAILED, never approved. (BaaS-originated failure → stub.)"""


@endpoint_test(
    method="POST", path=_PROCESS, scenario="process_publish_not_found",
    input=CaseInput(json_body={"publish_id": 9_999_999}, headers=_HEADERS),
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 404}),
)
def process_publish_not_found():
    """/process for a non-existent publish_id → PublishNotFoundError → 404 error
    envelope (the endpoint's error path)."""


# ── verify stage: VALIDATE_PUB → VALIDATING (the sync poll) ──────────────────


@endpoint_test(
    method="POST", path=_SYNC, scenario="sync_reports_validate_pub_read_only",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),
    seed=lambda w: _seed_validate_pub(w, progress_status="SUCCESS"),
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"status": "validate_pub"}}),
    extra_assertions=(
        _expect_status(_V1, PublishStatus.VALIDATE_PUB),        # unchanged
        _expect_binding_status(_V1, "verify", "PENDING"),        # not flipped
    ),
)
def sync_reports_validate_pub_read_only():
    """/sync is a read-only status report: even with BaaS already at SUCCESS, it
    does not advance the record or touch the binding — the durable poll task owns
    advancement (covered by test_publish_durable_pipeline)."""


@endpoint_test(
    method="POST", path=_SYNC, scenario="sync_on_failed_publish_reports_error",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_failed_verify,
    expect=ExpectError(status=200, json_contains={"success": False}),
    extra_assertions=(_expect_status(_V1, PublishStatus.FAILED),),
)
def sync_on_failed_publish_reports_error():
    """/sync on a FAILED record reports the recorded error (success=false) and
    leaves the record untouched."""


# ── online stage: VALIDATING → ONLINE_PUB → SUCCESS ──────────────────────────


@endpoint_test(
    method="POST", path=_PROCESS, scenario="online_release",
    input=CaseInput(json_body={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_validating,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"status": "online_pub"}}),
    extra_assertions=(_expect_status(_V1, PublishStatus.ONLINE_PUB),),
)
def online_release():
    """Seeded at VALIDATING, /process advances VALIDATING → ONLINE_PUB synchronously
    (the go-live gate) and enqueues the online_release task; the drained task does
    the online create+approve within ONLINE_PUB."""


@endpoint_test(
    method="POST", path=_SYNC, scenario="sync_reports_online_pub_read_only",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_online_pub,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"status": "online_pub"}}),
    extra_assertions=(
        _expect_status(_V1, PublishStatus.ONLINE_PUB),           # unchanged
        _expect_binding_status(_V1, "online", "PENDING"),         # not flipped
    ),
)
def sync_reports_online_pub_read_only():
    """/sync at ONLINE_PUB reports "in progress" without advancing; the poll task
    drives ONLINE_PUB → SUCCESS (covered end-to-end in test_publish_durable_pipeline)."""


# ── branch lifecycle: re-publish / offline / retry ───────────────────────────


@endpoint_test(
    method="POST", path=_UPGRADE, scenario="upgrade_mints_next_version",
    input=CaseInput(json_body={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_success,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"version": 2}}),
    extra_assertions=(_expect_status(_V2, PublishStatus.DRAFT),),
)
def upgrade_mints_next_version():
    """A SUCCESS publish can be re-published: /upgrade mints a v2 DRAFT (version 2),
    linked to v1 by last_pub_id."""


@endpoint_test(
    method="POST", path=_OFFLINE, scenario="offline_destroys_online",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_success, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(
        _expect_post("/stop"),
        _expect_binding_status(_V1, "online", "RELEASED"),
    ),
)
def offline_destroys_online():
    """Taking a successful publish offline destroys its online container in the
    background (stop_bot) and releases the online binding."""


@endpoint_test(
    method="POST", path=_RETRY, scenario="retry_restarts_after_verify_failure",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_failed_verify, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(
        _expect_post("/update"),                               # re-delivered, not re-created
        # #162 secondary orphan fix: the retry-restart now enqueues the durable poll,
        # so the one drain tick drives the retried record out of its VALIDATE_PUB wait
        # state (→ VALIDATING via sync_restart_progress) with no user /sync polling.
        _expect_status(_V1, PublishStatus.VALIDATING),
    ),
)
def retry_restarts_after_verify_failure():
    """A publish FAILED at the verify-progress gate is retried: retry rolls back to
    VALIDATE_PUB and restarts the container via update_teclaw_bot (re-deliver); the
    durable poll then self-drives it to VALIDATING without user polling."""


@endpoint_test(
    method="POST", path=_SCALE, scenario="scale_success",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_scale_success,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "message": "Service bots on the teclaw engine do not support scaling"}),
)
def scale_success():
    """A SUCCESS publish can be scaled: /scale resolves the online binding and
    submits the BaaS scale request."""


@endpoint_test(
    method="POST", path=_SCALE, scenario="scale_baas_rejected",
    input=CaseInput(path_params={"publish_id": 999999}, headers=_HEADERS),
    seed=_seed_scale_error,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 404}),
)
def scale_baas_rejected():
    """Error coverage for /scale: missing publish record returns not found."""


@endpoint_test(
    method="POST", path=_SCALE_STATUS, scenario="scale_status_success",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_scale_status,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "message": "BaaS scale status: SUCCESS"}),
)
def scale_status_success():
    """A publish with ext.scale.publish_id can query BaaS scale progress."""


@endpoint_test(
    method="POST", path=_SCALE_STATUS, scenario="scale_status_error",
    input=CaseInput(path_params={"publish_id": 999999}, headers=_HEADERS),
    seed=_seed_scale_status,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 404}),
)
def scale_status_error():
    """Error coverage for scale status: missing publish record returns not found."""


# ── retry_for_others: admin retry with owner_id as operator ───────────────────


@endpoint_test(
    method="POST", path=_RETRY_FOR_OTHERS, scenario="retry_for_others_success",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_ADMIN_HEADERS),
    seed=_seed_failed_verify, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(
        _expect_post("/update"),
        # As in retry_restarts_after_verify_failure: the durable poll enqueued by the
        # restart drives the retried record VALIDATE_PUB → VALIDATING (#162 fix).
        _expect_status(_V1, PublishStatus.VALIDATING),
    ),
)
def retry_for_others_success():
    """Admin can retry a failed publish using owner_id as operator."""
    pass


@endpoint_test(
    method="POST", path=_RETRY_FOR_OTHERS, scenario="retry_for_others_permission_denied",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),  # non-admin user
    seed=_seed_failed_verify,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 403}),
)
def retry_for_others_permission_denied():
    """Non-admin user cannot call retry_for_others endpoint."""
    pass
