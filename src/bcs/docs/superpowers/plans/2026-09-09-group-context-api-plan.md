# Group Context API — Implementation Plan

- **Date:** 2026-09-09
- **Spec:** `docs/superpowers/specs/2026-09-09-group-context-api-design.md`

---

## 1. 交付范围

| 分层 | 内容 |
|------|------|
| **数据面** | ContextEntry 存储：content + origin + time |
| **策略面** | Policy Template 定义 flow（visible_to + collect_from） |
| **治理面** | lineage（系统自动维护） + policy_version + obligations（仅『检索落审计』） |
| **API** | 4 个端点，统一 POST：status、createByTemplate、updateContent、retrieve |

---

## 2. 模型与 API

### 2.1 Policy Template

通过预定义模板管理策略字段。管理员预设模板，bot 通过模板创建 context。

**系统环境变量（上游注入，不在模板中声明）：**

| 变量 | 含义 |
|------|------|
| `tenant_id` | 当前租户 |
| `group_id` | 当前群组 |
| `session_id` | 当前会话 |
| `run_id` | 当前 run |
| `actor_id` | 当前调用者 |
| `tx_time` | 系统写入时间 |



### 2.2 模板字段

| 字段 | 类型 | 必填 | 含义 |
|------|------|------|------|
| `template_id` | string | 是 | 模板唯一标识 |
| `description` | string | 是 | 给 LLM 的使用说明 |
| `params` | param[] | 否 | 调用方传入的参数列表 |

**flow：**

| 字段 | 类型 | 必填 | 含义 |
|------|------|------|------|
| `visible_to` | dict | 是 | 可见范围。四要素 `tenant_id`/`group_id`/`session_id`/`run_id` + `user_ids`。支持 `{param}` 占位符 |
| `collect_from` | dict | 是 | 写入范围。四要素 `tenant_id`/`group_id`/`session_id`/`run_id` + `user_id`。支持 `{param}` 占位符 |

**consistency：**

| 字段 | 类型 | 必填 | 默认值 | 含义 |
|------|------|------|--------|------|
| `domain` | string | 是 | — | 版本标识。同 granularity + domain 条目互为版本。支持 `{param}` 占位符 |
| `granularity` | enum | 是 | — | run / session / group / tenant |
| `freshness_class` | enum | 否 | `stable` | volatile / stable / audit |
| `revalidate_due` | string/null | 否 | null | ISO 8601，仅 volatile 必填 |

### 2.3 模板示例

**模板 1：游戏规则**

```
template_id:   tpl_game_rule
description:   "游戏规则。长期有效。"
params:        []

flow:
  visible_to:
    tenant_id:  {tenant_id}
    group_id:   {group_id}
  collect_from:
    tenant_id:  {tenant_id}
    group_id:   {group_id}
    user_id:    admin_xxxx

consistency:
  domain:             game_rule
  granularity:        group
  freshness_class:    stable
```

此模板由管理员在 group 创建时直接生成一条 context 实例（content 由管理员填写），bot 通过 retrieve 查询，不调用 createByTemplate。

**模板 2：玩家底牌**

```
template_id:   tpl_player_word
description:   "每个玩家可见的自己的词语。创建后仅你和指定玩家可见。需要为每位玩家单独调用一次。"
params:
  - player_id

flow:
  visible_to:
    tenant_id:  {tenant_id}
    group_id:   {group_id}
    session_id: {session_id}
    user_ids:   [{actor_id}, {player_id}]
  collect_from:
    tenant_id:  {tenant_id}
    group_id:   {group_id}
    session_id: {session_id}
    user_id:    judge_bot_xxxx  # 裁判bot是固定的且写入权限大，写死

consistency:
  domain:             player_word_{player_id}
  granularity:        session
  freshness_class:    stable
```

**模板 3：玩家状态**

```
template_id:   tpl_player_state
description:   "全部玩家的存活/出局状态。全员可见。"
params:        []

flow:
  visible_to:
    tenant_id:  {tenant_id}
    group_id:   {group_id}
    session_id: {session_id}
  collect_from:
    tenant_id:  {tenant_id}
    group_id:   {group_id}
    session_id: {session_id}
    user_id:    judge_bot_xxxx

consistency:
  domain:             player_state
  granularity:        session
  freshness_class:    stable
```

