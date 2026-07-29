# Passport —— bot 的身份凭证

[English](README.md) | **简体中文**

Passport 是什么、它装了些什么、一个 bot 如何获得它又如何失去它，以及关于"更新它"的那条
最重要的规则 —— 其余所有代码都在防它。本文是
[`docs/mcp/README.zh-CN.md`](../mcp/README.zh-CN.md) 的配套：MCP 同步会写 Passport，
§7 把两者对照起来。但 Passport 本身是独立的一件事，这篇文档可以单独读。

下文每一段代码都逐字引自本仓库，并附带可点击的 `path:line` 引用。

---

## 1. 一句话版本

**Passport 是一个 bot 的身份证件。** 它用一份凭证回答"这个 bot 是谁"，让 bot 能向各类
服务出示；又用一份授权清单回答"这个 bot 被允许触达什么"。

这个名字本身就是最好的比方，而且相当贴切：

| 人的护照 | bot 的 Passport |
| --- | --- |
| 资料页 —— 姓名、证件号 | `agent_id`、`agent_code`、`credential_id` |
| 证件本身，过关时出示 | `token` —— 下发给设备，随出站调用一起带上 |
| 签证 —— 可以进哪些国家 | `resourceManifest` —— 可以用哪些 MCP server 和 CLI |
| 有效 / 过期 / 吊销 | `PENDING` → `ISSUED` → 冻结 → 销毁 |

为什么 bot 需要自己的证件，而不是借用它主人的：bot 是自己在跑的 —— 按计划任务、在容器里，
在创建它的那个人早就关掉页面之后很久。总得有某个东西能说明"这个请求来自 bot X，它被授权做
Y"，而这句话背后没有任何人的登录会话。

对外这个服务叫 **AgentPass / tcauthmng**；在本仓库里它藏在一个 Protocol 之后：

```python
@runtime_checkable
class PassportPlugin(Plugin, Protocol):
    """Plugin for passport lifecycle management (tcauthmng facade)."""
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:117`</sub>

---

## 2. 一份 Passport 究竟装了什么

看它被读回来时的结构最直观。下面是 gateway 契约测试锁定的 schema 快照：

```json
{
  "access_mode": {"type": "string"},
  "agent_code": {"type": "string"},
  "agent_id": {"type": "string"},
  "certificate_url": {"type": "string"},
  "credential_id": {"type": "string"},
  "expire_at": {"type": "string"},
  "mcps": {"type": "array", "items": {"...": "mcp_code / mcp_name / mcp_desc"}}
}
```

<sub>`src/backend/tests/community/contracts/gateway/schema_snapshots/response_data/rule01_GET_api_bots_id_passport.json`
（有删减）</sub>

三组内容，在脑子里分开放会省很多事：

**1. 身份。** `agent_id` / `agent_code` / `credential_id` / `certificate_url` /
`expire_at`，外加需要单独去取的 `token`。这部分回答"是谁"。

**2. 范围 —— 即 `resourceManifest`。** 目前有两种资源类型，MCP server 和 CLI，各自是一个
带类型的条目：

```python
class CliItem(TypedDict, total=False):
    """CLI scope item supplied by AgentPass query responses."""

    cli_code: str | None
    cli_name: str | None
    cli_desc: str | None


class McpScopeItem(TypedDict, total=False):
    """Provider-neutral MCP resource item for Agent Principal updates."""

    mcp_code: str
    mcp_name: str | None
    mcp_desc: str | None
    identity_mode: str
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:41`</sub>

**3. 状态。** 审批未完成时是 `PENDING`，授予后是 `ISSUED`，bot 休眠时被冻结，bot 删除
时消失。§4 会走一遍。

---

## 3. 最重要的一条规则：更新是覆盖，不是合并

如果这篇文档你只记住一件事，就记这条。

`updatePassport` 对每一类资源列表都是**整体替换**，而不是并入。只提交
`{mcp_codes: [...]}`，这个 bot 的 CLI 授权就没了 —— 不是被拒绝，不是被合并，而是*被悄无
声息地替换成空*。建模这次调用的类型在 docstring 里写明了这一点：

```python
class PassportResourceScope(TypedDict, total=False):
    """Complete resourceManifest scope for overwrite-style updatePassport calls.

    AgentPass/tcauthmng treats each resource list in updatePassport as a full
    replacement. Callers that update MCP or CLI scope must pass both lists here
    so one resource type does not accidentally clear the other.
    """

    mcp_codes: list[str]
    mcp_items: list[McpScopeItem]
    cli_items: list[CliItem]
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:58`</sub>

由此产生两条推论，塑造了各处调用方的写法：

**A. 任何要改范围的人，必须先把自己不改的那部分读回来。** 这就是为什么只关心 MCP 的同步
逻辑，在写入前要去查这个 bot 的 **CLI** 范围（见 `docs/mcp/README.zh-CN.md` §6.5）：

