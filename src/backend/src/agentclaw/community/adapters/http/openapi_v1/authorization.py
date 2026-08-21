"""Who may do what to a bot, for every operation on this surface.

One table, and it is the **only** place an operation's collaborator
authorization is declared. A handler declares nothing; the route class below
reads this table and attaches what the row calls for. That is a deliberate
reversal of the convention its neighbour ``admission.py`` follows, where each
route also names its own dependency and a test holds the two in step. The
reversal buys one thing: a single source. There is no second declaration to
drift, and no way for a route to disagree with the table because a route has
nothing to say.

What replaces the lost redundancy is that **omission is not survivable**. An
operation absent from this table cannot be constructed — ``PublicAPIRoute``
raises while its module is importing — so the application does not start, and a
missing row is never mistaken for "no check needed". A CI assertion catches the
same mistake one step later; this catches it before anything runs.

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
- :data:`SELF_CHECKED` — a retiring address under ``deprecated/``. Not
  adjudicated here; those routers carry whatever check they have.
  → disappears with that package.

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
from typing import Any, Callable

from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute

from agentclaw.community.core.bot_collaborator.models import PermissionLevel


@dataclass(frozen=True)
class Check:
    """**The seam enforces this**, at ``level`` or above; OWNER always passes.

    Getting the level wrong here refuses callers the surface means to admit, or
    admits callers it means to refuse — this is the enforcing column, unlike
    :class:`ServiceChecked`'s.
    """

    level: PermissionLevel


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

#: Scaffolding: a retiring address under ``deprecated/``. Those routers check
#: themselves or inherit their replacement's mount; either way the seam does not
#: adjudicate them, and the whole set disappears when the package is deleted.
SELF_CHECKED = _Scaffold("SELF_CHECKED")

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
    # ── Bot-scoped operations ─────────────────────────────────────────────
    ("DELETE", "/openapi/v1/bots/{bot_id}"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}"): OWNER_SCOPED,
    ("PUT", "/openapi/v1/bots/{bot_id}"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/activate"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/approvals/mode"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("PUT", "/openapi/v1/bots/{bot_id}/approvals/mode"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/approvals/modes"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("POST", "/openapi/v1/bots/{bot_id}/auth-status"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/authorized-apps"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.authorized_apps.router"),
    ("POST", "/openapi/v1/bots/{bot_id}/authorized-apps"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.authorized_apps.router"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/authorized-apps/{app_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.authorized_apps.router"),
    ("POST", "/openapi/v1/bots/{bot_id}/caller-identity"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/channels"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.channels.router"),
    ("POST", "/openapi/v1/bots/{bot_id}/channels"):
        ServiceChecked(PermissionLevel.ADMIN, "…openapi_v1.channels.router"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"):
        ServiceChecked(PermissionLevel.ADMIN, "…openapi_v1.channels.router"),
    ("GET", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.channels.router"),
    ("PATCH", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"):
        ServiceChecked(PermissionLevel.ADMIN, "…openapi_v1.channels.router"),
    ("PUT", "/openapi/v1/bots/{bot_id}/channels/{channel_id}/status"):
        ServiceChecked(PermissionLevel.ADMIN, "…openapi_v1.channels.router"),
    ("GET", "/openapi/v1/bots/{bot_id}/chats"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.bot_chat.service"),
    ("GET", "/openapi/v1/bots/{bot_id}/chats/{trace_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.bot_chat.service"),
    ("GET", "/openapi/v1/bots/{bot_id}/connection"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.engine_runtime.connection"),
    ("GET", "/openapi/v1/bots/{bot_id}/containers"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("POST", "/openapi/v1/bots/{bot_id}/containers/{instance_id}/restart"):
        ServiceChecked(PermissionLevel.OWNER, "…core.service_bot.services.service_publication_facade"),
    ("GET", "/openapi/v1/bots/{bot_id}/data-init"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/data-init"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/diagnostics/health"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.diagnostics.router"),
    ("POST", "/openapi/v1/bots/{bot_id}/diagnostics/health-check"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.diagnostics.router"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/edit-lock"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("GET", "/openapi/v1/bots/{bot_id}/edit-lock"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("POST", "/openapi/v1/bots/{bot_id}/edit-lock"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("POST", "/openapi/v1/bots/{bot_id}/edit-lock/steal"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("GET", "/openapi/v1/bots/{bot_id}/editors"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.bot_collaborator.services.collaborator_service"),
    ("POST", "/openapi/v1/bots/{bot_id}/editors"):
        ServiceChecked(PermissionLevel.ADMIN, "…core.bot_collaborator.services.collaborator_service"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/editors/me"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.bot_collaborator.services.collaborator_service"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/editors/{editor_id}"):
        ServiceChecked(PermissionLevel.ADMIN, "…core.bot_collaborator.services.collaborator_service"),
    ("PATCH", "/openapi/v1/bots/{bot_id}/editors/{editor_id}"):
        ServiceChecked(PermissionLevel.ADMIN, "…core.bot_collaborator.services.collaborator_service"),
    ("GET", "/openapi/v1/bots/{bot_id}/engine/available"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/engine/capabilities"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/engine/config"): OWNER_SCOPED,
    ("PUT", "/openapi/v1/bots/{bot_id}/engine/config"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/engine/restart"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/engine/status"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
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
    ("DELETE", "/openapi/v1/bots/{bot_id}/lifecycle"):
        ServiceChecked(PermissionLevel.OWNER, "…core.service_bot.services.service_publication_facade"),
    ("GET", "/openapi/v1/bots/{bot_id}/lifecycle"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/advance"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("GET", "/openapi/v1/bots/{bot_id}/lifecycle/approval"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("PUT", "/openapi/v1/bots/{bot_id}/lifecycle/approval"):
        ServiceChecked(PermissionLevel.OWNER, "…core.service_bot.services.service_publication_facade"),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/cancel-staging"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/offline"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/restart"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/retry"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.service_bot.services.service_publication_facade"),
    ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/upgrade"):
        ServiceChecked(PermissionLevel.OWNER, "…core.service_bot.services.service_publication_facade"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/local"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/local"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/local/auth-status"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/local/open-folder"): OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/local/restart"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/mcps"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("POST", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/activate"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("POST", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/deactivate"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("GET", "/openapi/v1/bots/{bot_id}/models"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/models/{model_id:path}"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/nodes"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/passport"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/render-screens"):
        NoCheck("share and group viewers must render panels without an Editor relation"),
    ("POST", "/openapi/v1/bots/{bot_id}/render-screens"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.render_screens.gating"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.render_screens.gating"),
    ("PATCH", "/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.render_screens.gating"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/resources"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/resources"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/resources/download"): OWNER_SCOPED,
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
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("POST", "/openapi/v1/bots/{bot_id}/sessions"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/sessions/favorites"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/sessions/{session_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/sessions/{session_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("PATCH", "/openapi/v1/bots/{bot_id}/sessions/{session_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("PUT", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/messages"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/messages"):
        ServiceChecked(PermissionLevel.MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("POST", "/openapi/v1/bots/{bot_id}/skill-sets"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/resources"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("PUT", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/activate"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/deactivate"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permission-requests"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permissions"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("PUT", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("PUT", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
    ("GET", "/openapi/v1/bots/{bot_id}/skills"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.bot_skill_asset_service"),
    ("POST", "/openapi/v1/bots/{bot_id}/skills"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.bot_skill_asset_service"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/skills/{skill_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.bot_skill_asset_service"),
    ("GET", "/openapi/v1/bots/{bot_id}/skills/{skill_id}"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.bot_skill_asset_service"),
    ("POST", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.bot_skill_asset_service"),
    ("GET", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/content"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.bot_skill_asset_service"),
    ("POST", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.bot_skill_asset_service"),
    ("GET", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.bot_skill_asset_service"),
    ("PUT", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters"):
        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.services.bot_skill_asset_service"),
    ("PUT", "/openapi/v1/bots/{bot_id}/space"): OWNER_SCOPED,
    ("DELETE", "/openapi/v1/bots/{bot_id}/startup-script"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/startup-script"): OWNER_SCOPED,
    ("PUT", "/openapi/v1/bots/{bot_id}/startup-script"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/status"): OWNER_SCOPED,

    # ── Operations that address no bot ────────────────────────────────────
    ("GET", "/openapi/v1/bots"): NoCheck("a collection, not one addressed bot"),
    ("POST", "/openapi/v1/bots"): NoCheck("a collection, not one addressed bot"),
    ("GET", "/openapi/v1/bots/all"): NoCheck("a collection, not one addressed bot"),
    ("GET", "/openapi/v1/bots/authorized"):
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
    ("GET", "/openapi/v1/bots/spaces"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/create"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/personal/initialize"):
        NoCheck("Space membership, adjudicated by the Space service"),
    ("POST", "/openapi/v1/bots/spaces/{space_id}/join-requests"):
        NoCheck("Space membership, adjudicated by the Space service"),
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
    ("POST", "/openapi/v1/bots/work-order-notifications/read-all"):
        NoCheck("the named user's own work orders and notifications"),
    ("GET", "/openapi/v1/bots/work-order-notifications/unread-count"):
        NoCheck("the named user's own work orders and notifications"),
    ("GET", "/openapi/v1/bots/work-order-notifications/{notification_id}"):
        NoCheck("the named user's own work orders and notifications"),
    ("POST", "/openapi/v1/bots/work-order-notifications/{notification_id}/read"):
        NoCheck("the named user's own work orders and notifications"),
    ("GET", "/openapi/v1/bots/work-orders"):
        NoCheck("the named user's own work orders and notifications"),
    ("GET", "/openapi/v1/bots/work-orders/{work_order_id}"):
        NoCheck("the named user's own work orders and notifications"),
    ("POST", "/openapi/v1/bots/work-orders/{work_order_id}/approve"):
        NoCheck("the named user's own work orders and notifications"),
    ("POST", "/openapi/v1/bots/work-orders/{work_order_id}/reject"):
        NoCheck("the named user's own work orders and notifications"),
    ("GET", "/openapi/v1/caller"): NoCheck("the caller's own verified identity"),
    ("GET", "/openapi/v1/org/user/iam-token"): NoCheck("the caller's own credential"),

    # ── Retiring addresses in ``deprecated/`` ─────────────────────────────
    ("GET", "/openapi/v1/bots/approvals/{bot_id}/mode"): SELF_CHECKED,
    ("PUT", "/openapi/v1/bots/approvals/{bot_id}/mode"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/approvals/{bot_id}/modes"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/connection/{bot_id}"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/engine/{bot_id}/available"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/engine/{bot_id}/capabilities"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/engine/{bot_id}/status"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/identity/{bot_id}"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): SELF_CHECKED,
    ("PUT", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/models/{bot_id}"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/models/{bot_id}/{model_id:path}"): SELF_CHECKED,
    ("DELETE", "/openapi/v1/bots/resources"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/resources"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/resources/download"): SELF_CHECKED,
    ("POST", "/openapi/v1/bots/resources/mkdir"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/resources/preview"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/resources/stat"): SELF_CHECKED,
    ("POST", "/openapi/v1/bots/resources/upload"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/routines"): SELF_CHECKED,
    ("POST", "/openapi/v1/bots/routines"): SELF_CHECKED,
    ("DELETE", "/openapi/v1/bots/routines/{routine_id}"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/routines/{routine_id}"): SELF_CHECKED,
    ("PATCH", "/openapi/v1/bots/routines/{routine_id}"): SELF_CHECKED,
    ("POST", "/openapi/v1/bots/routines/{routine_id}/run"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/routines/{routine_id}/runs"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/sessions/{bot_id}"): SELF_CHECKED,
    ("POST", "/openapi/v1/bots/sessions/{bot_id}"): SELF_CHECKED,
    ("DELETE", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): SELF_CHECKED,
    ("PATCH", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): SELF_CHECKED,
    ("DELETE", "/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages"):
        SELF_CHECKED,
    ("GET", "/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/skills"): SELF_CHECKED,
    ("POST", "/openapi/v1/bots/skills/upload"): SELF_CHECKED,
    ("DELETE", "/openapi/v1/bots/skills/{skill_id}"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/skills/{skill_id}"): SELF_CHECKED,
    ("POST", "/openapi/v1/bots/skills/{skill_id}/activate"): SELF_CHECKED,
    ("POST", "/openapi/v1/bots/skills/{skill_id}/deactivate"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/{bot_id}/auth-status"): SELF_CHECKED,
    ("GET", "/openapi/v1/bots/{bot_id}/engine-config"): SELF_CHECKED,
    ("PUT", "/openapi/v1/bots/{bot_id}/engine-config"): SELF_CHECKED,}


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
    "NoCheck",
    "OWNER_SCOPED",
    "SCAFFOLDING_MODES",
    "SELF_CHECKED",
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
    """Fail assembly on the two mistakes :class:`PublicAPIRoute` cannot see.

    It catches a *missing row* at construction. It cannot catch a router built
    without ``route_class=PublicAPIRoute`` — those routes never run its
    ``__init__`` — nor a row that matches no operation, which is a decision
    left behind after a rename. Both are checked here, at the end of assembly,
    so the application still refuses to start rather than serving an operation
    nothing governs.
    """
    seen: set[tuple[str, str]] = set()
    unguarded: list[str] = []
    for route in _walk(router):
        original = getattr(route, "original_route", None) or route
        path = getattr(route, "path", "") or getattr(original, "path", "")
        methods = set(getattr(route, "methods", None) or {"WEBSOCKET"})
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            seen.add((method, path))
        if not isinstance(original, PublicAPIRoute) and not _is_websocket(original):
            unguarded.append(f"{sorted(methods)} {path}")
    if unguarded:
        raise PublicRouteNotAuthorized(
            "these routes were not built with route_class=PublicAPIRoute, so "
            "their AUTHORIZATION row was never read: " + ", ".join(sorted(unguarded))
        )
    orphans = set(AUTHORIZATION) - seen
    if orphans:
        raise PublicRouteNotAuthorized(
            "these AUTHORIZATION rows match no live operation (renamed or "
            f"removed?): {sorted(orphans)}"
        )


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
    cannot carry :class:`PublicAPIRoute`. It is still covered: its row is
    required by the orphan check above and by the inventory test, and the
    socket operations on this surface are refused to app callers anyway.
    """
    return not hasattr(route, "methods")
