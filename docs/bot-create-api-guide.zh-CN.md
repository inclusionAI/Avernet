# Bot 创建接口对接指南（新 openapi / 老内部 API）

> 面向前端。覆盖两条创建链路的请求/响应契约、错误码，以及"从第三方 openapi 拿 aicoding 配置来创建 Bot"的接入指引。
>
> 适用版本：backend `feat/engine-vocabulary-template-form`（engine/aicoding 词汇分离）之后。

---

## 0. 两面接口总览

| | 新 openapi 面（推荐新接入） | 老 内部 API（存量页面） |
|---|---|---|
| 创建 | `POST /openapi/v1/bots` | `POST /api/bots` |
| 授权轮询 | `POST /openapi/v1/bots/{bot_id}/auth-status`（GET 拼写逐步退休中） | `POST /api/bots/auth-status` |
| 鉴权 | 经 OCB 网关注入租户身份；owner 以 `user_id` 口径（详见网关接入文档） | 浏览器会话（buservice cookie） |
| 引擎值 | **只收真实引擎**：`openclaw` / `claude_code` / `teclaw` / `hermes` / `moltis`；传 `aicoding` → 400 | 收上述引擎；传 `aicoding` 会被后端**自动折叠**为 `claude_code`（兼容存量调用） |
| 模板入参 | `engine_properties.template`（一个自由 dict，严格校验见 §1.3） | `template_type` + `template_config` 两个字段直传（校验宽松，见 §2.1） |
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
| `engine_properties` | object | — | 引擎私有入参；缺省=普通 bot；提供则**只允许一个键 `template`**（见 §1.3） |

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

**请求示例 B —— applicationCoding（aicoding 形态）bot：**

