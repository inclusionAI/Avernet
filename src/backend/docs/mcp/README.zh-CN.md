# Avernet 中的 MCP 是如何工作的

[English](README.md) | **简体中文**

一份关于 Model Context Protocol（MCP）子系统的走查：它是什么、哪些表和服务持有它、
一份用户凭据如何从一个 HTTP 请求一路走到运行中的 bot 内部的一次工具调用，以及正在进行中的
租户隔离工作（Track A Stage 5，PR #564）接在什么位置。

下文每一段代码都逐字引自本仓库，并附带可点击的 `path:line` 引用。

---

## 1. 一段话讲清 MCP 是什么

MCP 是让 AI agent 调用外部工具的一套标准。一个 **MCP server** 是一个进程或 HTTP
端点，它对外声明自己有哪些工具（`tools/list`），并按请求执行它们（`tools/call`）。
agent 的运行时持有一份"允许与之通信的 MCP server 列表"，以及每一个 server 对应的凭据。
当模型决定要用某个工具时，运行时就与对应的 MCP server 建立连接并把调用转发过去。

所以一套能跑起来的 MCP 集成需要三样东西：

1. **一份目录（catalog）** —— 有哪些 MCP server、各自暴露什么工具、URL 是什么。
2. **一份凭据 + 一个授权范围（scope）** —— *这个*用户/bot 可以用哪些 server，以及用什么
   API key 或 header。
3. **投递到运行时** —— agent 进程需要在工具调用时能读到上面两样东西，因此它们必须被写到
   进程读得到的地方。

Avernet 把这三件事拆给了三个组件。一旦你分清了谁是谁，读代码时的大部分困惑就消失了：

| 关注点 | 归属 | 说明 |
| --- | --- | --- |
| 目录 | **MCP Center**（外部服务），位于 `MCPCenterPlugin` 之后 | Avernet 不存任何 server 元数据表 |
| 凭据 + 范围 | **backend**（`ac_user_mcp_config`、skill set） | 这才是 Avernet 真正持久化的东西 |
| 投递 + 执行 | 设备上的 **engine**（`mcporter.json`） | backend 负责推送；engine 负责执行 |

---

## 2. 全局地图

```mermaid
flowchart TB
    subgraph FE["调用方"]
        UI["Workbench UI / API 客户端"]
    end

    subgraph BE["Backend (src/backend)"]
        R["adapters/http/mcp/router.py<br/>/api/mcp/*"]
        MS["MCPMarketService<br/>(目录读取)"]
        AS["MCPAuthService<br/>(权限)"]
        CS["MCPConfigService<br/>(凭据、合并)"]
        SS["MCPSyncService<br/>(编排)"]
        DB[("ac_user_mcp_config<br/>ac_skill_set_mcp<br/>ac_bot_mcp_call_config")]
    end

    MCPC["MCP Center<br/>(外部目录 + 权限)"]
    PP["Passport / Agent Principal<br/>(已声明的 scope)"]

    subgraph DEV["设备 (src/engine)"]
        EAPI["engine api/mcp/router.py<br/>/api/mcp/*"]
        MJ["mcporter.json"]
        SRV["MCP servers<br/>(HTTP / SSE / stdio)"]
    end

    UI --> R
    R --> MS & AS & CS & SS
    MS --> MCPC
    AS --> MCPC
    CS --> DB
    SS --> DB
    SS --> PP
    SS -->|DeviceSyncPlugin| EAPI
    EAPI --> MJ
    MJ -->|mcporter| SRV
```

有两个 HTTP 接口都叫 `/api/mcp`，但它们**不是**同一套 API：

- `src/backend/.../adapters/http/mcp/router.py` —— 面向用户的 backend API（市场、权限、
  "保存我的 API key"）。
- `src/engine/.../api/mcp/router.py` —— 设备侧 API，backend *调用*它来在运行中的 bot 里
  安装/移除 MCP server。

---

## 3. Avernet 究竟存了什么

### 3.1 `ac_user_mcp_config` —— 调用方在每个 server 上的凭据

这是整个子系统里最重要的一张表。一行 = "用户 X 在环境 Z 下针对 MCP server Y 的设置"。

