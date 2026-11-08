# 引擎扩展 API 中继（Engine Extension Relay）— 设计文档

> 状态：**草案** · 日期：2026-11-08 · 范围：`engine/adapter`（主要）、`apps/gateway`、前端、外部引擎 Owner
> English: [design.md](./design.md)

## 1. 背景

引擎适配器（`engine/adapter`，端口 20003）为其承载的所有引擎——OpenClaw、
AICoding、Claude Code、Hermes 以及未来的引擎——暴露同一套 HTTP/WS API。
其中大部分是**通用 API**：会话、对话、MCP、Skills、Cron、审批、文件、节点、模型。
每个引擎通过 `core/*` 下的 Plugin Protocol 实现通用 API，通过
`EngineCapabilities` 声明自身能力；Web 层在每个 handler 开头调用
`check_capability(...)`（`api/caps.py`），不支持的能力统一返回 501。

但有些 API 只对**某一个**引擎有意义。目前它们以三种方式进入适配器：

| 现有模式 | 示例 | 问题 |
| --- | --- | --- |
| 适配器内的引擎专属 router | `api/aicoding/*`、`api/aicoding_sessions/*`、`/api/openclaw/{config,test-connection,disconnect}` | 每个引擎在适配器里多一个模块；每次改动都要发适配器版本；1000 行限制和架构规则持续承压 |
| 引擎专属转发插件 | `/data/*` → `DataProxyService`（仅 AICoding，由 `DATA_PROXY_FORWARD` 能力把关） | 思路正确，但是定制的：每个上游一套 Protocol、一个能力、一个 router |
| WS 方法透传 | `RelayService.forward_request` 转发未知 WS 方法（如 OpenClaw gateway 的 `sessions.list`） | 只支持 WS，绑定的是引擎自身的上游进程，而不是独立部署的服务 |

我们需要一个**统一、通用的中继**来承载引擎专属 API：引擎 Owner 在别处运行自己的
服务，适配器只负责鉴权、校验与转发。

## 2. 目标与非目标

### 目标

1. 为所有引擎专属 HTTP API 提供一个稳定的 URL 命名空间：
   `/api/ext/{engine}/...`。
2. **配置驱动**注册引擎的扩展上游——新增或修改引擎专属 API 不需要改适配器代码，
   也不需要发适配器版本。
3. 默认安全：必须鉴权、白名单放行、限制大小与时长、凭证绝不泄露给上游、不做开放代理。
4. 透明：方法、路径、查询参数、请求体、状态码与响应体原样透传；支持流式
   （SSE / chunked）响应。
5. 可发现：前端可以查询某个引擎提供了哪些扩展 API。
6. 符合微内核架构宪章（`docs/arch/arch.rules.md`）：Protocol + 真实实现 +
   `Noop` / `Mock`，一致性测试位于 `tests/contracts/`，代码中不散落
   `if engine == ...`。

### 非目标

- 取代通用 API。两个及以上引擎都需要的能力应进入通用 API（见 §10 毕业机制）。
- 取代 `RelayService`（WS 方法透传到引擎自身的上游进程）。它保持不变；本设计面向
  由 Owner 独立部署的 HTTP 服务。
- 在中继中做业务逻辑、响应改写、缓存或聚合。中继只是一根管道。
- 托管/部署 Owner 的服务。服务由 Owner 运行，我们只负责访问它。
- 第一阶段不做 WebSocket 中继（§8 给出设计，第二阶段交付）。

## 3. 术语

| 术语 | 含义 |
| --- | --- |
| **扩展 API（Extension API）** | 只有一个引擎支持的 API。 |
| **扩展上游（Extension upstream）** | 由 Owner 运营、实现某引擎扩展 API 的 HTTP 服务。 |
| **中继（Relay）** | 适配器中把 `/api/ext/{engine}/...` 转发到扩展上游的组件。 |
| **路由规则（Route）** | 上游配置中一条白名单 `(方法, 路径模式)`。 |
| **清单（Manifest）** | 上游对自身扩展 API 的自描述（§6.3）。 |

## 4. 架构

