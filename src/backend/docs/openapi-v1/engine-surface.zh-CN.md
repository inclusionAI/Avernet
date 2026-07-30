# Track C —— Engine（运行时）面：`/openapi/v1` 包哪些、不包哪些

[English](engine-surface.md) | **简体中文**

_[`README.zh-CN.md`](README.zh-CN.md) 的参考配套文档。README 是活的状态看板；
本文件是稳定的裁定表：**每一个 engine 端点，以及公共 API 是否包装它。**
谁负责什么、整体进展到哪一步，请先看 README。_

---

## Track C 为什么存在

Track A 和 Track B 都假设公共 API 的数据在**后端库表**里。Bot 的**运行时**不在。
会话、对话、审批、模型 —— 这些都在 Bot 的设备上，由 **engine adapter**
（`src/engine`，端口 `20003`）提供，而今天客户端是**直连**它们的：

1. 前端调 `GET /api/v1/devices/bots/{bot_id}/connection`
   （`adapters/http/devices/router.py:476`）→ `{type, target, token, engine_type,
   url, available}`。
2. 前端把命中前缀的请求重写成 `/proxypass/{target}{path}` 并带上
   `X-PROXYPASS-TOKEN` 头，本地模式则是 `http://{target}{path}`
   （`src/frontend/src/requestConfig.ts:150-260`）。

这对内部 TeamClaw 前端没问题，对外部租户则是错的。它把 proxypass 拓扑和裸设备
token 暴露了出去，并且让**从未被设计成公共契约的 engine** 成为集成方直接编程的对象。

**Track C 把 engine 面向客户端的 HTTP 包装到 `/openapi/v1/bots/{bot_id}/…` 之下，
并用一个净化过的 socket 信息端点取代连接信息的移交。**

有两点让它比 Track A/B 更省力：

- **不需要 Track A 阶段，不需要 DDL。** 每一次 engine 调用都以 `bot_id` 为键，而
  bots 已经完成租户隔离（Stage 1，PR #456）。Track C 继承它的方式与 `identity`
  完全相同。**不新增任何表。**
- **传输层已经存在。** `DeviceContextResolver`（全仓唯一 provider 解析点）→
  `DeviceAdapterTransport.invoke()` / `.stream()`
  （`plugin_api/device_adapter_transport.py`）。`CronRelayService` 早在本轮工作开始
  之前就已在生产上跑这条链路服务 `/api/cron`，而
  `openapi_v1/routines/router.py:29` 已经 import 了 `CronRelayServiceProtocol`。
  **routines 就是 Track C 的样板** —— 动手写 handler 前先读它。

---

## 范围规则（这就是全部的决策）

engine 提供 **89 个 HTTP 路由 + 6 个 WebSocket 端点，分布在 25 个 router 中**。
Track C 并不包装其中的大多数。四条规则逐个裁定：

| # | 规则 | 后果 |
|---|---|---|
| **C1** | **前端 → engine 直连（HTTP）** → **包装。** | 这些端点今天在后端没有对应表示。公共调用方没有别的路径可走，所以公共 API 必须提供一条。 |
| **C2** | **前端 → 后端 → engine** → **不在范围内。** | 后端已经为它们提供了对外契约；再包一层 engine 路由会造出第二条、并且会漂移的路径。`/api/cron` 是最清楚的例子 —— 它已经就是 `routines` 类别。 |
| **C3** | **WebSocket** → **不包装。** | 公共 API 返回 socket URL 与需要携带的 headers，由调用方自己建连。转发帧等于把 engine 内部帧格式发布成公共契约。 |
| **C4** | **仅 AICoding** → **不在范围内。** | 产品专有面，不属于租户契约。 |

C1 的权威清单是 `src/frontend/src/requestConfig.ts:189-205` 里的 proxypass 前缀
数组 —— 前端会重写到 engine 的那一组路径前缀，就是它。

---

## 公共面 —— 16 个端点