```python
class UserMCPConfig(Base):
    """User-specific MCP Server configuration (API keys, etc.).

    Note: We store server_code directly instead of using a foreign key
    to ac_mcp_server table, as MCP server data is managed by MCP Center.
    """
    __tablename__ = "ac_user_mcp_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)  # 用户工号
    server_code = Column(String(256), nullable=False, index=True)  # MCP server code
    api_key = Column(String(500), nullable=True)  # LING_XI类型需要的API Key (向后兼容)
    custom_headers = Column(Text, nullable=True)  # JSON: 用户自定义Headers
    extra_config = Column(Text, nullable=True)  # JSON: 其他配置项
    env = Column(String(50), nullable=True)  # 环境标识: dev/pre/prod
```

<sub>`src/backend/src/agentclaw/community/core/models/mcp.py:56`</sub>

有两点需要先记住：

- **没有指向 server 表的外键** —— `server_code` 是一个不透明字符串，靠 MCP Center 解析。
  Avernet 从不拥有这份目录。
- **真正生效的负载放在 `extra_config`（JSON）里**，而不是那几个扁平列。
  `api_key`/`custom_headers` 属于遗留的向后兼容字段。访问器把真实结构写得很清楚：

```python
    def get_unified_config(self) -> dict:
        """获取统一的配置（从 extra_config 中解析）

        Returns:
            {
                "api_key": str or None,           # API Key（授权格式）
                "headers": dict,                   # 自定义 Headers
                "endpoint_env": str,              # 环境选择：PROD/PRE
            }
        """
```

<sub>`src/backend/src/agentclaw/community/core/models/mcp.py:100`</sub>

唯一键是 `(user_id, server_code, env)` —— 注意它完全没有提及这个 `user_id`
*属于哪个租户*。这个缺口正是 PR #564 要解决的问题，见 §8。

### 3.2 `ac_skill_set_mcp` —— 一个 skill set 带来哪些 server

**skill set** 是一组 bot 可以激活的能力包。把一个 MCP server 挂到某个 skill set 上，
才是让 bot 能够触达它的前提：

```python
class SkillSetMCPServer(Base):
    """Association table between SkillSet and MCP Server.
    ...
    """
    __tablename__ = "ac_skill_set_mcp"
```

<sub>`src/backend/src/agentclaw/community/core/models/mcp.py:14`</sub>

归属是有意拆开的：`UserMCPConfig` 属于 `mcp` 模块，`SkillSetMCPServer` 属于
`skill_center`（文件头注释里写明了这一点）。

### 3.3 `ac_bot_mcp_call_config` —— 这次调用以谁的身份执行

当一个 bot 发起工具调用时，它是以 bot 的**拥有者（owner）**身份、还是以正在与它对话的
**调用者（caller）**身份去做鉴权？默认是 owner；只有覆盖项才会写一行（稀疏表）：

```python
class BotMcpCallConfigModel(Base):
    """Sparse Caller overrides; Owner is represented by a missing row."""

    __tablename__ = "ac_bot_mcp_call_config"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    bot_pk = Column(BigInteger, nullable=False, comment="ac_bots.id")
    server_code = Column(String(256), nullable=False)
    engine_type = Column(String(64), nullable=False)
    call_type = Column(String(16), nullable=False)
```

<sub>`src/backend/src/agentclaw/community/core/caller_identity/models.py:30`</sub>

### 3.4 默认值 —— 每个 bot 白拿的 server

有一些 MCP server 会挂到某个引擎类型下的每一个 bot 上，不需要任何人做任何配置：

```python
_DEFAULT_MCP_SERVERS_BY_ENGINE: Dict[str, List[dict]] = {
    "openclaw": [
        {"server_code": "mcp.ant.antprocessai.anttaskmcp"},
        {"server_code": "mcp.ant.arkai.dimamcpserver"},
        ...
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/_defaults.py:27`</sub>

---

## 4. backend 的四个服务

四个服务都位于 `core/mcp/services/`，并在同一个 DI 模块里绑定为单例：

```python
        binder.bind(MCPMarketService, to=MCPMarketService, scope=singleton)
        binder.bind(MCPAuthService, to=MCPAuthService, scope=singleton)
        binder.bind(MCPConfigService, to=MCPConfigService, scope=singleton)
```

<sub>`src/backend/src/agentclaw/community/di/modules/mcp_module.py:70`</sub>