```
前端 / Backend / CLI
        │  HTTPS（用户 / Bot / AccessKey 凭证）
        ▼
   Gateway（apps/gateway）
        │  沿用现有路由到该 Bot 的适配器实例，
        │  附带签名的 X-Avernet-Principal
        ▼
   引擎适配器 :20003
   ├── /api/sessions, /api/chat, /api/mcp, ...      ← 通用 API（不变）
   └── /api/ext/{engine}/{path}                     ← 新增：扩展中继
            │ 0. 校验身份（任何查找之前，失败即 401）
            │ 1. engine == 当前激活引擎？
            │ 2. 是否配置了上游？         （ExtRelayRegistry）
            │ 3. 路由是否在白名单？
            │ 4. 构造出站请求头
            │ 5. 转发并流式回传          （ExtRelayService）
            ▼
   扩展上游（Owner 运营，可部署在任何可达位置）
     例：https://aicoding-ext.example.com/v1/...
```

放置位置的理由：

- **放在适配器，而不是 Gateway。** 适配器已经掌握"这个 Bot 跑的是哪个引擎"
  （`EngineManager`）以及能力把关。Gateway 保持与引擎无关，继续负责鉴权并路由到
  Bot 的适配器。
- **按 Bot 实例隔离。** 每个适配器只有一个激活引擎。在运行 `openclaw` 的适配器上
  请求 `/api/ext/hermes/...` 会被拒绝（§7），客户端无法借道错误的 Bot 访问其他引擎的上游。

### 4.1 组件（适配器内）

沿用适配器现有分层（`plugin_api/` 声明 Protocol → `plugins/` 实现，经 `core/`
再导出，由 DI 模块装配），与 `auth_gate` 结构一致：

```
src/engine/community/
├── plugin_api/ext_relay/
│   ├── protocol.py      # ExtRelayService、ExtRelayRegistry Protocol + 异常
│   └── models.py        # UpstreamConfig、RouteRule、RelayRequest、RelayResult
├── core/ext_relay/      # 面向 Web 层的再导出
├── plugins/ext_relay/
│   ├── httpx_impl.py    # 基于连接池 httpx.AsyncClient 的 HTTP 转发
│   ├── yaml_registry.py # 由 ext_relay 配置构建注册表（§5）
│   ├── noop_impl.py     # NoopExtRelayService：未配置 → 501
│   └── mock_impl.py     # 测试用内存实现
├── di/modules/          # 按配置绑定实现；代码中不写 `if engine == ...`
├── api/ext_relay/
│   └── router.py        # /api/ext/{engine}/{path:path}；只处理 HTTP 关注点
└── tests/contracts/test_ext_relay_contract.py
```

```python
class ExtRelayRegistry(Protocol):
    def get(self, engine: str) -> UpstreamConfig | None: ...
    def engines(self) -> list[str]: ...

class ExtRelayService(Protocol):
    async def forward(self, req: RelayRequest) -> RelayResult | StreamingRelayResult: ...
    async def manifest(self, engine: str) -> dict: ...
```

异常是语义化的（`RouteNotAllowed`、`UpstreamNotConfigured`、
`UpstreamUnreachable`、`UpstreamTimeout`、`PayloadTooLarge`），由 router 映射为
HTTP 状态码，沿用现有 `DataProxyError` 的模式。

## 5. 配置

上游在适配器配置（`application-*.yaml`）中注册，以 `DEFAULT_REGISTRY` 中的引擎名为键。示例：

```yaml
ext_relay:
  enabled: true
  defaults:
    connect_timeout_s: 3
    read_timeout_s: 30
    max_request_body_mb: 10
    max_concurrency: 64
  upstreams:
    aicoding:
      base_url: https://aicoding-ext.example.com/v1
      auth:
        type: bearer                    # none | bearer | hmac | mtls
        secret_ref: env:AICODING_EXT_TOKEN   # 禁止明文；使用 env:/file:/密钥服务引用
      forward_principal: true           # 转发签名身份（§7.3）
      routes:                           # 白名单；为空即全部拒绝
        - { methods: [GET],        path: "/workspaces/**" }
        - { methods: [POST],       path: "/workspaces/*/index" }
        - { methods: [GET],        path: "/events", stream: true, read_timeout_s: 600 }
      manifest_path: /.well-known/avernet-ext.json
    hermes:
      base_url: https://hermes-ext.example.com
      auth: { type: none }
      routes:
        - { methods: [GET, POST], path: "/memory/**" }
```

规则：

- 生产环境 `base_url` 必须是 `https`；`http` 仅限回环地址 / local profile。
- 配置值中不允许出现凭证，只允许引用。
- 路由路径是作用于**相对**路径的 glob 模式（`*` = 一段，`**` = 任意深度）。匹配前
  先规范化路径；`..`、编码后的斜杠、连续斜杠一律拒绝。
- 没有 `upstreams` 条目的引擎即没有扩展 API → 501。
- 配置在启动时加载；热加载放在第二阶段。

