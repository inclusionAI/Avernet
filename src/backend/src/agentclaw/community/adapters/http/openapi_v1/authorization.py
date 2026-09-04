"""Who may do what to a bot, for every operation on this surface.

One table, and it is the **only** place an operation's collaborator
authorization is declared. A handler declares nothing; the route class below
reads this table and attaches what the row calls for. That is a deliberate
reversal of the convention its neighbour ``admission.py`` follows, where each
route also names its own dependency and a test holds the two in step. The
reversal buys one thing: a single source. There is no second declaration to
drift, and no way for a route to disagree with the table because a route has
nothing to say.

**Omission is not survivable**: ``PublicAPIRoute`` rejects an absent operation
while its module imports, so a missing row never becomes "no check needed".
A CI assertion catches the same mistake one step later.

Two modes are permanent
-----------------------

- :class:`Check` — verify the caller's level on the bot the operation
  addresses. The level is a parameter rather than a further mode because the
  bars genuinely differ per operation: MEMBER to drive a bot's sessions, ADMIN
  to write a channel, OWNER to restart a container or delete the bot.
- :class:`NoCheck` — nothing to verify, and the reason says which kind of
  nothing. Either the operation addresses no bot (a name check, the
  marketplace, the caller's own identity), or it is bot-scoped and
  intentionally unguarded. The second really exists — render-screen reads serve
  share viewers who hold no Editor relation — and without a written reason a
  reviewer cannot tell it from an oversight.

Three modes are scaffolding
---------------------------

They exist only while operations are on their way to one of the two above, and
each is deleted when its last row leaves it:

- :class:`ServiceChecked` — a service still enforces this, somewhere else.
  → becomes ``Check(level)`` when that group migrates.
- :data:`OWNER_SCOPED` — no collaborator dimension has been decided; the
  operation resolves the bot as ``(bot_id, caller)``, so only the owner reaches
  it. → becomes ``Check(level)`` when #906 / #907 decide the bar.
- :data:`INHERITED` — a retiring address under ``deprecated/``, which is the
  replacement's own endpoint function re-registered at the path it used to
  have. It holds no decision: whatever governs the address that replaced it
  governs this one. → disappears with that package.

:func:`scaffolding_row_count` reports how many rows are still in them, so the
migration's remaining distance is a number rather than an impression.

**The levels on ``ServiceChecked`` rows are recorded, not enforced.** They were
read off the modules they cite, and nothing here can prove them: the inventory
test checks that the citation resolves to a module performing a permission
check, which is not the same as checking the number. Re-read the cited module
when you migrate a row; do not trust this column to be the whole truth.

Two things stay out of this table on purpose. Bot-*type* gating
(``SUPPORTED_BOT_TYPES``, answered 501) is a capability question, not an
authorization one. And whether a *machine* caller is admitted at all is
``admission.py``'s question, with its own seam and its own dependencies.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Annotated, Any, Callable, get_args, get_origin, get_type_hints

from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.adapters.http.openapi_v1.converter_human_chat_policy import authorization_rows


class _EditLock:
    """Require the caller to hold the Bot edit lock after authorization."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "EDIT_LOCK"


EDIT_LOCK = _EditLock()


@dataclass(frozen=True)
class Check:
    """**The seam enforces this**, at ``level`` or above; OWNER always passes.

    Getting the level wrong here refuses callers the surface means to admit, or
    admits callers it means to refuse — this is the enforcing column, unlike
    :class:`ServiceChecked`'s.
    """

    level: PermissionLevel
    edit_lock: _EditLock | None = None

    def __post_init__(self) -> None:
        """Refuse ``NONE``, which would be a gate that never refuses.

        ``_level`` returns ``NONE`` for every unresolvable case — absent bot,
        unreadable collaborator table, unwired injector — and the gate compares
        ``level < rule.level``. With ``NONE`` as the bar that comparison is
        false for exactly those cases, so a one-word typo in the table would
        turn the fail-closed gate into one that admits precisely the callers it
        exists to stop. Rejected at construction rather than left to a test,
        because the table is a literal: this raises while the module imports.
        """
        if self.level is PermissionLevel.NONE:
            raise ValueError(
                "Check(PermissionLevel.NONE) is not a bar — it admits every "
                "caller the gate would otherwise refuse. Name the level the "
                "operation actually requires."
            )
        if self.edit_lock not in (None, EDIT_LOCK):
            raise ValueError(
                "Check's second argument must be EDIT_LOCK when the operation "
                "requires the Bot edit lock."
            )


@dataclass(frozen=True)
class NoCheck:
    """**Nothing to verify**, deliberately. ``reason`` says which kind.

    Required rather than optional: an empty reason turns a decision into an
    oversight that reads exactly like a decision.
    """

    reason: str


@dataclass(frozen=True)
class ServiceChecked:
    """Scaffolding: a service enforces this, elsewhere. → ``Check(level)``.

    The row exists because every operation must have one — this is how an
    operation says "I am covered, just not here" — and it records the bar to
    preserve so the migration is a comparison rather than a guess. ``where`` is
    an abbreviated module path (``…`` for the package prefix), resolved by the
    inventory test.
    """

    level: PermissionLevel
    where: str


class _Scaffold:
    """A mode with nothing to parameterise, named for readable failures."""

    __slots__ = ("_name", "__weakref__")

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return self._name


#: Scaffolding: no collaborator dimension decided. The operation resolves the
#: bot as ``(bot_id, caller)``, so only the owner reaches it at all. Becomes
#: ``Check(level)`` once #906 / #907 decide the bar — which is a policy change,
#: not a mechanical migration: collaborators start getting through.
OWNER_SCOPED = _Scaffold("OWNER_SCOPED")

#: Scaffolding: a retiring address under ``deprecated/``. ``_relocate`` and
#: ``_requery`` re-register the *replacement's own endpoint function* at the old
#: path, so there is no second handler here and no second decision to record:
#: the row that governs one of these is the replacement's row. The whole set
#: disappears when the package is deleted.
#:
#: Named ``SELF_CHECKED`` until it was pointed out that nothing here checks
#: itself — the name asserted a property no legacy route has, and collided with
#: ``deprecated``'s unrelated ``SELF_CHECKED_ROUTES``, which really does mean a
#: router that performs its own grant check.
INHERITED = _Scaffold("INHERITED")