全部按 bot 收敛在 `/openapi/v1/bots/{bot_id}/…` 之下，全部返回
`openapi_v1/contracts.py` 的 `Envelope[T]` / `Page[T]` 形状。

> **每条路径都以字面量前缀 `/openapi/v1/bots/` 开头。** 下表中的 `…` 只是为了
> 表格宽度而做的缩写。这是硬性约束，不是风格问题：它让 Track C 与既有六个类别
> 保持一致，而且**网关正是按这个前缀转发到 agentclaw 的**，所以挂在别处的路由
> 在生产上根本不可达。有测试对此做断言。

### sessions（7）—— engine `/api/sessions`

> **仅限 personal bot。** 在 `service` bot 上，七条路由全部返回
> `501 "Not supported for this bot type"`，且在任何设备调用**之前**就判定。
> engine 接受 `user_id`、记了日志、然后**丢弃**它 —— `sessions_list()` 根本没有
> `user_id` 参数（`plugins/openclaw/_session.py:125-132`），因此设备会返回它持有的
> 全部会话。在 personal bot 上这一集合就是 owner 自己的；而 service bot 的设备服务
> 多个 caller，那就是所有人的。按 session key 里已带的 `user:<id>` 后缀过滤才是正解，
> 但它属于 **engine** —— 在未过滤的设备响应之上再由后端过滤是可以被绕过的。
> 以后放开到两种 bot 类型不会破坏任何契约。_2026-07-30 决定。_

| 方法 | 公共路径 | engine 路由 | 说明 |
|---|---|---|---|
| GET | `…/sessions` | `GET /api/sessions` | `agent_id`、`session_key`、分页 → `Envelope[Page[Session]]` |
| POST | `…/sessions` | `POST /api/sessions` | `201 Envelope[Session]` |
| GET | `…/sessions/{session_id}` | `GET /api/sessions/{session_id}` | `Envelope[Session]` |
| DELETE | `…/sessions/{session_id}` | `DELETE /api/sessions/{session_id}` | `Envelope[Deleted]` |
| GET | `…/sessions/{session_id}/messages` | `GET …/messages` | 分页 → `Envelope[Page[Message]]` |
| DELETE | `…/sessions/{session_id}/messages` | `DELETE …/messages` | 清空历史 → `Envelope[Deleted]` |
| PATCH | `…/sessions/{session_id}` | `POST …/{session_id}/update` | **差异：** 公共面上部分更新是资源上的 `PATCH`，不是 `/update` 子路径 |

### engine，只读（3）—— engine `/api/engine`

| 方法 | 公共路径 | engine 路由 | 说明 |
|---|---|---|---|
| GET | `…/engine/status` | `GET /api/engine/status` | 进程 / 切换阶段 / 连接数 |
| GET | `…/engine/capabilities` | `GET /api/engine/capabilities` | **Track C 里最重要的端点** —— 见下文《能力》 |
| GET | `…/engine/available` | `GET /api/engine/list` | **差异：** `list` 是动词路径，公共面改用名词。已注册引擎 + active 标记 + 版本 |

> **`POST /api/engine/switch` 与 `POST /api/engine/restart` 刻意不包装。**
> PR #494 已经让 `engine` 在 `PUT /openapi/v1/bots/{bot_id}` 上不可变
> （`extra="forbid"` → 422）；包装 `switch` 等于给那条裁定开后门。而
> `POST /openapi/v1/bots/{bot_id}/restart` 本身就会重新置备设备，包装 `restart`
> 会让同一个 bot 拥有两个影响范围不同的重启动词。_2026-07-30 决定。_

### models（2）—— engine `/api/models`

| 方法 | 公共路径 | engine 路由 | 说明 |
|---|---|---|---|
| GET | `…/models` | `GET /api/models` | `Envelope[Page[Model]]` |
| GET | `…/models/{model_id}` | `GET /api/models/{model_id:path}` | **模型 id 里带斜杠**（`openai/gpt-5.3`）。engine 用 `:path` 转换器；公共路由必须在「URL 编码」与「`:path` 转换器」之间定下来并写进文档 |