## 6. API 契约

### 6.1 中继端点

```
{METHOD} /api/ext/{engine}/{path:path}?{query}
```

| 部分 | 行为 |
| --- | --- |
| `METHOD` | `GET, POST, PUT, PATCH, DELETE`（及 `HEAD`）。`OPTIONS` 由适配器的 CORS 中间件应答，不转发。 |
| `engine` | 必须等于适配器当前激活引擎。 |
| `path` | 规范化并通过白名单后拼接到 `base_url`。 |
| `query` | 原样转发。 |
| 请求体 | 原样流式转发，受 `max_request_body_mb` 限制。例外：`hmac` 上游需缓冲请求体（同一上限）以计算签名（§7.3）。 |
| 响应 | 上游状态码、响应头（经过滤，§7.4）和响应体原样返回；`text/event-stream` 和 chunked 响应以流式回传。 |

示例：

```
GET /api/ext/aicoding/workspaces/ws_123/tree?depth=2
→ GET https://aicoding-ext.example.com/v1/workspaces/ws_123/tree?depth=2
```

### 6.2 响应信封

- **上游响应原样透传**——中继不做包装。*建议* Owner 使用平台信封
  `{code, message, data, request_id}`，以便客户端统一处理错误。
- **中继自身产生的错误**始终使用标准信封：

```json
{ "code": 502001, "message": "extension upstream unreachable", "data": null, "request_id": "<trace id>" }
```

客户端通过响应头 `X-Avernet-Relay-Error: <reason>` 区分中继错误与上游错误
（仅中继自身错误带该头）。

### 6.3 发现

```
GET /api/ext/{engine}/_manifest
```

返回上游清单（从 `manifest_path` 拉取，缓存 60 秒），并与配置白名单取交集，
确保不会公布中继会拒绝的路由：

```json
{
  "engine": "aicoding",
  "version": "2026.11.1",
  "apis": [
    { "id": "workspace.tree", "method": "GET", "path": "/workspaces/{id}/tree",
      "summary": "List workspace files", "stream": false },
    { "id": "events", "method": "GET", "path": "/events", "stream": true }
  ],
  "openapi": "/openapi.json"
}
```

首段以 `_` 开头的路径保留给中继自身使用；上游路由不得以 `_` 开头。

此外，当激活引擎配置了上游时，`GET /api/engine/capabilities` 会上报新能力
`ext.relay`（`Capability.EXT_RELAY`），前端据此决定是否调用 `_manifest`。

### 6.4 错误映射

| 场景 | 状态码 | `X-Avernet-Relay-Error` |
| --- | --- | --- |
| `engine` 未注册 | 404 | `unknown_engine` |
| `engine` ≠ 激活引擎 | 409 | `engine_mismatch` |
| 该引擎未配置上游 | 501 | `not_configured` |
| 路径不在白名单 / 保留路径 / 格式非法 | 404 | `route_not_allowed` |
| 该路径不允许此方法 | 405 | `method_not_allowed` |
| 缺少 / 无效身份 | 401 | `unauthenticated` |
| 请求体超限 | 413 | `payload_too_large` |
| 触发并发上限 | 429 | `overloaded` |
| 连接错误 / DNS / TLS | 502 | `upstream_unreachable` |
| 连接或读取超时 | 504 | `upstream_timeout` |
| 上游已应答（任意状态码） | 上游状态码 | —（不带该头） |

不在白名单的路径返回 404（而不是 403），避免暴露存在哪些路由。各项检查按 §4 的流水线顺序执行：身份校验最先进行，未鉴权的调用方一律得到 401，无法探测激活引擎、上游配置或白名单。

## 7. 安全

### 7.1 入站鉴权

中继只接受携带由 Gateway 签发的有效 `X-Avernet-Principal` 的请求。无身份 → 401。
中继绝不信任裸身份头。

注意：适配器的 HTTP 路由目前**不校验** Gateway 身份（适配器只能经由 Gateway /
Bot 沙箱网络访问）。由于中继是适配器上第一个把流量*向外*发送到第三方服务的路由，
不能只依赖网络位置；为适配器增加身份校验是第一阶段的前置依赖（见 §11 问题 2）。

### 7.2 引擎绑定

路径中的 `engine` 必须等于 `EngineManager.engine`。这可以防止把某个 Bot 的适配器当作
跳板访问其他引擎的上游，并保证一个 Bot 的流量只流向其自身引擎的 Owner。

### 7.3 出站身份

