# teclaw 引擎契约：`GET /api/engine/status`

> **给 teclaw owner 的实现说明。**
>
> 一句话：**容器内需要新增一个 HTTP 端点 `GET /api/engine/status`，返回一个
> 不带信封的 JSON 对象，平台只读其中三个字段。**
>
> 平台侧 `GET /openapi/v1/bots/{bot_id}/engine/status` 已经上线，它不查数据库，
> 而是实时转发到你们容器里的这个端点。今天 teclaw 容器没有实现它，所以这条
> 公共端点对 teclaw bot 一定失败。本文档给出实现所需的完整协议。
>
> **需要你们实现的只有一个端点。**请求怎么进容器、用什么框架、进程状态怎么
> 采集，都是你们的实现细节，本文档不过问。

---

## 1. 为什么需要这个

平台公共面（Track C）有一组「引擎运行态」端点，语义是**此刻**的运行状态，
而不是数据库里记录的置备结果。它们与 `GET /api/bots/{bot_id}/status` 是两个
不同的问题：

| | `GET /api/bots/{bot_id}/status` | `GET /openapi/v1/bots/{bot_id}/engine/status` |
| --- | --- | --- |
| 数据来源 | backend 自己的两张表 | **实时调用容器内的引擎** |
| 回答的问题 | 置备走完了吗 | 引擎进程现在活着吗 |
| 容器不可达时 | 照常返回 `PENDING` | 409 / 502 / 504 |

前者已经覆盖 teclaw（binding 状态由平台轮询 BaaS publish progress 回填），
**后者必须由容器自己回答，平台无法代答**——这是本文档存在的唯一原因。

ARCA / BaaS 系容器里跑的是平台自己的 engine adapter
（`src/engine/src/engine/community/api/engine/router.py`），这个端点是现成的。
teclaw 容器由你们管理，所以这一段需要你们对齐。

---

## 2. 请求：到达容器时长什么样

平台侧链路（仅供理解，不需要你们实现）：

```
GET /openapi/v1/bots/{bot_id}/engine/status?stage=draft
  → 准入 + 鉴权（MEMBER 级）
  → 解析 bot 与设备（DeviceContextResolver → TeclawConnInfoBuilder → BaaS get_ws_info）
  → EngineRuntimeRelay.call(method="GET", path="/api/engine/status", enveloped=False)
  → 经 agentclawproxy /proxypass 网关进入容器
```

**到达你们容器的请求：**

| 项 | 值 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/engine/status` |
| 端口 | BaaS `/http-info` 返回的 `engine_port`，缺省 **20003** |
| query 参数 | **无**（`stage` 只用于平台侧选哪个容器，不下传） |
| 请求体 | **无** |
| 鉴权 header | `x-proxypass-token`，由网关校验；容器侧通常无需再校验 |
| 超时 | transport 缺省 **120 秒**（`CommunityDeviceAdapterTransport._DEFAULT_TIMEOUT`），本路由不另设 |

> **平台不会在容器没起来时调你们。**设备解析失败（BaaS `get_ws_info` 拿不到
> 连接信息）在转发之前就变成 409 `Bot device is not ready`。也就是说，你们
> 收到这个请求时，容器本身是活的——**但引擎进程未必是**，这正是本端点要回答
> 的问题。

---

## 3. 响应：契约本体

### 3.1 形状

HTTP **200**，`Content-Type: application/json`，body 是一个 **JSON 对象**：

```json
{
  "engine": "teclaw",
  "active_connections": 2,
  "process": {
    "running": true
  },
  "transition": null
}
```

### 3.2 三条硬约束

**A1 — 不带信封。**这是整个引擎 HTTP 面**唯一**一个不带信封的路由。其余端点
都返回 `{success, data, message, ...}`，这一个**直接把状态对象放在顶层**。

```jsonc
// ✅ 正确
{"engine": "teclaw", "active_connections": 0, "process": {"running": true}}

