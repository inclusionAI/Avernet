"""Authorization rows for the collaboration-task surface; re-export by AUTHORIZATION."""

from .models_authorization import Authorization, NoCheck

#: Execute/dashboard/list and stateless secbaas-relay rows.
TASK_PUBLIC_AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    # ── Task public surface (execute/dashboard/list + grant/revoke stateless relay; mounted via the gateway
    # ── `collaboration-tasks` domain → backend). Tasks address no bot, so the
    # ── bot-level `Check` does not apply; the caller identity is resolved by
    # ── the gateway spanner + `_PUBLIC_AUTH`; `list` uses caller-selected
    # ── user_id only as a task-record filter. `NoCheck` is the settled mode here, not a
    # ── placeholder — see `admission.py` for the machine-caller decision.
    ("POST", "/openapi/v1/collaboration/tasks/execute"): NoCheck(
        "a task, not a bot; the submitter is the task owner"
    ),
    ("GET", "/openapi/v1/collaboration/tasks/dashboard"): NoCheck(
        "a task, not a bot; read-only task graph by task_id"
    ),
    ("GET", "/openapi/v1/collaboration/tasks/list"): NoCheck(
        "a task, not a bot; filters records by the named user"
    ),
    ("GET", "/openapi/v1/collaboration/tasks/bbs/list"): NoCheck(
        "a task, not a bot; paged read of BBS relay tasks"
    ),
    ("POST", "/openapi/v1/collaboration/tasks/grant"): NoCheck(
        "a stateless relay to secbaas; the human Cookie/Referer authorizes the grant, not a bot permission"
    ),
    ("POST", "/openapi/v1/collaboration/tasks/revoke"): NoCheck(
        "a stateless relay to secbaas; the human Cookie/Referer authorizes the revoke, not a bot permission"
    ),
}

#: BBS, Browse-Loop, feedback, and trajectory rows.
TASK_BBS_AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    # ── BBS content routes (community iteration) ──────────────────────────
    ("GET", "/openapi/v1/collaboration/tasks/bbs/topics"): NoCheck(
        "tenant-wide BBS Topic catalogue; no addressed bot"
    ),
    ("GET", "/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}"): NoCheck(
        "tenant-wide BBS Topic read; no addressed bot"
    ),
    ("GET", "/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts"): NoCheck(
        "tenant-wide BBS reply read; no addressed bot"
    ),
    ("POST", "/openapi/v1/collaboration/tasks/bbs/topics"): NoCheck(
        "public BBS write; author declared in the body, no addressed bot"
    ),
    ("POST", "/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts"): NoCheck(
        "public BBS reply write; author declared in the body, no addressed bot"
    ),
    ("POST", "/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/close"): NoCheck(
        "public BBS Topic close; author declared in the body, handler verifies the stored author"
    ),
    # BBS Browse-Loop toggle: addressed-Bot writes; owner declared via owner_user_id query
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-subscription",
    ): NoCheck(
        "public BBS browse-subscription join; operator declared as owner_user_id query, no addressed-owner grant on the wire"
    ),
    (
        "DELETE",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-subscription",
    ): NoCheck(
        "public BBS browse-subscription cancel; bot_id path-only, no addressed-owner grant on the wire"
    ),
    ("GET", "/openapi/v1/collaboration/tasks/bbs/browse-subscriptions"): NoCheck(
        "per-user BBS Browse-Loop subscription list scoped to the caller own work-no; no addressed bot"
    ),
    (
        "GET",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-subscription",
    ): NoCheck(
        "public per-Bot subscription read; owner is not assumed from the principal, no addressed owner on the wire"
    ),
    ("GET", "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/feed"): NoCheck(
        "public per-Bot Browse-Loop feed read; no addressed owner on the wire"
    ),
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/skills/install",
    ): NoCheck(
        "public BBS skill-install trigger; the addressed owner is resolved server-side from the subscription"
    ),
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-loop/trigger-framework",
    ): NoCheck("public A-mode one-shot Browse trigger; no addressed owner on the wire"),
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-loop/trigger-self",
    ): NoCheck("public B-mode one-shot Browse trigger; no addressed owner on the wire"),
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-loop/cron-register",
    ): NoCheck("public B-mode cron-register trigger; no addressed owner on the wire"),
    (
        "POST",
        "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-loop/cron-remove",
    ): NoCheck("public B-mode cron-remove trigger; no addressed owner on the wire"),
    ("POST", "/openapi/v1/collaboration/tasks/bbs/browse-reports"): NoCheck(
        "public BBS browse-run outcome report; bot_id declared in the body, no addressed bot"
    ),
    # Feedback channel (BBS experimental feedback shared by every module)
    ("GET", "/openapi/v1/collaboration/tasks/feedback"): NoCheck(
        "tenant-wide feedback read; no addressed bot"
    ),
    ("POST", "/openapi/v1/collaboration/tasks/feedback"): NoCheck(
        "public feedback write; reporter declared in the body, no addressed bot"
    ),
    ("GET", "/openapi/v1/collaboration/tasks/trajectory"): NoCheck(
        "a task, not a bot; read-only task trajectory by task_id (do_analysis=true triggers a deployment-configured analysis bot, not a caller-chosen bot)"
    ),
}
