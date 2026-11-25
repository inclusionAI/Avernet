"""POST /openapi/v1/bots/copy — copy an openclaw bot to a new dst bot (Route B).

Route B (manifest-reuse): capture source's live config, **serialize it into a
manifest config document**, persist it under the dst bot's key, and hand off to
the **reused manifest-creation** flow (``manifest_seam`` → ``create_job`` →
ManifestCompiler → apply engine). No ``CopyCompiler``, no ``state_ref`` — the
dst becomes a manifest-managed bot, and ``create_job`` / ``creation.py`` /
``apply_engine`` core are untouched (zero regression risk).

**v1 scope (script only).** ``identity`` / ``resources`` are file-based and
deferred to a later **sync** stage (``BotFileSyncPort``); ``skills`` / ``mcp``
are deferred to a later **能力集** stage. So the serialized manifest carries
only ``script`` (read from source via ``BotStartupScriptServiceProtocol``).
When sync + 能力集 stages land, the serializer emits the extra sections.

**Route mounting deferred to Task 7** (DI wiring): the router + handler are
defined here but not yet included in ``openapi_v1/__init__.py``. Handler-unit
tests pass deps by keyword.
"""

from __future__ import annotations

import random
import string
from datetime import datetime
from typing import Annotated, Any, Optional, TYPE_CHECKING

