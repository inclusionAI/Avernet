# Plan: Group Context API v1 Refinements

- **Date:** 2026-10-12
- **Spec:** `spec-v1-publish.md`
- **Scope marker:** 本文件只描述 2026-10-12 新增/变更部分；旧版 archived plan 不作为本次 coding 依据。

---

## 本次新增/变更摘要

1. `list` 返回改为 meta-only：返回 `name` / `scope_level` / `scope` / `permission` / `gmt_create` / `gmt_modified`，不返回 `content` / `valid_from` / `valid_to`。
2. `add` / `update` 支持业务有效期字段 `valid_from` / `valid_to`；`update` 是 patch 语义，未传字段继承上一版本。
3. 当前版本判断从 `valid_to IS NULL` 改为 `is_superseded = false`；新增 `gmt_superseded` 记录旧版本被替代的系统时间。
4. `valid_from` / `valid_to` 是业务字段，系统不按业务有效期过滤 `get` / `list`，supersede 时不改写旧版本 `valid_to`。
5. 本期不做 history API；只保留历史行，当前接口只面向 `is_superseded = false`。
6. CLI 增加 `--valid-from` / `--valid-to`，时间解析支持 RFC3339、本地无时区时间、Unix 秒/毫秒，不支持纯日期。
7. 谁是卧底测试 prompt 简化，去掉复杂 state_machine 依赖。

---

## Approach

在现有 Group Context v1 实现上做增量调整：保持 `/groupcontext/*` 直连 bcs-http 路由和 bcs-cli chat 同源鉴权不变，只调整数据模型、DTO、应用语义和 CLI 参数。存储层新增系统版本字段 `is_superseded` / `gmt_superseded`，并把“当前版本”的唯一约束与查询条件迁移到 `is_superseded = false`。业务有效期 `valid_from` / `valid_to` 作为内容元数据传递和保存，不参与当前版本判断或默认过滤。

## Affected Components

- `crates/contracts/bcs-domain/src/group_context.rs:82` — GroupContextEntry 当前使用 `valid_to` 表示活跃状态，需要新增 `is_superseded` / `gmt_superseded` 并调整 `is_active()` 语义。
- `crates/service-api/bcs-service-api/src/application/v1/group_context.rs:50` — application command/response DTO 需要增加 valid 字段，list item 需要改为 meta-only。
- `crates/services/bcs-group-context-store/src/store.rs:31` — repository create/supersede 参数和 SQL 查询需要切换到 superseded 字段。
- `crates/services/bcs-group-context-store/src/memory.rs` — 内存 repo 同步实现当前版本判断、唯一性和历史保留。
- `migrations/sqlite/032_group_context.sql:1` — SQLite schema 增加 `is_superseded` / `gmt_superseded`，partial unique index 改为 `WHERE is_superseded = 0`。
- `migrations/oceanbase/031_group_context.sql:1` — OceanBase schema 增加 `is_superseded` / `gmt_superseded`，filtered unique index 改为 `FILTER (is_superseded = 0)` 或等价实现。
- `crates/application/v1/bcs-app-group-context/src/service.rs:35` — add/update/systemset 需要接收和继承业务有效期；list/get 当前过滤改为 `is_superseded=false`，list 输出不含 content/valid。
- `crates/adapters/http/bcs-http/src/routes/group_context.rs:43` — HTTP request DTO 增加 `valid_from` / `valid_to`，并解析多种时间输入。
- `crates/tools/bcs-cli/src/main.rs:1480` — CLI group-context add/update/systemset 增加 `--valid-from` / `--valid-to`，JSON body 序列化新增字段。
- `crates/tools/bcs-cli/src/client.rs:788` — 调用路径保持不变，仅测试新增请求体字段。
- `singlebox/agents/group-context-undercover-test/referee/skills/groupcontext-undercover/SKILL.md` — 演示 prompt 简化。
- `.standalone-openclaw/workspaces/groupcontext-undercover-referee/skills/groupcontext-undercover/SKILL.md` — 当前运行 workspace 同步简化，便于立即测试。