### approvals（3）—— engine `/api/approvals`

| 方法 | 公共路径 | engine 路由 | 说明 |
|---|---|---|---|
| GET | `…/approvals/mode` | `POST /api/approvals/mode/get` | **差异：** 读操作用 `GET` + `session_key` query，不用 `POST` |
| PUT | `…/approvals/mode` | `POST /api/approvals/mode/set` | body `{session_key, mode}` |
| GET | `…/approvals/modes` | `GET /api/approvals/modes` | 静态枚举；注意这是 engine 侧唯一**没有**能力门禁的路由 |

### connection（1）—— 新增，engine 无对应物

`GET /openapi/v1/bots/{bot_id}/connection` → `Envelope[Connection]`

`get_device_connection` 的公共替代品。返回可直接使用的 socket，不返回 proxypass 拓扑：

```jsonc
{
  "engine": "openclaw",
  "expires_at": "2026-07-30T12:34:56Z",
  "sockets": [
    {
      "kind": "chat",
      "url": "wss://<host>/proxypass/<target>/api/openclaw/ws",
      "headers": { "x-proxypass-token": "<scoped token>" }
    }
  ]
}
```

`sockets` 是一个**以 `kind` 枚举为字段的列表**（`chat` | `terminal`），不是以
kind 为键的对象。以枚举为键的对象会生成成 `additionalProperties` +
`propertyNames`，而多数客户端生成器会直接丢弃它或摊平成无类型 map —— 那样枚举
就只剩文档意义。列表形式在所有生成器里都能产出真正的类型化枚举，将来加第三个
socket 也是干净的。

这个端点必须守住的规则：

- **只出现该 bot 当前引擎真正提供的 socket。** 列表中不存在某个 `kind` = 不支持。
  `terminal` 只在引擎声明 `WEB_SHELL_OPEN` 时出现；`chat` 依当前引擎解析为
  `/api/openclaw/ws` 或 `/api/claude_code/ws`（engine 还提供一个通用的
  `/api/{engine}/ws`，`api/app.py:310`）。
- **URL 是不透明且完整的。** 调用方不拼接任何东西。`target`、`type` 和裸 `token`
  **不是**字段 —— 它们正是我们要停止对外发布的东西。
- **`expires_at` 必填**，让调用方知道该重新获取，而不是在 token 过期后静默失败
  （内部 WS token TTL 为 120 分钟 ——
  `core/grt_chat/services/grt_chat_service.py:25`）。
- socket 集合是**由能力推导出来的**，因此本端点与 `…/engine/capabilities`
  永远不允许互相矛盾。

---

## 完整 engine 清单与逐项裁定