// ❌ 错误：包了信封
{"success": true, "data": {"engine": "teclaw", "process": {"running": true}}}
```

包了信封**不会报错**，而是静默降级：平台把整个 body 当作状态对象，读不到
`engine` / `active_connections` / `process`，于是返回
`{"engine": "", "active_connections": 0, "running": false}`，HTTP 仍是 200。
**一个健康的引擎会被报成 `running: false`。**这是本契约最容易踩的坑。

**A2 — 顶层不得出现 `"success": false`。**平台在取 payload **之前**先查这一项，
命中即抛 `EngineUpstreamError` → 公共面 **502**。业务失败请用 HTTP 状态码表达，
不要用 `success: false`。

**A3 — body 必须是 JSON 对象。**数组、字符串、空 body、非 JSON 都会被判为
「设备故障」→ **502**。

### 3.3 字段

| 字段 | 类型 | 必填 | 平台如何读取 | 含义 |
| --- | --- | --- | --- | --- |
| `engine` | string | 是 | `str(raw.get("engine") or "")` | 当前生效的引擎标识，teclaw 容器填 `"teclaw"` |
| `active_connections` | int | 是 | `int(raw.get("active_connections") or 0)` | 引擎当前正在服务的连接数；没有连接概念时填 `0` |
| `process` | object | 是 | 非 dict 时按 `{}` 处理 | 进程状态容器 |
| `process.running` | bool | 是 | `bool(process.get("running", False))` | **引擎进程此刻是否活着** |
| `transition` | object \| null | 否 | 接受但当前不发布 | 切换/重启中间态，没有就给 `null` |

**其它字段一律忽略，不会报错**（与 `engine-convergence-contract.zh-CN.md` A5
「未知字段忽略」一致），所以你们可以自由附加排查用的字段。

> **缺字段不报错，而是取默认值。**漏了 `active_connections` → `0`；把
> `process.running` 写成 `processRunning` → `false`。全都是 HTTP 200。
> **请严格使用上表的 snake_case 字段名。**

### 3.4 平台发布出去的形状

平台只发布三个字段（`process` / `transition` 是引擎自行拼装的开放 dict，
只有语义稳定的那一个字段对外）：

```json
{
  "success": true,
  "data": { "engine": "teclaw", "active_connections": 2, "running": true }
}
```

---

## 4. 状态码：怎么表达失败

| 容器返回 | 公共面 | 语义 |
| --- | --- | --- |
| `200` + 合法对象 | `200` | 正常 |
| `404` | `404 Not found` | **资源不存在**，与「这不是你的 bot」同形（刻意掩码） |
| `501` | `501 Not supported by this bot's engine` | 该能力未声明 |
| 其它非 2xx | `502 Engine service error` | 上游故障 |
| 非 JSON / 非对象 body | `502` | 设备异常 |
| 超时无响应 | `504` | |

### 关键一条：引擎挂了要回 200，不要回 5xx

**「引擎进程不在」是一个正常的、可表达的状态，不是错误。**

```json
{"engine": "teclaw", "active_connections": 0, "process": {"running": false}}
```

HTTP 200 + `running: false`。**不要**用 503 / 500 表达——那会被映射成 502
`Engine service error`，调用方无法区分「引擎挂了」和「设备坏了 / 你们的端点
本身有 bug」，而前者恰恰是本端点存在的意义。

同理，**不要**因为引擎未就绪而返回 404：404 在公共面与「bot 不存在」同形，
会误导调用方以为 bot 没了。

---

## 5. 最小实现参考

不规定框架，仅示意（FastAPI 写法）：

```python
@router.get("/api/engine/status")
async def engine_status() -> dict:
    running = await probe_engine_process()   # 你们的进程探活
    return {
        "engine": "teclaw",
        "active_connections": current_connection_count(),
        "process": {"running": running},
        "transition": None,
    }
```

异常处理建议：**探活本身失败时也返回 200**，把结果落到 `running: false`，
可选带上排查信息（平台会忽略，但便于你们自查）：

