# Bot Public OpenAPI catalog

## Goal

Add user-scoped OpenAPI replacements for the existing public Bot search and
recommendation endpoints. Keep the legacy `/api/v1/bot-public/search` and
`/api/v1/bot-public/discover` endpoints unchanged during migration.

## Public contract

### Search

`GET /openapi/v1/bots/public/search`

| Query | Required | Constraints |
| --- | --- | --- |
| `user_id` | yes | Non-empty and equal to the verified user principal. |
| `search` | no | Bot-name or owner-name keyword. |
| `page` | no | Default `1`; integer >= `1`. |
| `page_size` | no | Default `20`; integer from `1` to `100`. |

### Discover

`GET /openapi/v1/bots/public/discover`

| Query | Required | Constraints |
| --- | --- | --- |
| `user_id` | yes | Non-empty and equal to the verified user principal. |
| `keyword` | yes | Non-empty keyword. |
| `top_k` | no | Default `10`; integer from `1` to `20`. |
| `min_score` | no | Default `0.1`; number from `0` to `1`. |
| `runtime_state` | no | Closed value set; default `online`, translated internally to the recommendation filter. |

Both success responses are the normal OpenAPI envelope, with
`code: 200000`, `message: "OK"`, `data.total`, `data.items`, and
`request_id`.

`PublicBot` is an allowlist projection with `bot_id`, `entity_id`,
`bot_type` (`personal`, `service`, or `desktop`), `name`, `description`,
optional `owner_name`, `engine`, `status`, and optional `friendship`.
`friendship` contains only `status` (`PENDING`, `ACCEPTED`, `REJECTED`, or
`CANCELLED`) and `requires_approval`. A discovery item additionally contains
`recommendation.score`, `recommendation.reasons`, and optional
`recommendation.short_profile`.

The public model must never contain a binding id, database primary key, device
id, extension data, credential, environment value, instance selector, or raw
recommendation response.

## Identity and admission

- Both operations are `REFUSED` in the admission inventory and declare
  `refuse_app_only_caller`.
- They use the shared `UserIdDep` / `require_user_id` seam. A user-only or
  user-plus-app caller may proceed only when query `user_id` equals the
  verified user. A pure application caller receives `401001`.
- A mismatched user id is `403001`. The shared mismatch error mapping changes
  from the current generic `403000` to preserve one identity implementation.
- Only the verified dependency result is passed to the search/discover service.
- Success and failure diagnostics record a safely shortened user id,
  request id, result count or failure category; never log payloads or raw
  recommendation data.

### User-directed implementation exception (2026-08-19)

The shared `BotDiscoverService` keeps its pre-existing log format. The
catalog adapter remains responsible for its own low-sensitivity request
diagnostics, but this task deliberately does not reformat or sanitize the
legacy discovery-service logging path.

## Runtime-addressing boundary

Catalog responses do not expose or resolve bindings. Existing connection
addressing remains:

`GET /openapi/v1/bots/{bot_id}/connection?user_id=<caller>&owner_id=<entity>&stage=<draft|verify|online>`.

Its internal model is `BotAddress(bot_id, entity_id)`, caller context, runtime
stage, runtime target, and internal-only resolved binding. Resolution is tenant
and `(bot_id, entity_id)` scoped; bot type comes from storage; owner or
collaborator permission masks unavailable targets as `404001`; draft uses the
active binding; service verify/online resolves a successful publication record
by Bot primary key and its stage binding extension, never the Bot row's active
binding. Provider-level affinity or controlled device selection handles service
Bot multi-instance choice without exposing an instance or binding to callers.

## Delivery changes

1. Add a thin OpenAPI catalog adapter, schema models, mapper, admission entries,
   assembly mount, and route/path/documentation test updates in Avernet.
2. Add focused HTTP contract tests before implementation: success projection,
   caller state isolation, no sensitive fields, validation, mismatched user,
   user-plus-app, and pure-app refusal.
3. Add exact `GET` route-security overrides (`user: required`, `app: optional`)
   in both Avernet and OCB. The generic bots upstream forwarding already covers
   these paths.
4. Generate/update the served `bots.openapi.json` in Avernet, then synchronise
   the two paths and their models into the OCB gateway artifact.
5. Keep legacy endpoints working unchanged, and record validation plus the task
   summary in the repository task log.

## Acceptance checks

- Missing or blank `user_id` is a standard validation envelope; a forged id is
  `403001`; a pure app is `401001`; a valid user and user-plus-app both work.
- Search receives only the verified caller id and yields caller-specific
  friendship data without internal fields.
- Discover passes the approved runtime filter and emits only projected
  recommendation data.
- Service bot stage, same `bot_id` under different owners, multi-instance, and
  collaborator behavior remain covered by the established connection resolver
  tests; no new binding API is introduced.
- Generated OpenAPI and both gateway configurations expose the paths and exact
  identity requirements.