```python
            # resource_scope 是完整快照：MCP 来自同步结果，CLI 来自当前许可证 + 引擎默认 CLI。
            self.passport_update.update_passport(
                bot_id=bot_id,
                user_id=user_id,
                resource_scope={
                    "mcp_codes": synced_server_codes,
                    "cli_items": cli_items,
                },
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/sync_service.py:913`</sub>

**B. 完全不传 `resource_scope`，才是"别动授权"的表达方式。** 只改元数据的更新（改名、
改管理员）什么范围都不传；负责解包这个字段的 helper 把这种情况显式编码成 `None`：

```python
def unpack_resource_scope(
    resource_scope: PassportResourceScope | None,
) -> tuple[list[McpScopeItem] | None, list[CliItem] | None]:
    """Return DTO-ready MCP/CLI lists, or None pair for non-resource updates.

    Non-resource updates, such as admins or metadata, intentionally omit
    resource_scope so existing MCP/CLI grants are left untouched.
    """
    if resource_scope is None:
        return None, None
    try:
        cli_items = resource_scope["cli_items"]
    except KeyError as e:
        raise ValueError(
            "resource_scope must include mcp_codes and cli_items"
        ) from e
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:90`</sub>

注意那个 `KeyError → ValueError`：传一份**残缺的**范围会直接报错。安全的状态只有两个 ——
"完整快照"和"什么都不传"；危险的中间态在边界上就被挡掉了。community 实现明明没有外部注册
中心要通知，却依然调用这次解包，就是为了让这道校验在每种部署形态下都活着：

```python
        # No external registry to notify — scope/admin changes are a no-op.
        # ``unpack_resource_scope`` is still called so a malformed scope raises
        # the same ValueError the corp impl would, keeping callers honest.
        unpack_resource_scope(resource_scope)
```

<sub>`src/backend/src/agentclaw/community/plugins/community/passport.py:53`</sub>

---

## 4. 生命周期

### 4.1 申请 —— 在 bot 创建时

bot 的 Passport 是作为创建流程的一部分申请下来的；而且用户创建的**第一个** bot 走的是另一个
调用（首次申请涉及一次用户尚未给出的授权）：

```python
    apply = (
        passport_plugin.apply_first_agent_passport
        if is_first_bot
        else passport_plugin.apply_agent_passport
    )
```

<sub>`src/backend/src/agentclaw/community/core/bot_management/create_flow.py:204`</sub>

注意传进去的东西：`mcp_codes` 和 `cli_items` 是**申请的入参**。一个 bot 的初始范围是在它
出生时就声明的，不是事后补挂上去的。

拿到的 `agent_code` 会持久化到 bot 记录上，省得后面的代码再去查一次：

```python
def _build_ext(
    *, avatar_url: str | None, agent_code: str | None, issued: bool = False
) -> dict[str, Any] | None:
    """Assemble the bot's ``ext`` payload; ``None`` when there is nothing to store."""
```

<sub>`src/backend/src/agentclaw/community/core/bot_management/create_flow.py:227`</sub>

### 4.2 授权步骤 —— 为什么创建有时是两段式

对桌面版 bot，申请可能返回"需要人来批"。于是创建被拆成两段：先申请、返回"需要授权"并给出
一个链接，然后轮询：

```python
        # 桌面版两段式：先返回 need_authorization，前端授权后再调 auth-status
        if result.get("need_authorization"):
```

<sub>`src/backend/src/agentclaw/community/adapters/http/desktop/router.py:120`</sub>

```python
        status = auth_status.get("status")

        if status == "PENDING":
            return {
                "success": True,
                "data": {"status": "PENDING", "message": "授权处理中"},
            }

        if status == "ISSUED":
            result = service.create_after_authorization(
```

<sub>`src/backend/src/agentclaw/community/adapters/http/desktop/router.py:190`</sub>

`PENDING` → 继续等；`ISSUED` → 凭证已存在，bot 可以完成创建。这是全仓最能直观看出"Passport
是被**授予**的，而不只是被分配的"的地方。

### 4.3 使用 —— token 抵达设备

凭证只有在运行中的 bot 手里才有意义。发布 / 启动时，backend 取出 token 并下发给设备：

```python
            agent_pass_token = self._passport_plugin.query_token(bot_id, owner_id) or ""
```

<sub>`src/backend/src/agentclaw/community/core/service_bot/services/bot_build_service.py:493`</sub>

这一行上方的注释值得留意：`查询 passport token（非阻塞，失败不影响发布）`。发布并不以
"能取到凭证"为前置条件。

### 4.4 更新 —— 范围在 bot 生命周期里的变化