向上游发送两类相互独立的身份：

1. **服务凭证**（`auth.type`）：证明调用方是*中继*。`bearer` →
   `Authorization: Bearer <secret>`；`hmac` → 对方法、路径、时间戳和请求体哈希签名；
   `mtls` → 客户端证书。由于请求体哈希必须在发送请求头之前算出，`hmac` 上游会缓冲
   请求体（受 `max_request_body_mb` 限制）而不是流式转发；响应仍然流式回传。
2. **终端用户身份**（`forward_principal: true`）：中继在 `X-Avernet-Principal`
   中转发一个面向上游受众（`aud = ext:{engine}`）的身份令牌，并附带
   `X-Avernet-Bot-Id` 与 `X-Avernet-Engine`。
   为专用受众重新签发（而不是直接转发入站令牌）可确保上游泄露的令牌无法被重放到
   适配器或其他服务——见 §11 问题 2。

   **出站令牌必须不含凭证。** Gateway 内部的身份模型携带密钥——`Bot.token` 与
   `AccessKey.access_key_token`（`apps/gateway/.../spi/authn/_models.py`）——而
   `PrincipalSigner.sign()` 会把完整的身份模型序列化进可读的 `principals` claim。
   更换受众并不能阻止泄露的密钥被单独重放。因此出站令牌必须由专用投影构建，
   绝不能直接使用内部模型的 `model_dump()`：

   | Claim | 来源 | 说明 |
   | --- | --- | --- |
   | `sub_type` | 身份类型 | `user` / `bot` / `app` / `access_key` |
   | `user_id`、`tenant_id` | `UserPrincipal.subject` | 仅标识符 |
   | `bot_id`、`owner_id`、`app_id`、`tenant` | `BotPrincipal.bot` | **绝不**包含 `token` |
   | `app_id`、`tenant` | `AppPrincipal.app` | 仅标识符 |
   | `access_key`、`tenant`、`expire_at` | `AccessKeyPrincipal.access_key` | 仅 key id，**绝不**包含 `access_key_token` |
   | `iss`、`aud`、`iat`、`exp`、`jti` | 中继 / 令牌交换 | 短 TTL（≤ 60 秒） |

   该投影是 claim 白名单（内部模型新增字段在加入此表之前不会被转发），一致性测试
   断言出站令牌中不出现任何密钥类字段。

   **验签需要非对称密钥。** `bare` 签名器使用 HS256（共享密钥）；把该密钥交给外部
   Owner 会让他们能够签发平台令牌。出站令牌使用专用于 `ext:*` 受众的非对称密钥
   （如 ES256/RS256）签名，Owner 通过公开的 JWKS 验签。上游绝不能拿到任何可用于签名的密钥。

### 7.4 请求头策略

| 方向 | 删除 | 新增 |
| --- | --- | --- |
| 请求 → 上游 | 逐跳头（`Connection`、`Keep-Alive`、`TE`、`Trailer`、`Transfer-Encoding`、`Upgrade`、`Proxy-*`）、`Host`、`Authorization`、`Cookie`、入站 `X-Avernet-*` | `X-Forwarded-For/Proto/Host`、`traceparent`、`X-Request-Id`、服务凭证、出站身份 |
| 响应 → 客户端 | 逐跳头、`Set-Cookie`、`Server`、上游 CORS 头 | `X-Request-Id`；CORS 由适配器中间件按自身策略处理 |

### 7.5 SSRF 与滥用防护

- 目标主机**只**来自配置中的 `base_url`；客户端只能控制路径，永远不能控制主机。
  **不**跟随重定向（3xx 原样返回给客户端）。
- 路径规范化与白名单匹配在任何出站请求之前完成。
- 限制：请求体大小、连接/读取超时、单上游并发、响应流式回传（不完整缓冲大响应体）。
- 可选的按路由 `rate_limit`（第二阶段）。

## 8. 流式与 WebSocket

- **第一阶段（HTTP）：** `text/event-stream` 或无 `Content-Length` 的响应按块流式
  回传（`StreamingResponse`）；标记 `stream: true` 的路由使用独立的
  `read_timeout_s`（两个块之间的空闲超时）。客户端断开时取消上游请求。
- **第二阶段（WebSocket）：** 对标记 `ws: true` 的路由提供
  `WS /api/ext/{engine}/{path}`。鉴权、引擎绑定与白名单规则相同；中继连接上游后
  双向透传帧、不解析内容，任一端关闭则两端同时关闭。之所以不放在第一阶段，是因为
  适配器现有 WS 端点承载会话/对话语义，需要单独评审。