---

### 2.4 API

**使用流程（bot 首次进入群组时）：**

```
1. POST /groupcontext/status → 获取 (contexts[], context_templates[])
2. contexts 中 permission 含 W → POST /groupcontext/updateContent
3. context_templates 中可创建的 → POST /groupcontext/createByTemplate
   （createByTemplate 已有活跃版本会返回 conflict，应改用 updateContent）
4. contexts 中 permission 含 R → POST /groupcontext/retrieve
```

#### status

获取当前 bot 的 context 视图。

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| 路径 | `/groupcontext/status` |

**请求参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `tenant_id` | string | 是 | — | 框架注入 |
| `group_id` | string | 否 | — | 框架注入（按操作粒度选填） |
| `session_id` | string | 否 | — | 框架注入（按操作粒度选填） |
| `run_id` | string | 否 | — | 框架注入（按操作粒度选填） |
| `actor_id` | string | 是 | — | 框架注入 |

> 调用方按操作粒度传入相应字段：bot 想知道某 group 的状态就传 `tenant_id + group_id`，想知道某 session 的状态就再传 `session_id`，想知道本次 run 的状态就再传 `run_id`。**未传字段不参与过滤**，不影响结果——这是调用方主动声明查询范围的方式，不是系统自动注入。

**响应：**

```json
{
  "contexts": [
    {
      "domain": "game_rule",
      "granularity": "group",
      "description": "游戏规则。长期有效。",
      "permission": "R"
    },
    {
      "domain": "player_state",
      "granularity": "session",
      "description": "全部玩家的存活/出局状态。全员可见。",
      "permission": "WR"
    }
  ],
  "context_templates": [
    {
      "template_id": "tpl_player_word",
      "granularity": "session",
      "description": "每个玩家可见的自己的词语。需要为每位玩家单独调用一次。",
      "params": ["player_id"]
    }
  ]
}
```

**permission 说明：**

| 取值 | 含义 |
|------|------|
| `W` | 仅可写（createByTemplate / updateContent） |
| `R` | 仅可读（retrieve） |
| `WR` | 可读可写 |

`contexts` 中 `permission` 含 `W` 的条目可以做 updateContent；

`permission` 含 `R` 的条目可以做 retrieve。

`context_templates` 中仅返回当前 bot 可创建的模板（`collect_from` 匹配），bot 可以通过 `createByTemplate` 创建实例。

---

#### createByTemplate

通过模板创建一条 context。

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| 路径 | `/groupcontext/createByTemplate` |

**请求参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `tenant_id` | string | 是 | — | 框架注入 |
| `group_id` | string | 否 | — | 框架注入 |
| `session_id` | string | 否 | — | 框架注入 |
| `run_id` | string | 否 | — | 框架注入 |
| `actor_id` | string | 是 | — | 框架注入 |
| `template_id` | string | 是 | — | 模板 ID |
| `content` | string | 是 | — | context 内容 |
| `params` | dict | 否 | `{}` | 模板参数键值对 |

**服务端行为：**

```
参数解析：
  1. 填充模板参数：从系统环境变量和 params 取值
  2. domain 实例化（如 "player_word_{player_id}" → "player_word_player_1"）
  3. visible_to / collect_from 实例化：按模板中显式声明的字段填充；
     未声明的字段不参与过滤（不额外限制——如 tpl_game_rule 未含 session_id，
     则 visible_to 不限 session）

输入校验：
  4. content 字节数 ≤ CONTENT_MAX_BYTES（默认 1 KB），超出返回 413 payload_too_large

PDP 判定：
  5. 提取当前 actor_id
  6. 比对 collect_from → actor_id 是否匹配
     → 不匹配则拒绝，返回 permission_denied

幂等检查：
  7. 按 domain + granularity 查同范围内是否已有生效版本
      ┃ 按 (tenant_id, group_id, [session_id], [run_id], domain) 查同 (domain, granularity) 是否已有 valid_to=null 的活跃版本：
      ┃ - granularity=tenant → 仅 (tenant, domain)
      ┃ - granularity=group → (tenant, group, domain)
      ┃ - granularity=session → (tenant, group, session, domain)
      ┃ - granularity=run → (tenant, group, session, run, domain)
     → 如存在，返回 conflict，拒绝创建
     （createByTemplate 仅用于首次写入；后续写入统一走 updateContent）

写入：
  8. 构造 ContextEntry（flow 字段使用实例化后的快照），写入存储
  9. 返回 context_id
```

