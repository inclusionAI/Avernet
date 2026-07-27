# TeClaw 语雀文档（Links / Yuque）能力对齐讨论稿

> 目的：对齐"语雀文档（yuque docs）能力在 TeClaw 引擎上的落地"，明确哪些是 Avernet 后端的工作、哪些需要 TeClaw 引擎侧配合。
> 结论先行：**大部分是后端工作**，但有 **两点需要 TeClaw 引擎侧确认/配合**（见第 4、5 节）。

---

## 1. 背景：links / yuque 现在是怎么工作的

Avernet 有一个 links 接口（`POST /api/resources/links`、`PUT /api/resources/links/{id}`），
用来给 bot 挂载"外部知识源"链接。它支持三种 link 类型：

```python
# src/backend/src/agentclaw/community/adapters/http/resources/router.py:307
VALID_LINK_TYPES = {"yuque", "dima", "antcode"}
```

目前**只有 `yuque` 类型真正产生效果**：`dima` / `antcode` 只是被存成资源、暂时没有联动逻辑。
yuque 链接的作用，正是**控制"语雀文档 bot"（skylark MCP）能访问哪些语雀文档**。

创建 / 更新 yuque 链接时会做两件事：

1. **解析 URL**：通过 skylark MCP 工具 `skylark_resolve_url` 把 URL 解析成 `doc_id` / `book_id` / `title`；
2. **同步权限**：把该 bot 名下所有 yuque 链接汇总，调用 passport 的 `save_sub_resources`
   （对应 TCAuth `saveSubResources`）做一次**全量覆盖**同步。

```python
# src/backend/src/agentclaw/community/adapters/http/resources/router.py:418-419
# Sync yuque permissions via saveSubResources (non-blocking, full update)
sync_yuque_permissions(effective_bot_id, effective_user_id, resource_repo, passport)
```

关键点：**"能看哪些文档"这个范围（scope）落在 passport，不在 bot 的运行时配置里**。
skylark MCP server 在被调用时，按 passport 里的授权动态鉴权。

```python
# src/backend/src/agentclaw/community/core/resources/dependencies/service_dep.py:49-74
sub_resources = []
for item in yuque_links:
    attrs = item.get("attributes") or {}
    ...
    sub_resource_type = "YUQUE_BOOK" if yuque_type == "Book" else "YUQUE_DOC"
    access_modes = attrs.get("access_modes") or ["READ"]
    detail_config = {
        "access_modes": access_modes,
        "server_code": _YUQUE_MCP_CODE,   # mcp.ant.faas.skylarkmcpserver.skylarkmcpserver
        "doc_id": ...,
        "book_id": ...,
    }
    sub_resources.append(SubResourceItem(
        resource_type="MCP_TOOL",
        sub_resource_type=sub_resource_type,
        sub_resource_code=url,
        detail_config=detail_config,
    ))
result = passport.save_sub_resources(bot_id, user_id, sub_resources)
```

**因此，一个 bot 能用语雀能力，取决于两件事同时成立：**

- **(A) 运行时里有 skylark MCP server**（bot 能真正发起对语雀的工具调用）；
- **(B) passport 里有对应文档的授权**（skylark 侧鉴权通过）。

其中 (B) 是 engine 无关的、创建链接时已经会同步；(A) 是分引擎的默认配置。

---

## 2. 契约：TeClaw 是"外部引擎"，通过 config_artifact 消费 MCP

TeClaw 是一个**外部引擎**，它消费后端在发布时冻结下发的 `config_artifact`（JSON），
契约由 `artifact.schema.json` 定义（schema 自己就写明这是"external engine consumes"的契约）。

`mcp.servers` 是**必填字段**，也就是说：**TeClaw 引擎本身就被约定要消费一份 MCP server 列表。**

```jsonc
// src/backend/src/agentclaw/community/kernel/bot_config/artifact.schema.json
{
  "required": ["schema_version","version","engine_type","mcp","skills",
               "resources","identity_files","stores","engine_overrides","engine_ext"],
  "properties": {
    "engine_type": { "type": "string",
      "description": "Target engine, e.g. 'openclaw' (ARCA) or 'teclaw' (external)." },
    "mcp": {
      "required": ["servers"],
      "properties": {
        "servers": { "type": "array", "items": { "$ref": "#/definitions/mcpServerRef" } }
      }
    }
  },
  "definitions": {
    "mcpServerRef": {
      "required": ["server_code"],
      "properties": {
        "server_code": { "type": "string" },
        "name":        { "type": ["string","null"] },
        "endpoint":    { "type": ["string","null"],
          "description": "May carry an inlined ?authorization=<token> query." },
        "transport":   { "type": ["string","null"] },
        "headers":     { "type": "object",
          "description": "May carry inlined secret headers (e.g. x-ling-auth)." }
      }
    }
  }
}
```