## 9. 可观测性

- 链路：透传 `traceparent` / `X-Request-Id`（适配器 `TraceContextMiddleware`）。
- 每次中继调用一条访问日志：引擎、路由模式（而非原始路径）、方法、状态码、上游耗时、
  出入字节数、中继错误原因。绝不记录请求体、查询参数值或凭证头。
- 指标：`ext_relay_requests_total{engine,route,status}`、
  `ext_relay_upstream_latency_seconds{engine,route}`、
  `ext_relay_inflight{engine}`。
- `/api/engine/status` 新增 `ext_relay` 字段：是否已配置、最近一次清单拉取结果、
  上游是否可达。

## 10. 治理：什么应该走中继

| 问题 | 结论 |
| --- | --- |
| 只有一个引擎需要？ | 扩展 API → 走中继。 |
| 两个及以上引擎需要，或前端希望与引擎无关地使用？ | **毕业**：在 `core/*` 中定义通用 Protocol + 能力，各引擎实现，下线对应扩展路由。 |
| 需要适配器本地状态（文件系统、引擎进程、沙箱）？ | 不适合走中继；应作为适配器内的引擎插件。 |
| 会改变 Engine / Backend / BCS 的实体归属？ | 不允许通过中继实现；实体归属规则依然有效。 |

Owner 的义务：发布清单；在同一主版本内保持向后兼容（`base_url` 中带 `/v1`）；
校验转发的身份；对自己上游的可用性负责。上游故障只能降级该引擎的扩展功能，
绝不能影响通用 API。

## 11. 待决问题

1. **前端调用路径。** 前端是通过现在访问适配器的同一条 Gateway 路由调用
   `/api/ext/...`，还是暴露一个专用的 Gateway 域？（建议：沿用同一路由，Gateway 不改。）
2. **适配器中的身份处理。** (a) 入站：适配器需要 Gateway 的验签密钥来校验
   `X-Avernet-Principal`。(b) 出站：为 `aud=ext:{engine}` 重新签发（需要适配器具备
   签名能力，或由 Gateway 提供令牌交换端点）还是原样转发入站令牌（更简单，隔离更弱）。
   建议：校验入站；通过 Gateway 令牌交换重新签发。
3. **配置来源。** 第一阶段按部署使用静态 YAML，后续是否改为由 Backend 管理的注册表，
   让 Owner 自助注册上游。
4. **密钥管理。** 第一阶段除 `env:` 与 `file:` 引用外，是否还需要密钥服务 / mTLS
   后端；如不需要，先交付 httpx + Noop + Mock。
5. **现有引擎专属路由的迁移。** 是否把 `/data/*`（AICoding `DataProxyService`）和
   `api/aicoding/*` 迁移到 `/api/ext/aicoding/*` 并设置弃用窗口，还是保持现状。

## 12. 上线计划

| 阶段 | 交付物 |
| --- | --- |
| 0 | 本设计文档评审通过（EN + 中文）。 |
| 1 | `plugin_api/ext_relay` Protocol、httpx / `Noop` / `Mock` 实现、入站身份校验、router、`EXT_RELAY` 能力、`_manifest`、一致性测试、单元测试（白名单、路径规范化、请求头策略、错误映射、流式）。未设置 `ext_relay.enabled` 时默认关闭。 |
| 2 | WebSocket 中继、限流、配置热加载、密钥服务 / mTLS。 |
| 3 | 迁移第一个真实的引擎专属 API（候选：AICoding `/data/*`），旧路径设置弃用窗口。 |

## 13. 测试策略

- **一致性测试**（`tests/contracts/test_ext_relay_contract.py`）：每个
  `ExtRelayService` 实现（httpx、Noop、Mock）满足相同行为。
- **单元测试**：路径规范化与穿越拒绝、glob 匹配、方法白名单、请求头删除/新增表、
  错误 → 状态码映射、请求体与并发限制、清单取交集。
- **集成测试**：使用进程内假上游（httpx `MockTransport` / 本地 ASGI 应用），覆盖
  状态码/响应体透传、SSE 流式、客户端断开取消、超时与上游不可达。
- **安全测试**：伪造 `X-Avernet-Principal`、`engine` 不匹配、编码穿越
  （`%2e%2e`、`%2f`）、不跟随重定向、上游请求与日志中不出现凭证、Bot 与 AccessKey 调用方的出站身份令牌中不含
  `token` / `access_key_token`。
