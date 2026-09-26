"""Public operation admission table; re-exported by admission."""

from .admission_modes import AdmissionMode
from .admission_account_routes import ACCOUNT_ADMISSION

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
    ("GET", "/openapi/v1/bots/{bot_id}/avatar"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("PUT", "/openapi/v1/bots/{bot_id}/avatar"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/{bot_id}/links"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("POST", "/openapi/v1/bots/{bot_id}/links"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/links/{resource_id}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/links/{resource_id}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/local/start-progress",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/metadata/digital-employees"): AdmissionMode.OPEN,
    (
        "GET",
        "/openapi/v1/bots/metadata/digital-employees/{agent_id}",
    ): AdmissionMode.OPEN,
    # Tenant-wide lookup returns display fields, never ownership or runtime internals.
    ("POST", "/openapi/v1/bots/metadata/queries"): AdmissionMode.OPEN,
    ("POST", "/openapi/v1/bots/{bot_id}/iam-token"): AdmissionMode.REFUSED,
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
    ("GET", "/openapi/v1/bots/{bot_id}"): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    ("PUT", "/openapi/v1/bots/{bot_id}"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/space",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("DELETE", "/openapi/v1/bots/{bot_id}"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/restart",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    # The auth-status poll completes a pending creation, so it is a POST; the
    # GET row is its retiring spelling (deprecated/auth_status.py), the same
    # operation at the same address, kept while the old method still answers.
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/auth-status",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/auth-status",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/status",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
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
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/startup-script",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/startup-script",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/activate",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/recycle",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/data-init",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
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
    # W9's CLI tools sit beside the config manifest and for the same reason:
    # collaborator-scoped (MEMBER to read, ADMIN to write), so the owner arrives
    # on the wire and the grant is checked against that addressed owner.
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/cli-tools",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/cli-tools",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/cli-tools/{name}",
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
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references/{reference_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
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
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/identity",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/identity/{file_type}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/identity/{file_type}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    # resources — every operation is bot-first and addressed by workspace path.
    # There are no record-id routes left: a record id cannot address a file the
    # bot created itself, and links are no longer part of this group.
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/resources",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/resources",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/resources/stat",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/resources/upload",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/resources/download",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/resources/download-dir",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/resources/preview",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/resources/mkdir",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    # routines — query ``bot_id``, except the create, which carries it in the
    # path, so the shared dependency checks it like every other operation.
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/routines",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/routines",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    # The owner-level aggregate lists the named user's fleet, not one bot —
    # gated on a live delegation like the ceiling (see owner_router).
    ("GET", "/openapi/v1/bots/routines/all"): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/routines/{routine_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "PATCH",
        "/openapi/v1/bots/{bot_id}/routines/{routine_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/routines/{routine_id}",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "POST",
        "/openapi/v1/bots/{bot_id}/routines/{routine_id}/run",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/routines/{routine_id}/runs",
    ): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
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
    ("GET", "/openapi/v1/collaboration/tasks/bbs/list"): AdmissionMode.OPEN,
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
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/skills/import-from-git",
    ): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}",
    ): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/consumable",
    ): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions",
    ): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}",
    ): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files",
    ): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files/{path:path}",
    ): AdmissionMode.USER_GATED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/copy",
    ): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files",
    ): AdmissionMode.USER_GATED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path:path}",
    ): AdmissionMode.USER_GATED,
    (
        "PUT",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path:path}",
    ): AdmissionMode.REFUSED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/upgrade",
    ): AdmissionMode.REFUSED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/refresh-from-git",
    ): AdmissionMode.REFUSED,
    (
        "DELETE",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft",
    ): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline-impact",
    ): AdmissionMode.REFUSED,
    (
        "POST",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline",
    ): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/grants",
    ): AdmissionMode.USER_GATED,
    (
        "PUT",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}",
    ): AdmissionMode.REFUSED,
    (
        "DELETE",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}",
    ): AdmissionMode.REFUSED,
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
    (
        "POST",
        "/openapi/v1/bots/work-orders/{work_order_id}/approval",
    ): AdmissionMode.USER_GATED,
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
    **ACCOUNT_ADMISSION,
}