| engine router | 前缀 | HTTP | WS | 裁定 | 理由 |
|---|---|---|---|---|---|
| `api/session` | `/api/sessions` | 7 | — | ✅ **C1 —— 包装** | 在前端 proxypass 列表中 |
| `api/engine` | `/api/engine` | 5 | — | ✅ **C1 —— 5 取 3** | `switch`/`restart` 排除，见上文 |
| `api/models` | `/api/models` | 2 | — | ✅ **C1 —— 包装** | 在 proxypass 列表中 |
| `api/approvals` | `/api/approvals` | 3 | — | ✅ **C1 —— 包装** | 在 proxypass 列表中 |
| `api/node` | `/api/nodes` | 1 | — | ⛔ **2026-07-30 移除** | 在 proxypass 列表中，按 C1 本应包装 —— 但产品并不需要在公共面上暴露节点清单。以后可增量加回。 |
| `api/cron` | `/api/cron` | 10 | — | ⛔ **C2** | **已经是 `routines` 类别** —— 后端 `/api/cron` → `CronRelayService` → engine。在前端 proxypass 列表里被显式注释掉（`requestConfig.ts:195`） |
| `api/file` | `/api/file` | 5 | — | ⛔ **C2** | 后端在服务端调用 `/api/file/{read,upload,list,remove,rmtree}`；前端从不 proxypass 它 |
| `api/skills` | `/api/skills` | 10 | — | ⛔ **C2** | 后端 `skills_pool` / `skill_center` 驱动 layout、symlink、bindpath；属内部文件系统机制，没有面向租户的契约 |
| `api/mcp` | `/api/mcp` | 10 | — | ⛔ **C2** | 后端把 MCP 配置推送到设备；公共 `mcp` 类别（市场 + 调用方配置）才是租户契约 |
| `api/resource_materialization` | `/api/resource-materializations` | 2 | — | ⛔ **C2** | 后端 `session_resources` 在服务端调用 |
| `api/bot` | `/api/bot` | 1 | — | ⛔ **C2** | `POST /config` —— 后端 `bot_public` 同步路径 |
| `api/bash` | `/api/bash` | 1 | — | ⛔ **C2** | 后端已有 `POST /api/v1/devices/exec_shell`；无论如何，在租户设备上执行任意 shell 都不该是 v1 的公共契约 |
| `api/work_item` | `/api/work-items` | 3 | — | ⛔ **C2** | 经由后端 |
| `api/session_favorites` | `/api/session-favorites` | 3 | — | 🟡 **延后** | engine 自己的文档说它由前端经 Engine Proxy 直连，但它**不在 proxypass 前缀列表里**，且在 `src/frontend` 中零引用。大概率只有 corp 前端在用。后续可增量加入；v1 排除。_2026-07-30 决定。_ |
| `api/routers/openclaw_http` | `/api/openclaw` | 3 | — | 🟡 **延后** | `test-connection` / `disconnect` / `config`。它在 proxypass 数组里写作 `'api/openclaw'` —— **没有前导斜杠**，因此 `url.startsWith()` 永远匹配不到 `/api/openclaw/...`，该条目按现状是死的（`requestConfig.ts:191`）。而且是 openclaw 专有的网关调试工具 |
| `api/default_config` | `/api/openclaw` | 1 | — | 🟡 **延后** | 同一个失效前缀条目 |
| `api/zero_check` | `/api/openclaw/zero-check` | 2 | — | 🟡 **延后** | 同一个失效前缀条目 |
| `api/web_shell` | — | 2 | 1 | ⛔ **C3 / 非 v1** | `GET /terminal`、`/terminal/health`、`WS /ws/terminal`。当引擎声明 `WEB_SHELL_OPEN` 时，socket 可经 `…/connection` 触达；那两个 HTTP 路由是 shell 自身的引导 |
| `api/routers/ws` | — | — | 1 | 🔌 **C3 —— 连接信息** | `/api/openclaw/ws` |
| `api/routers/claude_code_ws` | `/api/claude_code` | — | 1 | 🔌 **C3 —— 连接信息** | `/api/claude_code/ws` |
| `openclaw/router` | `/api/openclaw` | — | 1 | 🔌 **C3** | `/client` —— 网关侧 socket，不是租户 socket |
| `api/app`（模块级） | — | 6 | 2 | ⛔ / 🔌 | `/health`、`/readiness`、`/config`、`/test-connection`、`/disconnect`、`/api/evaluation/report` 属运维面。`WS /ws` 与 `WS /api/{engine}/ws` 是通用对话 socket → `…/connection` |
| `api/aicoding_sessions` | `/api/aicoding/sessions` | 10 | — | ⛔ **C4** | 仅 aicoding |
| `api/aicoding/skill_router` | `/api/aicoding` | 1 | — | ⛔ **C4** | 仅 aicoding |
| `api/aicoding/data_proxy_router` | `/data` | 1 | — | ⛔ **C4** | harness-data 反向代理 |

