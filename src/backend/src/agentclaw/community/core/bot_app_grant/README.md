# `agentclaw.community.core.bot_app_grant`

User-granted authorizations letting one third-party application reach one bot
**as the user who granted it**.

A row means *"app A may act as user U on bot B, which O owns"*. The two people
are separate columns because they are separate people whenever the bot is
shared: `user_id` is the delegating user, whose access is being lent, and
`owner_id` is the bot's owner, who may never have heard of the application.

A user authorizes a named application for any bot they can **operate** — their
own, or one they collaborate on at member level or above — which is the bar
`core/engine_runtime/gate.py` already applies to driving the bot. The rule is
therefore *you may delegate exactly the access you have*.

**Nothing about the delegator's authority is stored here.** No permission level,
no capability list, and that absence is the design:

> An application's reach is exactly its granting user's reach, and never more.

Not a copy of it taken at consent time — the live thing. Every app-only request
is re-adjudicated against the same collaborator gate the human would face, so
removing the delegator from a bot ends the application's access on the next
request, with no revocation and nothing to clean up. A snapshot would go stale
in the dangerous direction: it would keep answering "yes".

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

**Who reads this record, and when.** `find` is the authorization probe the
public API runs on every bot-scoped request from an application acting alone —
one unique-key lookup, and the only thing standing between that caller and the
operation. `adapters/http/openapi_v1/admission.py` decides which operations
consult it at all. A record coming back means the delegation exists; whether the
delegating user may *still* operate that bot is asked separately and live, which
is what makes the invariant above true rather than aspirational.

## Context Boundary

```yaml
purpose: Own the record that says which application a user has authorized to act as them on which bot, and the rules for granting, withdrawing and reading it.
provides:
  - BotAppGrantSweepProtocol
  - BotAppGrantModel
  - BotAppGrantLogModel
  - BotAppGrantRecord
  - GrantAction
  - BotAppGrantService
  - GrantNotFoundError
consumes:
  - "BotAppGrantRepositoryProtocol (core.repository) — persistence for both tables"
  - "BotRepository (core.repository) — live-bot ids, so a grant outliving its bot is not reported as access"
consumed_by:
  - "adapters/http/openapi_v1 — the public API's admission seam reads a grant on every request from an application acting alone"
  - "core/bot_management (delete_bot) — withdraws every authorization on a bot as part of deleting it"
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

**Widths are not free to change, and neither is the key's shape.** The unique
key is 2392 of InnoDB's 3072 bytes. `user_id` and `owner_id` are 256 because of
it: widening either to `ac_bots`' 1024 would give 5464. It also carries
`user_id` and *not* `owner_id` — carrying both would be 3416, likewise past the
cap — so "add the other one too" is not an available fix for anything.

That the key is on the delegating user is a semantic choice as well as a
budgetary one. Two collaborators may each authorize the same application for the
same bot, and those are two independently withdrawable loans of two different
authorities. Keyed on the owner they would collide, and the idempotent grant
path would swallow the second as "already live" — quietly handing the second
user an application bounded by the *first* user's access.

`app_name` is 1024 precisely because it is in no index. The asymmetry is the
constraint, not an oversight.

**A deployable `sql/` migration ships with the models and must stay in step with
them.** `create_all` runs only in the local SQLite plugin; `CommunityDatabase`
never calls it, so in any persistent deployment the checked-in DDL is the only
thing that creates these tables. Column-for-column drift between the two is the
failure that DDL exists to prevent.

`sql/` now holds a `CREATE` for fresh installs and an `ALTER` for the already-
deployed tables, and **all three descriptions must agree** — models, `CREATE`,
`ALTER`. The `ALTER` is a pure schema change only while the tables are empty; it
says so at the top, and says what the migration becomes once they are not.

**Deletion withdraws everything, and the ordering is load-bearing.**
`BotService.delete_bot` calls `revoke_all_for_bot` before the device release and
the passport destruction, not after: a failure then aborts while the bot is
still intact, instead of leaving a bot that is already unusable with live
authorizations against it and no deletion left to trigger the sweep again.
Failures propagate — swallowing one would reintroduce the gap quietly, which is
worse than the gap, because the sweep would look like it ran.

`list_for_app` still filters its report against live bots. That is now a second
line rather than the only one, and it is cheap: one id-only query.
