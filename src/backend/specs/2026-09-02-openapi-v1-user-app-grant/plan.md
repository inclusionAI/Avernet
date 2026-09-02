# Plan: Public API — User-Level Authorization for Applications

## Approach

One new persisted record (`ac_user_app_grant` + `ac_user_app_grant_log`),
three operations over it beneath the org group
(`/openapi/v1/org/user/authorized-apps`), and one new dependency in the
admission seam (`require_granted_user`) that every `USER_GATED` operation
declares. The bot-level machinery is reused in shape, not in code: a sibling
core module, a sibling repository, a sibling router, and the same two-table
live/log split, so a reader who knows one knows the other.

The retired mechanism is the bot-grant proxy — "any live bot grant from the
named user admits the application to a user-level operation" — implemented as
`ActingCaller.granted_bot_ids()` with `owned_by_delegator=False`, called from a
private `_require_user_delegation` helper copied into five routers and written
inline in two more. Every one of those call sites moves onto the dependency,
the helper copies are deleted, and `granted_bot_ids()` loses the switch: its
only remaining callers are the owner-scoped listings, which always narrow.

Alternatives rejected:

- **Keep the bot-grant proxy as a fallback.** Would make the new record
  meaningless on exactly the operations it exists for, and keep the
  over-broad consent it removes. Rejected; the compatibility cost is one
  re-consent per integration.
- **Let a user-level grant imply bot access.** Recreates the over-broad
  consent in the other direction. Rejected; the two records stay independent.
- **Put the routes under `/openapi/v1/bots/authorized-apps`.** Routable, but
  the operation is a property of the user, the org group is already "the
  caller's own identity", and the reserved-component list under `/bots` would
  grow. The gateway routes `/openapi/v1/org/**` to the same backend.
- **Add a new admission mode.** `USER_GATED` already names the shape; only its
  proof changes. A new mode would leave the old one defined and unused, which
  `test_every_mode_is_used` refuses.

## Affected Components

- `core/user_app_grant/` — **new**: `models.py` (two tables, record, action
  enum), `errors.py`, `user_app_grant_service_protocol.py`, `services/`,
  `sql/2026_09_02_user_app_grant.sql`, `README.md` (context boundary).
- `core/repository/protocols/bot/user_app_grant.py` and
  `core/repository/implementations/bot/user_app_grant.py` — **new**, in the
  `bot` domain beside the bot-level grant.
- `api/user_app_grant_service.py` — **new** re-export.
- `di/modules/user_app_grant_module.py` — **new**, registered in `container.py`.
- `core/schema.py` — imports the models for `create_all`.
- `adapters/http/openapi_v1/admission.py` — `ActingCaller.user_grants` and
  `require_user()`; `granted_bot_ids()` always narrows; three `REFUSED` rows;
  the skill README relabelled `REFUSED`.
- `adapters/http/openapi_v1/principal.py` — resolves both grant readers for
  an application caller; `require_granted_user` and `DelegatedUserIdDep`.
- Routers moved onto the dependency: `bots/router.py` (ceiling),
  `routines/owner_router.py`, `repository_catalog.py`, `market/router.py`
  (sync), `spaces/router.py`, `spaces/skill_routes.py`,
  `work_orders/router.py`, `local/router.py` (devices).
- `adapters/http/openapi_v1/authorized_apps/user_router.py` — **new** group;
  `schemas.py` gains `UserAuthorizedApp`; mounted in `__init__.py`.
- `adapters/http/openapi_v1/authorization.py` — three `NoCheck` rows.
- `adapters/http/openapi_v1/responses.py` — error mappings.
- `src/gateway/configs/application.yaml` — `POST /openapi/v1/org/user/authorized-apps`
  requires user + app; `GET /openapi/v1/bots/skills/{skill_id}/readme` requires
  a user.

## Data Model

```text
ac_user_app_grant        live rows only; UNIQUE (avernet_tenant, app_id, user_id, env)
  id, app_id, app_name(1024), user_id(256, utf8mb4_bin), env, avernet_tenant,
  gmt_create, gmt_modified
  KEY idx_user_app_grant_user (avernet_tenant, user_id, env)

ac_user_app_grant_log    append-only, no unique key
  id, app_id, app_name, user_id, action ('granted'|'revoked'), env,
  avernet_tenant, gmt_create
  KEY idx_user_app_grant_log_user (avernet_tenant, user_id, env, gmt_create)
```

Both tables register the tenant guard. `user_id` is 256 to match the
bot-level record so one user id is storable in both or neither; a longer one
is refused at consent time.

## Admission

`ActingCaller` carries both readers for an application caller. Which one a
request consults is the operation's shape:

| Dependency | Reads | Declared on |
| --- | --- | --- |
| `require_granted_own_bot` / `require_granted_addressed_bot` | bot grant | `GRANT_CHECKED_*` |
| `require_granted_user` | account-level grant | `USER_GATED` |

`test_admission_inventory.py::test_every_grant_checked_operation_declares_its_modes_dependency`
now parametrizes over `USER_GATED` too, so the set of routes declaring
`require_granted_user` must equal the table's `USER_GATED` rows in both
directions. A missing grant raises `GrantNotResolvableError`, mapped to the
masked 404 the bot dependencies already use.

## Gateway

Spaces, work orders and notifications stay `user: required` at the edge, so
an application acting alone never reaches them regardless of the backend's
mode; the backend dependency is still declared there so the two hops agree on
what a machine caller would need. The new grant operation is method-qualified
under `/openapi/v1/org/**` (user required, app optional) to require the app
too; list and withdraw inherit the org rule.

## Testing

- `tests/community/core/user_app_grant/test_grant_service.py` — real SQLite:
  idempotent grant, the log outliving the row, grant/withdraw twice through
  the unique key, the insert race, identity length.
- `tests/community/adapters/http/openapi_v1/authorized_apps/test_user_router.py`
  — the three operations, both identity postures, the machine-caller refusal.
- `test_app_only_listings.py` — the ceiling and device reads admit with the
  account-level grant and refuse with a bot grant alone.
- `test_admission_inventory.py`, `test_app_only_refusals.py`,
  `test_explicit_user_id.py` (pin 225 → 228), `test_route_security.py`.