前端 proxypass 列表里还有两个前缀 —— **`/api/teclaw`** 与 **`/api/notify`** ——
在 OSS engine 中**没有对应 router**，它们属于 corp/teclaw 构建。没有东西可包；
若将来它们进入本仓库，则属于 C1 候选，届时必须重审本表。

---

## 每个 Track C handler 都会继承的契约机制

Track C 复用 Track B 的基建（`responses.py`、`contracts.py`、`principal.py`、
`errors.py` —— 见 README 的《Track B —— 可复用的公共 API 基建》）。下面这五项是
**Track C 新增的**，应当只建一次，而不是每组重复。

### 1. 两套信封必须合成一套

engine 返回 `ApiResponse{success, data, message, warning, total}`
（`src/engine/.../api/response.py`）；公共 API 返回 `Envelope[T]` / `Page[T]`。
一个映射 helper，不是七个：

- `data` → `Envelope.data`，配合每组各自的形状映射。
- `total` → `Page.total`。
- `success: false` → 抛异常，不要透传 —— 带着 `success: false` 的 `200`
  绝不能到达公共调用方。
- **`warning` 在公共契约里还没有位置。** 它是能力*受限*信号（"该引擎只能列出当前
  会话"），静默丢掉会降低答案的正确性。**建议：给 `Envelope` 增加一个可选
  `warning` 字段。** 这是 Track C 唯一改动共享 Track B 契约的地方，需要 bots
  owner 签字。

### 2. 能力是公共契约的逃生舱

engine 的每个 handler 都会调用 `check_capability()`
（`src/engine/.../api/caps.py`）：不支持 → **501**（携带引擎声明的 `fallback`
文案），受限 → body 里带 `warning`。支持集合因引擎而异（`openclaw`、
`claude_code`、`aicoding`、`teclaw`、`hermes`），因此**同一个公共路径，对同一租户
名下的两个 bot 会给出不同答案。**

- `CapabilityNotSupportedError` / 传输层的 501 需要一条 `ENVELOPE_ERRORS` 条目，
  配固定公共文案，并指引调用方去看 `…/engine/capabilities`。
- 这正是 `…/engine/capabilities` 进入 v1 而非延后的原因：它是调用方在事前判断
  另外 16 个端点里哪些会被自己的 bot 真正应答的唯一方式。

### 3. `user_id` 必须来自 principal，绝不来自调用方

若干 engine 路由把 `user_id` 作为 **query 参数**（`GET /api/sessions`、
`POST /api/approvals/mode/get`、`/api/session-favorites`）。在公共面上，它们必须
由 `caller_owner_id(principal)` 填充，并在请求中出现时**予以拒绝**（body 上
`extra="forbid"`，query 模型里显式不收）。原样转发调用方给的 `user_id`，就是同一
租户内的跨调用方读取 —— 隔离守卫抓不到它，因为 engine 根本没有租户维度。

`/api/sessions` 与 `/api/models` 上的 `engine=` 覆盖参数同理：**bot 的当前引擎
才是权威**，不要暴露它。

### 4. 设备就绪是公共错误，不是 500

冷启动、休眠或正在重启的设备会让每一次 engine 调用在传输层失败。请复用
`core/bot_management/readiness.py`（#494 抽出的），而不是另造一套策略，并为全部
16 个端点定下**同一种**行为：掩码 `409 device not ready`，还是自动唤醒后重试。
无论选哪种，`GET /openapi/v1/bots/{bot_id}/status` 仍然是告诉调用方*原因*的端点。

### 5. 封闭值集合用枚举，开放的保持字符串

这是一份对外的、会被生成工具消费的契约，所以真正封闭的值集合应当以真枚举而不是
裸 `str` 抵达客户端生成器。有四个符合条件 —— `SocketKind`（`chat` | `terminal`，
我们自己定义）、`ApprovalMode`（`approve` | `on-miss` | `never`）、`MessageRole`
（`src/engine/.../core/session/models.py:46` 处真实的 `Literal`），以及
`EngineName`（复用 bots 类别已有的，不要另立一份）。