## Data Model Changes

### New / changed columns

Add to `bcs_group_contexts`:

| Column | Type | Meaning |
|---|---|---|
| `is_superseded` | bool / tinyint | 系统当前版本标记；false 表示当前版本 |
| `gmt_superseded` | timestamp null | 旧版本被 supersede 的系统时间 |

Keep existing business fields:

| Column | Meaning |
|---|---|
| `valid_from` | 业务有效开始时间；add 默认 now，update 未传继承旧值 |
| `valid_to` | 业务有效结束时间；不参与当前版本判断，不被 supersede 自动改写 |

### Index / uniqueness

Replace active-version uniqueness:

```sql
-- old
UNIQUE (name, scope) WHERE valid_to IS NULL

-- new
UNIQUE (name, scope) WHERE is_superseded = false
```

Query indexes that currently filter by `valid_to IS NULL` should switch to `is_superseded = false`.

### Migration note

该功能尚未上线，不需要兼容线上老数据。迁移可以直接更新当前 feature migration SQL；无需新增 backfill 脚本。

## API / Interface Changes

### add / systemset request

Add optional fields:

```json
{
  "valid_from": "2026-10-12T14:56:08+08:00",
  "valid_to": "2026-10-13 14:56:08"
}
```

Defaults:

- `valid_from`: server now
- `valid_to`: null

### update request

Add optional fields:

```json
{
  "content": "optional new content",
  "valid_from": 1791797768,
  "valid_to": 1791797768000,
  "visible_to": {"userIds": []},
  "collect_from": {"userIds": ["judge"]}
}
```

Patch semantics:

- provided fields override current version;
- omitted `content` / `valid_from` / `valid_to` / `visible_to` / `collect_from` inherit current version;
- update still creates a new version and marks old version `is_superseded=true`, `gmt_superseded=now`.

### list response

Change from content-bearing item to meta-only item:

```json
{
  "contexts": [
    {
      "name": "game_rule",
      "scope_level": "group",
      "scope": "game-room-7",
      "permission": "R",
      "gmt_create": "2026-10-12T06:56:08Z",
      "gmt_modified": "2026-10-12T06:56:08Z"
    }
  ]
}
```

Do not include `content`, `valid_from`, or `valid_to` in `list`.

### get response

`get` continues to return content and business valid fields for visible current versions. It does not filter by `valid_from` / `valid_to`.

### Time input parsing

Accept:

- RFC3339 with timezone: `2026-10-12T14:56:08+08:00`, `2026-10-12T06:56:08Z`
- local server timezone string: `2026-10-12 14:56:08`
- Unix timestamp seconds or milliseconds

Reject:

- pure date strings like `2026-10-12`

## Key Files & Functions