见 §3。凡是 bot 的授权发生变化就会触发：MCP 同步（`docs/mcp/README.zh-CN.md` §6.2）、
skill set 的 CLI 变更（`adapters/http/skill_center/skillsets.py:1481`）、管理员或元
数据修改。

### 4.5 冻结 / 解冻 —— 休眠

进入休眠的 bot，凭证是被**冻结**而不是销毁的，这样重新激活时能恢复。冻结是刻意做成
best-effort 的 —— 走到这一步设备早已释放，失败最多是计费上的浪费，不影响正确性：

```python
        try:
            self._passport_plugin.freeze_agent_passport(
                bot_id=candidate.bot_id,
                owner_workno=candidate.owner_id,
                reason="dormant recycle",
            )
        except Exception as exc:
            logger.warning(
                "[DormantBotService._execute_recycle] passport freeze failed "
                "bot_id=%s: %s — continuing (recycle considered complete)",
```

<sub>`src/backend/src/agentclaw/community/core/bot_dormant/service.py:373`</sub>

解冻恰好相反 —— 严格，并且带后置条件，因为 bot 没有可用 token 就起不来：

```python
        """Bring the credential online and make a runtime token queryable.

        Implementations must return only after ``query_token`` can provide the
        token required by device bootstrap, or raise when that postcondition
        cannot be established.
        """
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:237`
—— "必须在 `query_token` 能提供设备启动所需的 token 之后才返回；无法建立该后置条件时必须
抛错。"</sub>

这种不对称正是设计本身：**丢掉一份你已经不用的凭证是可以接受的；声称恢复了一份其实不可用的
凭证则不可以。**

### 4.6 销毁 —— bot 删除

```python
                self._passport_plugin.destroy_passport(bot_id, user_id)
```

<sub>`src/backend/src/agentclaw/community/core/bot_management/services/bot_service.py:3195`</sub>

---

## 5. Agent Principal 与身份模式

除了 `update_passport`，还有一条更窄的写入路径：

```python
    def update_mcp_identity_to_agent_principal(
        self,
        *,
        bot_id: str,
        user_id: str,
        mcp_items: list[McpScopeItem],
    ) -> None:
        """Replace the Agent Principal MCP scope while preserving other resources."""
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:140`</sub>

它与 §3 那种全量覆盖的差别就写在 docstring 里：这一个替换 MCP 范围，但**保留其他资源**。
它之所以存在，是因为 `McpScopeItem` 带着一个纯 code 列表装不下的字段 —— `identity_mode`，
取值 `owner` 或 `caller`：

- **owner** —— bot 以"谁拥有这个 bot"的身份去调 MCP server。
- **caller** —— bot 以"此刻正在跟它说话的那个用户"的身份去调。

这个"每个 server 一个选择"存在 `ac_bot_mcp_call_config`（见
`docs/mcp/README.zh-CN.md` §3.3），并在这里发布出去。发布时校验很严，就在插件内部：

```python
        if any(
            not item.get("mcp_code")
            or item.get("identity_mode") not in {"owner", "caller"}
            for item in mcp_items
        ):
            raise ValueError("invalid MCP identity scope")
```

<sub>`src/backend/src/agentclaw/community/plugins/community/passport.py:170`</sub>

`caller` 模式正是"共享的服务型 bot"能够成立的前提：你跟它说话时它用*你的*权限办事，而不是
默默借用它主人的。

---

## 6. Passport **不是**什么

三点澄清，能省掉很多困惑：

**Passport 在工具调用时不做任何强制。** 它是一份声明。在 MCP 这条路径上，真正阻止 bot 调用
某个 server 的，是推到设备上的白名单（`filter-servers`）—— 见 `docs/mcp/README.zh-CN.md`
§6.2：设备写入在**前**，passport 只在设备成功之后才更新。**Passport 是权限的记录，设备才是
执行点。**

**Passport 不保存 MCP 凭据。** MCP server 的 API key 和 header 存在
`ac_user_mcp_config` 里，并被内联进设备产物。Passport 保存的是 bot *自己的*凭证，以及它可用
server 的*名单* —— 从不保存连上这些 server 所需的钥匙。

**Passport 属于 bot，不属于用户。** 它以 `bot_id` 为键，`owner_workno`（拥有者）只是用于
鉴权。一个用户有十个 bot，就有十份 Passport。

---

## 7. MCP 在哪些地方碰到 Passport

给从 MCP 走查过来的读者，这是完整的交集：

| MCP 文档 | 对 Passport 做了什么 | 调用 |
| --- | --- | --- |
| §6.2 | 设备白名单落地后，发布新的 MCP code 列表 | `update_passport(resource_scope=...)` |
| §6.5 | 把 CLI 范围读回来，避免这次 MCP 写入把它抹掉 | `query_passport_clis` |
| §6.9 | 剔除 LOCAL/stdio server —— 没有权限系统会为它们授权 | `passport_mcp_items_from_entries` |
| §3.3 | 按 server 发布 owner / caller 身份 | `update_mcp_identity_to_agent_principal` |