同样重要的是，下面这些**保持字符串**，因为来源本身是开放的，硬造枚举会在出现第
一个新值时炸掉：`Session.permission_mode` /
`.runtime` / `.model`、engine status 里的 `process` 与 `transition` 字典，以及
**能力名**（engine 的 `Capability` 枚举虽然封闭，但明确声明"新增条目是安全的"，
因此在*响应*字段上用严格枚举，会把一次本应向后兼容的 engine 发布变成对外 500）。

动手写枚举前值得知道的两个坑：

- **审批模式不是一套词表，而是两套，并且哪套都没有被强制。**
  `core/approval/models.py:15-19` 把规范三元组写作 **`always` / `on-miss` /
  `never`**，紧接着又声明了一个六值 `Literal`，多出来的三个是别名
  （`approve`≈`always`、`on_miss`≈`on-miss`、`off`≈`never`）。而
  `GET /api/approvals/modes` 公布的是*第三种*组合 ——
  **`approve` / `on-miss` / `never`**（`approvals/router.py:104-125`）。
  没有任何地方做归一化：`plugins/openclaw/_approval.py:57` 把字符串原样转发给
  上游，`core/adapters/openclaw/approval.py:76` 回显的是**请求里的** mode 而不是
  真正提交的值 —— 与它自己的 docstring 相矛盾。本地 stub 返回 `"auto"`
  （`local/openclaw/plugin_impl.py:93`），是 `Literal` 之外的第七个值。后端还
  各自保留了两份同样的列表（`adapters/http/approvals/router.py:117,175-185`）。
  **所以：请求上用枚举**（公布的那三个 —— 只发布一种拼写），
  **响应上用 `str`**（响应端用严格枚举会在 local / singlebox stub 上直接 500）。
- **枚举没有逐成员说明就没什么价值。** OpenAPI 没有原生的成员说明位，所以用
  `json_schema_extra={"x-enum-descriptions": {...}}` 再加字段描述里的散文说明，
  并让枚举继承 `str, Enum`，这样 schema 才会输出 `type: string` + `enum`。

**被包装分组的能力矩阵**，来自 `engines/openclaw/engine.py:55-134` 与
`engines/claude_code/engine.py:45-105`：

| 组 | openclaw | claude_code |
|---|---|---|
| sessions（list/get/delete/messages/update） | ✅ | ✅ |
| sessions **create** | ✅ | ⚠️ limited —— 会返回真实的 warning 文案 |
| approvals get/set | ✅ | ❌ 501，且未声明 `fallback` |
| models | ✅ | ✅ |
| engine status/capabilities/available | ✅ 无门禁 | ✅ 无门禁 |

`claude_code` 的 **limited** `SESSION_CREATE` 正是让 `Envelope.warning` 从"理论
需要"变成"实际必需"的活例子。

一处刻意的背离：`GET /api/approvals/modes` 是 engine 侧唯一**没有**能力门禁的
路由（`approvals/router.py:104`），所以在 `claude_code` bot 上它照样公布三个模式，
而 get 与 set 都返回 501。公共侧的 `…/approvals/modes` 以 `APPROVAL_GET` 为门禁，
让三条审批路由在同一个 bot 上保持一致。（`Capability.APPROVAL_LIST` 定义在
`core/engine/capability.py:84`，但**没有任何引擎声明它、也没有任何路由检查它**
—— 是死代码。）

### 6. 传输层抛出的错误

`DeviceAdapterTransport` 会抛 `DeviceAdapterEndpointNotFoundError`（设备返回 404
—— 该运行时不提供此端点）、`DeviceAdapterHTTPStatusError`（其它非 2xx）和
`DeviceAdapterTimeoutError`。三者都需要 `ENVELOPE_ERRORS` 条目。按 README 里
Track B 的坑：**基类放最后** —— `ENVELOPE_ERRORS` 按插入顺序第一个
`isinstance` 命中即返回。

