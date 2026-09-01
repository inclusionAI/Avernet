# Bot 创建接口对接指南（新 openapi / 老内部 API）

> 面向前端。覆盖两条创建链路的请求/响应契约、错误码，以及"从模板工厂 / 第三方 openapi 拿 aicoding 配置来创建 Bot"的接入指引。
>
> 适用版本：backend `feat/engine-vocabulary-template-form`（engine/aicoding 词汇分离）之后，含 openapi 模板入参 v3 契约（`engine_properties.template_type` + `template_config`，工厂快照透传）。
> v3 契约设计文档：[docs/superpowers/specs/2026-09-01-openapi-template-config-passthrough-design.md](superpowers/specs/2026-09-01-openapi-template-config-passthrough-design.md)

---

## 0. 两面接口总览

| | 新 openapi 面（推荐新接入） | 老 内部 API（存量页面） |
|---|---|---|
| 创建 | `POST /openapi/v1/bots` | `POST /api/bots` |
| 授权轮询 | `POST /openapi/v1/bots/{bot_id}/auth-status`（GET 拼写逐步退休中） | `POST /api/bots/auth-status` |
| 鉴权 | 经 OCB 网关注入租户身份；owner 以 `user_id` 口径（详见网关接入文档） | 浏览器会话（buservice cookie） |
| 引擎值 | **只收真实引擎**：`openclaw` / `claude_code` / `teclaw` / `hermes` / `moltis`；传 `aicoding` → 400 | 收上述引擎；传 `aicoding` 会被后端**自动折叠**为 `claude_code`（兼容存量调用） |
| 模板入参 | `engine_properties.template_type` + `template_config`（工厂快照透传——与 available-tc-list item 逐字段对应；或手填 applicationCoding。严格校验见 §1.3） | `template_type` + `template_config` 两个字段直传（校验宽松，见 §2.1） |
| 响应封套 | `{code, message, data, request_id}`，`code = HTTP状态码×1000` | `{success, message, error_code, data}` |
| 状态 | 测试中，未上线 | 已在线 |

**创建流程（两面相同）**：创建 → 首次可能需要用户授权（返回授权 URL）→ 前端引导用户完成 → 轮询 auth-status → `ISSUED` 时 Bot 才真正落库创建。

---

## 1. 新 openapi 面

### 1.1 创建 `POST /openapi/v1/bots`

**请求体**（`extra=forbid`，多传字段直接 422）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `bot_name` | string | ✅ | 非空、不含 `@`、首尾空白会被 trim、租户内唯一（重复 409） |
| `bot_desc` | string | ✅ | 描述，可为空串 |
| `engine` | string | ✅ | 真实引擎：`openclaw` / `claude_code` / `teclaw` / `hermes` / `moltis`；以部署配置为准（可从 available-engines 端点读取）。**`aicoding` 不是合法值，400** |
| `cluster_name` | string | ✅ | `ACRA`（teclaw 以外的一切引擎）/ `ANDC`（仅 teclaw），与 engine 一一对应，错配 400 |
| `bot_type` | string | ✅ | `personal` 或 `service` |
| `space_id` | string | — | 业务空间上下文，缺省用个人空间 |
| `engine_properties` | object | — | 引擎私有入参；缺省=普通 bot。提供则**只允许两个键**：`template_type`（工厂快照必传、照抄模板值；手填省略或写死 `applicationCoding`）+ `template_config`（非空 dict，必填）。形态判定与校验见 §1.3 |

**请求示例 A —— 普通 bot：**

```json
POST /openapi/v1/bots
{
  "bot_name": "research-assistant",
  "bot_desc": "Summarizes weekly industry news.",
  "engine": "openclaw",
  "cluster_name": "ACRA",
  "bot_type": "personal"
}
```

**请求示例 B —— 手填 applicationCoding（aicoding 形态，散字段拼装）：**

```json
POST /openapi/v1/bots
{
  "bot_name": "coding-bot",
  "bot_desc": "应用研发 bot",
  "engine": "claude_code",
  "cluster_name": "ACRA",
  "bot_type": "personal",
  "engine_properties": {
    "template_type": "applicationCoding",
    "template_config": {
      "devflow_workflow": "app-flow-id",
      "code_repos": ["https://code.example.com/team/svc"],
      "yuque_kb_repos": ["team/kb"],
      "bot_template_config": { "ext_config": { "thetaKey": "..." } }
    }
  }
}
```

（`template_type` 可省略——手填形态允许省略或写 `applicationCoding`，传其它值 422，见 §1.3 ②。）