| 服务 | 职责 | 依赖 |
| --- | --- | --- |
| `MCPMarketService` | 读目录（list / detail / tenants） | `MCPCenterPlugin` |
| `MCPAuthService` | "这个用户能用这个 server 吗？" + 申请权限 | `MCPAuthPlugin` + `MCPCenterPlugin` |
| `MCPConfigService` | 增删改查调用方的凭据，并与默认值做**合并** | `UserMCPConfigRepository` |
| `MCPSyncService` | 编排：收集 → 合并 → 推送到设备 → 更新 scope | 以上全部 |

真正关键的一条分工：**`MCPConfigService` 从不向设备发 HTTP 请求。** 它自己的
docstring 就把这条边界钉死了：

```python
class MCPConfigService:
    """管理用户级 MCP 配置（CRUD + 负载构建）。

    **不**向设备发送 HTTP 请求 —— 该职责由 ``MCPSyncService`` /
    ``DeviceMCPSyncPlugin`` 承担。
    """
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/config_service.py:17`</sub>

`MCPSyncService` 是用显式 provider 装配的，而不是普通的 `@inject`，因为 MCP 投递正好
卡在一个依赖环的中间：

```python
        return MCPSyncService(
            ...
            resolver_provider=lambda: injector.get(DeviceContextResolver),
            device_sync_dispatcher_provider=lambda: injector.get(DeviceSyncDispatcher),
        )
```

<sub>`src/backend/src/agentclaw/community/di/modules/mcp_module.py:133` —— 它上方的
docstring 点名了这个环：`MCPSyncService → DeviceContextResolver → ArcaConnInfoBuilder
→ DeviceService → BotService → SkillSetServiceFactory → MCPSyncService`。</sub>

---

## 5. 走查 A —— 用户保存一个 API key

这是端到端的写入路径。入口：`POST /api/mcp/user/config`。

### 第 0 步 —— 在动任何东西之前先校验

```python
    # 校验 endpoint_env
    if request.endpoint_env is not None and request.endpoint_env not in ("PROD", "PRE"):
        raise HTTPException(status_code=400, detail="endpoint_env must be PROD or PRE")
```

<sub>`src/backend/src/agentclaw/community/adapters/http/mcp/router.py:250`</sub>

### 第 1–3 步 —— 先查外部依赖，再写库，再推送，失败则回滚

这里的顺序是刻意安排的，也是整个子系统里最有启发性的一段：

```python
        # 步骤1：校验 MCP 存在性（外部依赖先校验，避免写库后再回滚）
        mcp_data = market_service.get_mcp_detail(request.server_code)
        if not mcp_data:
            raise HTTPException(status_code=404, detail=f"MCP server {request.server_code} not found")

        # 步骤2：写库，保留旧配置供回滚
        old_config = config_service.update_user_unified_config(
            user_id=user.staffId,
            server_code=request.server_code,
            ...
        )

        # 步骤3：推送到该用户/实体下的所有设备
        result = await sync_service.sync_mcp_detail_to_all_bots(...)

        if not result["success"]:
            config_service.rollback_unified_config(
                user_id=user.staffId,
                server_code=request.server_code,
                old_config=old_config,
            )
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to sync to all devices"))
```

<sub>`src/backend/src/agentclaw/community/adapters/http/mcp/router.py:271`（为篇幅做了省略）</sub>

这段应该这样理解：*数据库本身并不是唯一事实来源 —— 一份设备从未收到的配置会被回滚。*
系统里没有后台对账任务去事后修复这种不一致，所以这次写入只能靠手写的补偿逻辑来兜底。

### 合并规则之一 —— 部分更新的语义

`None` 表示"别动它"，而不是"清空它"：

```python
        if existing:
            # 合并策略：入参为 None 表示不修改，沿用旧值
            old_extra = old_config or {}
            extra_config = {
                "api_key": api_key if api_key is not None else old_extra.get("api_key"),
                "headers": headers if headers is not None else old_extra.get("headers", {}),
                ...
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/config_service.py:120`</sub>

### 合并规则之二 —— 用户 header 盖过引擎默认值

当一份配置要被转成推给设备的负载时，默认值作为基底，用户的值优先。这里还有一个真的很容易
踩坑的特例：