---

## 隔离：Track C 需要什么、不需要什么

- **不需要 Track A 阶段。** 没有新表，没有需要加 `avernet_tenant` 的对象。
- **不需要 DDL。** README 的带外库表变更小节不新增内容。
- **隔离完全来自 `bot_id` 查询。** 每个 handler 在触达设备**之前**，都要先用
  `caller_owner_id(principal)` 收敛地经 bot service 解析该 bot。外部或跨租户的
  `bot_id` 必须是**掩码 404**，与"无此 bot"逐字节一致 —— `BotModel` 上的
  Track A 守卫免费提供这一点，方式与 `identity` 完全相同。
- **只能通过已解析的 bot 触达设备。** 任何 handler 都不得接受调用方传入的
  `binding_id`、`device_uuid` 或 `target`。

---

## 路由注意事项

新组位于 `/openapi/v1/bots/{bot_id}/{sessions,engine,models,approvals,connection}`
—— 比 `{bot_id}` 通配符**深一段**，因此不需要 `openapi_v1/__init__.py:32-40` 中
`_SUBGROUPS` 强制的"字面子组优先"顺序。但仍必须保证注册后
`/openapi/v1/bots/mcp`（字面市场组）继续先于 `/openapi/v1/bots/{bot_id}` 命中。

留意一处近似冲突：按 bot 收敛的 MCP 会是 `/openapi/v1/bots/{bot_id}/mcp/...`，
它与现有市场组 `/openapi/v1/bots/mcp/...` 是**不同的资源**。Track C 不新增它
（规则 C2），但后来的读者绝不能把两者合并。

---

## 已定的决策

- **2026-07-30 —— 采纳范围规则（C1–C4）。** 包装前端直连的 engine HTTP；经由后端
  的 engine 调用交给已经在其之上的后端契约；socket 返回连接信息而不是转发；排除
  aicoding。
- **2026-07-30 —— v1 面定为 16 个端点**：sessions 7、engine 3、models 2、
  approvals 3、connection 1。**移除 nodes** —— 前端确实 proxypass 了
  `/api/nodes`，按规则 C1 本应包装，但产品并不需要在公共面上暴露节点清单。
  以后可增量加回。
- **2026-07-30 —— 排除 `engine/switch` 与 `engine/restart`**，以保住 #494 的引擎
  不可变裁定，并避免出现两个重启动词。
- **2026-07-30 —— `session-favorites` 与 `/api/openclaw` HTTP 三件套
  （外加 default-config、zero-check）延后**，不是取消。两者都是增量的：以后再加
  不会破坏任何已发布的契约。
- **2026-07-30 —— sessions 组仅服务 `personal` bot**；`service` 返回 `501`。
  `BotType` 是 `Literal["personal", "service"]`，而 PR #494 已经允许外部租户创建
  两者之一，所以这是真实存在的情况，不是假设。其余四个组两种类型都服务。
- **2026-07-30 —— 对话仍然是 WebSocket。** 不做 `POST /chat`，不做 SSE。公共 API
  交还 URL 与 headers，socket 由调用方自己持有。

## 留给 SDD 的开放问题

1. **`Envelope` 上的 `warning`** —— 增加可选字段（推荐）、走响应头，还是丢弃该
   信号？涉及共享的 Track B 契约。
2. **就绪行为** —— 掩码 `409` 还是自动唤醒后重试，16 个端点统一。
3. **带斜杠的 `model_id`** —— `:path` 转换器还是强制 URL 编码。
4. **分页** —— engine 收 `limit`/`offset`；公共 `Page` 形状必须干净映射，包括
   `/api/models` 这个返回扁平列表、没有 `total` 的路由。
5. **超时** —— `DeviceAdapterTransport.invoke()` 支持逐调用超时；公共面需要为每组
   定一个写进文档的截止时间。