而且后端的 compose 阶段**已经有 TeClaw 专属的 MCP endpoint 选择逻辑**（说明这条链路是为 teclaw 打通过的）：

```python
# src/backend/src/agentclaw/community/core/config_compose/services/collector.py:188-211
# Endpoint-selection policy is per-engine: teclaw selects deterministically
# by network priority (OFFICE > INTERNET > INTRANET) ...
network_priority = mcp_network_priority_for(req.engine_type)
for md in raw:
    md = self._enrich_mcp_detail(svc, md)
    api_key, headers, endpoint_env, transport = (
        self._mcp_config_service.build_mcp_sync_payload(
            user_id=req.user_id, mcp_data=md, engine_type=req.engine_type,
        )
    )
    inputs.append(McpComposeInput(mcp_data=md, api_key=api_key, headers=headers,
                                  endpoint_env=endpoint_env, transport_protocol=transport,
                                  network_priority=network_priority))
```

**小结：** artifact 里带 `mcp.servers`、compose 也已适配 teclaw —— 下发 MCP 的"管道"是通的。
问题只在于：**这份列表里默认没有 skylark**（见问题一），以及 **skylark 在 teclaw 运行时是否真的能跑起来**（见第 5 节需确认项）。

---

## 3. 两个问题

### 问题一：TeClaw 没有默认 MCP / skills（缺 skylark）

默认 MCP 列表是按引擎分桶的，**没有 `teclaw` 这个 key**，
所以 `get_default_mcp_servers("teclaw")` 返回空列表（未知引擎 → fail-closed 空）。
对比之下，openclaw / claude_code / hermes / aicoding 默认都带 skylark：

```python
# src/backend/src/agentclaw/community/core/mcp/services/_defaults.py:27-90
_DEFAULT_MCP_SERVERS_BY_ENGINE = {
    "openclaw":    [ ..., {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"}, ... ],
    "claude_code": [ ..., {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"}, ... ],
    "hermes":      [ ..., {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"}, ... ],
    "aicoding":    [ ..., {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"}, ... ],
    "moltis":      [],          # 已废弃引擎
    # ⚠️ 没有 "teclaw" 这一项 → get_default_mcp_servers("teclaw") == []
}
```

**影响链路：** 没有默认项 → 默认 skill set 里不会种入 skylark →
`collect_bot_active_mcps` 收不到 skylark → 下发的 `config_artifact.mcp.servers` 里没有 skylark →
即使 passport 授权同步了，TeClaw bot 运行时也没有工具去读语雀。

**修复（后端）：** 给 teclaw 增加一份默认 MCP 列表（至少含 skylark），
让它随默认 skill set 种入、经既有 compose 链路进 artifact。

---

### 问题二：语雀文档变更后需要"同步到 teclaw"，让 MCP scope / 权限正确生效

这一条比最初设想的要轻，因为 **文档 scope 不在 artifact 里，而在 passport 里、由 skylark 动态鉴权**：

- `sync_yuque_permissions` → `passport.save_sub_resources` 是 **engine 无关** 的，
  每次创建 / 更新链接都会跑（`router.py:419 / 540 / 614`），按 `bot_id` / owner 维度，不区分引擎；
- 所以一个**已经挂好 skylark 的** TeClaw bot，文档 scope 变更会通过 passport 动态生效，
  **不需要重新下发 artifact，也不需要额外"推送到容器"**。

TeClaw 唯一相关的是 **MCP 出网规则（outbound egress rule）**，它携带 `agent_pass_token`，
在发布 / 升级时刷新；而 token 是 per-bot 稳定的，scope 变更并不需要刷新它：

```python
# src/backend/src/agentclaw/community/core/service_bot/services/bot_build_service.py:1194-1198
self._baas_service.update_teclaw_outbound_rule_by_bot_uuid(
    bot_uuid,
    agent_pass_token=agent_pass_token,
)
```

```python
# 该刷新只在发布/升级路径触发：
# src/backend/src/agentclaw/community/core/service_bot/services/publish_flow/provider_behavior.py:177-178
def refresh_after_upgrade(self, *, bot_uuid: str, bot: dict) -> None:
    self._build_service.refresh_teclaw_mcp_outbound_rule(bot_uuid=bot_uuid, bot=bot)
```