```python
        # 当 api_key 是 x-ling-auth 格式时，需要把默认 headers 里的同名 key 删掉，
        # 否则设备端会收到两个冲突的 authorization header。
        if _api_key and "=" in _api_key:
            key_name, _ = _api_key.split("=", 1)
            if key_name.lower() == "x-ling-auth":
                config_headers = {
                    k: v for k, v in config_headers.items() if k.lower() != "x-ling-auth"
                }

        # 合并策略：默认 headers 作为基底，用户 headers 覆盖同名键。
        merged_headers = {**config_headers, **user_headers}
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/config_service.py:233`</sub>

注意 `api_key` 的格式：它**不是**一个裸 token，而是 `"name=value"` —— 其中的 name
决定了这份凭据最终变成一个 header 还是一个 query 参数（见 §6.2）。

### 扇出 —— 推给每一个装了该 server 的 bot

```python
        for bot_id in bot_ids:
            ...
            has_mcp = await asyncio.to_thread(plugin.has_mcp, server_code)
            if not has_mcp:
                logger.warning(
                    "[MCPSyncService] bot=%s 设备上未找到 MCP %s", bot_id, server_code
                )
                sync_results.append({
                    "bot_id": bot_id, "synced": False, "reason": "设备上未找到该 MCP",
                })
                continue
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/sync_service.py:478`</sub>

以及失败判定规则 —— 设备上没有这个 server、或者压根没有设备绑定，*都不算*失败：

```python
        # 只有"确实有该 MCP 的设备全部失败"时才整体报错；
        # 如果设备上没有该 MCP 或者根本没有设备，不算失败。
        if has_mcp_devices > 0 and not any_success:
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/sync_service.py:538`</sub>

### 读取时会做掩码

对应的 GET 接口从不返回已存储的 key：

```python
    api_key = config.get("api_key")
    masked_key = None
    if api_key:
        masked_key = api_key[:4] + "****" + api_key[-4:] if len(api_key) > 8 else "****"
```

<sub>`src/backend/src/agentclaw/community/adapters/http/mcp/router.py:370`</sub>

---

## 6. 走查 B —— 一个 bot 的工具集发生变化

保存凭据是小路径。更大的那条路径在 skill set 被激活/取消激活时触发，也就是当一个 bot
可用的 server *集合*发生变化时。这条路径是 `MCPSyncService.refresh_mcp_scope`，
它做了两件互相独立的事。

### 6.1 向设备声明白名单

```python
        # 先向设备声明白名单：即使 active_mcps 为空也会调用，防止设备残留旧白名单。
        scope_result = await self._declare_mcp_scope(...)
        if not scope_result.get("success"):
            return scope_result

        # 白名单声明成功后，再更新 passport 供前端权限校验使用。
        passport_result = await self._update_passport(...)
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/sync_service.py:378`</sub>

这份列表来自 `skill_center`，而不是 `mcp` 模块 —— `mcp` 模块只认识 `BotMCPProvider`
这个 Protocol：

```python
@runtime_checkable
class BotMCPProvider(Protocol):
    """Interface for fetching a bot's MCP list from skill_center.

    Implemented by ``SkillSetService``.  MCPSyncService depends only on
    this protocol so that the mcp module does not need to import
    skill_center internals.
    """
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/repositories.py:11`</sub>

详情推送是并发的，但做了限流，因为目标是同一个容器：

```python
        # 3. 并发推送，但限制并发数为 5，防止一次性向设备发太多 HTTP 请求把引擎压垮。
        semaphore = asyncio.Semaphore(5)
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/sync_service.py:681`</sub>

### 6.2 两种投递形态，同一个插件边界

backend 不按容器类型做分支。它解析出 per-bot 的 `DeviceSyncPlugin` 并调用四个方法；
*怎么投递*由各实现自己决定：

```python
    def sync_all_mcp_servers(self, mcp_servers: list[dict[str, Any]]) -> bool:
        """Declare the full set of allowed MCP servers to the device
        (filter-servers). Returns ``True`` on success."""
        ...

    def sync_single_mcp(
        self,
        mcp_data: dict[str, Any],
        *,
        api_key: Optional[str] = None,
        custom_headers: Optional[dict[str, str]] = None,
        endpoint_env: str = "PROD",
        transport_protocol: Optional[str] = None,
    ) -> bool:
```

<sub>`src/backend/src/agentclaw/community/plugin_api/device_sync.py:89`</sub>

这些方法上方的注释点明了两种投递风格：