**请求示例 C —— 工厂模板快照照抄（从 available-tc-list 选模板创建，推荐）：**

把 `/openapi/v1/bot-templates/available-tc-list` 返回的 item **逐字段照抄**即可——零键名映射、零二次 resolve 调用：

```json
POST /openapi/v1/bots
{
  "bot_name": "my-bot",
  "bot_desc": "从模板工厂选模板创建",
  "engine": "claude_code",
  "cluster_name": "ACRA",
  "bot_type": "personal",
  "engine_properties": {
    "template_type": "normalCC",
    "template_config": {
      "template_key": "applicationCoding",
      "template_uid": "aicoding_bot_template",
      "template_version": "V1",
      "template_version_id": 2800006,
      "template_name": "应用 Bot",
      "image": "reg.antgroup-inc.cn/aicoding/bot-runtime:1.2.3",
      "resource_spec": { "cpu": "4", "memory": "8g", "disk": "50" },
      "envs": { "APP_LANG": "zh-CN" },
      "capabilities": { "enable_network": true },
      "bot_template_config": { "ext_config": { "thetaKey": "..." } }
    }
  }
}
```

请求字段与 tc-list item 的逐字段对应：

| 请求字段 | 来源（tc-list item） | 说明 |
|---|---|---|
| `engine` | `item.engine_type` | — |
| `bot_type` | `item.bot_type` | — |
| `engine_properties.template_type` | `item.template_type` | **照抄原值，任意值**（`normalCC` / `architect` / 用户自建字符串都合法，后端不校验值域）；工厂形态**必传**，缺失/空串 422 |
| `engine_properties.template_config` | `item.template_config` | **整段原样回传**：`template_key` / `template_uid` / `template_version` / `template_version_id` / `template_name` / `image` / `resource_spec` / `envs` / `capabilities` / `bot_template_config` 等快照键全部保留，**一个键都不删**（含 `template_uid`，见 §1.3 ①） |

用户在模板动态表单里填的值**追加**为 `template_config.custom_field_values`（dict，键名以该模板的表单 schema 为准；模板未开动态表单则不带该键）：

```json
"custom_field_values": { "repo_url": "https://code.example.com/team/svc" }
```

**响应：**

- **201**（`code: 201000`）— 不需授权，直接创建成功，`data` 为 Bot 对象（见 §1.4 字段）。
- **202**（`code: 202000`）— 需要用户授权，`data`：

```json
{
  "bot_id": "20260813_a7k2m9p1",
  "iframe_url": "https://auth.example.com/passport/consent?flow=f-123",
  "redirect_url": ""
}
```

  前端引导用户完成 `iframe_url` 授权，然后轮询 §1.2。

**错误码**：400 不支持的引擎/集群错配（`code: 400000`）、403 无权限、404 空间不存在、409 名称重复或组合不支持（见 §1.3 ⑤）、422 请求体校验失败（server-managed 字段 / 混传 / 形态冒充，见 §1.3）。

### 1.2 授权轮询 `POST /openapi/v1/bots/{bot_id}/auth-status`

**不是只读操作**——授权通过后 Bot 在这里才真正创建。轮询时必须把创建时的属性**原样回传**（不回传会按默认值创建，与用户请求矛盾），且创建接口的所有校验会全部重跑：

```json
POST /openapi/v1/bots/20260813_a7k2m9p1/auth-status
{
  "engine": "claude_code",
  "cluster_name": "ACRA",
  "bot_name": "coding-bot",
  "bot_desc": "应用研发 bot",
  "bot_type": "personal",
  "engine_properties": { "template_type": "…", "template_config": { "…": "与创建时一致" } }
}
```

**响应 `data`（`code: 200000`）：**

```json
{
  "status": "ISSUED",          // PENDING=继续轮询; ISSUED=已创建; 其它值(如 REJECTED)=终态,以400返回
  "message": null,
  "bot": { "bot_id": "...", "engine": "claude_code", "template_type": "applicationCoding", "..." : "..." }
}
```

`bot` 仅在 `ISSUED` 时返回。Passport 尚未就绪时返回 `PENDING` + 提示 message，继续轮询即可。

### 1.3 `engine_properties` 校验规则（重点）

`engine_properties` 只收两个键（`extra=forbid`，多传 422 `unsupported engine_properties fields: [...]`，**旧键 `template` 已废**）：

- `template_type`（string，可省）
- `template_config`（非空 dict；缺失/空 → 422 `applicationCoding template_config must not be empty`）