**小结：** 只要问题一解决（skylark 进了 teclaw artifact，且出网规则 / token 有效），
问题二基本被覆盖——scope 变更靠既有的 passport 同步自动生效。
**前提是：skylark 在 TeClaw 运行时能真正跑起来，并且按 passport / agent_pass_token 鉴权（见第 5 节）。**

---

## 4. 职责划分

| 事项 | 归属 | 说明 |
|---|---|---|
| 给 teclaw 增加默认 MCP 列表（含 skylark） | **后端** | 问题一，纯配置 + 既有链路 |
| yuque 文档 scope 的下发同步 | **后端（已具备）** | 问题二，passport 同步 engine 无关，已在跑 |
| 出网规则 / agent_pass_token | **后端 / BaaS** | 发布时刷新，token 稳定 |
| skylark MCP client 在 teclaw 运行时可用 | **TeClaw 引擎** | 需确认（第 5 节 Q1） |
| 创建链接时的 yuque URL 解析路径 | **待定（引擎或后端）** | 需确认（第 5 节 Q2） |

---

## 5. 需要与 TeClaw 团队确认的问题

### Q1. TeClaw 运行时的 MCP client 能否真正连接并调用 skylark？

`mcp.servers` 是 artifact 必填字段、compose 也适配了 teclaw，所以理论上 teclaw 已能消费 MCP。
但 **skylark 有一个已知的调用怪癖**：`skylark_resolve_url` 对"位置参数过多"会报错，
我们在 claude_code 网关里是特判处理的：

```typescript
// src/engine/src/engine/community/claude_code_gateway/src/mcp/handlers.ts:320
// (e.g. skylark_resolve_url errors with "Too many positional arguments").
```

**请确认：** TeClaw 的 MCP client 是否已 GA、能否正确连接 skylark 并调用其工具（尤其是参数传递方式）？

### Q2. 创建 / 编辑 yuque 链接时的 **URL 解析** 走哪条路？

后端 `resolve_yuque_url` 会 POST 到 **bot 自己引擎的** `/api/mcp/call-tool`，
用 skylark 把 URL 解析成 `doc_id` / `book_id`：

```python
# src/backend/src/agentclaw/community/core/resources/yuque_resolve.py:111-115
call_tool_url = f"{engine_url.rstrip('/')}/api/mcp/call-tool"
payload = {
    "tool": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_resolve_url",
    "args": [f"url={url}"],
}
```

这个 `/api/mcp/call-tool` 目前是 **ARCA 引擎** 的接口：

```python
# src/engine/src/engine/community/api/mcp/router.py:256
@router.post("/call-tool", response_model=ApiResponse)
```

而 TeClaw bot 的 `conn_info` 走 BaaS invoke-http 指向 TeClaw 引擎：

```python
# src/backend/src/agentclaw/community/core/devices/services/conn_info_builders/teclaw_builder.py:52-58
return build_baas_conn_info_for_http(
    bind_id=binding.id, ws_info=ws_info,
    engine_type=TECLAW_DEVICE_PROVIDER,   # "teclaw"
    device_provider=TECLAW_DEVICE_PROVIDER,
)
```

**问题：** 如果 TeClaw 引擎没有 `/api/mcp/call-tool`（或没有 skylark），
那么给 TeClaw bot **添加 yuque 链接会在解析阶段就失败**（存储之前）。

两种解法，需要确定归属：
- **(a) 引擎侧：** TeClaw 引擎暴露一个等价的 call-tool 能力；或
- **(b) 后端侧：** 把 TeClaw 的 yuque 解析改走一个共享的 ARCA/skylark 实例，而不是 bot 自己的引擎。

### Q3. skylark 的鉴权模型在 teclaw 出网链路上是否成立？

我们期望：skylark 在被调用时，用 bot 的 `agent_pass_token` + passport 里的 sub-resource 授权做动态鉴权。
**请确认：** teclaw 的出网规则（携带 `agent_pass_token`）能让容器访问到 skylark endpoint，
且 skylark 侧能据此 token 解析出该 bot 的语雀文档 scope？

---

## 6. 一页话总结

- **skylark 进 teclaw 默认 MCP** → 后端做（问题一）。
- **文档 scope 同步** → 后端已具备，engine 无关，随 passport 自动生效（问题二）。
- **需要 TeClaw 引擎侧确认/配合的：** ① teclaw 运行时 MCP client 能跑 skylark；
  ② yuque URL 解析（call-tool）在 teclaw 上怎么走。

> 备注：TeClaw 引擎不在本仓库内，以上 Q1 / Q2 / Q3 是从后端契约侧梳理出的**依赖项**，
> 需要与引擎团队最终确认，无法仅凭后端代码断定。
