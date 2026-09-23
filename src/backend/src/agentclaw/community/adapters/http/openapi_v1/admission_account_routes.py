"""Account/local and retiring operation admission, composed into ADMISSION."""

from .admission_modes import AdmissionMode

ACCOUNT_ADMISSION: dict[tuple[str, str], AdmissionMode] = {
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
    # ── USER_DELEGATED: acts for the named user, addresses no bot yet ────────
    # The creations. None of these has a bot for a grant to cover when the
    # request arrives: the id is allocated inside the handler, and for most of
    # a creation's life there is no bot record at all. These used to be
    # ``REFUSED`` for exactly that reason — admitting an application needed a
    # check able to authorize an app→user pair *before* a bot exists, and the
    # surface had none.
    #
    # It has one now. ``require_delegated_user`` asks the user-level
    # delegation (``ac_user_app_grant``): has this person authorized this
    # application to act as them where no bot is addressed? Without a live row
    # the application is answered as if the user did not exist, the same 404 a
    # missing bot grant gets — so guessing a ``user_id`` still buys nothing.
    #
    # And once admitted, **the application is granted the bot it creates**, as
    # an ordinary bot grant written when the creation starts
    # (``creation_grant.grant_the_creating_app``). That is what lets the polls
    # below — bot-scoped, and carrying the authorization handles — take the
    # own-bot dependency like every other bot operation rather than a special
    # case: by the time an application polls, the grant it needs already exists.
    # The bot's owner sees that grant in the bot's own listing and can withdraw
    # it there, exactly as one they consented to by hand.
    ("POST", "/openapi/v1/bots"): AdmissionMode.USER_DELEGATED,
    ("POST", "/openapi/v1/bots/with-manifest"): AdmissionMode.USER_DELEGATED,
    ("POST", "/openapi/v1/bots/local"): AdmissionMode.USER_DELEGATED,
    # The creations' polls. Each completes or observes a creation the
    # application was granted at submission, so each resolves the bot as the
    # delegating user's own and checks that grant — the same dependency the
    # ordinary ``auth-status`` poll above has carried all along.
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/with-manifest/status",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/local/auth-status",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    # ── REFUSED — each for its own reason ────────────────────────────────────
    # The caller's own identity. An app-only caller names no end user, so there
    # is nothing to return — its scope question is answered by
    # ``GET /openapi/v1/bots/authorized`` instead.
    ("GET", "/openapi/v1/org/user"): AdmissionMode.REFUSED,
    # Delegation is a human act. An application must not be able to widen its
    # own access, withdraw a competitor's, or enumerate what else reaches a bot.
    ("POST", "/openapi/v1/bots/{bot_id}/authorized-apps"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/{bot_id}/authorized-apps"): AdmissionMode.REFUSED,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/authorized-apps/{app_id}",
    ): AdmissionMode.REFUSED,
    # The user-level delegation — the consent the ``USER_DELEGATED`` group
    # above is admitted on — is granted, listed and withdrawn by the user
    # alone, for the same reason the bot-level group is: an application must
    # not be able to delegate to itself.
    ("POST", "/openapi/v1/bots/authorized-apps"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/authorized-apps"): AdmissionMode.REFUSED,
    ("DELETE", "/openapi/v1/bots/authorized-apps/{app_id}"): AdmissionMode.REFUSED,
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
    # ── Pinned retiring addresses ──────────────────────────────────────────
    # These resources and routines addresses predate bot-first addressing —
    # their bots travel as query or body parameters their paths cannot offer a
    # ``Check`` gate. When their replacements moved onto the seam and gained an
    # addressed owner, their retiring shims stayed owner-resolved
    # (``deprecated._requery.pin_owner_to_user``), so the mode that matches
    # what they enforce is still the own-bot one. Written out rather than
    # derived because they diverge from their replacements' modes: the
    # derivation below fills only what is not already decided here.
    ("GET", "/openapi/v1/bots/resources"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("DELETE", "/openapi/v1/bots/resources"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/resources/download"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("POST", "/openapi/v1/bots/resources/mkdir"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/resources/preview"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/resources/stat"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("POST", "/openapi/v1/bots/resources/upload"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("GET", "/openapi/v1/bots/routines"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    ("POST", "/openapi/v1/bots/routines"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "DELETE",
        "/openapi/v1/bots/routines/{routine_id}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/routines/{routine_id}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "PATCH",
        "/openapi/v1/bots/routines/{routine_id}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "POST",
        "/openapi/v1/bots/routines/{routine_id}/run",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/routines/{routine_id}/runs",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
}