```json
POST /openapi/v1/bots
{
  "bot_name": "coding-bot",
  "bot_desc": "应用研发 bot",
  "engine": "claude_code",
  "cluster_name": "ACRA",
  "bot_type": "personal",
  "engine_properties": {
    "template": {
      "devflow_workflow": "app-flow-id",
      "code_repos": ["https://code.example.com/team/svc"],
      "yuque_kb_repos": ["team/kb"],
      "bot_template_config": { "ext_config": { "thetaKey": "..." } }
    }
  }
}
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

**错误码**：400 不支持的引擎/集群错配（`code: 400000`）、403 无权限、404 空间不存在、409 名称重复、422 请求体校验失败（含 server-managed 字段，见 §1.3）。

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
  "engine_properties": { "template": { "...": "与创建时一致" } }
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

### 1.3 `engine_properties.template` 校验规则（重点）

这是一个**自由透传 dict**，但有以下硬规则：

**① 顶层 server-managed 键，出现即 422（整单拒绝）：**

`workspace_id`、`template_uid`、`bot_id`、`workspace_status`、`workspace_state`、`start_status`、`engine_form`

这些是**平台管理的身份/形态字段**，不收外部输入。注意：`engine_form`（aicoding 形态标记）由后端在需要时写入，前端**永远不要传**。

**② 已知键的类型检查（类型不符 422）：**

| 键 | 类型 | 说明 |
|---|---|---|
| `devflow_workflow` | string \| object | 工作流标识/描述 |
| `code_repos` | array | 代码仓库 |
| `yuque_kb_repos` | array | 语雀知识库 |
| `bot_template_config` | object | 引擎侧配置（密钥放 `ext_config` 下透传，如 `thetaKey`；`token` 会加密落库） |
| `token` | string | 若出现必须非空 |

**③ 其它未知键**：原样存活、随快照落库（引擎自有扩展），但**平台不解读**——见 §3 命名提醒。

**④ 组合约束**（带 `template` 创建时）：`engine` 必须 `claude_code` + `bot_type=personal` + 个人空间 + 云端。违反 → 409。

### 1.4 查询接口（创建后核对 / 列表展示）

| 端点 | 说明 |
|---|---|
| `GET /openapi/v1/bots?keyword=&engine=&status=&page=&page_size=` | 本人 bot 分页列表 |
| `GET /openapi/v1/bots/{bot_id}` | 单个 bot 详情 |
| `GET /openapi/v1/bots/all`（Header `X-Space-Id` 可选） | 统一卡片列表（个人云/服务/本地） |

响应中的模板相关字段（三个端点一致）：

```json
"template_type": "applicationCoding",        // 无模板为 null
"template_config": {                          // 白名单投影，secret 永不返回
  "template_key": "...", "template_uid": "...",
  "code_repos": [...], "yuque_kb_repos": [...], "devflow_workflow": "...",
  "engine_form": "aicoding"                   // 仅 aicoding 形态 bot 带 markers
}
```

> `template_config` 是**服务的白名单投影**（`engine_form`/`code_repos` 等展示安全键），`token`、`ext_config.thetaKey` 等密钥永远不回。**前端判定"是否 aicoding 形态"：`template_config.engine_form == "aicoding"`，或 `template_type` 非空且不等于 `"normalCC"`**（任一命中即 aicoding 运行时；`normalCC` 是纯 claude_code 模板）。`engine` 字段只可能是真实引擎。

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

## 3. aicoding 场景接入指引（第三方配置 → 创建）

前端从其他团队 openapi 取到 aicoding 配置后调我们的创建接口，按下面映射：

**第一步：engine 固定填 `claude_code`（两个接口都一样）。**
不要透传第三方配置里可能出现的 `aicoding` 引擎值——openapi 面会 400；老 API 虽会自动折叠，也请显式传 `claude_code` 以获得一致行为。

**第二步：把第三方配置字段映射进 `engine_properties.template`（openapi）/ `template_config`（老 API）：**

| 第三方可能的字段 | 我们契约的键位 | 说明 |
|---|---|---|
| 工作流 | `devflow_workflow` | str 或 object |
| 代码仓库 | `code_repos` | list；模板工厂命名是 `repos`/`init_repos`/`application_repo_urls` |
| 语雀/知识库 | `yuque_kb_repos` | list |
| 密钥类（thetaKey 等） | `bot_template_config.ext_config.*` | 嵌套透传，落库加密策略同现状 |
| token | `token` | 出现就必须非空 |

**第三步：剔除/不要携带这些键（openapi 面必 422）：**
`template_uid`、`workspace_id`、`bot_id`、`workspace_status`、`workspace_state`、`start_status`、`engine_form`。如果第三方配置里带 `template_uid`，**由前端丢弃**（模板 uid 由平台侧解析/分配）。

**注意事项：**

1. **命名即语义**：未知键虽然会原样存活落库，但平台只解读上表的键名——第三方字段名若与上表不一致，配置会"存了但没人读"，静默失效。拿到第三方 openapi 的字段表后，先与后端对一遍映射。
2. **形态不需要前端表达**：`engine=claude_code` + applicationCoding 模板创建的 bot，运行时自动路由 aicoding 实现；`engine_form` 由后端在折叠老链路 `aicoding` 引擎值时写入，前端不传。查询面识别形态的判据见 §1.4。
3. **约束**：applicationCoding 创建目前仅支持 `bot_type=personal` + 个人空间。
4. **模板工厂模板（normalCC/architect/用户自建）**：老 API 可用（`template_type` 直传），openapi 面**暂无创建入口**，需要的话单独提需求。

---

## 4. 常见错误速查

| 现象 | 原因 | 处理 |
|---|---|---|
| openapi 创建 400 `unsupported engine` | `engine` 传了 `aicoding` 或未部署的引擎 | 改传 `claude_code` 等真实引擎 |
| openapi 创建 422 提到 `server-managed fields` | `template` 里带了 `template_uid`/`engine_form` 等 | 剔除这些键（§3 第三步） |
| openapi 创建 422 `extra inputs not permitted` | 请求体多了未知字段（如 `engine_options`） 或 `engine_properties` 下不是 `template` | 对照 §1.1 字段表 |
| openapi 创建 409 `application coding does not support...` | 带模板但 engine≠claude_code / bot_type≠personal / 非个人空间 | 对照 §1.3 ④ |
| 老接口创建成功但 `active_engine` 变成 `claude_code` | `engine_type=aicoding` 被折叠（预期行为） | 无需处理；形态看查询面的 `engine_form`/`template_type` |
| auth-status 一直 PENDING | 用户未完成 iframe 授权或 Passport 未就绪 | 继续轮询；终态值（REJECTED 等）会以 400 返回 |