**响应：**

```json
{
  "status": "ok",
  "context_id": "ctx_xxx",
  "domain": "player_word_player_1",
  "granularity": "session",
  "content": "你的词语是：香蕉",
  "superseded_id": null
}
```

| 响应字段 | 类型 | 说明 |
|----------|------|------|
| `status` | string | `ok` / `permission_denied` / `conflict` / `invalid_param` |
| `error_msg` | string \| null | 错误描述 |
| `context_id` | string \| null | 新创建的条目 ID |
| `domain` | string \| null | 解析后的实际 domain（动态参数时返回） |
| `granularity` | string \| null | 模板预设的 granularity |
| `content` | string \| null | 写入的 content |
| `superseded_id` | string \| null | 被取代的旧条目 ID（首次创建为 null） |

---

#### updateContent

更新已存在 context 的 content，系统自动 supersede 旧版本。**仅更新内容，不修改策略字段（策略以条目创建时的快照为准）。**

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| 路径 | `/groupcontext/updateContent` |

**请求参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `tenant_id` | string | 是 | — | 框架注入 |
| `group_id` | string | 否 | — | 框架注入 |
| `session_id` | string | 否 | — | 框架注入 |
| `run_id` | string | 否 | — | 框架注入 |
| `actor_id` | string | 是 | — | 框架注入 |
| `domain` | string | 是 | — | 要更新的 domain |
| `granularity` | string | 否 | — | 可选。当同一 domain 名下有多条可写条目时需显式消歧，否则系统自动匹配 |
| `content` | string | 是 | — | 新内容 |

**服务端行为：**

```

输入校验：
  1. content size 校验：
       - 传入 content 字节数 ≤ CONTENT_MAX_BYTES（默认 1 KB），超出返回 413 payload_too_large
         否则提前拒绝（避免写入后才发现超出）
         
候选集检索：
  2. 按 domain 匹配所有条目

PDP 判定：
  3. 逐条比对条目内嵌的 collect_from（创建时刻从模板冻结的快照，非当前模板）
     → 过滤出 bot 有写权限的条目
     collect_from 中的四要素与当前 origin 逐层对比：
       - tenant_id 必须匹配
       - group_id 必须匹配（如果 collect_from 指定了 group_id）
       - session_id 必须匹配（如果 collect_from 指定了 session_id）
       - user_id 必须匹配（如果 collect_from 指定了 user_id）
     → 无可写条目 → permission_denied

版本链定位：
  4. 将可写条目按 granularity 分组，每组对应一条独立的版本链
     → 仅一组 → 自动定位
     → 多组：
       - granularity 未传 → ambiguity（返回所有可选 granularity）
       - granularity 已传但无对应组 → not_found
       - granularity 已传且匹配 → 精确定位

取代写入：
  5. 在目标组内查 valid_to=null 的当前活跃版本
     → 不存在 → not_found
  6. 用新 content 替换旧 content
  7. 最终 content size 校验：
    - 最终总字节数  ≤ CONTENT_MAX_BYTES（默认 1 KB），超出返回 413 payload_too_large
  8. 回填旧条目 valid_to = tx_time（生命周期字段维护）
  9. 回填旧条目 lineage.superseded_by = 新 context_id，lineage.superseded_at = tx_time
     （明确"谁取代了我、什么时候取代的"，旧条目数据面 content/origin/provenance 不变）
  10. 写入新条目，lineage.supersedes 指向旧条目
  11. 返回新 context_id
```

