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

## 2026-08-20 Review 收敛增量

- 对外路径改为 `GET /openapi/v1/bots/catalog/search` 与 `GET /openapi/v1/bots/catalog/discover`。
- 两个接口新增 `platform` query：默认 `team_claw`，格式为 `^[a-z][a-z0-9_]{0,63}$`；当前仅 `team_claw` 绑定现有 Bot 数据源，其他合法平台返回 `200000` 空页，非法格式返回统一 `422000`。
- 目录改为租户内认证调用者一致的公开数据：Gateway 继承 `/openapi/v1/bots/**` 的 `user: optional`、`app: optional`，Backend admission 使用 `OPEN`；删除显式 `user_id`、用户态 `friendship` 和目录专用 `401001`。
- 无 User/App 身份仍由 Backend `require_principal` 统一返回 `401000`。目录 DTO 继续严格白名单，不暴露 binding、设备、ext、凭据和环境字段。
- `team_claw` 请求调用既有搜索/推荐服务；未知合法平台不得调用这些服务，直接返回空结果。诊断日志仅记录 platform、request_id、结果数或低敏失败类别，不记录关键词或认证信息。

### 2026-08-20 最终调整

- 按用户最终确认，暂不发布 `platform` 参数；前述 platform 设计作废。两个 catalog 接口直接查询当前部署的数据源。