§6.9 的那道过滤值得从 Passport 这一侧再说一遍，因为它解释了两份列表*为什么*不一样：

```python
def filter_passport_mcp_codes(
    mcp_codes: Iterable[str],
    *,
    local_registry: LocalMCPRegistry | None = None,
) -> list[str]:
    """Return MCP codes that should be declared to the passport service.

    LOCAL/stdio MCP servers are runtime-local capabilities. They still need to
    be synced to the device, but the passport service does not own their permission scope.
    """
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/passport_scope.py:11`</sub>

发给设备的列表和发给 Passport 的列表，是刻意不相同的。一个跑在 bot 自己容器里的 stdio
server，本来就不是外部权威能"授予"的东西。

---

## 8. 部署形态

和仓库其余部分同一个套路 —— 一个 Protocol，三种实现：

| 形态 | 实现 | 行为 |
| --- | --- | --- |
| corp | `ProdPassportPlugin`（不在本仓库） | 真实调用 tcauthmng / AgentPass |
| community | `SelfIssuedPassportPlugin` | 本地自签一个确定性 token；范围更新是 no-op |
| local / test | `LocalPassportPlugin` | Mock seam；写操作只打日志，返回 mock token |

community 实现最有意思，因为它用"没有人可问时会发生什么"反过来说明了 Passport 是**干什么
用的**：

```python
"""SelfIssuedPassportPlugin — community bot-credential broker.

The corp ``PassportPlugin`` brokers per-bot "agent passport" credentials through
tcauthmng/AgentPass. A community deployment is the sole authority over its own
bots, so it **self-issues** those credentials locally: a deterministic, stateless
function of ``bot_id`` — no external approval system, no consent step, no I/O.
"""
```

<sub>`src/backend/src/agentclaw/community/plugins/community/passport.py:1`
—— "community 部署对自己的 bot 就是唯一权威，因此在本地**自签**这些凭证：一个由 `bot_id`
决定的确定性无状态函数 —— 没有外部审批系统，没有授权步骤，没有 I/O。"</sub>

```python
def _token_for(bot_id: str) -> str:
    return f"community-passport-{bot_id}"
```

<sub>`src/backend/src/agentclaw/community/plugins/community/passport.py:34`</sub>

community 部署自己就是权威，所以"授权"这一步是空的，token 也只是一个惰性的 bearer 占位符。
但上面讲的一切仍然照常运行 —— 调用、快照纪律、校验，只是就地解析掉了。local/test 插件则明确
声明它证明不了真集成的任何事：

```python
    """Local Mock 实现 —— 不代表 AgentPass 真集成。

    所有写操作仅打日志、apply_*/query_* 返回 mock token，**不**向 tcauthmng/AgentPass
    发任何请求。本地无法验证 AgentPass 端真实写入/生效；admin 同步的真实回执只能 prod 验证。
    """
```

<sub>`src/backend/src/agentclaw/community/plugins/local/passport.py:32`</sub>

---

## 9. 阅读顺序与测试地图

1. `plugin_api/passport.py` —— 整份契约就在这一个文件里（283 行）。先看顶部那四个类型，
   再看 Protocol。
2. `plugin_api/passport.py:90` —— `unpack_resource_scope`，也就是 §3 那条规则的代码形态。
3. `plugins/community/passport.py` —— 一份完整实现，小到可以从头读到尾。
4. `core/bot_management/create_flow.py:204` —— Passport 诞生的地方。
5. `core/mcp/services/sync_service.py:849` —— 全仓对 `update_passport` 最谨慎的调用方，
   也是你要再写一个调用方时最好的模板。

| 测试 | 覆盖 |
| --- | --- |
| `src/backend/tests/community/contracts/test_passport.py` | `PassportPlugin` 一致性 |
| `src/backend/tests/community/plugins/community/test_passport.py` | 自签实现 |
| `src/backend/tests/community/plugins/test_passport_save_sub_resources.py` | 二级资源同步 |
| `src/backend/tests/community/services/test_bot_passport.py` | bot 生命周期集成 |
| `src/backend/tests/community/core/mcp/services/test_caller_identity_principal_sync.py` | Agent Principal 身份范围 |
| `src/backend/tests/community/core/bot_management/services/test_default_bot_passport_repair_service.py` | Passport 缺失时的修复路径 |

相关文档：

- [`src/backend/docs/mcp/README.zh-CN.md`](../mcp/README.zh-CN.md) —— 本文配套的
  MCP 走查。
- `src/backend/specs/2026-07-10-dormant-unfreeze-passport-one/` —— 冻结 / 解冻后置条件的
  规格文档。