> Per impl: arca/baas push per-MCP over `/api/mcp`; teclaw delivers the whole
> composed artifact; local is no-op.
>
> （各实现：arca/baas 逐个 MCP 走 `/api/mcp` 推送；teclaw 投递整份组装好的产物；
> local 是 no-op。）

<sub>`src/backend/src/agentclaw/community/plugin_api/device_sync.py:81`</sub>

在本（community）仓库里，local 实现是 no-op —— `LocalDeviceSyncPlugin.sync_single_mcp`
直接返回 `True`（`plugins/local/device_sync.py:338`）。真正做 HTTP 推送的 corp/arca
实现不在本仓库内。

对于"整份产物"这种形态，manifest 是在 **backend 侧**组装的，凭据被内联进去 ——
这段代码是全仓对凭据格式说得最清楚的地方：

```python
        """Inline the resolved credential, mirroring ``convert_to_device_format``.

        ``api_key`` is ``"name=value"``. ``authorization`` is appended to the
        endpoint URL query; ``x-ling-auth`` becomes a header; any other name is
        ignored (device-path parity).
        """
        merged_headers: dict[str, str] = {}

        if api_key and "=" in api_key:
            key_name, key_value = api_key.split("=", 1)
            lowered = key_name.lower()
            if lowered == "authorization" and endpoint:
                separator = "&" if "?" in endpoint else "?"
                endpoint = f"{endpoint}{separator}{key_name}={key_value}"
            elif lowered == "x-ling-auth":
                merged_headers[key_name] = key_value
```

<sub>`src/backend/src/agentclaw/community/core/config_compose/services/mcporter_composer.py:125`</sub>

**凭据是在组装时以明文内联进去的** —— 设备侧没有 secret broker，也没有按引用间接解析的
机制。模块 docstring 明确写了这一点；这也正是设备推送失败时要回滚数据库写入的原因：改
`api_key` 就是改产物的字节，而一个过期的容器会继续用旧凭据对外发请求。

同一个文件里还放着 endpoint 选择规则（从目录给出的若干 URL 里挑哪一个）：

```python
# Teclaw containers reach networks in this order; an endpoint on an earlier
# network always wins over a later one (network is the primary sort key).
TECLAW_MCP_NETWORK_PRIORITY = ("OFFICE", "INTERNET", "INTRANET")
```

<sub>`src/backend/src/agentclaw/community/core/config_compose/services/mcporter_composer.py:53`</sub>

### 6.3 Passport —— 已声明的 scope，排除掉 local server

在推送设备的同时，bot 的 scope 也会被声明给 passport / Agent Principal 服务。
`stdio`/LOCAL 类型的 server 被有意排除，因为它们是运行时本地能力，没人需要为它们授权：

```python
def passport_mcp_items_from_entries(
    mcps: Iterable[Mapping[str, Any]],
    *,
    identity_modes: Mapping[str, object],
    local_registry: LocalMCPRegistry | None = None,
) -> list[McpScopeItem]:
    """Build the complete non-local MCP identity scope for Agent Principal."""
    ...
        raw_mode = identity_modes.get(code, "owner")
        mode = getattr(raw_mode, "value", raw_mode)
        normalized_mode = str(mode).strip().lower()
        if normalized_mode not in {"owner", "caller"}:
            raise ValueError("identity mode must be owner or caller")
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/passport_scope.py:44`</sub>

这里的 `owner`/`caller` 值，就是 §3.3 里 `ac_bot_mcp_call_config` 所做的决定，在
scope 被发布出去的这一刻浮出水面。

---

## 7. 设备上发生了什么

engine 暴露了它自己的 `/api/mcp`，并把所有端点都转发给一个 engine 专属的插件：

```python
"""MCP router — dispatches every endpoint through ``EngineManager.mcp``.

Engine-specific behaviour (mcporter file edits, ``mcporter`` CLI shell-out)
lives in ``engines/openclaw/mcp.OpenClawMCPService``; AiCoding ships its own
plugin proxying to teamclaw-aicoding-relay. The router only marshals
HTTP↔Plugin types and applies capability guards.
"""
```

<sub>`src/engine/src/engine/community/api/mcp/router.py:1`</sub>

对 OpenClaw 引擎来说，"安装一个 MCP server"字面意思就是改一个 JSON 文件：