import yaml
from fastapi import APIRouter, HTTPException, Path, Request
from fastapi_injector import Injected
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    ERROR_RESPONSES,
    USER_SCOPED_403,
    Envelope,
    ErrorEnvelope,
    error_example,
)
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum
from agentclaw.community.adapters.http.openapi_v1.principal import (
    DelegatedUserDep,
    OwnerNameDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.core.bot_config_manifest.creation import ManifestCreationSeam
from agentclaw.community.core.bot_management.bot_service_protocol import (
    BotServiceProtocol,
)
from agentclaw.community.core.bot_management.create_flow import (
    BotCreateContext,
    BotCreateDeploymentMode,
    BotCreateSpec,
    BotCreateTemplateValidationMode,
    creation_spec_to_payload,
)
from agentclaw.community.core.bot_copy.repository.protocol import (
    BotCopyRepositoryProtocol,
)
from agentclaw.community.core.bot_copy.status_derive import compute_copy_status
from agentclaw.community.core.bot_copy.types import BotCopyRow, BotCopyStatus
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_management.render_screen_service_protocol import (
    RenderScreenServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot.collaborator import (
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:
    # Typed only for the serializer's signature; the runtime values come from
    # DI. Quoted to avoid pulling the dataclasses at module-import time.
    from agentclaw.community.core.skill_center.skill_set_apply import (
        SkillSetApplySnapshot,
    )

#: Copy supports openclaw bots only (PRD v1); teclaw's container is composed by
#: the artifact at provision, a different mechanism from manifest-creation's
#: pre/post-container delivery.
_COPYABLE_ENGINES = {"openclaw"}

#: Source lifecycle states that block copy (PRD precondition: deploying /
#: failed / unknown). Active / prestable / offline / prestable-pending copy OK.
_NON_COPYABLE_LIFECYCLE = {"DEPLOYING", "FAILED", "UNKNOWN"}

#: Operator retry cas_retry throttle window (seconds). FE shows the retry
#: button for every copy_state except CONVERGED and may click freely; this CAS
#: is the spam guard. create_job's UNIQUE active_idempotency_key dedupes if a
#: task is still live.
_RETRY_THROTTLE_SECONDS = 60


class BotCopyRequest(BaseModel):
    """POST /openapi/v1/bots/copy body."""

    model_config = {"extra": "forbid"}

    source_bot_id: str = Field(..., description="源 bot 的 bot_id(仅 openclaw)")
    source_owner_id: str = Field(
        ...,
        description="源 bot 的 owner_id。bot_id 非全局唯一(共享 bot_id 如 "
        "`default` 每用户一行),后端按 bot_id + owner_id + env 精确定位源行,"
        "避免回取他行导致 owner 错配 403。",
    )
    bot_name: str = Field(..., description="dst bot 显示名")
    bot_desc: Optional[str] = Field(default=None, description="dst bot 描述")
    request_detail: Optional[dict[str, Any]] = Field(
        default=None,
        description='JSON: {"excludes": [...], ...未来 copy 参数}',
    )


class BotCopyResponse(BaseModel):
    """POST /copy response — success (OSS sync ISSUED) or auth_required (internal)."""

    type: str = Field(
        ...,
        description='"success" (OSS NoOp sync ISSUED) | "auth_required" (internal license PENDING)',
    )
    operation_id: str = Field(..., description="copy 操作幂等 id + FE correlation id")
    bot_id: str = Field(..., description="新建 dst bot 的 bot_id")
    iframe_url: Optional[str] = Field(
        default=None,
        description="AgentPass authorisation iframe URL (only on auth_required).",
    )
    redirect_url: Optional[str] = Field(
        default=None,
        description="AgentPass authorisation redirect URL (only on auth_required).",
    )


# The copy route's failure table. 403 is the user_id owner-mismatch the surface
# carries everywhere; 409 is the source-not-copyable refusal (engine / lifecycle
# / not found / no edit permission). 202 is a success (auth_required), not an
# error, so it carries the response model rather than ErrorEnvelope.
BOT_COPY_RESPONSES: dict[int | str, dict[str, object]] = {
    **USER_SCOPED_403,
    409: {
        "model": ErrorEnvelope,
        "description": "Source bot is not copyable — wrong engine, lifecycle "
        "blocks copy, not found, or the caller lacks edit permission.",
        **error_example(409, "Source bot not copyable"),
    },
}

# The retry route's failure table. 403 is the owner-mismatch; 404 is a bot with
# no copy operation ledger row backing it.
RETRY_RESPONSES: dict[int | str, dict[str, object]] = {
    **USER_SCOPED_403,
    404: ERROR_RESPONSES[404],
}


router = APIRouter(prefix="/openapi/v1/bots", tags=["bots"], route_class=PublicAPIRoute)


def _generate_operation_id() -> str:
    """A globally-unique operation id (mirrors `generate_bot_id`'s time+random shape)."""
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"copy_{date_part}_{random_part}"


#: The literal ``path`` the copy serializer writes on the file-sync resources
#: entry. It is a constant, not resolved from the source or dst bot: a
#: per-engine sync strategy builds the real source/target workspace paths at
#: sync time, and the manifest's job is to declare *that a workspace sync
#: happens* and *which source bot to read from*, not where the bytes live on
#: any host. The trailing slash marks it a directory tree.
_FILE_SYNC_PATH = "workspace/"


def _serialize_skill_sets(
    *,
    snapshot: "SkillSetApplySnapshot | None",
) -> list[dict[str, Any]] | None:
    """Serialize the source's capability-set state into a manifest ``skill_sets`` segment.

    Returns the list to write under ``manifest.skill_sets``, or ``None`` when the
    segment is omitted (the dst reads as a brand-new bot: default enabled, no
    ordinary sets — Spec §0 "省略段" invariant). The segment is derived solely
    from the source's ``SkillSetApplySnapshot`` — the apply-engine source of
    truth that carries every Set's members by origin (Spec §6).

    Default-enabled is decided by whether the snapshot carries a default set.
    Each ordinary Set's members are serialized by origin from the snapshot's
    ``skills`` catalog: center → ``{name, origin: center, skill_uuid}``,
    repo → ``{name, origin: repo, repo_path}``, local → ``{name, origin:
    local, from: from_bot}``, and MCP members become ``{server_code}`` rows
    (Spec §0). Local members reference the top-level ``sources.from_bot``
    named source — wave-1 opened the ``(SKILLS, BOT)`` ``support_matrix`` cell
    and wave-2's ``BotSyncedLocalSkill`` resolve reverse-looks-up the source
    bot's ``ac_skill`` row and registers the dst row without uploading (the
    physical package rides the copy's ``resources: workspace/`` file-sync).
    ``None`` is returned when the snapshot has no sets at all — the source is a
    brand-new bot and the dst inherits the same baseline.

    The function is pure: it neither reads nor writes any store. Its output is
    fed back into ``validate_capabilities`` (``skill_sets.py``) and the
    ``ManifestCompiler`` by the S1 unit tests.
    """
    return _derive_skill_sets_from_snapshot(snapshot)



def _derive_skill_sets_from_snapshot(
    snapshot: "SkillSetApplySnapshot | None",
) -> list[dict[str, Any]] | None:
    """Branch B: derive the ``skill_sets`` segment from a snapshot.

    The snapshot is the legacy Genome flow's source-of-truth. Its ``sets``
    tuple carries the ordinary sets and (optionally) one default set row. The
    default switch is the default set's presence in ``snapshot.sets``: present
    → enabled (emit ``{name: default}``), absent → disabled. Skill members are
    serialized by origin from the snapshot's ``skills`` catalog — center →
    ``{name, origin: center, skill_uuid}``, repo → ``{name, origin: repo,
    repo_path}``, local → ``{name, origin: local, from: from_bot}``, MCP →
    ``{server_code}``. Local members reference the top-level ``sources.from_bot``
    named source that ``_build_manifest_document`` emits for the file-sync
    entry — wave-1 opened the ``(SKILLS, BOT)`` ``support_matrix`` cell so
    ``validate_document`` accepts that declaration, and wave-2's
    ``BotSyncedLocalSkill`` resolve reverse-looks-up the source bot's
    ``ac_skill`` row and registers the dst row without uploading (the
    physical package rides the copy's ``resources: workspace/`` file-sync).

    ``None`` is returned when the snapshot has no sets at all — the source is
    a brand-new bot and the dst inherits the same baseline (Spec §0).
    """
    if snapshot is None or not snapshot.sets:
        return None
    catalog = {int(item["id"]): item for item in snapshot.skills}
    by_uuid = {
        item["skill_uuid"]: item
        for item in snapshot.skills
        if item.get("skill_uuid")
    }
    default_present = any(item.is_default for item in snapshot.sets)
    result: list[dict[str, Any]] = []
    if default_present:
        # Spec §0 — the default entry carries only ``{name}``. Its presence is
        # the dst's default switch; listing its members would be rejected by
        # ``validate_capabilities`` (``unknown_field``).
        result.append({"name": "default"})
    for group in snapshot.sets:
        if group.is_default:
            continue
        skills: list[dict[str, Any]] = []
        references = dict(group.skill_references)
        for raw_id in group.skill_ids:
            uuid = references.get(raw_id)
            asset = by_uuid.get(uuid) if uuid else catalog.get(raw_id)
            if asset is None:
                # The snapshot is the live source of truth; a member id with no
                # backing asset is a legacy row no longer resolvable. Skipping
                # keeps the copy from failing on a stale reference the source
                # itself no longer resolves — the dst's apply will report a
                # ``ResolveFailure`` when center/repo is re-resolved.
                continue
            skill_entry: dict[str, Any] = {"name": asset.get("name")}
            git_path = str(asset.get("git_path", "") or "")
            if git_path.startswith("center://"):
                skill_entry["origin"] = "center"
                skill_entry["skill_uuid"] = asset.get("skill_uuid")
            elif git_path.startswith("local://"):
                # Spec §1.4 — local skill members reference the top-level
                # ``sources.from_bot`` named source that ``_build_manifest_document``
                # emits for the file-sync entry. Wave-1 opened
                # ``support_matrix._VERDICTS`` ``(SKILLS, BOT)`` (and
                # ``(CAPABILITIES, BOT)``) so ``validate_skill_entry``'s
                # ``resolve_source`` accepts the declaration; wave-2's
                # ``BotSyncedLocalSkill`` resolve reverse-looks-up the source
                # bot's ``ac_skill`` row (name/description/digest) and
                # recomputes the dst git_path without uploading — the package
                # arrives via the ``resources: workspace/`` file-sync. Name is
                # taken verbatim from the snapshot asset row (the same field
                # the source declared), and ``from: from_bot`` is the literal
                # named-source key the document carries.
                skill_entry["origin"] = "local"
                skill_entry["from"] = "from_bot"
            else:
                # Git-sourced skills store their location as ``git://<scope>/<path>``
                # (e.g. ``git://infra/todo-runner``): a logical path the platform's
                # registry resolves, not a fetchable URL. The manifest's
                # ``repo_path`` is that bare path — ``RepoSkillPath`` accepts only
                # the ``[A-Za-z0-9._/-]`` vocabulary — so the ``git://`` scheme
                # prefix is stripped here. The scope (``business``/``infra``/
                # ``third-party``) stays as the leading path segment. The dst's
                # resolve closes the loop symmetrically: ``find_repo_skill`` re-prefices
                # ``git://`` onto ``path`` before the ``get_by_git_path`` lookup
                # (``skill_query_service.py``), so the bare path round-trips back to
                # the same registry row. The pre-strip behaviour was a double fault:
                # ``invalid_path`` at schema validation on the ``:``, and a
                # ``git://git://...`` double prefix at dst resolve.
                skill_entry["origin"] = "repo"
                skill_entry["repo_path"] = (
                    git_path[len("git://"):] if git_path.startswith("git://") else git_path
                )
            skills.append(skill_entry)
        mcp_entries = [{"server_code": code} for code in group.mcp_codes]
        row: dict[str, Any] = {
            "name": group.name,
            "is_active": bool(group.is_active),
            "skills": skills,
            "mcp": mcp_entries,
        }
        if group.description:
            row["description"] = group.description
        result.append(row)
    # Spec §0 Q1 row "default 关闭且无普通 Set" — the snapshot carried no
    # default set and no ordinary set; unreachable here (``not snapshot.sets``
    # returns ``None`` above), but the branch-B fallback does not by itself
    # express "explicit empty", so the dst reads as brand-new on a truly empty
    # snapshot. That matches Spec §2 Q1's brand-new-source row.
    return result


def _build_manifest_document(
    *,
    script_body: str,
    source_bot_id: str,
    source_owner_id: str,
    excludes: tuple[str, ...] = (),
    render_screen_rows: tuple[dict[str, str], ...] = (),
    skill_sets: list[dict[str, Any]] | None = None,
) -> str:
    """Serialize source's config into a manifest config document.

    The document is what ``manifest_seam.persist`` stores under the dst bot's
    key and what ManifestCompiler compiles. v1 carried ``script`` only; this
    adds the file-sync section: a top-level ``sources.from_bot`` declaring
    ``protocol: bot`` + the source bot's addressing (``bot_id`` and
    ``owner_id`` — a ``bot_id`` is not globally unique, so ``owner_id``
    disambiguates the source row the sync reads), and one ``resources`` entry
    that uses it with the literal ``workspace/`` path. ``excludes`` is the
    same list the caller sent under ``request_detail`` — written into the
    manifest entry so the compiler and a future sync materialiser read one
    source of truth rather than reaching back across modules for the
    operation ledger.

    ``render_screen_rows`` carries the source bot's render-screen set as the
    v1 ``bot_config.render_screen`` kind. Each row is the ``(name, cdn_url)``
    pair the RenderScreenService writes, serialised verbatim — an empty tuple
    omits the ``render_screen`` key, so a source with no screens leaves that
    kind undeclared (untouched) rather than as an overwrite-to-empty.

    ``bot_config.task_nav`` is **always emitted** (one entry, ``type: "bot"``
    referencing the source bot) so every copy converges the destination's
    workflow-permission grants from the source — the clawweb clone endpoint
    handles a source with no grants gracefully (``no_source_grants``), so
    always-emit needs no probe; the handler is fire-and-log and never aborts
    the copy on the clone outcome.

    ``skill_sets`` is the S1 payload (Spec §6): either the list to write as
    ``manifest.skill_sets`` or ``None`` to omit the segment (the dst reads as
    a brand-new bot per Spec §0). Branch A (source has a manifest) passes the
    source segment through verbatim; branch B (legacy Genome bot) derives it
    from a ``SkillSetApplySnapshot``. The handler computes the list via
    ``_serialize_skill_sets`` and passes it in here; the serializer itself
    never reads the source's stores, so this function stays pure and testable.
    """
    resources_entry: dict[str, Any] = {"path": _FILE_SYNC_PATH, "from": "from_bot"}
    if excludes:
        resources_entry["excludes"] = list(excludes)
    bot_config: dict[str, Any] = {
        "task_nav": [
            {
                "type": "bot",
                "source_bot_id": source_bot_id,
                "source_owner_id": source_owner_id,
            }
        ],
    }
    if render_screen_rows:
        bot_config["render_screen"] = [dict(row) for row in render_screen_rows]
    manifest: dict[str, Any] = {
        "resources": [resources_entry],
        "bot_config": bot_config,
    }
    if skill_sets is not None:
        manifest["skill_sets"] = skill_sets
    sources: dict[str, Any] = {
        "from_bot": {
            "protocol": "bot",
            "bot_id": source_bot_id,
            "owner_id": source_owner_id,
        },
    }
    document: dict[str, Any] = {
        "schema_version": 1,
        "script": {"body": script_body},
        "sources": sources,
        "manifest": manifest,
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def submit_copy(
    *,
    body: BotCopyRequest,
    owner_id: str,
    owner_name: str,
    bot_repo: BotRepository,
    bot_copy_repo: BotCopyRepositoryProtocol,
    bot_script_service: BotStartupScriptServiceProtocol,
    manifest_seam: ManifestCreationSeam,
    passport_plugin: PassportPlugin,
    space_context: BusinessSpaceContextProtocol,
    render_screen_service: RenderScreenServiceProtocol,
    bot_service: BotServiceProtocol,
    skill_set_service: SkillSetManagementServiceProtocol,
    collaborator_repository: CollaboratorRepositoryProtocol,
) -> BotCopyResponse:
    """Validate source + serialize manifest + persist + apply Passport + hand off.

    Route B: the manifest document is built from source's live config (v1:
    script only), persisted under the dst bot's key, and the reused
    manifest-creation flow (``manifest_seam.start_job`` → ``create_job``) drives
    apply + provision. ``creation.py`` / ``create_job`` are untouched.
    """
    del owner_name
    # bot_id 非全局唯一(共享 bot_id 如 `default` 每用户一行):裸 get_by_id 的
    # .first() 会回取环境内任一 live 行,owner_id 错配 → 误判 no-edit-permission 403。
    # 按 bot_id + source_owner_id + env 精确定位源行(BotRepository.get_by_id_and_owner,
    # 自带 is_delete/env 过滤)。source_owner_id 由前端从listing 的源 bot owner 透传,
    # 是源 bot 的真实 owner(非调用者),故非 owner 的 edit 协作者复制也能命中正确源行。
    source = bot_repo.get_by_id_and_owner(body.source_bot_id, body.source_owner_id)
    if source is None:
        raise HTTPException(
            status_code=409, detail=f"source bot {body.source_bot_id} not found"
        )
    engine = source.get("active_engine") or source.get("engine_type")
    if engine not in _COPYABLE_ENGINES:
        raise HTTPException(
            status_code=409,
            detail=f"copy supports openclaw bots only; source engine={engine}",
        )
    if source.get("status") in _NON_COPYABLE_LIFECYCLE:
        raise HTTPException(
            status_code=409,
            detail=f"source bot status {source.get('status')} not copyable",
        )
    # Access gate (shared with the manifest ``from_bot`` source check in
    # ``source_bot_access.assert_from_bot_source_access``): the caller passes as
    # the source Bot's owner, or as a collaborator on it (any role —
    # ``ac_bot_collaborator`` row keyed by the source's ``ac_bots.id``). The
    # earlier ``"edit" not in actions`` test was unreachable — ``bot_repo``'s row
    # has no ``actions`` column (actions are listing-time, per-caller), so only
    # owners ever passed; explicit collaborators were wrongly refused.
    if source.get("owner_id") != owner_id and (
        collaborator_repository.get_by_bot_and_user(
            int(source["id"]), owner_id, get_current_env()
        )
        is None
    ):
        raise HTTPException(status_code=403, detail="no edit permission on source bot")

    operation_id = _generate_operation_id()
    # Lazy import: bot_service sits in a circular edge with workspace_service
    # that only post-top-import ordering resolves; importing at module top can
    # trip it when this module is imported lazily inside openapi_v1's router
    # composition.
    from agentclaw.community.core.bot_management.services.bot_service import (
        generate_bot_id,
    )

    dst_bot_id = generate_bot_id(owner_id, bot_repo)

    # Copy is same-space only: the dst lands in the source's business space, not
    # the caller's personal default. Routing ``source.space_id`` through
    # ``resolve_current`` (the same resolver the plain create route uses for its
    # ``body.space_id``) gives both the membership enforcement and the quota
    # scope split for free — a team ``space_id`` resolves to a team scope (the
    # caller must be a member, else ``_require_member_space`` raises), and a
    # NULL/``personal:<owner>`` value resolves to the caller's personal space.
    # The resolved ``space_id`` then flows into the quota preflight below and the
    # durable creation spec, so ``create_job``'s downstream ``create_bot`` →
    # ``guard_add`` re-evaluates quota in the *same* scope, never silently
    # re-routing a team copy back to the owner's personal ceiling.
    current_space = space_context.resolve_current(
        owner_id=owner_id, header_space_id=source.get("space_id")
    )
    space_id = getattr(current_space, "numeric_id", None)

    # Pre-flight platform validation (quota / reserved-engine / name) before
    # Passport or any manifest is spent — the same call ordinary creation makes
    # ahead of its passport step. Copy reuses the manifest-creation flow, whose
    # ``create_job`` also enforces quota, but only *after* Passport auth and the
    # dst ``ac_bots`` row are written: an over-quota copy would otherwise send
    # the owner through AgentPassport authorization, then FAIL with no dst row
    # for FE's detail poll to land on (60s timeout + a misleading message).
    # Branching the check here closes that window for the same engine/name
    # validations create runs. Mirrors ``create_flow._create``'s placement of
    # ``check_create_bot_preflight`` ahead of ``_apply_passport``.
    bot_service.check_create_bot_preflight(
        user_id=owner_id,
        bot_id=dst_bot_id,
        engine_type=engine,
        bot_name=body.bot_name,
        space_id=space_id,
        space_quota=True,
    )

    # Route B: serialize source's config into a manifest document (v1: script only).
    source_owner_id = source.get("owner_id") or owner_id
    source_entity_id = source.get("entity_id") or source_owner_id
    script_body = bot_script_service.get_body(
        entity_id=source_entity_id, bot_id=source["bot_id"]
    )
    # ``excludes`` rides both the operation ledger (``request_detail``, so retry
    # and audit see the caller's intent) and this manifest document (so the
    # compiler reads one source of truth rather than reaching across modules
    # for the ledger row). They are the same value under two roles.
    request_excludes = tuple((body.request_detail or {}).get("excludes") or ())
    # Source's render-screen set → the v1 ``bot_config.render_screen`` kind.
    # ``None`` owner reads every non-deleted row under the source bot (scope
    # ``bot`` strips the caller's owner filter; scope ``owner`` is keyed by the
    # source's own row). Empty → the kind is omitted, so a screenless source
    # produces a document the validator/compiler treats as undeclared bot_config.
    source_render_screens = tuple(
        {"name": r.name, "cdn_url": r.cdn_url}
        for r in render_screen_service.list_render_screens(
            bot_id=source["bot_id"],
            owner_id=source_owner_id,
            current_user_id=owner_id,
        )
    )
    # Spec §6 S2 — read the source's capability-set state and serialize it
    # into the dst manifest's ``skill_sets`` segment. The snapshot is the
    # apply-engine source of truth; it covers manifest-managed and legacy
    # Genome bots alike. ``read_apply_snapshot`` takes ``actor_id``; the caller
    # (``owner_id``) is the actor — the source.edit gate above already verified
    # the caller has edit permission on the source bot, and the snapshot read
    # is read-only with its own scope checks inside.
    # ``manifest_seam.preflight`` below runs the document through
    # ``validate_document``, which exercises ``validate_capabilities`` and
    # refuses any malformed segment — so an unrepresentable source state is a
    # 4xx at preflight, not silently stored.
    source_snapshot = skill_set_service.read_apply_snapshot(
        bot_id=source["bot_id"],
        owner_id=source_owner_id,
        actor_id=owner_id,
    )
    skill_sets = _serialize_skill_sets(snapshot=source_snapshot)
    manifest_document = _build_manifest_document(
        script_body=script_body,
        source_bot_id=source["bot_id"],
        source_owner_id=source_owner_id,
        excludes=request_excludes,
        render_screen_rows=source_render_screens,
        skill_sets=skill_sets,
    )

    # Validate the document before anything is spent (manifest-creation's rule:
    # an invalid doc never reaches Passport). Raises ManifestValidationError.
    # engine_type is the source's live engine (validated ∈ _COPYABLE_ENGINES
    # above), not a hardcoded literal — copy preserves whatever the source runs.
    manifest_seam.preflight(
        document=manifest_document,
        engine_type=engine,
        bot_type=source.get("bot_type") or "personal",
    )
    # Persist under the dst bot's key — ManifestCompiler reads this at apply time.
    manifest_seam.persist(
        spec_entity_id=owner_id,
        bot_id=dst_bot_id,
        document=manifest_document,
        modifier=owner_id,
        engine_type=engine,
        bot_type=source.get("bot_type") or "personal",
    )

    # Operation ledger: FE correlation id + source/dst link + excludes. Written
    # after the manifest is stored so a persist failure leaves no orphan row.
    bot_copy_repo.insert(
        BotCopyRow(
            operation_id=operation_id,
            source_bot_id=source["bot_id"],
            source_owner_id=source_owner_id,
            dst_bot_id=dst_bot_id,
            dst_owner_id=owner_id,
            operator_id=owner_id,
            status=BotCopyStatus.INITIAL,
            request_detail=body.request_detail,
            env=get_current_env(),
            avernet_tenant=get_current_avernet_tenant(),
        )
    )

    # Route B reuses the manifest-creation flow, whose deserializer
    # (`creation_spec_from_payload`) requires a complete creation payload — not
    # just the bot fields, but the context block too (deployment_mode/space_kind/
    # space_quota). Build it via the canonical `creation_spec_to_payload(spec,
    # context)` so every strict key is present and compliant: copy is a public
    # OpenAPI surface, so PUBLIC validation mode + CLOUD deployment, mirroring
    # `submit_manifest_creation`. Copy-specific extras (request_detail /
    # source_bot_id) ride along — the deserializer ignores unknown keys.
    payload_spec: dict[str, Any] = {
        **creation_spec_to_payload(
            BotCreateSpec(
                entity_id=owner_id,
                engine_type=engine,
                bot_type=source.get("bot_type") or "personal",
                bot_name=body.bot_name,
                bot_desc=body.bot_desc,
                space_id=space_id,
                template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
            ),
            BotCreateContext(
                deployment_mode=BotCreateDeploymentMode.CLOUD,
                space_kind=current_space.kind,
                space_quota=True,
            ),
        ),
        "request_detail": body.request_detail,
        "source_bot_id": source["bot_id"],
    }

    passport_result = passport_plugin.apply_agent_passport(
        bot_id=dst_bot_id,
        owner_workno=owner_id,
        mcp_codes=[],
        bot_name=body.bot_name,
        bot_desc=body.bot_desc,
        engine_type=engine,
        bot_type=str(source.get("bot_type") or "personal"),
    )
    iframe_url = (passport_result or {}).get("iframe_url")
    redirect_url = (passport_result or {}).get("redirect_url")
    token = (passport_result or {}).get("token")
    if not token and not iframe_url and not redirect_url:
        raise HTTPException(
            status_code=500,
            detail="passport returned no token and no authorization url",
        )

    manifest_seam.start_job(
        bot_id=dst_bot_id,
        entity_id=owner_id,
        user_id=owner_id,
        document_owner=owner_id,
        spec=payload_spec,
        iframe_url=iframe_url,
        redirect_url=redirect_url,
    )

    if token:
        return BotCopyResponse(
            type="success", operation_id=operation_id, bot_id=dst_bot_id
        )
    return BotCopyResponse(
        type="auth_required",
        operation_id=operation_id,
        bot_id=dst_bot_id,
        iframe_url=iframe_url,
        redirect_url=redirect_url,
    )


class RetryResult(_DocumentedEnum):
    """The retry endpoint's outcome."""

    RE_ENQUEUED = "re_enqueued"
    STILL_RUNNING = "still_running"
    TERMINAL = "terminal"

    __descriptions__ = {
        "re_enqueued": "Copy re-handed off to create_job; FE should refresh and follow copy_state.",
        "still_running": "Within the 60s retry throttle; the caller should wait and retry.",
        "terminal": "The copy has converged; no retry is needed.",
    }


class RetryResponse(BaseModel):
    """The retry endpoint's body: the outcome + the operation_id for FE context."""

    operation_id: str = Field(..., description="the copy operation retried")
    result: RetryResult = Field(..., description="the retry outcome")


def retry_copy(
    *,
    bot_id: str,
    operator_id: str,
    bot_copy_repo: BotCopyRepositoryProtocol,
    apply_repo: BotConfigManifestApplyRepositoryProtocol,
    bot_repo: BotRepository,
    manifest_seam: ManifestCreationSeam,
    space_context: BusinessSpaceContextProtocol,
    now: datetime,
) -> RetryResult:
    """Operator-triggered retry of the copy operation whose dst is ``bot_id``.

    The manifest document was persisted at first submission under the dst bot's
    key; retry re-hands-off to ``create_job`` via ``manifest_seam.start_job``
    with the same payload spec. ``create_job`` is idempotent on ``bot_id``
    (``complete_bot_authorization`` returns the existing row; ``apply_pre_container``
    checks existing apply records; ``active_idempotency_key`` UNIQUE dedupes the
    task). FE shows retry for every ``copy_state`` except CONVERGED.
    """
    del operator_id
    row = bot_copy_repo.get_by_dst_bot_id(env=get_current_env(), dst_bot_id=bot_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"copy operation for bot {bot_id} not found"
        )

    applies = apply_repo.recent(
        env=row.env, entity_id=row.dst_owner_id, bot_id=row.dst_bot_id, limit=10
    )
    bot = bot_repo.get_by_id(row.dst_bot_id)
    bot_status = (bot or {}).get("status")
    derived = compute_copy_status(
        copy_row=row, dst_apply_records=applies, dst_bot_status=bot_status
    )
    if derived is BotCopyStatus.CONVERGED:
        return RetryResult.TERMINAL

    if not bot_copy_repo.cas_retry(
        env=row.env,
        operation_id=row.operation_id,
        now=now,
        throttle_seconds=_RETRY_THROTTLE_SECONDS,
    ):
        return RetryResult.STILL_RUNNING

    # Re-derive the payload spec from the dst bot's own record — copy preserves
    # the source's engine/type at create time, so the dst already carries them.
    # No hardcoded literals; if the dst record is partial (first submit failed
    # before persist), the missing fields stay None until a later stage fills them.
    dst = bot or {}
    # Same canonical `creation_spec_to_payload(spec, context)` path as
    # submit_copy: re-resolve the creation context from the dst owner's space so
    # the durable payload carries every strict key the deserializer reads
    # (entity_id/engine_type/bot_type/template_validation_mode +
    # deployment_mode/space_kind/space_quota). The dst's own record supplies the
    # bot fields; PUBLIC + CLOUD mirror the public OpenAPI surface. Copy is
    # openclaw-only, so a missing engine on a still-partial dst falls back to
    # openclaw rather than a None BotCreateSpec.engine_type would reject.
    # ``header_space_id`` is the dst's recorded space_id (not None) so a retry of
    # a copy-into-team-space preserves the team scope/space_kind — falling back
    # to None here would re-resolve the owner's personal space and re-route the
    # downstream ``create_bot`` → ``guard_add`` quota check away from the
    # original target. A missing/NULL dst space_id keeps the personal fallback
    # (matches submit_copy's personal branch).
    dst_space_id = dst.get("space_id")
    retry_space = space_context.resolve_current(
        owner_id=row.dst_owner_id,
        header_space_id=str(dst_space_id) if dst_space_id is not None else None,
    )
    payload_spec: dict[str, Any] = {
        **creation_spec_to_payload(
            BotCreateSpec(
                entity_id=row.dst_owner_id,
                engine_type=dst.get("active_engine") or dst.get("engine_type") or "openclaw",
                bot_type=dst.get("bot_type") or "personal",
                bot_name=dst.get("bot_name"),
                bot_desc=dst.get("bot_desc"),
                space_id=dst.get("space_id"),
                template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
            ),
            BotCreateContext(
                deployment_mode=BotCreateDeploymentMode.CLOUD,
                space_kind=retry_space.kind,
                space_quota=True,
            ),
        ),
        "request_detail": row.request_detail,
        "source_bot_id": row.source_bot_id,
    }
    manifest_seam.start_job(
        bot_id=row.dst_bot_id,
        entity_id=row.dst_owner_id,
        user_id=row.operator_id,
        document_owner=row.operator_id,
        spec=payload_spec,
        iframe_url=None,
        redirect_url=None,
    )
    return RetryResult.RE_ENQUEUED


@router.post(
    "/copy",
    status_code=200,
    response_model=Envelope[BotCopyResponse],
    responses={
        202: {"description": "Needs user authorization (internal license gate)"},
        **BOT_COPY_RESPONSES,
    },
)
@envelope_errors
async def copy_bot(
    body: BotCopyRequest,
    request: Request,
    owner_id: UserIdDep,
    owner_name: OwnerNameDep,
    caller: DelegatedUserDep,
    bot_repo: BotRepository = Injected(BotRepository),
    bot_copy_repo: BotCopyRepositoryProtocol = Injected(BotCopyRepositoryProtocol),
    bot_script_service: BotStartupScriptServiceProtocol = Injected(
        BotStartupScriptServiceProtocol
    ),
    manifest_seam: ManifestCreationSeam = Injected(ManifestCreationSeam),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    space_context: BusinessSpaceContextProtocol = Injected(BusinessSpaceContextProtocol),
    render_screen_service: RenderScreenServiceProtocol = Injected(
        RenderScreenServiceProtocol
    ),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    skill_set_service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
    collaborator_repository: CollaboratorRepositoryProtocol = Injected(
        CollaboratorRepositoryProtocol
    ),
) -> Envelope[BotCopyResponse]:
    """Copy an openclaw bot to a new dst bot (v1, Route B manifest-reuse)."""
    del caller
    result = submit_copy(
        body=body,
        owner_id=owner_id,
        owner_name=owner_name,
        bot_repo=bot_repo,
        bot_copy_repo=bot_copy_repo,
        bot_script_service=bot_script_service,
        manifest_seam=manifest_seam,
        passport_plugin=passport_plugin,
        space_context=space_context,
        render_screen_service=render_screen_service,
        bot_service=bot_service,
        skill_set_service=skill_set_service,
        collaborator_repository=collaborator_repository,
    )
    return envelope(result, request)


@router.post(
    "/copy/{bot_id}/retry",
    status_code=200,
    response_model=Envelope[RetryResponse],
    responses=RETRY_RESPONSES,
)
@envelope_errors
async def retry_copy_bot(
    bot_id: Annotated[str, Path(
        description="The dst bot whose copy_state FE is polling; the backend "
        "resolves it to the operation ledger row.",
    )],
    request: Request,
    owner_id: UserIdDep,
    caller: DelegatedUserDep,
    bot_copy_repo: BotCopyRepositoryProtocol = Injected(BotCopyRepositoryProtocol),
    apply_repo: BotConfigManifestApplyRepositoryProtocol = Injected(
        BotConfigManifestApplyRepositoryProtocol
    ),
    bot_repo: BotRepository = Injected(BotRepository),
    manifest_seam: ManifestCreationSeam = Injected(ManifestCreationSeam),
    space_context: BusinessSpaceContextProtocol = Injected(BusinessSpaceContextProtocol),
) -> Envelope[RetryResponse]:
    """Re-hand-off a copy operation to create_job (operator-triggered retry).

    The path's bot_id is the dst bot (the one FE is polling copy_state on); the
    backend resolves it to the operation ledger row.
    """
    del caller
    result = retry_copy(
        bot_id=bot_id,
        operator_id=owner_id,
        bot_copy_repo=bot_copy_repo,
        apply_repo=apply_repo,
        bot_repo=bot_repo,
        manifest_seam=manifest_seam,
        space_context=space_context,
        now=datetime.now(),
    )
    # Re-fetch the row to surface the operation_id in the response (retry_copy
    # returned the result enum only, not the row).
    row = bot_copy_repo.get_by_dst_bot_id(env=get_current_env(), dst_bot_id=bot_id)
    return envelope(
        RetryResponse(
            operation_id=row.operation_id if row else "", result=result
        ),
        request,
    )


__all__ = [
    "BotCopyRequest",
    "BotCopyResponse",
    "RetryResponse",
    "RetryResult",
    "retry_copy",
    "router",
    "submit_copy",
    "_serialize_skill_sets",
]
