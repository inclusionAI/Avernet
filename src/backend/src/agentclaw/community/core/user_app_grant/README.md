# `agentclaw.community.core.user_app_grant`

User-granted authorizations letting one third-party application act **as the
user who granted it** on the public API's account-level operations — the ones
that name no bot.

A row means *"app A may act as user U at the account level"*. It is the
sibling of `core/bot_app_grant`, which answers a different question — *"may
app A reach bot B as user U"* — and the two are deliberately independent. A
bot grant confers nothing on the account; an account-level grant confers
nothing on any bot. Consent is per thing.

**Why a separate record exists.** Before it, an application was admitted to a
user-level operation (`admission.py` mode `USER_GATED`) if it held *any* bot
grant from the named user. That made a consent on one bot a consent on the
user's Spaces, work orders, local devices and their file trees, with no way to
withdraw the account-level part on its own. This record is the consent those
operations actually need.

**Nothing about the user's own authority is stored here.** What the
application may then do is bounded by that user's live access on every
request — Space membership, work-order recipiency, device ownership — each
enforced by the service that owns it, exactly as for the human.

Two tables, for the reason `core/bot_app_grant/models.py` gives at length:
`ac_user_app_grant` holds live grants only, so a row exists iff access is in
force; `ac_user_app_grant_log` is append-only with no unique key, because its
job is to accept every event.

Nothing here reads a framework, a request, or an HTTP status (Rule 7). The HTTP
seam is `adapters/http/openapi_v1/authorized_apps/user_router.py`; the Service
API contract the adapter and the admission seam depend on is
`api/user_app_grant_service.py`.

**Who reads this record, and when.** `find` is the admission probe the public
API runs on every `USER_GATED` request from an application acting alone —
`adapters/http/openapi_v1/principal.py::require_granted_user`, declared on
every such route and held to the admission table by
`test_admission_inventory.py`.

## Context Boundary

```yaml
purpose: Own the record that says which application a user has authorized to act as them at the account level, and the rules for granting, withdrawing and reading it.
provides:
  - UserAppGrantModel
  - UserAppGrantLogModel
  - UserAppGrantRecord
  - UserGrantAction
  - UserAppGrantService
  - UserAppGrantServiceProtocol
  - UserGrantNotFoundError
  - UserGrantIdentityTooLongError
consumes:
  - "UserAppGrantRepositoryProtocol (core.repository) — persistence for both tables"
consumed_by:
  - "adapters/http/openapi_v1 — the admission seam reads a grant on every USER_GATED request from an application acting alone; the org/user/authorized-apps group grants, lists and withdraws"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.log
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```

### Change impact

This module decides who may act as a user on the account-level public surface
without a human present, so a change here is a change to an authorization
boundary rather than to a data model.

**The tenant guard is load-bearing and is not this module's own code.** Both
tables call `register_avernet_tenant_guard`; that is where the cross-tenant
guarantee lives, which is why no tenant comparison exists in the service.

**Widths are not free to change.** `user_id` is 256 and in the unique key, so
widening it is a rekey; it matches the bot-level record so one user id is
storable in both or neither.

**A deployable `sql/` migration ships with the models and must stay in step
with them.** `create_all` runs only in the local SQLite plugin; in any
persistent deployment the checked-in DDL is the only thing that creates these
tables.