提供 `engine_properties` 时走哪种**形态由 `template_config` 的内容自动判定**（判定与运行时消费完全对齐，前端不用声明）：

| 形态 | 判定 | 落库行为 |
|---|---|---|
| **工厂快照透传**（§1.1 示例 C） | `template_config.template_key` 与 `template_config.template_uid` **双非空** | 快照原样落库，`template_type` 用透传值（值域开放，照抄 item）；**不做键级类型校验** |
| **手填 applicationCoding**（§1.1 示例 B） | 无上述双键身份（零散工厂键按未知键存活） | 固定 `template_type=applicationCoding`；键级类型校验照旧 |

**① server-managed 键，出现即 422（整单拒绝，`template_config contains server-managed fields: [...]`）：**

两种形态**都拒**：`workspace_id`、`bot_id`、`workspace_status`、`workspace_state`、`start_status`、`engine_form`

这些是**平台管理的身份/形态字段**，不收外部输入。`engine_form`（aicoding 形态标记）由后端在需要时写入，前端**永远不要传**。

- 工厂快照形态：此外对**四个工厂身份键 `template_key` / `template_uid` / `template_version` / `template_version_id` 放行**——快照里的身份键照抄回传即可，**`template_uid` 不需要前端剔除**。
- 手填形态：上表之外还拒 **`template_uid`**（模板 uid 由平台侧解析/分配；手填里出现即拒，想要工厂身份就走完整快照）。

**② 手填形态的 `template_type` 冒充防护：**

手填形态 `template_type` 省略或写死 `"applicationCoding"`；传**其它任何值 → 422**（`engine_properties.template_type must be applicationCoding for non factory snapshots`）。想要 `normalCC`/`architect`/自建的 `template_type`，必须带完整工厂快照（`template_key` + `template_uid` 双键）走工厂路径。

**③ 已知键的类型检查（类型不符 422）——仅手填形态；工厂快照不做键级类型校验，透传：**

| 键 | 类型 | 说明 |
|---|---|---|
| `devflow_workflow` | string \| object | 工作流标识/描述 |
| `code_repos` | array | 代码仓库 |
| `yuque_kb_repos` | array | 语雀知识库 |
| `bot_template_config` | object | 引擎侧配置（密钥放 `ext_config` 下透传，如 `thetaKey`；顶层 `token` 会加密落库） |
| `token` | string | 若出现必须非空 |

**④ 未知键**：原样存活、随快照落库（引擎自有扩展），但**平台不解读**——见 §3 命名提醒。工厂快照同理：未知键透传落库、平台不解读。

**⑤ 混传拒绝 + 组合约束：**

- **混传**：完整工厂快照（双键身份）+ 手填专用键（`devflow_workflow` / `code_repos` / `yuque_kb_repos`）→ 422 `template factory snapshot must not mix application-coding fields: [...]`。工厂路径不解读手填键，两种形态**二选一**。只带零散工厂键（缺 `template_uid`）混有手填键 → 走手填路径，工厂键按未知键存活。
- **组合约束（工厂与手填同受约束）**：`engine` 必须 `claude_code` + `bot_type=personal` + 个人空间 + 云端。违反 → 409（`BotCombinationUnsupportedError`，既语文案如 `application coding is cloud-only` / `application coding does not support engine: ...`）。

**密钥落库口径**：顶层 `token` 出现即按既有策略加密落密文；`bot_template_config.ext_config.thetaKey` 后端无加密入口，密文由调用方产生、原样落库。查询面按 #1785 verbatim 决策随存随显（见 §1.4）。

### 1.4 查询接口（创建后核对 / 列表展示）

| 端点 | 说明 |
|---|---|
| `GET /openapi/v1/bots?keyword=&engine=&status=&page=&page_size=` | 本人 bot 分页列表 |
| `GET /openapi/v1/bots/{bot_id}` | 单个 bot 详情 |
| `GET /openapi/v1/bots/all`（Header `X-Space-Id` 可选） | 统一卡片列表（个人云/服务/本地） |

响应中的模板相关字段（三个端点一致）——**整段落库快照 verbatim 回显**（REL20260901 #1785 决策：查询面 owner-scoped，回显的是创建者自己的输入，不做投影过滤）：