> **"不修改旧条目"澄清：** supersede 不修改旧条目的数据面（content / origin / provenance），但生命周期字段（`time.valid_to`、`lineage.superseded_by`、`lineage.superseded_at`）由系统在 step 7-8 维护，这是 lineage 链可遍历的前提。

**响应：**

```json
{
  "status": "ok",
  "context_id": "ctx_yyy",
  "domain": "player_state",
  "granularity": "session",
  "content": "玩家1出局",
  "superseded_id": "ctx_xxx"
}
```

| 响应字段 | 类型 | 说明 |
|----------|------|------|
| `status` | string | `ok` / `permission_denied` / `not_found` / `ambiguous` / `invalid_operation` |
| `error_msg` | string \| null | 错误描述（ambiguous 时列出可选的 granularity 列表） |
| `context_id` | string \| null | 新创建的条目 ID |
| `domain` | string \| null | 实际 domain |
| `granularity` | string \| null | 实际 granularity |
| `content` | string \| null | 写入的 content |
| `superseded_id` | string \| null | 被取代的旧条目 ID |

---

#### retrieve

按 domain 检索 context。

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| 路径 | `/groupcontext/retrieve` |

**请求参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `tenant_id` | string | 是 | — | 框架注入 |
| `group_id` | string | 否 | — | 框架注入 |
| `session_id` | string | 否 | — | 框架注入 |
| `run_id` | string | 否 | — | 框架注入 |
| `actor_id` | string | 是 | — | 框架注入 |
| `domain` | string | 是 | — | 要检索的 domain |
| `limit` | int | 否 | `10` | 返回条数上限 |

**服务端行为：**

```
存储过滤：
  1. 按 domain 匹配所有条目

PDP 判定：
  2. 逐条比对 visible_to — 当前 bot 的 actor_id 是否在可见范围内，
     同时按 tenant_id / group_id / session_id / run_id 过滤
     → 只保留当前调用者所在作用域可及的条目（不限 granularity）
     → 不可见的条目直接跳过

版本处理：
  3. 按 granularity 分组，逐组取 valid_to=null 的最新条目

freshness 过期过滤：
  4. 对 freshness_class=volatile 的条目，若 now ≥ revalidate_due 则视为过期，跳过
     （audit / stable 不受 revalidate_due 约束）

审计：
  5. 写入审计日志：actor_id, tenant_id, group_id, session_id, run_id,
     domain, 返回的 context_id 列表, tx_time
     （即使返回空集也要落审计，记录"查询过但无可见内容"）

返回：
  6. 从细到粗排序（run → session → group → tenant），返回结果列表
```

**示例：code_review_rule**

bot 传 `domain=code_review_rule`，系统从细到粗返回全部粒度的有效版本：

```json
{
  "items": [
    {
      "context_id": "ctx_001",
      "domain": "code_review_rule",
      "granularity" : "session",
      "content": "本次评审关注并发安全",
      "time.valid_from": "2026-09-09T10:00:00Z"
    },
    {
      "context_id": "ctx_002",
      "domain": "code_review_rule",
      "granularity" : "group",
      "content": "团队通用：所有 PR 必须过 CI",
      "time.valid_from": "2020-09-01T00:00:00Z"
    },
    {
      "context_id": "ctx_003",
      "domain": "code_review_rule",
      "granularity" : "tenant",
      "content": "公司级：禁止提交密钥",
      "time.valid_from": "2010-01-01T00:00:00Z"
    }
  ]
}
```

>   ┃ retrieve 的 domain 字段为已实例化的具体 domain 字符串（不支持通配符或占位符）。同 domain 名下可能存在多个 granularity 的活跃版本（取决于各模板的
granularity 声明），retrieve 会一并返回。

**响应：**

| 响应字段 | 类型 | 说明 |
|----------|------|------|
| `items` | array | 匹配的条目列表，从细到粗排序 |

`items[]` 条目结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `context_id` | string | 条目 ID |
| `domain` | string | 所属 domain |
| `granularity` | enum | 所属粒度（run / session / group / tenant）。调用方据此区分继承链中各条目的作用域 |
| `content` | string | 内容 |
| `time.valid_from` | string | 生效时间 |

