"""Which public operations admit a caller with no human on the wire.

One table, and it is policy rather than plumbing: every operation on this
surface appears in :data:`ADMISSION` exactly once, and an operation's mode
follows from its **shape** — which identities it takes, and how it resolves the
bot it acts on — not from taste. ``test_principal_seam.py`` fails if the surface
and this table disagree in either direction, so a route added tomorrow is
refused until someone puts it in a group on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.adapters.http.openapi_v1.admission_modes import (
    AdmissionMode,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    GrantNotResolvableError,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.log import get_logger
logger = get_logger()
_SPACE_SKILL_BASE = "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}"
_SPACE_SKILL_PUBLICATION = f"{_SPACE_SKILL_BASE}/publications"
#: Every public operation, keyed by ``(method, path)`` exactly as FastAPI
#: reports it. Grouped by mode, with the reason each group has the mode it has.
#:
#: **This table has a counterpart at the edge.** The gateway's
#: ``route_security`` (``src/gateway/configs/application.yaml``) decides which
#: identities are *resolvable* for a path; this decides which operations admit a
#: machine caller once they arrive. Both must agree that a ``REFUSED`` operation
#: still requires a human — an operation left open at both hops because someone
#: edited only one is the hole the pair exists to prevent.
#:
#: The agreement is pinned on the gateway side
#: (``tests/unit/core/authn/test_route_security.py``), because that is where the
#: path matcher lives and a second implementation of "most specific" is exactly
#: what ``gateway/core/paths/_pattern.py`` exists to prevent. Change ``REFUSED``
#: here and that test is the one that will fail.
ADMISSION: dict[tuple[str, str], AdmissionMode] = {
    # Tenant-wide lookup returns display fields, never ownership or runtime internals.
    ("POST", "/openapi/v1/bots/metadata/queries"): AdmissionMode.OPEN,
    ("POST", "/openapi/v1/bots/{bot_id}/iam-token"): AdmissionMode.REFUSED,
    # Human-chat requires an end user; BCN friendship is checked by its handler.
    ("GET", "/openapi/v1/bots/{bot_id}/human-chat/sessions"): AdmissionMode.REFUSED,
    ("POST", "/openapi/v1/bots/{bot_id}/human-chat/sessions"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/{bot_id}/human-chat/sessions/favorites"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/{bot_id}/human-chat/sessions/{session_id}"): AdmissionMode.REFUSED,
    ("PATCH", "/openapi/v1/bots/{bot_id}/human-chat/sessions/{session_id}"): AdmissionMode.REFUSED,
    ("DELETE", "/openapi/v1/bots/{bot_id}/human-chat/sessions/{session_id}"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/{bot_id}/human-chat/sessions/{session_id}/connection"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/{bot_id}/human-chat/sessions/{session_id}/messages"): AdmissionMode.REFUSED,
    ("DELETE", "/openapi/v1/bots/{bot_id}/human-chat/sessions/{session_id}/messages"): AdmissionMode.REFUSED,
    ("PUT", "/openapi/v1/bots/{bot_id}/human-chat/sessions/{session_id}/favorite"): AdmissionMode.REFUSED,
    ("DELETE", "/openapi/v1/bots/{bot_id}/human-chat/sessions/{session_id}/favorite"): AdmissionMode.REFUSED,
    # The item routes resolve the addressed owner from the asset and perform
    # the grant check against that exact Bot/owner pair in their handler.
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/content",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    # ── own bot: names a bot, resolved as the delegating user's ──────────────
    # The caller can only ever reach their own bots here, so an application
    # acting as them can only reach the same ones.
    ("GET", "/openapi/v1/bots/{bot_id}"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("PUT", "/openapi/v1/bots/{bot_id}"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/space",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("DELETE", "/openapi/v1/bots/{bot_id}"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("POST", "/openapi/v1/bots/{bot_id}/restart"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    # The auth-status poll completes a pending creation, so it is a POST; the
    # GET row is its retiring spelling (deprecated/auth_status.py), the same
    # operation at the same address, kept while the old method still answers.
    ("POST", "/openapi/v1/bots/{bot_id}/auth-status"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/{bot_id}/auth-status"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/{bot_id}/status"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/{bot_id}/passport"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/engine/config",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/engine/config",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/startup-script",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/startup-script",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/startup-script",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("POST", "/openapi/v1/bots/{bot_id}/activate"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/{bot_id}/data-init"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    # The config manifest may address a *shared* bot: its collaborator bars are
    # MEMBER to read and ADMIN to write (authorization.py), so the owner arrives
    # on the wire rather than being pinned to the caller, and the grant is
    # checked against that addressed owner.
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/config-manifest",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/config-manifest",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/config-manifest",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/config-manifest/capabilities",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    # Apply's mode is not a free choice — it FOLLOWS from its Check(...) row.
    # ``require_check``'s gate declares OwnerIdDep -> resolve_owner_id ->
    # AddressedBotGrantDep, so any row carrying Check(...) pulls in
    # require_granted_addressed_bot, and test_admission_inventory holds each
    # route to the dependency its mode calls for. The surface has exactly two
    # coherent pairings, and mixing one from each is what principal.py calls its
    # oldest defect. Naming it because the pairing looks like two decisions and
    # is one.
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/config-manifest/apply",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/config-manifest/applies/{apply_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/config-manifest/last-apply",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skill-sets",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/skill-sets",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skill-sets/resources",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/activate",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/deactivate",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permissions",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permission-requests",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references"): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references"): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references/{reference_id}"): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/mcps",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/caller-context",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PATCH",
        "/openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PATCH",
        "/openapi/v1/bots/{bot_id}/clis/{cli_code}/call-type",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/mcps/{server_code}/activate",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/mcps/{server_code}/deactivate",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/data-init",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/{bot_id}/identity"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/identity/{file_type}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/identity/{file_type}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    # resources — every operation is bot-first and addressed by workspace path.
    # There are no record-id routes left: a record id cannot address a file the
    # bot created itself, and links are no longer part of this group.
    ("GET", "/openapi/v1/bots/{bot_id}/resources"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/resources",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/resources/stat",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/resources/upload",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/resources/download",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/{bot_id}/resources/download-dir"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/{bot_id}/resources/preview"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/resources/mkdir",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    # routines — query ``bot_id``, except the create, which carries it in the
    # path, so the shared dependency checks it like every other operation.
    ("GET", "/openapi/v1/bots/{bot_id}/routines"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("POST", "/openapi/v1/bots/{bot_id}/routines"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    # The owner-level aggregate lists the named user's fleet, not one bot —
    # gated on a live delegation like the ceiling (see owner_router).
    ("GET", "/openapi/v1/bots/routines/all"): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/routines/{routine_id}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "PATCH",
        "/openapi/v1/bots/{bot_id}/routines/{routine_id}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/routines/{routine_id}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/routines/{routine_id}/run",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/routines/{routine_id}/runs",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    # Skills consistently receive an owner-addressed Bot target.  The owner is
    # resolved at the HTTP boundary and all downstream reads use that same pair.
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skills",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/skills",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/skills/upload-folder",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    # channels — collaborators may address a shared Bot, while every write
    # additionally requires ADMIN permission in the handler. The application
    # grant is still checked against the addressed owner at the shared seam.
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/channels",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/channels",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/channels/{channel_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PATCH",
        "/openapi/v1/bots/{bot_id}/channels/{channel_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/channels/{channel_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/channels/{channel_id}/status",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/sessions",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/sessions",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PATCH",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}/messages",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}/messages",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/sessions/favorites",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files/upload-intents",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files/upload-complete",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files/{resource_id}/materialize-status",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files/{resource_id}/content",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files/{resource_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/engine/available",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/engine/capabilities",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/engine/status",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/engine/restart",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/approvals/mode",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/approvals/mode",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/approvals/modes",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/models",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/models/{model_id:path}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/nodes",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/connection",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/chats",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/chats/{trace_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/lifecycle/upgrade",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/lifecycle/{publication_id}/upgrade",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/lifecycle",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/lifecycle",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/lifecycle/approval",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/lifecycle/approval",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/lifecycle/advance",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/lifecycle/restart",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/lifecycle/cancel-staging",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/lifecycle/offline",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/lifecycle/retry",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/edit-lock",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/edit-lock",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/edit-lock",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/edit-lock/steal",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/containers",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/containers/{instance_id}/restart",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/diagnostics/health",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/diagnostics/health-check",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    # harness — bot-scoped diagnostics and patching under the addressed bot.
    # These operations now go through the addressed-bot grant seam so that both
    # human collaborators and delegated application callers can reach the bot,
    # with the owner resolved by the harness access dependency itself.
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/harness/diagnose",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/harness/preview",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/harness/apply",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/harness/rollback",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/harness/dim-report",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/harness/dim-history",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    # ── B: returns a set of bots, narrowed to the granted ones ───────────────
    ("GET", "/openapi/v1/bots"): AdmissionMode.GRANT_FILTERED,
    # The application's own view, and the **complete** one: a granted bot the
    # delegating user does not own appears in no listing of that user's bots, so
    # without this it would be undiscoverable.
    ("GET", "/openapi/v1/bots/authorized"): AdmissionMode.GRANT_FILTERED,
    # ── C: no bot dimension, but about the named user's account ──────────────
    ("GET", "/openapi/v1/bots/ceiling"): AdmissionMode.USER_GATED,
    # ── OPEN: no user on the wire, tenant-identical answer ───────────────────
    # Not a new exposure: every authenticated caller in the tenant already gets
    # the identical answer, and there is no user here to gate against.
    ("GET", "/openapi/v1/bots/check-name"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/bots/skills/repository"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/skills/repository/tree"): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/skills/repository/{skill_id}",
    ): AdmissionMode.USER_GATED,
    ("POST", "/openapi/v1/bots/skills/repository/sync"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/mcp/servers"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/bots/mcp/servers/{server_code}"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/bots/mcp/tenants"): AdmissionMode.OPEN,
    # New-version bcs publish-to-users: auth baseline only, no grant check; authz deferred.
    # Served at the external contract path the gateway verbatim-forwards here.
    ("POST", "/openapi/v1/collaboration/bots/{bot_uuid}/public"): AdmissionMode.OPEN,
    # Task public surface (execute/dashboard/list) — mounted together with the
    # gateway `collaboration-tasks` domain → backend. Tasks are not bot-scoped
    # (no grant), and the public face is open to any authenticated app caller
    # (a human principal is admitted regardless); `list` still scopes its answer
    # by the `user_id` it receives. OPEN is the contract label — at the
    # require_principal gate it behaves the same as USER_GATED.
    ("POST", "/openapi/v1/collaboration/tasks/execute"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/collaboration/tasks/dashboard"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/collaboration/tasks/list"): AdmissionMode.OPEN,
    # Grant/revoke are stateless relays to secbaas (api-key server-side; the
    # human Cookie/Referer authorizes the action) — OPEN at the gate, secbaas
    # authorizes. Same shape as the other task public operations.
    ("POST", "/openapi/v1/collaboration/tasks/grant"): AdmissionMode.OPEN,
    ("POST", "/openapi/v1/collaboration/tasks/revoke"): AdmissionMode.OPEN,
    # Department directory search — a tenant-wide catalogue read, not a user's.
    # ── Source credentials (W3, #1471) — OPEN, application-operated ─────────
    # These are machine operations, and the *edge* is what makes them so:
    # the gateway's route_security requires an app credential on every
    # path of the group (`user: optional, app: required`, the same shape
    # as /openapi/v1/bots/authorized), so the caller that arrives here is
    # an application of the tenant — there is no user axis for this table
    # to gate and no bot for a grant to address. The one identity that
    # matters, the owning application (rotation and delete are the
    # creating application's alone), is enforced in the service, against
    # the row.
    ("PUT", "/openapi/v1/bots/source-credentials/{name}"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/bots/source-credentials/{name}"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/bots/source-credentials"): AdmissionMode.OPEN,
    ("DELETE", "/openapi/v1/bots/source-credentials/{name}"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/org/dept"): AdmissionMode.OPEN,
    ("POST", "/openapi/v1/bots/market/skills"): AdmissionMode.OPEN,
    ("POST", "/openapi/v1/bots/market/mcp-servers"): AdmissionMode.OPEN,
    ("POST", "/openapi/v1/bots/market/skill-center/skills"): AdmissionMode.OPEN,
    ("POST", "/openapi/v1/bots/market/skill-center/sync"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/catalog/search"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/bots/catalog/discover"): AdmissionMode.OPEN,
    # ── Space and work-order APIs ───────────────────────────────────────────
    # Read and self-service operations resolve an application-only caller to
    # its delegating user and continue enforcing live Space membership or
    # work-order recipient checks. Space ownership initialization, team/member
    # administration, and work-order review remain human-only.
    ("GET", "/openapi/v1/bots/spaces"): AdmissionMode.USER_GATED,
    ("POST", "/openapi/v1/bots/spaces/personal/initialize"): AdmissionMode.REFUSED,
    ("POST", "/openapi/v1/bots/spaces/create"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/members"): AdmissionMode.USER_GATED,
    # The caller must arrive on behalf of a real user.  The handler then
    # resolves that user as the actor and SpaceAccessService enforces the
    # concrete space membership; this is deliberately not OPEN or
    # GRANT_FILTERED because the result is scoped by the path's space_id.
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills"): AdmissionMode.USER_GATED,
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills"): AdmissionMode.REFUSED,
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/import-from-git"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/consumable"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files/{path:path}"): AdmissionMode.USER_GATED,
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/copy"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path:path}"): AdmissionMode.USER_GATED,
    ("PUT", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path:path}"): AdmissionMode.REFUSED,
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/upgrade"): AdmissionMode.REFUSED,
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/refresh-from-git"): AdmissionMode.REFUSED,
    ("DELETE", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline-impact"): AdmissionMode.REFUSED,
    ("POST", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/grants"): AdmissionMode.USER_GATED,
    ("PUT", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}"): AdmissionMode.REFUSED,
    ("DELETE", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}"): AdmissionMode.REFUSED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/owner-transfer",
    ): AdmissionMode.REFUSED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-requests",
    ): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease",
    ): AdmissionMode.REFUSED,
    (
        "PUT",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease",
    ): AdmissionMode.REFUSED,
    (
        "DELETE",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease",
    ): AdmissionMode.REFUSED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease/takeover",
    ): AdmissionMode.REFUSED,
    ("GET", f"{_SPACE_SKILL_BASE}/publication-impact"): AdmissionMode.REFUSED,
    ("POST", _SPACE_SKILL_PUBLICATION): AdmissionMode.REFUSED,
    ("GET", _SPACE_SKILL_PUBLICATION): AdmissionMode.REFUSED,
    ("GET", f"{_SPACE_SKILL_PUBLICATION}/{{attempt_id}}"): AdmissionMode.REFUSED,
    ("POST", f"{_SPACE_SKILL_PUBLICATION}/{{attempt_id}}/retry"): AdmissionMode.REFUSED,
    ("POST", "/openapi/v1/bots/spaces/{space_id}/members"): AdmissionMode.REFUSED,
    (
        "DELETE",
        "/openapi/v1/bots/spaces/{space_id}/members/{member_user_id}",
    ): AdmissionMode.REFUSED,
    (
        "PUT",
        "/openapi/v1/bots/spaces/{space_id}/members/{member_user_id}/role",
    ): AdmissionMode.REFUSED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/market-favorites",
    ): AdmissionMode.USER_GATED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/market-favorites/cancel",
    ): AdmissionMode.USER_GATED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/market-favorites/search",
    ): AdmissionMode.USER_GATED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/market-favorites/status",
    ): AdmissionMode.USER_GATED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/join-requests",
    ): AdmissionMode.REFUSED,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/editor-requests",
    ): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/skills/{skill_code}/publish/status"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/bots/skills/{skill_id}/readme"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/market/skill-center/tags"): AdmissionMode.OPEN,
    ("POST", "/openapi/v1/bots/work-orders/events"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/work-orders"): AdmissionMode.USER_GATED,
    ("GET", "/openapi/v1/bots/work-orders/{work_order_id}"): AdmissionMode.USER_GATED,
    ("POST", "/openapi/v1/bots/work-orders/{work_order_id}/approval"): AdmissionMode.USER_GATED,
    (
        "POST",
        "/openapi/v1/bots/work-orders/{work_order_id}/approve",
    ): AdmissionMode.REFUSED,
    (
        "POST",
        "/openapi/v1/bots/work-orders/{work_order_id}/reject",
    ): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/work-order-notifications/{notification_id}",
    ): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/work-order-notifications/unread-count",
    ): AdmissionMode.USER_GATED,
    (
        "POST",
        "/openapi/v1/bots/work-order-notifications/{notification_id}/read",
    ): AdmissionMode.USER_GATED,
    (
        "POST",
        "/openapi/v1/bots/work-order-notifications/read-all",
    ): AdmissionMode.USER_GATED,
    # ── Workshop/local admission follows the operation's shape ───────────────
    # Both listings are owner-scoped, so an application sees only the named
    # user's own bots that user delegated to it. The restriction is applied by
    # the services before pagination; an application granted nothing gets an
    # empty page.
    ("GET", "/openapi/v1/bots/all"): AdmissionMode.GRANT_FILTERED,
    ("GET", "/openapi/v1/bots/local"): AdmissionMode.GRANT_FILTERED,
    # Device discovery names no bot but does expose the named user's account.
    # A live delegation from that user proves the relationship; no delegation
    # is masked as not-found before the desktop service is called.
    ("GET", "/openapi/v1/bots/local/devices"): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/local/devices/{machine_id}/files",
    ): AdmissionMode.USER_GATED,
    # Existing local-bot operations resolve the bot as the delegating user's
    # own, exactly like the ordinary bot lifecycle routes above.
    ("GET", "/openapi/v1/bots/{bot_id}/local"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/local/restart",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("DELETE", "/openapi/v1/bots/{bot_id}/local"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/local/open-folder",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    # A Bot grant lends the delegating user's live Bot permissions. Editor and
    # render-screen operations therefore admit an application only after the
    # addressed Bot/owner grant is proven; the domain services still enforce
    # the delegator's effective Owner/Admin/Member level for the requested act.
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/editors",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/editors",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PATCH",
        "/openapi/v1/bots/{bot_id}/editors/{editor_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/editors/{editor_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/editors/me",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/render-screens",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/render-screens",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PATCH",
        "/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    # ── REFUSED — each for its own reason ────────────────────────────────────
    # The caller's own identity. An app-only caller names no end user, so there
    # is nothing to return — its scope question is answered by
    # ``GET /openapi/v1/bots/authorized`` instead.
    ("GET", "/openapi/v1/org/user"): AdmissionMode.REFUSED,
    # Local creation has no existing bot for a grant to cover and may initiate
    # Passport consent. Polling completes that same creation transaction, so
    # both require a human on the wire.
    ("POST", "/openapi/v1/bots/local"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/{bot_id}/local/auth-status"): AdmissionMode.REFUSED,
    # No bot exists yet for a grant to cover, and creation spends the user's
    # quota. Auto-granting the new bot would invent consent nobody gave.
    ("POST", "/openapi/v1/bots"): AdmissionMode.REFUSED,
    # Creating a bot with its manifest is the same creation, so it carries the
    # same refusal — and the reason is sharper here than "no grant to check".
    #
    # These routes take their owner from ``UserIdDep``. For an app-only caller
    # ``require_user_id`` returns the ``user_id`` **query parameter verbatim**,
    # deferring the question of whether the app may act for that user to
    # "whichever grant dependency the route declares". There is no grant
    # dependency these routes *can* declare: a grant covers a bot, and at
    # submission there is no bot. So admitting an app here would mean any
    # application credential could create a bot as any user — spending that
    # user's quota and running a caller-supplied startup script under their
    # identity — with nothing anywhere proving the user ever authorized it.
    #
    # W13's reviewer asked for these to admit applications, and they should:
    # this pair exists for unattended integration. But "admit" needs a check
    # that can run *before* a bot exists, and the surface has none today. That
    # is the user-level admission mode the review names as future work, not
    # something ``OPEN`` provides — ``OPEN`` here would be no check at all.
    #
    # The poll carries the same refusal for the same mechanism: its ``user_id``
    # is equally unchecked for an app-only caller, and what it returns includes
    # the authorization handles.
    ("POST", "/openapi/v1/bots/with-manifest"): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/with-manifest/status",
    ): AdmissionMode.REFUSED,
    # Delegation is a human act. An application must not be able to widen its
    # own access, withdraw a competitor's, or enumerate what else reaches a bot.
    ("POST", "/openapi/v1/bots/{bot_id}/authorized-apps"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/{bot_id}/authorized-apps"): AdmissionMode.REFUSED,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/authorized-apps/{app_id}",
    ): AdmissionMode.REFUSED,
    # Bot logs: here ``user_id`` means *whose traces to read* over a
    # tenant-level observability surface, not *whose call this is*. A grant
    # covers a bot; it does not translate into that meaning.
    ("GET", "/openapi/v1/bots/logs/traces"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/logs/traces/{trace_id}"): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/logs/sessions/{session_key}/traces",
    ): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/logs/groups/{group_id}/traces"): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/logs/tasks/{biz_scene}/{biz_task_id}/traces",
    ): AdmissionMode.REFUSED,
    # MCP *configuration* — account-level state with no bot dimension. A grant
    # is consent to reach a bot, not to reconfigure an account. (The catalogue
    # reads above are a different thing and are OPEN.)
    ("GET", "/openapi/v1/bots/mcp/servers/{server_code}/config"): AdmissionMode.REFUSED,
    ("PUT", "/openapi/v1/bots/mcp/servers/{server_code}/config"): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/mcp/servers/{server_code}/permissions",
    ): AdmissionMode.REFUSED,
    # Load-test endpoints: no user scope, no bot, and nothing this feature is
    # about. Left exactly as they were.
    ("GET", "/openapi/v1/bots/loadtest/hello"): AdmissionMode.REFUSED,
    ("WEBSOCKET", "/openapi/v1/bots/loadtest/ws/echo"): AdmissionMode.REFUSED,
}

#: Kept as an explicit empty set so the admission-inventory test continues to
#: make any future handler-level grant exception visible in review.
SKILL_SCOPED_OPERATIONS = frozenset()

#: Harness operations resolve the bot owner from the repository record rather
#: than from an ``owner_id`` query parameter. This preserves the existing wire
#: contract (``bot_id`` on the path, ``user_id`` in the query) while still
#: running an addressed-bot grant check for application callers inside the
#: handler's own ``require_harness_bot_access`` dependency.
HARNESS_SCOPED_OPERATIONS = frozenset({
    ("POST", "/openapi/v1/bots/{bot_id}/harness/diagnose"),
    ("POST", "/openapi/v1/bots/{bot_id}/harness/preview"),
    ("POST", "/openapi/v1/bots/{bot_id}/harness/apply"),
    ("POST", "/openapi/v1/bots/{bot_id}/harness/rollback"),
    ("GET", "/openapi/v1/bots/{bot_id}/harness/dim-report"),
    ("GET", "/openapi/v1/bots/{bot_id}/harness/dim-history"),
})

#: The modes that admit a caller naming no end user. Everything else refuses at
#: ``require_principal``, which is what a route inherits by saying nothing.
ADMITTING_MODES = frozenset(
    {
        AdmissionMode.GRANT_CHECKED_OWN_BOT,
        AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
        AdmissionMode.GRANT_FILTERED,
        AdmissionMode.USER_GATED,
        AdmissionMode.OPEN,
    }
)


@dataclass(frozen=True)
class ActingCaller:
    """Who a request acts for, and what the calling application may reach.

    Built once per request by the seam in ``principal.py``. It carries the two
    ids the surface scopes by, and the one question only a grant can answer.
    """

    #: The end user this request acts for. For a human caller, themselves; for
    #: an application, the user who delegated to it. Downstream code cannot tell
    #: the difference, which is the point — an admitted application is that user
    #: for the length of the request, bounded by their live access.
    user_id: str

    #: The calling application, or ``None`` for a human caller.
    #:
    #: ``None`` is a real state of the contract — "no grant applies to this
    #: caller" — rather than a widened type, and every consumer branches on it
    #: explicitly. That is what keeps a human from being resolved against a
    #: grant, and an application from falling through to an unscoped read.
    app_id: int | None

    #: The grant reader. Never consulted for a human caller.
    grants: BotAppGrantServiceProtocol | None = None

    @property
    def is_application(self) -> bool:
        """Whether a grant governs this request."""
        return self.app_id is not None

    def require_bot(self, bot_id: str, *, owner_id: str) -> str:
        """Confirm the application may act on the addressed bot; return its owner.

        **It returns the owner, never the delegating user**, and the two are the
        same person only when someone addresses their own bot. The value is
        ``owner_id`` — the bot this request addresses — handed back once it is
        known to be covered:

        - a **human** caller is not governed by a grant, so there is nothing to
          check and the addressed owner passes straight through.
        - an **application** must hold a live grant for
          ``(app, bot, owner, delegating user)``. The grant's own ``owner_id``
          equals the one passed in, because that is what it was looked up by —
          so the returned value is the same either way, and the difference is
          only whether it was allowed to be returned at all.

        The engine-runtime groups consume it as the owner of the bot they are
        about to operate; the user-scoped groups discard it, having addressed
        their own bot by construction.

        A missing grant raises :class:`GrantNotResolvableError`, which the app
        maps to a ``404`` byte-identical to a nonexistent bot: an application
        must not be able to tell a bot it was not granted from one that does not
        exist.

        **The pair is the address, and the lookup takes the pair.** ``ac_bots``
        has no unique key on ``bot_id`` — the legacy ``default`` convention gave
        many owners one — so a probe keyed on the id alone asks a question with
        more than one answer. An earlier revision did exactly that and compared
        ``record.owner_id`` afterwards: safe while the unique key happened to
        make the row singular, and it foreclosed ever keying the record on the
        bot's real identity, because the read could not have supplied it.

        A grant is not the whole answer. It says the delegation exists; whether
        the delegating user may still operate the bot is asked separately, live,
        by the same gate they would face — which is what stops a delegation
        outliving the access it lends.
        """
        if self.app_id is None:
            return owner_id
        if self.grants is None:
            # Unreachable through the seam, which always supplies the reader for
            # an application. Refusing rather than defaulting, because the
            # alternative to a grant check here is no check at all.
            raise GrantNotResolvableError("no grant reader for an application caller")
        record = self.grants.find(
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=self.user_id,
            app_id=self.app_id,
        )
        if record is None:
            # Which user, which owner and which bot were asked for go to the
            # **log**, not into the exception message. The message is carried
            # into a log line verbatim by the handlers in ``app.py`` and
            # ``error_logging.py``, and all three are caller-chosen and
            # unbounded — so interpolating them there would let the party being
            # refused inject extra log lines and choose how many bytes each
            # refusal costs.
            #
            # ``app_id`` is safe to name: it is an int off the verified
            # principal, not something the request supplied.
            logger.warning(
                "[bot_app_grant] app_id=%s holds no live grant from user=%s "
                "on bot=%s owned by=%s",
                self.app_id,
                for_log(self.user_id),
                for_log(bot_id),
                for_log(owner_id),
            )
            raise GrantNotResolvableError(
                f"app {self.app_id} holds no live grant for the requested bot"
            )
        return record.owner_id

    def granted_bot_ids(
        self, *, owned_by_delegator: bool = False
    ) -> frozenset[str] | None:
        """The bots to narrow a listing to, or ``None`` to not narrow at all.

        ``None`` and the empty set are different answers and must stay so:
        ``None`` is a human caller, whose listing is not filtered; an empty set
        is an application that has been granted nothing, whose listing is empty.
        Collapsing them would hand an ungranted application the delegating
        user's entire bot list.

        ``owned_by_delegator`` narrows further, to grants naming the delegating
        user as the bot's owner, and an **owner-scoped listing must set it**.
        The ids are bare ``bot_id`` strings, and ``bot_id`` is not unique across
        owners: filtering an owner-scoped query by a set that includes someone
        else's ``default`` matches the delegating user's own ``default`` and
        returns a bot nobody granted. Nothing is lost by narrowing — an
        owner-scoped listing cannot show a bot the user does not own anyway.

        Callers that only ask *whether any delegation exists* leave it off:
        there the question is about the relationship, not about which bots.
        """
        if self.app_id is None:
            return None
        if self.grants is None:
            return frozenset()
        records = self.grants.list_for_app(app_id=self.app_id, user_id=self.user_id)
        return frozenset(
            record.bot_id
            for record in records
            if not owned_by_delegator or record.owner_id == self.user_id
        )


__all__ = [
    "ADMISSION",
    "ADMITTING_MODES",
    "HARNESS_SCOPED_OPERATIONS",
    "SKILL_SCOPED_OPERATIONS",
    "ActingCaller",
    "AdmissionMode",
]