```python
    except Exception as e:
        return {
            "engine": "teclaw",
            "active_connections": 0,
            "process": {"running": False, "last_error": str(e)},
            "transition": None,
        }
```

---

## 6. 自查清单

- [ ] `GET /api/engine/status` 在 `engine_port`（缺省 20003）上可达
- [ ] 200 响应体是 JSON **对象**，**顶层没有** `success` / `data` 包裹
- [ ] 顶层任何情况下都不出现 `"success": false`
- [ ] 字段名严格为 `engine` / `active_connections` / `process.running`（snake_case）
- [ ] 引擎进程不在时 → **200 + `process.running: false`**，而不是 5xx / 404
- [ ] 无 query 参数、无请求体也能正常响应
- [ ] 响应远快于 120 秒（探活不要串行等待长超时）
- [ ] 重复调用无副作用（纯读）

### 验收用例

| 场景 | 容器应返回 | 公共面结果 |
| --- | --- | --- |
| 引擎正常，2 条连接 | `200 {"engine":"teclaw","active_connections":2,"process":{"running":true}}` | `{"engine":"teclaw","active_connections":2,"running":true}` |
| 引擎进程不在 | `200 {"engine":"teclaw","active_connections":0,"process":{"running":false}}` | `{"engine":"teclaw","active_connections":0,"running":false}` |
| 端点未实现 | `404` | `404 Not found` |
| 包了信封（反例） | `200 {"success":true,"data":{...}}` | `{"engine":"","active_connections":0,"running":false}` ← **静默错误** |
| 返回 503（反例） | `503` | `502 Engine service error` ← 无法区分引擎挂了还是设备坏了 |

---

## 7. 不在本次范围内

同组还有三个端点，**本次不要求实现**，列出仅为避免误读：

| 公共路径 | 容器路由 | 说明 |
| --- | --- | --- |
| `…/{bot_id}/engine/capabilities` | `GET /api/engine/capabilities` | 能力发现端点，**带信封** |
| `…/{bot_id}/engine/available` | `GET /api/engine/list` | 已注册引擎列表，**带信封** |
| `…/{bot_id}/engine/restart` | `POST /api/engine/restart` | 只重启引擎进程，**带信封**；≠ Bot 级 restart（后者重建整个容器） |

注意这三个都是**带信封**的（`{success, data, ...}`），只有 `status` 不带——
不要照抄 `status` 的裸返回形状去实现它们。

`POST /api/engine/switch` 平台刻意不封装（引擎在创建后不可变），无需考虑。

---

## 附：平台侧对应实现（便于对照排查）

| 关注点 | 位置 |
| --- | --- |
| 公共路由与字段投影 | `src/backend/src/agentclaw/community/adapters/http/openapi_v1/engine_runtime/engine/router.py:80` |
| 发布出去的响应模型 | 同目录 `schemas.py` `EngineStatus` |
| `enveloped=False` 的理由与 `success: false` 前置检查 | `src/backend/src/agentclaw/community/core/engine_runtime/relay.py`（`call` docstring 与 `_normalise`） |
| 错误 → HTTP 映射表 | `src/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py:715-757` |
| 传输层（端口、鉴权 header、超时） | `src/backend/src/agentclaw/community/plugins/community/device_adapter_transport.py` |
| teclaw 连接信息构造 | `src/backend/src/agentclaw/community/core/devices/services/conn_info_builders/teclaw_builder.py` |
| 平台自有 engine adapter 的同名实现（参考） | `src/engine/src/engine/community/api/engine/router.py:29`、`manager.py:747` |

相关文档：

- `src/backend/docs/openapi-v1/engine-surface.zh-CN.md` — 公共面 ↔ 引擎路由映射全表
- `src/backend/docs/bot-config-manifest/engine-convergence-contract.zh-CN.md` — 跨引擎收敛语义（A5「未知字段忽略」）
- `src/backend/docs/bot-config-manifest/teclaw-cli-contract.zh-CN.md` — `cli_tools` 契约增补