#: The modes that must be empty for the surface to have reached its final shape.
#: ``_Scaffold`` covers both sentinels, so a fourth scaffolding sentinel added
#: later is counted by :func:`scaffolding_row_count` without touching this.
SCAFFOLDING_MODES = (ServiceChecked, _Scaffold)

Authorization = Check | NoCheck | ServiceChecked | _Scaffold

#: Every operation on this surface, exactly once, keyed as ``ADMISSION`` is.
#:
#: No row is :class:`Check` yet: this change builds the seam, and moving each
#: group onto it is its own session (``spec.md`` *Decisions* 4).
AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    ("POST", "/openapi/v1/bots/metadata/queries"):
        NoCheck("tenant-wide display metadata for caller-supplied known bot ids"),
    # ── Bot-scoped operations ─────────────────────────────────────────────
    ("DELETE", "/openapi/v1/bots/{bot_id}"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}"): OWNER_SCOPED,
    ("PUT", "/openapi/v1/bots/{bot_id}"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/activate"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/approvals/mode"): Check(PermissionLevel.MEMBER),
    ("PUT", "/openapi/v1/bots/{bot_id}/approvals/mode"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/approvals/modes"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/auth-status"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/authorized-apps"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.authorized_apps.router"),
    ("POST", "/openapi/v1/bots/{bot_id}/authorized-apps"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.authorized_apps.router"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/authorized-apps/{app_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.authorized_apps.router"),
    ("POST", "/openapi/v1/bots/{bot_id}/iam-token"): OWNER_SCOPED,
    # Config manifest. Read at MEMBER, write at ADMIN — the same split the
    # channels rows above make, and for the same reason: reading how a bot is
    # configured is part of working on it, while replacing that configuration
    # decides what the bot is. There is no reason a collaborator who may read a
    # bot's channels may not read its manifest, which is why these are not
    # OWNER_SCOPED like the startup script beside them.
    #
    # No EDIT_LOCK. The lock asks who holds the Bot's *draft*, and a manifest is
    # not drafted — one `PUT` replaces the whole document, and the row it lands
    # on is guarded by its own uniqueness key rather than by a lease.
    ("GET", "/openapi/v1/bots/{bot_id}/config-manifest"): Check(PermissionLevel.MEMBER),
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/config-manifest/capabilities",
    ): Check(PermissionLevel.MEMBER),
    ("PUT", "/openapi/v1/bots/{bot_id}/config-manifest"): Check(PermissionLevel.ADMIN),
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/config-manifest",
    ): Check(PermissionLevel.ADMIN),
    # Apply takes its own bar rather than the document group's: it rewrites a
    # bot's whole configuration, which is owner-level on its own terms, and it
    # is a broad mutation so it carries the lock. Decided on apply's shape, not
    # derived from the categories it touches. Three of the six are owner-only
    # through their own endpoints and a manifest must not be the way around
    # them — held per category by the dominance test in
    # ``test_config_manifest_apply_bars.py``, which is also where the full
    # reasoning lives (W10's spec, *Apply Declares Its Own Bars*).
    ("POST", "/openapi/v1/bots/{bot_id}/config-manifest/apply"):
        Check(PermissionLevel.OWNER, EDIT_LOCK),
    # The reads sit at MEMBER beside GET .../config-manifest: reading how a bot
    # is configured is part of working on it, and a report carries no secret.
    ("GET", "/openapi/v1/bots/{bot_id}/config-manifest/applies/{apply_id}"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/config-manifest/last-apply"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/channels"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/channels"): Check(PermissionLevel.ADMIN, EDIT_LOCK),
    ("DELETE", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"): Check(PermissionLevel.ADMIN, EDIT_LOCK),
    ("GET", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"): Check(PermissionLevel.MEMBER),
    ("PATCH", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"): Check(PermissionLevel.ADMIN, EDIT_LOCK),
    ("PUT", "/openapi/v1/bots/{bot_id}/channels/{channel_id}/status"): Check(PermissionLevel.ADMIN, EDIT_LOCK),
    ("GET", "/openapi/v1/bots/{bot_id}/caller-context"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/chats"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.bot_chat.service"),
    ("GET", "/openapi/v1/bots/{bot_id}/chats/{trace_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.bot_chat.service"),
    ("GET", "/openapi/v1/bots/{bot_id}/connection"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.engine_runtime.connection"),
    ("GET", "/openapi/v1/bots/{bot_id}/containers"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/containers/{instance_id}/restart"): Check(PermissionLevel.OWNER),
    ("GET", "/openapi/v1/bots/{bot_id}/data-init"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/data-init"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/diagnostics/health"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/diagnostics/health-check"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("DELETE", "/openapi/v1/bots/{bot_id}/edit-lock"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/edit-lock"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/edit-lock"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/edit-lock/steal"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/editors"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/editors"): Check(PermissionLevel.ADMIN),
    ("DELETE", "/openapi/v1/bots/{bot_id}/editors/me"): Check(PermissionLevel.MEMBER),
    ("DELETE", "/openapi/v1/bots/{bot_id}/editors/{editor_id}"): Check(PermissionLevel.ADMIN),
    ("PATCH", "/openapi/v1/bots/{bot_id}/editors/{editor_id}"): Check(PermissionLevel.ADMIN),
    ("GET", "/openapi/v1/bots/{bot_id}/engine/available"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/engine/capabilities"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/engine/config"): OWNER_SCOPED,
    ("PUT", "/openapi/v1/bots/{bot_id}/engine/config"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/engine/restart"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/engine/status"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/harness/apply"):
        ServiceChecked(PermissionLevel.ADMIN, "…openapi_v1.harness.router"),
    ("POST", "/openapi/v1/bots/{bot_id}/harness/diagnose"):
        ServiceChecked(PermissionLevel.ADMIN, "…openapi_v1.harness.router"),
    ("GET", "/openapi/v1/bots/{bot_id}/harness/dim-history"):
        ServiceChecked(PermissionLevel.ADMIN, "…openapi_v1.harness.router"),
    ("GET", "/openapi/v1/bots/{bot_id}/harness/dim-report"):
        ServiceChecked(PermissionLevel.ADMIN, "…openapi_v1.harness.router"),
    ("POST", "/openapi/v1/bots/{bot_id}/harness/preview"):
        ServiceChecked(PermissionLevel.ADMIN, "…openapi_v1.harness.router"),
    ("POST", "/openapi/v1/bots/{bot_id}/harness/rollback"):
        ServiceChecked(PermissionLevel.ADMIN, "…openapi_v1.harness.router"),
    ("GET", "/openapi/v1/bots/{bot_id}/identity"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/identity/{file_type}"): OWNER_SCOPED,
    ("PUT", "/openapi/v1/bots/{bot_id}/identity/{file_type}"): OWNER_SCOPED,
    ("DELETE", "/openapi/v1/bots/{bot_id}/lifecycle"): Check(PermissionLevel.OWNER, EDIT_LOCK),
    ("GET", "/openapi/v1/bots/{bot_id}/lifecycle"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/advance"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("GET", "/openapi/v1/bots/{bot_id}/lifecycle/approval"): Check(PermissionLevel.MEMBER),
    ("PUT", "/openapi/v1/bots/{bot_id}/lifecycle/approval"): Check(PermissionLevel.OWNER, EDIT_LOCK),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/cancel-staging"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/offline"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/restart"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/retry"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/upgrade"): Check(PermissionLevel.OWNER, EDIT_LOCK),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/{publication_id}/upgrade"): Check(PermissionLevel.ADMIN, EDIT_LOCK),
    ("DELETE", "/openapi/v1/bots/{bot_id}/local"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/local"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/local/auth-status"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/local/open-folder"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/local/restart"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/mcps"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/activate"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/deactivate"): Check(PermissionLevel.MEMBER),
    ("PATCH", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type"): Check(PermissionLevel.OWNER, EDIT_LOCK),
    ("PATCH", "/openapi/v1/bots/{bot_id}/clis/{cli_code}/call-type"): Check(PermissionLevel.OWNER, EDIT_LOCK),
    ("GET", "/openapi/v1/bots/{bot_id}/models"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/models/{model_id:path}"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/nodes"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/passport"): OWNER_SCOPED,
    ("POST", "/openapi/v1/collaboration/bots/{bot_uuid}/public"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/render-screens"):
        NoCheck("share and group viewers must render panels without an Editor relation"),
    ("POST", "/openapi/v1/bots/{bot_id}/render-screens"): Check(PermissionLevel.MEMBER),
    ("DELETE", "/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}"): Check(PermissionLevel.MEMBER),
    ("PATCH", "/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}"): Check(PermissionLevel.MEMBER),
    ("DELETE", "/openapi/v1/bots/{bot_id}/resources"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/resources"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/resources/download"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/resources/download-dir"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/resources/mkdir"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/resources/preview"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/resources/stat"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/resources/upload"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/restart"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/routines"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/routines"): OWNER_SCOPED,
    ("DELETE", "/openapi/v1/bots/{bot_id}/routines/{routine_id}"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/routines/{routine_id}"): OWNER_SCOPED,
    ("PATCH", "/openapi/v1/bots/{bot_id}/routines/{routine_id}"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/routines/{routine_id}/run"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/routines/{routine_id}/runs"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/sessions"):
        ServiceChecked(PermissionLevel.MEMBER,
                       "…openapi_v1.engine_runtime.sessions.router"),
    ("POST", "/openapi/v1/bots/{bot_id}/sessions"):
        ServiceChecked(PermissionLevel.MEMBER,
                       "…openapi_v1.engine_runtime.sessions.router"),
    ("GET", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files/upload-intents"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files/upload-complete"): Check(PermissionLevel.MEMBER),
    ("DELETE", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files/{resource_id}"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files/{resource_id}/content"): Check(PermissionLevel.MEMBER),
    ("GET",
     "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files/{resource_id}"
     "/materialize-status"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/sessions/favorites"):
        ServiceChecked(PermissionLevel.MEMBER,
                       "…openapi_v1.engine_runtime.sessions.router"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/sessions/{session_id}"):
        ServiceChecked(PermissionLevel.MEMBER,
                       "…openapi_v1.engine_runtime.sessions.router"),
    ("GET", "/openapi/v1/bots/{bot_id}/sessions/{session_id}"):
        ServiceChecked(PermissionLevel.MEMBER,
                       "…openapi_v1.engine_runtime.sessions.router"),
    ("PATCH", "/openapi/v1/bots/{bot_id}/sessions/{session_id}"):
        ServiceChecked(PermissionLevel.MEMBER,
                       "…openapi_v1.engine_runtime.sessions.router"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite"):
        ServiceChecked(PermissionLevel.MEMBER,
                       "…openapi_v1.engine_runtime.sessions.router"),
    ("PUT", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite"):
        ServiceChecked(PermissionLevel.MEMBER,
                       "…openapi_v1.engine_runtime.sessions.router"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/messages"):
        ServiceChecked(PermissionLevel.MEMBER,
                       "…openapi_v1.engine_runtime.sessions.router"),
    ("GET", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/messages"):
        ServiceChecked(PermissionLevel.MEMBER,
                       "…openapi_v1.engine_runtime.sessions.router"),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/skill-sets"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/resources"): Check(PermissionLevel.MEMBER),
    ("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}"): Check(PermissionLevel.MEMBER),
    ("PUT", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/activate"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/deactivate"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permission-requests"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permissions"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps"): Check(PermissionLevel.MEMBER),
    ("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("PUT", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills"): Check(PermissionLevel.MEMBER),
    ("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("PUT", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references/{reference_id}"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/skills"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.skill_query_service"),
    ("POST", "/openapi/v1/bots/{bot_id}/skills"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.local_skill_upload_service"),
    ("POST", "/openapi/v1/bots/{bot_id}/skills/upload-folder"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.local_skill_upload_service"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/skills/{skill_id}"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("GET", "/openapi/v1/bots/{bot_id}/skills/{skill_id}"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/content"): Check(PermissionLevel.MEMBER),
    ("POST", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters"): Check(PermissionLevel.MEMBER),
    ("PUT", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters"): Check(PermissionLevel.MEMBER, EDIT_LOCK),
    ("PUT", "/openapi/v1/bots/{bot_id}/space"): OWNER_SCOPED,
    ("DELETE", "/openapi/v1/bots/{bot_id}/startup-script"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/startup-script"): OWNER_SCOPED,
    ("PUT", "/openapi/v1/bots/{bot_id}/startup-script"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/status"): OWNER_SCOPED,

    # ── Operations that address no bot ────────────────────────────────────
    ("GET", "/openapi/v1/org/user"): NoCheck("the caller's own verified identity"),
    # Source credentials (W3, #1471): tenant-scoped by the request's
    # tenant guard, no bot to address and no collaborator scenario to
    # check — the callers this surface admits are applications (the edge
    # requires an app credential), and applications are not a grant
    # relationship. The one identity that matters, the owning
    # application, is checked by the service against the stored row for
    # exactly the two operations that mutate it.
    ("PUT", "/openapi/v1/bots/source-credentials/{name}"):
        NoCheck("tenant-guarded credential write; the owner-app check is the service's"),
    ("GET", "/openapi/v1/bots/source-credentials/{name}"): NoCheck("tenant-guarded masked metadata; every tenant app may read"),
    ("GET", "/openapi/v1/bots/source-credentials"): NoCheck("tenant-guarded inventory; every tenant app may read"),
    ("DELETE", "/openapi/v1/bots/source-credentials/{name}"):
        NoCheck("tenant-guarded credential delete; the owner-app check is the service's"),
    ("GET", "/openapi/v1/org/dept"): NoCheck("the caller's own directory record"),
    ("GET", "/openapi/v1/bots"): NoCheck("a collection, not one addressed bot"),
    ("POST", "/openapi/v1/bots"): NoCheck("a collection, not one addressed bot"),
    ("POST", "/openapi/v1/bots/with-manifest"): NoCheck("a creation, not one addressed bot — as POST /openapi/v1/bots"),
    ("GET", "/openapi/v1/bots/{bot_id}/with-manifest/status"): NoCheck("the caller's own creation: for most of one there is no bot record to check against, so what scopes it is that every row it reads — the job's idempotency key included — is keyed by the entity_id the caller's principal resolves to. That holds ONLY BECAUSE admission REFUSES an app-only caller here: for one of those require_user_id returns the user_id QUERY PARAMETER, and the entity_id would be request-supplied. Lifting that refusal without a check able to authorize an app→user pair before a bot exists invalidates this reason — see admission.py"),
    ("GET", "/openapi/v1/bots/all"): NoCheck("a collection, not one addressed bot"),
    ("GET", "/openapi/v1/bots/authorized"):
        NoCheck("a collection, not one addressed bot"),
    ("GET", "/openapi/v1/bots/routines/all"):
        NoCheck("a collection, not one addressed bot"),
    ("GET", "/openapi/v1/bots/catalog/discover"): NoCheck("tenant-identical catalogue"),
    ("GET", "/openapi/v1/bots/catalog/search"): NoCheck("tenant-identical catalogue"),
    ("GET", "/openapi/v1/bots/ceiling"):
        NoCheck("the named user's account quota, not a bot"),
    ("GET", "/openapi/v1/bots/check-name"):
        NoCheck("tenant-wide name availability, identical for every caller"),
    ("GET", "/openapi/v1/bots/loadtest/hello"):
        NoCheck("a load-test probe that touches no bot"),
    ("WEBSOCKET", "/openapi/v1/bots/loadtest/ws/echo"):
        NoCheck("a load-test probe that touches no bot"),
    ("GET", "/openapi/v1/bots/local"): NoCheck("a collection, not one addressed bot"),
    ("POST", "/openapi/v1/bots/local"): NoCheck("a collection, not one addressed bot"),
    ("GET", "/openapi/v1/bots/local/devices"): NoCheck("the named user's own devices"),
    ("GET", "/openapi/v1/bots/local/devices/{machine_id}/files"):
        NoCheck("the named user's own devices"),
    ("GET", "/openapi/v1/bots/logs/groups/{group_id}/traces"):
        NoCheck("trace lookups keyed by trace, session or group"),
    ("GET", "/openapi/v1/bots/logs/sessions/{session_key}/traces"):
        NoCheck("trace lookups keyed by trace, session or group"),
    ("GET", "/openapi/v1/bots/logs/tasks/{biz_scene}/{biz_task_id}/traces"):
        NoCheck("trace lookups keyed by trace, session or group"),
    ("GET", "/openapi/v1/bots/logs/traces"):
        NoCheck("trace lookups keyed by trace, session or group"),
    ("GET", "/openapi/v1/bots/logs/traces/{trace_id}"):
        NoCheck("trace lookups keyed by trace, session or group"),
    ("POST", "/openapi/v1/bots/market/mcp-servers"):
        NoCheck("tenant-identical marketplace"),
    ("POST", "/openapi/v1/bots/market/skill-center/skills"):
        NoCheck("tenant-identical marketplace"),
    ("POST", "/openapi/v1/bots/market/skill-center/sync"):
        NoCheck("tenant-identical materialized Skill Center synchronization"),
    ("GET", "/openapi/v1/bots/market/skill-center/tags"):
        NoCheck("tenant-identical marketplace"),
    ("POST", "/openapi/v1/bots/market/skills"): NoCheck("tenant-identical marketplace"),
    ("GET", "/openapi/v1/bots/mcp/servers"):
        NoCheck("tenant-identical MCP catalogue and per-caller config"),
    ("GET", "/openapi/v1/bots/mcp/servers/{server_code}"):
        NoCheck("tenant-identical MCP catalogue and per-caller config"),
    ("GET", "/openapi/v1/bots/mcp/servers/{server_code}/config"):
        NoCheck("tenant-identical MCP catalogue and per-caller config"),
    ("PUT", "/openapi/v1/bots/mcp/servers/{server_code}/config"):
        NoCheck("tenant-identical MCP catalogue and per-caller config"),
    ("GET", "/openapi/v1/bots/mcp/servers/{server_code}/permissions"):
        NoCheck("tenant-identical MCP catalogue and per-caller config"),
    ("GET", "/openapi/v1/bots/mcp/tenants"):
        NoCheck("tenant-identical MCP catalogue and per-caller config"),
    ("GET", "/openapi/v1/bots/skills/repository"):
        NoCheck("the shared skill repository, owned by no bot"),
    ("POST", "/openapi/v1/bots/skills/repository/sync"):
        NoCheck("the shared skill repository, owned by no bot"),
    ("GET", "/openapi/v1/bots/skills/repository/tree"):
        NoCheck("the shared skill repository, owned by no bot"),
    ("GET", "/openapi/v1/bots/skills/repository/{skill_id}"):
        NoCheck("the shared skill repository, owned by no bot"),
    ("GET", "/openapi/v1/bots/skills/{skill_code}/publish/status"):
        NoCheck("Skill Center publish status, keyed by skill code not by bot"),
    ("GET", "/openapi/v1/bots/skills/{skill_id}/readme"):
        NoCheck("Skill README access is adjudicated by the Skill query service"),
    ("GET", "/openapi/v1/bots/spaces"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/create"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/personal/initialize"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/join-requests"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/{bot_id}/editor-requests"):
        NoCheck("Team Space membership and Bot eligibility, adjudicated by the work-order service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites/cancel"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites/search"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites/status"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/members"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/members"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("DELETE", "/openapi/v1/bots/spaces/{space_id}/members/{member_user_id}"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("PUT", "/openapi/v1/bots/spaces/{space_id}/members/{member_user_id}/role"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills"):
        NoCheck("Space membership and immutable Draft creation, adjudicated by the Skill service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/import-from-git"):
        NoCheck("Space membership and Git snapshot creation, adjudicated by the Skill service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}"):
        NoCheck("Space membership and Skill visibility, adjudicated by the Skill service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/consumable"):
        NoCheck("Space membership and Canonical Ready state, adjudicated by the Version service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions"):
        NoCheck("Space membership and Published Version visibility, adjudicated by the Version service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}"):
        NoCheck("Space membership and exact Published Version visibility, adjudicated by the Version service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files"):
        NoCheck("Space membership and exact Canonical Version visibility, adjudicated by the Version service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files/{path:path}"):
        NoCheck("Space membership and exact Canonical file visibility, adjudicated by the Version service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/copy"):
        NoCheck("Skill Owner or Manager, Offline state, exact Version and idempotency, adjudicated by the Skill service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files"):
        NoCheck("Space membership and Draft visibility, adjudicated by the Skill service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path:path}"):
        NoCheck("Space membership and Draft file visibility, adjudicated by the Skill service"),
    ("PUT", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path:path}"):
        NoCheck("Skill Grant, revision CAS and Lease fencing, adjudicated by the Skill service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/upgrade"):
        NoCheck("Skill Grant, exact Published Version and idempotency, adjudicated by the Skill service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/refresh-from-git"):
        NoCheck("Skill Grant, frozen Git source and revision CAS, adjudicated by the Skill service"),
    ("DELETE", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft"):
        NoCheck("Skill Grant, revision CAS, Lease fencing and aggregate history, adjudicated by the Skill service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline-impact"):
        NoCheck("Skill Owner or Manager Grant and fail-closed lineage, adjudicated by the Offline service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline"):
        NoCheck("Skill Owner or Manager Grant and transactional blocker recheck, adjudicated by the Offline service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/grants"):
        NoCheck("Space membership and Skill Grants, adjudicated by the Grant service"),
    ("PUT", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}"):
        NoCheck("Skill Owner Grant, adjudicated by the Grant service"),
    ("DELETE", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}"):
        NoCheck("Skill Owner Grant, adjudicated by the Grant service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/owner-transfer"):
        NoCheck("Skill Owner or Space administrator, adjudicated by the Grant service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-requests"):
        NoCheck("Team membership and Skill Grant eligibility, adjudicated by the Skill service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease"):
        NoCheck("Space membership and Skill Grants, adjudicated by the Lease service"),
    ("PUT", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease"):
        NoCheck("Skill Owner or Manager Grant, adjudicated by the Lease service"),
    ("DELETE", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease"):
        NoCheck("Lease holder and fencing token, adjudicated by the Lease service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease/takeover"):
        NoCheck("Skill Owner or Manager Grant, adjudicated by the Lease service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publication-impact"):
        NoCheck("Skill Owner or Manager Grant and current Installation state, adjudicated by the Publication service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publications"):
        NoCheck("Skill Grant, Draft, Lease, idempotency and task recovery, adjudicated by the Publication service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publications"):
        NoCheck("Space membership and Publication history, adjudicated by the Publication service"),
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publications/{attempt_id}"):
        NoCheck("Space membership and exact Publication Attempt scope, adjudicated by the Publication service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publications/{attempt_id}/retry"):
        NoCheck("Skill Grant and Attempt recovery state, adjudicated by the Publication service"),
    ("POST", "/openapi/v1/bots/work-order-notifications/read-all"):
        NoCheck("the named user's own work orders and notifications"),
    ("GET", "/openapi/v1/bots/work-order-notifications/unread-count"):
        NoCheck("the named user's own work orders and notifications"),
    ("GET", "/openapi/v1/bots/work-order-notifications/{notification_id}"):
        NoCheck("the named user's own work orders and notifications"),
    ("POST", "/openapi/v1/bots/work-order-notifications/{notification_id}/read"):
        NoCheck("the named user's own work orders and notifications"),
    ("POST", "/openapi/v1/bots/work-orders/events"):
        NoCheck("the named user creates their own work-order event"),
    ("GET", "/openapi/v1/bots/work-orders"):
        NoCheck("the named user's own work orders and notifications"),
    ("GET", "/openapi/v1/bots/work-orders/{work_order_id}"):
        NoCheck("the named user's own work orders and notifications"),
    ("POST", "/openapi/v1/bots/work-orders/{work_order_id}/approval"):
        NoCheck("the named user's own work orders and notifications"),
    ("POST", "/openapi/v1/bots/work-orders/{work_order_id}/approve"):
        NoCheck("the named user's own work orders and notifications"),
    ("POST", "/openapi/v1/bots/work-orders/{work_order_id}/reject"):
        NoCheck("the named user's own work orders and notifications"),
    # ── Task public surface (execute/dashboard/list + grant/revoke stateless relay; mounted via the gateway
    # ── `collaboration-tasks` domain → backend). Tasks address no bot, so the
    # ── bot-level `Check` does not apply; the caller identity is resolved by
    # ── the gateway spanner + `_PUBLIC_AUTH`; `list` uses caller-selected
    # ── user_id only as a task-record filter. `NoCheck` is the settled mode here, not a
    # ── placeholder — see `admission.py` for the machine-caller decision.
    ("POST", "/openapi/v1/collaboration/tasks/execute"):
        NoCheck("a task, not a bot; the submitter is the task owner"),
    ("GET", "/openapi/v1/collaboration/tasks/dashboard"):
        NoCheck("a task, not a bot; read-only task graph by task_id"),
    ("GET", "/openapi/v1/collaboration/tasks/list"):
        NoCheck("a task, not a bot; filters records by the named user"),
    ("GET", "/openapi/v1/collaboration/tasks/bbs/list"):
        NoCheck("a task, not a bot; paged read of BBS relay tasks"),
    ("POST", "/openapi/v1/collaboration/tasks/grant"):
        NoCheck("a stateless relay to secbaas; the human Cookie/Referer authorizes the grant, not a bot permission"),
    ("POST", "/openapi/v1/collaboration/tasks/revoke"):
        NoCheck("a stateless relay to secbaas; the human Cookie/Referer authorizes the revoke, not a bot permission"),

    # ── Retiring addresses in ``deprecated/`` ─────────────────────────────
    ("GET", "/openapi/v1/bots/approvals/{bot_id}/mode"): Check(PermissionLevel.MEMBER),
    ("PUT", "/openapi/v1/bots/approvals/{bot_id}/mode"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/approvals/{bot_id}/modes"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/connection/{bot_id}"): INHERITED,
    ("GET", "/openapi/v1/bots/engine/{bot_id}/available"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/engine/{bot_id}/capabilities"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/engine/{bot_id}/status"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/identity/{bot_id}"): INHERITED,
    ("GET", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): INHERITED,
    ("PUT", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): INHERITED,
    ("GET", "/openapi/v1/bots/models/{bot_id}"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/models/{bot_id}/{model_id:path}"): Check(PermissionLevel.MEMBER),
    ("DELETE", "/openapi/v1/bots/resources"): INHERITED,
    ("GET", "/openapi/v1/bots/resources"): INHERITED,
    ("GET", "/openapi/v1/bots/resources/download"): INHERITED,
    ("POST", "/openapi/v1/bots/resources/mkdir"): INHERITED,
    ("GET", "/openapi/v1/bots/resources/preview"): INHERITED,
    ("GET", "/openapi/v1/bots/resources/stat"): INHERITED,
    ("POST", "/openapi/v1/bots/resources/upload"): INHERITED,
    ("GET", "/openapi/v1/bots/routines"): INHERITED,
    ("POST", "/openapi/v1/bots/routines"): INHERITED,
    ("DELETE", "/openapi/v1/bots/routines/{routine_id}"): INHERITED,
    ("GET", "/openapi/v1/bots/routines/{routine_id}"): INHERITED,
    ("PATCH", "/openapi/v1/bots/routines/{routine_id}"): INHERITED,
    ("POST", "/openapi/v1/bots/routines/{routine_id}/run"): INHERITED,
    ("GET", "/openapi/v1/bots/routines/{routine_id}/runs"): INHERITED,
    ("GET", "/openapi/v1/bots/sessions/{bot_id}"): INHERITED,
    ("POST", "/openapi/v1/bots/sessions/{bot_id}"): INHERITED,
    ("DELETE", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): INHERITED,
    ("GET", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): INHERITED,
    ("PATCH", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): INHERITED,
    ("DELETE", "/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages"): INHERITED,
    ("GET", "/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages"): INHERITED,
    ("GET", "/openapi/v1/bots/skills"): INHERITED,
    ("POST", "/openapi/v1/bots/skills/upload"): INHERITED,
    ("DELETE", "/openapi/v1/bots/skills/{skill_id}"): INHERITED,
    ("GET", "/openapi/v1/bots/skills/{skill_id}"): INHERITED,
    ("POST", "/openapi/v1/bots/skills/{skill_id}/activate"): INHERITED,
    ("POST", "/openapi/v1/bots/skills/{skill_id}/deactivate"): INHERITED,
    ("GET", "/openapi/v1/bots/{bot_id}/auth-status"): INHERITED,
    ("GET", "/openapi/v1/bots/{bot_id}/engine-config"): INHERITED,
    ("PUT", "/openapi/v1/bots/{bot_id}/engine-config"): INHERITED,
    **authorization_rows(NoCheck),}


#: Operations whose router exists but which ``build_public_router`` does not
#: mount, so they are in the table without being on the surface.
#:
#: Empty today. The ``openapi_v1/task`` surface used to live here: its twin
#: ``/api/v1`` router answered under ``/api/v1/collaboration/tasks`` in
#: ``adapters/http/task``, while the ``/openapi/v1`` twin stayed unmounted until
#: the gateway's configuration declared the collaboration-tasks domain. That
#: domain is now declared (gateway ``collaboration-tasks`` → backend) and the
#: ``/openapi/v1`` twin is mounted in ``build_public_router``, so the three
#: task operations became live and their rows are real decisions, not
#: placeholders.
#:
#: They carry rows anyway, and that is the point. The router is built with
#: ``PublicAPIRoute`` like every other, so **whoever mounts it later cannot do
#: so unguarded** — they will have to replace these placeholder rows with a
#: real decision. Leaving the router without a route class would have been the
#: easy fix and the wrong one: it would mount silently unchecked.
#:
#: :func:`assert_every_route_authorized` subtracts these before reporting
#: orphans, so an unmounted row is not mistaken for a decision left behind by a
#: rename. Delete an entry the moment its operation is mounted.
UNMOUNTED_OPERATIONS = frozenset()


def scaffolding_row_count() -> int:
    """How many operations are still in a mode that must eventually be empty.

    The migration's burn-down. When this reaches zero every operation is either
    ``Check`` or ``NoCheck`` and the three scaffolding modes can be deleted
    along with this function.
    """
    return sum(
        1 for rule in AUTHORIZATION.values() if isinstance(rule, SCAFFOLDING_MODES)
    )


__all__ = [
    "AUTHORIZATION",
    "Authorization",
    "Check",
    "EDIT_LOCK",
    "INHERITED",
    "NoCheck",
    "OWNER_SCOPED",
    "SCAFFOLDING_MODES",
    "UNMOUNTED_OPERATIONS",
    "ServiceChecked",
    "scaffolding_row_count",
]


class PublicRouteNotAuthorized(RuntimeError):
    """A public operation was constructed without a row in :data:`AUTHORIZATION`.

    Raised while the route's own module is importing, so the application never
    starts. That is the whole fail-closed property: a new operation is refused
    until someone decides what governs it, rather than served because nobody
    noticed. Nothing catches it — there is no "continue without a decision".
    """


class PublicAPIRoute(APIRoute):
    """The route type every ``/openapi/v1`` router is built with.

    Reads the operation's row and attaches what it calls for, so a handler
    never declares its own authorization and cannot opt out of it. A row that
    is absent raises rather than defaulting to anything.

    This runs at *decoration* time — when ``@router.get(...)`` constructs the
    route — which is earlier than assembly and earlier than the first request.
    """

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        rule = _rule_for(path, kwargs.get("methods"))
        if isinstance(rule, Check):
            # Imported here, not at module scope: ``bot_access`` reads ``Check``
            # off this module, so a top-level import would be a cycle. The cost
            # is one lookup per adjudicated route at import time, the cheapest
            # place to pay it.
            from agentclaw.community.adapters.http.openapi_v1.bot_access import (
                require_check,
            )

            kwargs["dependencies"] = [
                *(kwargs.get("dependencies") or []),
                Depends(require_check(rule)),
            ]
        if isinstance(rule, Check) and rule.edit_lock is EDIT_LOCK:
            from agentclaw.community.adapters.http.openapi_v1.contracts import (
                ErrorEnvelope,
                error_example,
            )

            responses = dict(kwargs.get("responses") or {})
            responses.setdefault(
                423,
                {
                    "model": ErrorEnvelope,
                    "description": (
                        "A Bot with collaborators requires the caller to "
                        "hold its edit lock."
                    ),
                    **error_example(423, "Edit lock required"),
                },
            )
            kwargs["responses"] = responses
        super().__init__(path, endpoint, **kwargs)


def _rule_for(path: str, methods: Any) -> Authorization:
    """The row for this operation, or raise naming what is missing.

    Every method a route declares must resolve to the same rule; a route
    serving two methods with different bars would have to pick one, and
    silently picking is how a surface acquires a hole. ``HEAD`` and ``OPTIONS``
    are excluded for the reason the inventory excludes them: FastAPI adds them
    alongside ``GET`` and they are not separate decisions.
    """
    wanted = sorted(set(methods or ["GET"]) - {"HEAD", "OPTIONS"})
    rules = []
    for method in wanted:
        rule = AUTHORIZATION.get((method, path))
        if rule is None:
            raise PublicRouteNotAuthorized(
                f"{method} {path} has no row in AUTHORIZATION. Every public "
                f"operation must declare what governs it; add a row in "
                f"openapi_v1/authorization.py."
            )
        rules.append(rule)
    if len({repr(rule) for rule in rules}) > 1:
        raise PublicRouteNotAuthorized(
            f"{path} declares methods {wanted} with differing authorization "
            f"rules {rules}; split the route or give them the same rule."
        )
    return rules[0]


def assert_every_route_authorized(router: APIRouter) -> None:
    """Fail assembly on the mistakes :class:`PublicAPIRoute` cannot see itself.

    It catches a *missing row* at construction. Four things it cannot catch are
    checked here, at the end of assembly, so the application refuses to start
    rather than serving an operation nothing governs:

    1. a router built without ``route_class=PublicAPIRoute`` — its routes never
       ran that ``__init__``;
    2. a row matching no operation, left behind by a rename;
    3. a WebSocket operation with no row — it never runs the route class
       either, so nothing else would notice;
    4. a ``Check`` row the seam could not honour, in any of three shapes — a
       WebSocket operation, a route whose handler does not consume the owner the
       gate adjudicates, and a route carrying no ``{bot_id}`` on its path for
       the gate to read. Each would leave the table promising enforcement that
       never happens; see ``_assert_check_rows_are_enforceable``.
    """
    seen: set[tuple[str, str]] = set()
    sockets: set[tuple[str, str]] = set()
    checked_handlers: list[tuple[tuple[str, str], object]] = []
    unguarded: list[str] = []
    for route in _walk(router):
        original = getattr(route, "original_route", None) or route
        path = getattr(route, "path", "") or getattr(original, "path", "")
        methods = set(getattr(route, "methods", None) or {"WEBSOCKET"})
        is_socket = _is_websocket(original)
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            seen.add((method, path))
            if is_socket:
                sockets.add((method, path))
            elif isinstance(AUTHORIZATION.get((method, path)), Check):
                checked_handlers.append(((method, path), original.endpoint))
        if not isinstance(original, PublicAPIRoute) and not is_socket:
            unguarded.append(f"{sorted(methods)} {path}")
    if unguarded:
        raise PublicRouteNotAuthorized(
            "these routes were not built with route_class=PublicAPIRoute, so "
            "their AUTHORIZATION row was never read: " + ", ".join(sorted(unguarded))
        )
    # The reverse direction, which matters only for the socket plane. An HTTP
    # route with no row cannot exist — ``PublicAPIRoute`` refused to build it —
    # so this is guaranteed empty there. A WebSocket route never runs that
    # ``__init__`` at all, so without this check one could be served with no
    # declared authorization whatsoever, which is the single gap the route
    # class cannot close on its own.
    missing = seen - set(AUTHORIZATION) - UNMOUNTED_OPERATIONS
    if missing:
        raise PublicRouteNotAuthorized(
            "these live operations have no row in AUTHORIZATION: "
            + ", ".join(sorted(f"{method} {path}" for method, path in missing))
        )
    orphans = set(AUTHORIZATION) - seen - UNMOUNTED_OPERATIONS
    if orphans:
        raise PublicRouteNotAuthorized(
            "these AUTHORIZATION rows match no live operation (renamed or "
            f"removed?): {sorted(orphans)}"
        )
    _assert_check_rows_are_enforceable(sockets, checked_handlers)


def _assert_check_rows_are_enforceable(
    sockets: set[tuple[str, str]], checked_handlers: list[tuple[tuple[str, str], object]]
) -> None:
    """Refuse a ``Check`` row the seam could not actually enforce.
    A row that declares enforcement the mechanism cannot deliver is worse than
    no row: the table reads as covered, and the inventory agrees, while the
    operation is served unguarded. Three shapes of that.

    The third is the seam's permanent limit rather than a gap to close. The gate
    runs *before* the handler, so the only bot it can adjudicate is one the
    request itself carries on the path — that is what ``BotIdPath`` reads. An
    operation whose bot arrives any other way cannot be keyed on the same value
    the handler acts on, and a ``Check`` row for it would adjudicate something
    the handler never saw.

    The **retiring skills addresses** are the live example: two carry the bot in
    the query string, and four name no bot at all — the skill id resolves its
    own bot, inside the handler, after this check would have had to answer. They
    keep the checks they already have; what this refuses is the table claiming
    the seam covers them.

    **This does not catch harness**, and it is worth saying so where someone
    would otherwise assume it. Those six routes are mounted under
    ``/openapi/v1/bots/{bot_id}/harness`` and do declare ``bot_id`` on the path,
    so they pass this refusal. What stops them today is the *second* one — no
    harness handler consumes ``OwnerIdDep`` — and what should stop them after
    that is judgement: they pass ``entity_id=body.entity_id`` to the service
    beside ``bot_id``, so the gate would adjudicate one thing while the
    operation acted on another. Adding ``OwnerIdDep`` there would satisfy all
    three refusals and still be wrong. That is a defect to fix (#1323 filed it),
    not a limit to encode.
    """
    socket_checks = sorted(
        f"{method} {path}"
        for (method, path) in sockets
        if isinstance(AUTHORIZATION.get((method, path)), Check)
    )
    if socket_checks:
        raise PublicRouteNotAuthorized(
            "these WebSocket operations declare Check, but FastAPI builds them "
            "as APIWebSocketRoute so the route class never attaches the gate — "
            "the declaration would be unenforced: " + ", ".join(socket_checks)
        )

    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
        resolve_owner_id,
    )

    divergent = sorted(
        f"{method} {path}"
        for (method, path), endpoint in checked_handlers
        if not _consumes(endpoint, resolve_owner_id)
    )
    if divergent:
        raise PublicRouteNotAuthorized(
            "these operations declare Check but their handler does not take "
            "OwnerIdDep, so the gate would adjudicate the addressed owner while "
            "the handler acted on a different one (see bot_access's contract): "
            + ", ".join(divergent)
        )

    # Read off the route's own path template rather than its resolved
    # parameters: ``BotIdPath`` is what the gate declares, and FastAPI fills a
    # path parameter only when the template names it. A route whose template
    # has no ``{bot_id}`` cannot supply one whatever its handler does.
    unkeyable = sorted(
        f"{method} {path}"
        for (method, path), _endpoint in checked_handlers
        if "{bot_id}" not in path
    )
    if unkeyable:
        raise PublicRouteNotAuthorized(
            "these operations declare Check but do not carry {bot_id} on their "
            "path, so the gate has no bot to resolve and the row cannot be "
            "enforced as written. Refused here rather than left to fail per "
            "request: a table that claims enforcement the seam cannot deliver "
            "is the thing this module exists to prevent. The gate runs before "
            "the handler, so this is a permanent limit rather than a gap — an "
            "operation addressing its bot any other way keeps whatever check it "
            "already has and must not claim Check. Offending rows: "
            + ", ".join(unkeyable)
        )


def _consumes(endpoint: object, dependency: object) -> bool:
    """Whether ``endpoint``'s own signature declares ``Depends(dependency)``.
    Its *own* signature, deliberately: the gate itself takes ``OwnerIdDep``, so
    walking the route's whole dependency tree would find it every time and the
    check would pass vacuously. ``get_type_hints`` follows ``__wrapped__``, so a
    handler behind ``@envelope_errors`` reports its real parameters.
    """
    # ``get_type_hints`` rather than ``signature().parameters[...].annotation``:
    # every router in this package declares ``from __future__ import
    # annotations``, so the raw annotations are *strings* and no amount of
    # ``get_origin`` on them finds anything. Reading them unresolved would make
    # this check answer "no" for every real handler — a false refusal on the
    # first migration, which is exactly when it must be trustworthy.
    # ``include_extras`` keeps the ``Annotated`` metadata the dependency lives in.
    try:
        hints = get_type_hints(endpoint, include_extras=True)
    except Exception:  # pragma: no cover - unresolvable forward reference
        return False
    for annotation in hints.values():
        if get_origin(annotation) is not Annotated:
            continue
        for meta in get_args(annotation)[1:]:
            if getattr(meta, "dependency", None) is dependency:
                return True
    return False


def _walk(router: APIRouter):
    """Every operation as the application will really serve it.
    ``include_router`` stores a lazy wrapper rather than copying routes, so the
    effective contexts — not ``router.routes`` — are what the surface serves.
    """
    for route in getattr(router, "routes", []):
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        elif hasattr(route, "dependant"):
            yield route


def _is_websocket(route: Any) -> bool:
    """WebSocket routes are ``APIWebSocketRoute``, which takes no route class.
    FastAPI offers no per-router class for the socket plane, so a socket route
    cannot carry :class:`PublicAPIRoute` and ``_rule_for`` never runs for it.
    It is covered by the *missing* check above rather than by this exemption —
    which is the whole reason that check exists, since the orphan check looks
    the other way and would let a row-less socket route through.
    """
    return not hasattr(route, "methods")