### 2.5 错误码

| HTTP | status | 含义 | 适用接口 |
|------|--------|------|----------|
| 200 | `ok` | 操作成功 | 全部 |
| 400 | `invalid_param` | 参数校验失败（缺必填、格式错误、content 超过 size 上限等） | 全部 |
| 403 | `permission_denied` | 调用方无权执行此操作 | createByTemplate / updateContent / retrieve |
| 404 | `not_found` | 指定 domain 下无匹配条目或无活跃版本 | updateContent / retrieve |
| 409 | `conflict` | 同 domain 已有活跃版本，拒绝创建 | createByTemplate |
| 409 | `ambiguous` | 同一 domain 对应多条版本链，需传 granularity 消歧 | updateContent |
| 413 | `payload_too_large` | content 超过单条上限（默认 1 KB） | createByTemplate / updateContent |
| 500 | `internal_error` | 服务端内部错误 | 全部 |

错误时 `context_id` / `superseded_id` / `content` 等数据字段为 null，`error_msg` 携带人类可读说明。

---

## 3. 代码布局

按 `CLAUDE.md` 分层规范：HTTP route → delivery adapter → application → core → repo trait → store。

### 3.1 crate / 目录

```
service-api/bcs-service-api/src/
  application/
    group_context.rs            # GroupContextService trait
  core/
    group_context.rs            # GroupContextCoreService trait
  port/repo/
    group_context_repo.rs       # GroupContextRepo trait
    policy_template_repo.rs     # PolicyTemplateRepo trait
  dto/
    group_context.rs            # 请求/响应/错误 wire DTO

services/bcs-group-context/     # application + core 实现
  src/
    lib.rs
    application.rs
    core.rs
    model.rs                    # ContextEntry / Flow / Consistency / Lineage / Governance
    template.rs                 # PolicyTemplate 类型定义 + 参数实例化
    error.rs                    # GroupContextError（含 to_http_status 映射）
    audit.rs                    # 审计日志写入

services/bcs-group-context-store/  # repo 实现
  src/
    lib.rs                      # DbGroupContextStore（DbPlugin + DbSqlFlavor）

adapters/http/bcs-http/src/
  routes/
    group_context.rs            # POST /groupcontext/* 路由定义

tools/bcs-cli/src/command/
  group_context.rs              # bcs-cli groupcontext status/create/update/retrieve
```

### 3.2 分层关系

| 层 | 依赖 | 不依赖 |
|----|------|--------|
| `application::GroupContextService` | `core::GroupContextCoreService`、`error`/`audit` | HTTP 类型、DB 客户端、具体 store |
| `core::GroupContextCoreService` | `port::repo::GroupContextRepo`（trait 注入） | application、delivery adapter |
| `port::repo::GroupContextRepo` | — | core、application |

`application` 必须体现 use-case 编排（如 retrieve 串审计日志）；core 持 `Arc<dyn GroupContextRepo>`，不依赖具体 store；delivery adapter 只调 `application`。

### 3.3 model.rs 类型

- `ContextEntry`：content + origin + time + type + provenance + sensitivity + derived
- `Flow`：visible_to + collect_from + propagate_to（写死 {groups:0}）
- `Consistency`：domain + granularity + freshness_class + revalidate_due
- `Lineage`：supersedes + superseded_by + superseded_at
- `Governance`：owner + policy_version + audit_ref
- `PolicyTemplate`：template_id + params + description + flow + consistency
- `StatusRequest/Response`、`CreateRequest/Response`、`UpdateContentRequest/Response`、`RetrieveRequest/Response`

---

## 4. 存储

> 待进行。

### 4.1 并发安全

`supersede` 的"查活跃版本 → 回填 valid_to → 写新条目"三步非原子。存储实现阶段通过 `DbPlugin::transaction` 保证版本链更新的原子性（per-(domain, granularity) 互斥锁或 CAS 操作），防止并发写入产出两个 valid_to=null 的活跃版本。