```json
"template_type": "normalCC",
"template_config": {
  "template_key": "applicationCoding", "template_uid": "aicoding_bot_template",
  "template_version": "V1", "template_version_id": 2800006,
  "template_name": "应用 Bot",
  "image": "reg.antgroup-inc.cn/...", "resource_spec": { "cpu": "4", "memory": "8g", "disk": "50" },
  "envs": { "...": "..." }, "capabilities": { "...": false },
  "custom_field_values": { "repo_url": "..." },
  "bot_template_config": { "...": "..." }
}
```

- **创建时存进去什么，查询就回什么**——包括调用方自己传入的 `token` / `bot_template_config.ext_config.thetaKey`（按 #1785 的 owner-scoped 决策随存随显）；`token` 落库时按既有策略存的是密文。
- `template_type` = 创建时的值（透传值或手填形态的 `applicationCoding`）。
- 工厂 bot 与手填 applicationCoding bot 查询行为一致，无分轨。

> **前端判据**：**识别工厂 bot**——`template_config.template_uid` 非空（或 `template_key` / `template_version` / `template_version_id` 任一存在）。**判定 aicoding 形态**——`template_config.engine_form == "aicoding"`，或 `template_type` 非空且不等于 `"normalCC"`（任一命中即 aicoding 运行时；`normalCC` 是纯 claude_code 模板）。`engine` 字段只可能是真实引擎。

---

## 2. 老 内部 API

### 2.1 创建 `POST /api/bots`

**请求体**（宽松直传，浏览器会话取 user_id）：

| 字段 | 必填 | 说明 |
|---|---|---|
| `bot_name` | — | 不传按规则默认命名 |
| `bot_desc` | — | |
| `engine_type` | — | 缺省 `openclaw`。传 `aicoding` 会被后端**自动折叠为 `claude_code`**（响应与落库都是 claude_code，存量调用零破坏） |
| `bot_type` | — | 缺省 `personal` |
| `entity_id` / `entity_type` | — | 缺省 `staff_{user_id}` / `staff` |
| `template_type` | — | 如 `applicationCoding`；模板工厂类型（`normalCC` 等）也可 |
| `template_config` | — | 配置 dict；**LEGACY 模式，不拒 server-managed 字段**（`template_uid` 等平台字段可带） |
| `avatar_url` / `share_policy` | — | |

**响应**（`{success, message, error_code, data}`）：

```json
// 授权已具备，直接创建成功（error_code=200）
{ "success": true, "data": {
    "bot": { "bot_id": "...", "active_engine": "claude_code", "...": "..." },
    "passport": { "token": "...", "status": "ISSUED", "is_first_bot": false } } }

// 需要授权（error_code=401，前端引导 iframe_url 后轮询 2.2）
{ "success": false, "error_code": 401, "data": {
    "need_authorization": true, "bot_id": "...",
    "iframe_url": "...", "redirect_url": "" } }
```

**错误码**：400 参数非法、401 需授权（见上）、409 重名或组合不支持（`BotCombinationUnsupportedError`）、429 数量上限、500/501 服务异常、5400 授权服务异常。

### 2.2 授权轮询 `POST /api/bots/auth-status`

Body 必带 `bot_id`，其余与创建一致回传。响应 `data.status ∈ {PENDING, ISSUED}`，`ISSUED` 时带 `data.bot`。

---

## 3. aicoding 场景接入指引（模板工厂 / 第三方配置 → 创建）

前端拿到 aicoding 配置的来源分两类，拼装方式二选一：**模板快照**（available-tc-list 选模板）→ 工厂照抄；**散字段**（第三方 openapi）→ 手填拼装。

**第一步：engine 固定填 `claude_code`（两个接口都一样）。**
不要透传第三方配置里可能出现的 `aicoding` 引擎值——openapi 面会 400；老 API 虽会自动折叠，也请显式传 `claude_code` 以获得一致行为。

**第二步：按配置来源分流。**

- **来源是 available-tc-list item（模板快照，推荐）→ 直接走工厂快照照抄（§1.1 请求示例 C）**：`engine ← item.engine_type`、`bot_type ← item.bot_type`、`engine_properties.template_type ← item.template_type`（照抄原值）、`engine_properties.template_config ← item.template_config` **整段原样回传**（含 `template_uid` 等身份键，后端放行，无需剔除），用户动态表单值**追加**为 `template_config.custom_field_values`。**这条路径零键名映射、不做键级类型校验、不剔除任何键。**
- **来源是散字段（第三方给的不是模板快照）→ 按第三步映射表手填拼装。**

**第三步（手填形态）：映射进 `engine_properties.template_config`（openapi，附 `template_type: "applicationCoding"` 或省略）/ `template_config`（老 API），并剔除 server-managed 键：**

