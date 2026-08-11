# `agentclaw.community.core.bot_app_grant`

Owner-granted authorizations letting one third-party application reach one bot.

A bot's owner authorizes a named application, sees which applications are
authorized, and withdraws an authorization. The record this module owns is what
a later machine-only call path will be checked against; **nothing here admits
such a caller today** — every route over it still requires an end user.

Two tables, and the split is the design rather than a filing decision. An
authorization has to answer two questions that pull against each other: *may
this app reach this bot right now* — one answer, which wants a unique key — and
*when could it, historically* — unboundedly many answers, which wants no key at
all. `ac_bot_app_grant` holds live grants only, so a row exists if and only if
access is in force; `ac_bot_app_grant_log` is append-only with no unique key,
because its job is to accept every event including the fourth revocation of one
pair. See `models.py` for why soft-deleting a single table cannot serve both.

Nothing here reads a framework, a request, or an HTTP status (Rule 7). The HTTP
seam is `adapters/http/openapi_v1/authorized_apps/`; the Service API contract the
adapter depends on is `api/bot_app_grant_service.py`.

## Context Boundary

```yaml
purpose: Own the record that says which application an owner has authorized to reach which bot, and the rules for granting, withdrawing and reading it.
provides:
  - BotAppGrantModel
  - BotAppGrantLogModel
  - BotAppGrantRecord
  - GrantAction
  - BotAppGrantService
  - GrantNotFoundError
consumes:
  - "BotAppGrantRepositoryProtocol (core.repository) — persistence for both tables"
  - "BotRepository (core.repository) — live-bot ids, so a grant outliving its bot is not reported as access"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.log
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```

### Change impact

This module decides who may reach a bot without a human present, so a change
here is a change to an authorization boundary rather than to a data model.

**The tenant guard is load-bearing and is not this module's own code.** Both
tables call `register_avernet_tenant_guard`, which confines every read to the
request's tenant and *refuses* an insert naming another. That is where this
feature's cross-tenant guarantee lives — not in a comparison the service makes,
which is why no such comparison exists. Removing a registration would silently
remove the guarantee, and the log's registration is the less obvious of the two:
it is read *after* the live row is deleted, so it has no guarded parent left to
inherit isolation from at the moment it matters most.

**Widths are not free to change.** `owner_id` is 256 because it is *in* the
unique key, and widening it to `ac_bots`' 1024 would push that key from 2392 to
5464 bytes, past InnoDB's 3072-byte cap. `app_name` is 1024 precisely because it
is in no index. The asymmetry is the constraint, not an oversight.

**A deployable `sql/` migration ships with the models and must stay in step with
them.** `create_all` runs only in the local SQLite plugin; `CommunityDatabase`
never calls it, so in any persistent deployment the checked-in DDL is the only
thing that creates these tables. Column-for-column drift between the two is the
failure that DDL exists to prevent.

**Known gap, carried deliberately:** grants are not revoked when a bot is
deleted. `BotAppGrantService.list_for_app` filters the *report* against live
bots, but `delete_bot` soft-deletes and leaves the row. Revoking on deletion
belongs with bot lifecycle; until it exists, the later machine-caller path —
which will resolve on `(app_id, bot_id)` — would still find the row.
