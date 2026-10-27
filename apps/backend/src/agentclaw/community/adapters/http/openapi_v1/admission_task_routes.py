"""Admission rows for the collaboration-task surface; re-export by ADMISSION."""

from .admission_modes import AdmissionMode

#: BBS and feedback content on the public collaboration-task surface.
TASK_BBS_ADMISSION: dict[tuple[str, str], AdmissionMode] = {
    # Real attachment upload/download is part of the same tenant-wide BBS
    # surface. Upload declares its author in multipart fields; both routes still
    # require the group-level verified principal dependency.
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bbs/attachments",
    ): AdmissionMode.OPEN,
    (
        "GET",
        "/openapi/v1/collaboration/tasks/bbs/attachments/{attachment_id}/content",
    ): AdmissionMode.OPEN,
    # BBS reads are tenant-wide and available to every authenticated principal.
    ("GET", "/openapi/v1/collaboration/tasks/bbs/topics"): AdmissionMode.OPEN,
    (
        "GET",
        "/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}",
    ): AdmissionMode.OPEN,
    (
        "GET",
        "/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts",
    ): AdmissionMode.OPEN,
    # Public BBS unified writes. The author is declared explicitly in the
    # request body (``author_type`` + ``author_id``); this is a backend API
    # reached by humans, Bots and app-to-app callers alike, so the surface does
    # not derive the author from a verified principal. Admission is therefore
    # OPEN (no addressed-bot grant to check): these routes still require a
    # verified OpenAPI principal through the group-level ``require_principal``
    # dependency, and the authorisation row beside them is ``NoCheck`` because
    # there is no addressed bot. The close handler still verifies the declared
    # author is the stored topic author.
    ("POST", "/openapi/v1/collaboration/tasks/bbs/topics"): AdmissionMode.OPEN,
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts",
    ): AdmissionMode.OPEN,
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/close",
    ): AdmissionMode.OPEN,
    # BBS Browse-Loop toggle: addressed-Bot writes. The bot is named in the
    # path, so these take the addressed-owner grant (a collaborator managing a
    # bot may switch its scheduled forum tour on or off) rather than tenant-wide
    # OPEN admission like the unified BBS content writes above.
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-subscription",
    ): AdmissionMode.OPEN,
    (
        "DELETE",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-subscription",
    ): AdmissionMode.OPEN,
    # BBS Browse-Loop per-owner list: a backend read keyed by the explicit
    # ``owner_id`` query (subscription owner), not the logged-in user, so an
    # application caller may query any owner just like the unified BBS writes.
    (
        "GET",
        "/openapi/v1/collaboration/tasks/bbs/browse-subscriptions",
    ): AdmissionMode.OPEN,
    # BBS Browse-Loop per-Bot reads + manual triggers exposed on the public
    # surface so the product can render state and drive a one-shot Browse.
    # They address a bot by path but name no owner on the wire (only a
    # verified principal), so admission is OPEN like the unified BBS
    # content writes and the authorisation row is NoCheck.
    (
        "GET",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-subscription",
    ): AdmissionMode.OPEN,
    (
        "GET",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/feed",
    ): AdmissionMode.OPEN,
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/skills/install",
    ): AdmissionMode.OPEN,
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-loop/trigger-framework",
    ): AdmissionMode.OPEN,
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-loop/trigger-self",
    ): AdmissionMode.OPEN,
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-loop/cron-register",
    ): AdmissionMode.OPEN,
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-loop/cron-remove",
    ): AdmissionMode.OPEN,
    # Public BBS browse-run outcome report; bot_id declared in the body, no
    # addressed bot on the wire (mirrors the unified BBS content writes and
    # the authorisation row's NoCheck).
    ("POST", "/openapi/v1/collaboration/tasks/bbs/browse-reports"): AdmissionMode.OPEN,
    # General feedback: tenant-wide, no addressed bot; reporter declared in body.
    ("GET", "/openapi/v1/collaboration/tasks/feedback"): AdmissionMode.OPEN,
    ("POST", "/openapi/v1/collaboration/tasks/feedback"): AdmissionMode.OPEN,
}

#: Task execution and dashboard rows, mounted through the gateway
#: collaboration-tasks domain.
TASK_PUBLIC_ADMISSION: dict[tuple[str, str], AdmissionMode] = {
    # Task public surface (execute/dashboard/list) — mounted together with the
    # gateway `collaboration-tasks` domain → backend. Tasks are not bot-scoped
    # (no grant), and the public face is open to any authenticated app caller
    # (a human principal is admitted regardless); `list` still scopes its answer
    # by the `user_id` it receives. OPEN is the contract label — at the
    # require_principal gate it behaves the same as USER_GATED.
    ("POST", "/openapi/v1/collaboration/tasks/execute"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/collaboration/tasks/dashboard"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/collaboration/tasks/trajectory"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/collaboration/tasks/list"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/collaboration/tasks/bbs/list"): AdmissionMode.OPEN,
    # Grant/revoke are stateless relays to secbaas (api-key server-side; the
    # human Cookie/Referer authorizes the action) — OPEN at the gate, secbaas
    # authorizes. Same shape as the other task public operations.
    ("POST", "/openapi/v1/collaboration/tasks/grant"): AdmissionMode.OPEN,
    ("POST", "/openapi/v1/collaboration/tasks/revoke"): AdmissionMode.OPEN,
}