| 第三方可能的字段 | 我们契约的键位 | 说明 |
|---|---|---|
| 工作流 | `devflow_workflow` | str 或 object |
| 代码仓库 | `code_repos` | list；模板工厂命名是 `repos`/`init_repos`/`application_repo_urls` |
| 语雀/知识库 | `yuque_kb_repos` | list |
| 密钥类（thetaKey 等） | `bot_template_config.ext_config.*` | 嵌套透传，落库加密策略同现状 |
| token | `token` | 出现就必须非空 |

**手填必须剔除/不要携带这些键（openapi 面 422）：**
`template_uid`、`workspace_id`、`bot_id`、`workspace_status`、`workspace_state`、`start_status`、`engine_form`。如果第三方配置里带 `template_uid`，**由前端丢弃**（模板 uid 由平台侧解析/分配；这条只约束手填形态——工厂快照来源整段回传放行）。**也不要把手填键拼进完整工厂快照**（422 混传拒绝，见 §1.3 ⑤）。

**注意事项：**

1. **命名即语义（手填形态）**：未知键虽然会原样存活落库，但平台只解读上表的键名——第三方字段名若与上表不一致，配置会"存了但没人读"，静默失效。拿到第三方 openapi 的字段表后，先与后端对一遍映射。
2. **形态不需要前端表达**：`engine=claude_code` + applicationCoding 手填创建的 bot，运行时自动路由 aicoding 实现；`engine_form` 由后端在折叠老链路 `aicoding` 引擎值时写入，前端不传。查询面识别形态的判据见 §1.4。
3. **约束**：applicationCoding 创建目前仅支持 `engine=claude_code` + `bot_type=personal` + 个人空间 + 云端（手填与工厂快照同受此组合约束，违反 409）。
4. **模板工厂模板（normalCC/architect/用户自建）**：openapi 面单已支持——走工厂快照透传（§1.1 请求示例 C），`template_type` 照抄 item 值；老 API 照旧 `template_type` 直传。

---

## 4. 常见错误速查

| 现象 | 原因 | 处理 |
|---|---|---|
| openapi 创建 400 `unsupported engine` | `engine` 传了 `aicoding` 或未部署的引擎 | 改传 `claude_code` 等真实引擎 |
| openapi 创建 422 `applicationCoding template_config must not be empty` | `template_config` 缺失或空 dict | 工厂=快照整段回传；手填=散字段拼装，非空必填 |
| openapi 创建 422 提到 `server-managed fields` | 手填 `template_config` 带了 `template_uid`/`engine_form` 等；或工厂快照带了 `workspace_id`/`engine_form` 等 | 手填：剔除这些键（§3 第三步）；工厂快照：四个工厂身份键（`template_key`/`template_uid`/`template_version`/`template_version_id`）放行，其余 server-managed 键剔除 |
| openapi 创建 422 `template factory snapshot must not mix application-coding fields` | 完整工厂快照（`template_key`+`template_uid`）里混了 `devflow_workflow`/`code_repos`/`yuque_kb_repos` 手填键 | 工厂与手填二选一：快照整段回传，别拼手填键（§1.3 ⑤） |
| openapi 创建 422 `engine_properties.template_type is required for template factory snapshots` | 工厂快照未传 `engine_properties.template_type`（或空串） | 照抄 tc-list item 的 `template_type`（§1.1 示例 C 字段表） |
| openapi 创建 422 `engine_properties.template_type must be applicationCoding for non factory snapshots` | 手填形态传了 `applicationCoding` 以外的 `template_type` | 手填省略该键或写死 `applicationCoding`；要其它值走工厂快照 |
| openapi 创建 422 `extra inputs not permitted` | 请求体多了未知字段（如 `engine_options`），或 `engine_properties` 下不是 `template_type`/`template_config`（旧键 `template` 已废） | 对照 §1.1 字段表与 §1.3 键域 |
| openapi 创建 409 `application coding does not support...` 等 | 带模板载荷（工厂**或**手填）但 engine≠claude_code / bot_type≠personal / 非个人空间 / 非云端 | 对照 §1.3 ⑤（工厂与手填同受约束） |
| 老接口创建成功但 `active_engine` 变成 `claude_code` | `engine_type=aicoding` 被折叠（预期行为） | 无需处理；形态看查询面的 `engine_form`/`template_type` |
| auth-status 一直 PENDING | 用户未完成 iframe 授权或 Passport 未就绪 | 继续轮询；终态值（REJECTED 等）会以 400 返回 |