- `crates/contracts/bcs-domain/src/group_context.rs:85` — add entry fields and change `is_active()` from `valid_to.is_none()` to `!is_superseded`.
- `crates/service-api/bcs-service-api/src/application/v1/group_context.rs:50` — add `valid_from` / `valid_to` to `AddGroupContext`, `UpdateGroupContext`, `SystemSetGroupContext`.
- `crates/service-api/bcs-service-api/src/application/v1/group_context.rs:122` — replace `GroupContextListItem` fields with meta-only fields.
- `crates/service-api/bcs-service-api/src/application/v1/group_context.rs:137` — include valid fields in `GroupContextGetItem` if not already complete.
- `crates/services/bcs-group-context-store/src/store.rs:83` — select and map new columns.
- `crates/services/bcs-group-context-store/src/store.rs:144` — insert `is_superseded=false`, `gmt_superseded=null` on create.
- `crates/services/bcs-group-context-store/src/store.rs:50` — supersede transaction should update old row `is_superseded=true`, `gmt_superseded=params.tx_time` and insert new current row without modifying old `valid_to`.
- `crates/application/v1/bcs-app-group-context/src/service.rs:35` — `create` should accept optional valid values and default only for add/systemset create.
- `crates/application/v1/bcs-app-group-context/src/service.rs:65` — `supersede` should accept valid values from patch resolution instead of always `Utc::now()`.
- `crates/application/v1/bcs-app-group-context/src/service.rs:147` — update patch resolution should inherit omitted valid and permission fields.
- `crates/application/v1/bcs-app-group-context/src/service.rs:172` — list mapping should output meta-only fields.
- `crates/adapters/http/bcs-http/src/routes/group_context.rs:43` — request DTOs add valid fields; use a shared parser for accepted time forms.
- `crates/tools/bcs-cli/src/main.rs:1482` — add `--valid-from` / `--valid-to` to Add/Update/Systemset commands.
- `crates/tools/bcs-cli/src/main.rs:4044` — include valid fields in generated JSON bodies only when provided.
- `crates/tools/bcs-cli/tests/group_context_client.rs` — cover CLI request body shape.
- `crates/application/v1/bcs-app-group-context/tests/group_context_service.rs` — cover patch inheritance, list meta-only, get not filtering expired business valid.
- `crates/services/bcs-group-context-store/tests/conformance_group_context_repo.rs` — cover superseded uniqueness and historical retention.
- `crates/adapters/http/bcs-http/tests/group_context_routes.rs` — cover wire-level valid parsing and list response shape.

## Dependencies

No new external service dependency is required. Time parsing can use existing `chrono`; if local timezone conversion requires a crate already absent from the workspace, prefer a small internal parser that uses the server process local offset, and document the choice in code.

## Risks & Mitigations

- **Risk:** Confusing business validity with system current-version state again.
  **Mitigation:** Keep `valid_to` out of active-version SQL and tests; tests must assert `valid_to < now` rows are still returned when `is_superseded=false`.
- **Risk:** Partial unique index support differs between SQLite and OceanBase.
  **Mitigation:** Preserve existing dialect-specific migration pattern; if OceanBase `FILTER` is not supported in target version, use an equivalent generated-column/index strategy in the migration and document it.
- **Risk:** `list` response removal of content breaks prompts or tests that used `list` as read.
  **Mitigation:** Update demo prompts to use `list` only for existence/meta and `get` for content; add route/service tests that list omits content.
- **Risk:** Ambiguous local time parsing.
  **Mitigation:** Reject date-only strings; document that no-timezone datetime strings use server local timezone.

## Alternatives Considered

- Use `valid_to IS NULL` as the active-version marker.
  - Rejected because `valid_to` is a business field and may be a past/future business validity window.
- Filter `get/list` by business valid window.
  - Rejected because default system semantics should expose current context version regardless of business expiry; business consumers interpret valid fields.
- Add history API now.
  - Rejected for v1 refinements; storage retains history but no API exposes it yet.
- Make `update` full replacement.
  - Rejected; confirmed desired behavior is patch semantics with omitted fields inherited.

## Rollout

- Feature is not yet online; update existing feature migrations rather than adding compatibility backfills.
- Build/test locally, then restart BCS backend and bots as before.
- Prompt simplification can be applied immediately to `singlebox/agents/...` and current `.standalone-openclaw/workspaces/...` without waiting for code implementation.

## Test Strategy

- Domain/contract tests: new fields serialize/deserialize; list item has no content/valid fields.
- Store conformance: duplicate current `(name, scope)` conflicts via `is_superseded=false`; supersede marks old row and preserves old `valid_to`; new row is current.
- Application tests: add defaults valid fields; update inherits omitted content/valid/permission fields; update can change only valid range; `valid_to < now` current rows still appear in get/list.
- HTTP tests: valid time parsing formats accepted/rejected; list response is meta-only; get still returns content.
- CLI tests: `--valid-from` / `--valid-to` serialize correctly; omitted update fields are absent/null according to existing body construction expectations.
- Manual demo: simplified 谁是卧底 prompt can add player words, players get their own word using group session id, and referee can update/list/get core entries.