```python
    def _mcp_load(self) -> "tuple[dict[str, Any], str, dict[str, Any]]":
        """Load mcporter.json; return (root, servers_key, servers_dict)."""
```

<sub>`src/engine/src/engine/community/plugins/openclaw/_mcp.py:24`</sub>

……而白名单是通过 shell 调用 `mcporter` CLI 来生效的。注意"什么都不允许"的哨兵值 ——
这也解释了为什么空列表仍然要推送一次：

```python
        csv_codes = (
            ",".join(normalized) if normalized else "__EMPTY_FILTER_DISABLE_ALL__"
        )
        command = ["mcporter", "filter-servers", csv_codes]
```

<sub>`src/engine/src/engine/community/plugins/openclaw/_mcp.py:260`</sub>

工具执行也是同样的形态：

```python
        cmd = ["mcporter", "call", tool]
        for key, value in (args or {}).items():
            cmd.append(f"{key}={value}")
```

<sub>`src/engine/src/engine/community/plugins/openclaw/_mcp.py:209`</sub>

engine 的传输模型是一小组 dataclass，也正是在这里，`stdio` / `http` / `sse` 的区别
才终于变得具体：

```python
@dataclass
class MCPServerConfig:
    """Declared configuration for an MCP server.

    `server_code` is the stable identifier used to look up the server.
    Transport-dependent fields:
      - HTTP / SSE: `url` (and optional `headers`)
      - stdio: `command` + `args` (and optional `env`)
    """
```

<sub>`src/engine/src/engine/community/core/mcp/models.py:31`</sub>

并不是每个引擎都支持每个操作；router 用能力检查（`check_capability(Capability.MCP_CREATE)`
等）逐个把关，引擎没实现时返回 `501` —— 见
`src/engine/src/engine/community/api/mcp/router.py:145`。

---

## 8. PR #564 接在哪里 —— 租户隔离

上面所有内容都默认只有一个租户。`ac_user_mcp_config` 的键是
`(user_id, server_code, env)` —— 一个 user id 只在*某个租户内部*才有意义，而这一行上
没有任何字段记录它属于哪个租户。

今天这没问题，因为所有数据都属于内部租户。但一旦 `/openapi/v1` 对外部租户开放，它就不再
成立了 —— 那些路由已经以桩的形式存在了：

```python
@router.get(
    "/servers/{server_code}/config",
    response_model=Envelope[McpConfig],
)
async def get_mcp_config(
    server_code: str, principal: PrincipalDep
) -> Envelope[McpConfig]:
    """Read the caller's unified config for an MCP server."""
    raise NotImplementedError
```

<sub>`src/backend/src/agentclaw/community/adapters/http/openapi_v1/mcp/router.py:72`</sub>

公共 API 这项工作被拆成两条 Track，规则是：某个类别的数据没有完成隔离之前，不得实现它的
端点（见 `src/backend/docs/openapi-v1/README.zh-CN.md`）：

- **Track A** —— 给某个数据类别加上租户隔离。不实现任何端点。
- **Track B** —— 实现该类别的 `/openapi/v1` 端点。

这套机制已经在 Track A Stage 1（PR #456）里于 bots 上建成并验证过，后续原样复用：一个
按请求维度的租户载体，加上注册在模型上的两个 SQLAlchemy guard ——

> - 在 `Session` 类上的 `do_orm_execute` **读 guard** →
>   `with_loader_criteria(...)`。它同时约束 `Query.update()`/`Query.delete()`，
>   所以写路径不需要额外加过滤条件。
> - `before_insert` **插入 guard** → 未设置时自动打标；显式传入冲突租户时抛
>   `CrossTenantInsertError`。

<sub>`src/backend/docs/openapi-v1/README.md:168`</sub>

**PR #564 就是 Track A Stage 5：把这套机制套用到 MCP 配置上。** 它目前只包含 spec 文档
（`src/backend/specs/2026-07-29-tenant-isolation-mcp/spec.md`），plan、tasks 和实现
随后跟进。具体来说，就是给下面两者加上 `avernet_tenant` 列以及那两个 guard：

1. `UserMCPConfig`（§3.1）—— 也就是 API key 和 header，这个类别里最敏感的数据。
2. `BotMcpCallConfigModel`（§3.3）—— PR 里把它标为一个需要判断的点。它的行挂在 bot 之下，
   所以 Stage 1 的 bots 隔离已经覆盖了*通过 bot* 触达的一切；但它的聚合查询是直接按
   `bot_pk` 查的，全程不碰任何 bot 记录，因此 guard 覆盖不到这些读。

`mcp` 的六个端点里有四个完全不需要 Stage 5 做任何事（市场、租户列表、server 详情、权限）
—— 它们由 MCP Center 提供服务，而正如 §1 所确立的，我们对它们什么都不存。

---

## 9. 部署 profile —— 为什么"外部服务"不是硬依赖

目录只通过一个 Protocol 访问：

```python
@runtime_checkable
class MCPCenterPlugin(Plugin, Protocol):
    """Read-only queries against MCP Center API."""

    def get_mcp_detail(self, server_code: str) -> dict[str, Any] | None:
```

<sub>`src/backend/src/agentclaw/community/plugin_api/mcp_center.py:13`</sub>

在 `DEPLOY_PROFILE=community` 下，这个 Protocol 由一个基于配置文件、权限全放开的目录实现
来满足，因此整个子系统在没有内部 MCP-Center 服务的情况下也能装配起来：

```python
class CommunityMCPCenter(MCPCenterPlugin):
    """Config-file-backed MCP catalog with allow-all permission + default tenant."""
```

<sub>`src/backend/src/agentclaw/community/plugins/community/mcp_center.py:31`</sub>

它的模块 docstring 讲清了"为什么空目录依然是一套能用的系统"这个关键点：

> The bot-run path uses bring-your-own MCP configs (`ac_user_mcp_config`) and
> skill-set references, which sync to devices regardless of this catalog — so an
> empty catalog never blocks a configured MCP server from working.
>
> （bot 运行路径使用自带的 MCP 配置（`ac_user_mcp_config`）和 skill set 引用，
> 它们与这份目录无关地同步到设备 —— 所以空目录永远不会挡住一个已配置好的 MCP server
> 正常工作。）

<sub>`src/backend/src/agentclaw/community/plugins/community/mcp_center.py:19`</sub>

在 `configs/local-mcp-servers.yaml` 里声明的 server 由 `LocalMCPRegistry` 加载，并被
规整成 MCP-Center 形状的 dict（`core/mcp/services/local_mcp_registry.py:18`），这就是
为什么其余代码可以对两种来源一视同仁。

还有一个有用的逃生口：当调用方带上 IAM token 时，server 详情会**实时**从 MCP server 本身
拉取（`core/mcp/services/mcp_live_fetcher.py:14` 里是一次真实的 `initialize` →
`tools/list` JSON-RPC 交互），而不是相信可能已经过期的目录元数据；任何一步失败都会退回到
目录数据（`adapters/http/mcp/router.py:144`）。

---

## 10. 阅读顺序与测试地图

如果你想端到端地跟一条线，建议按这个顺序读：

1. `core/models/mcp.py` —— 存了什么（117 行）。
2. `adapters/http/mcp/router.py:237` —— 写入端点，完整五步。
3. `core/mcp/services/config_service.py` —— 合并规则。
4. `core/mcp/services/sync_service.py:439` —— 扇出。
5. `plugin_api/device_sync.py:81` —— 投递边界。
6. `src/engine/.../plugins/openclaw/_mcp.py` —— 设备实际做的事。

值得当作可执行文档来读的测试：

| 测试 | 覆盖 |
| --- | --- |
| `src/backend/tests/community/api/mcp/routers/test_mcp.py` | backend router 行为 |
| `src/backend/tests/community/endpoints/test_mcp_device_sync.py` | 设备同步编排 |
| `src/backend/tests/community/_flows/mcp/api_lifecycle.py` | 完整配置生命周期流程 |
| `src/backend/tests/community/contracts/test_mcp_center.py` | `MCPCenterPlugin` 一致性 |
| `src/engine/.../api/tests/test_mcp_router.py` | engine 侧 router |
| `src/engine/.../plugins/openclaw/tests/test_mcp_port.py` | `mcporter.json` 编辑 |

相关文档：

- `src/backend/src/agentclaw/community/core/mcp/README.md` —— 本模块受机器校验的
  context boundary。
- `src/backend/docs/openapi-v1/README.zh-CN.md` —— Track A / Track B 交接看板。
- `src/engine/docs/heterogeneous-engine-architecture.md` §6.2 —— engine 侧 MCP 模型。
