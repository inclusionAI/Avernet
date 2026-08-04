# Bot 身份全局唯一化 · 解散 `default` 约定（#556 落地方案）

> **关联**: 解决 `inclusionAI/Avernet#556`（跨租户 bot 身份键碰撞）。是 `/openapi/v1`
> 多租户开放前必须清算的硬门之一。另见 handoff README
> `ocb-public/src/backend/docs/openapi-v1/README.md`。

## 0. 摘要

不再用 `bot_id == "default"` 这个字符串约定承载"首 bot / 主 bot"语义。新 bot 一律分配
全局唯一 `bot_id`；"首 bot"判断改为 `count_by_owner == 0` 派生；删除保护改为"至少保留
一个 bot"的计数规则；collaborator / bot_chat 的 `default` 短路改成归属/权限判断。发证策略
按租户分流：默认租户 `teamclaw` 维持首/非首（`applyFirst` / `applyAgent`），其他租户
（openapi）一律 `applyFirst`（跳过审批）。

结果是 `ac_bots` 无需新列、`tcauthmng` wire 不带 tenant、ocb corp（`ProdPassportPlugin`）
不动；外部租户永远不产生 `default`，从根上消除跨租户 `default` 碰撞。实施是 **Avernet-only**
PR；ocb 仓仅在一条外部 gate 触发时才动 corp。

---

## 1. 改造背景

### 现状

- `ac_bots.bot_id` 无唯一约束（`plugin_api/models.py:40`），每个 owner 的首个 bot 都叫
  `"default"`（`generate_bot_id`：owner 无 `default` 就返回 `"default"`，否则
  `yyyymmdd_<8 随机>`）。
- `is_first_bot = bot_id == "default"`（`create_flow.py:329`），用字符串判断首 bot，
  决定走 tcauthmng `applyFirstAgentPassport`（首次发证）还是 `applyAgentPassport`
  （非首次）。
- `bot_id == "default"` 在社区代码里有约 42 处引用，承载多种语义：删除保护、collaborator
  owner 解析短路、bot_chat 鉴权短路、首 bot 命名、`create_bot_for_others` 等。
- tcauthmng 护照以 `(bot_id, owner_workno)` 复合键存储（已与 tcauthmng 团队确认）。
  `owner_workno` = 蚂蚁工号 = `entity_id` = `owner_id` = `user_id`（创建时同值），
  工号全局唯一（一个人一个工号，跨租户同一个人）。

### 问题

1. **跨租户 `default` 碰撞（潜伏）**：`exists_by_owner_and_bot_id` 的查询带 env 作用域
   （`Model.env == get_current_env()`）+ Track A tenant guard（`avernet_tenant == 当前请求
   租户`）。同一工号 X 在租户 B 创建时看不到租户 A 里已有的 `default` → 又分配一个 `default`
   → 同一工号在 A、B 两租户各一行 `default` → tcauthmng `(default, X)` 只有一条护照 →
   碰撞。今天 `resolve_avernet_tenant` 还是 stub（都落 teamclaw）所以尚未发作；auth 落地、
   真实租户进来即触发。
2. **`default` 字符串过度承载语义**：把"首 bot/主 bot/归属/权限"多种含义捆在一个字符串
   上，导致多租户改造要么侵入 42 处 sentinel，要么给 tcauthmng wire 加 tenant —— 都不干净。
3. **openapi 照分 `default`**：`openapi_v1/bots/router.py:247` 用同一个 `generate_bot_id`，
   外部租户首个 bot 也叫 `default`，是问题 1 的入口。

### 目标

- 外部租户（openapi 链路）永远不产生 `default`，bot_id 全局唯一。
- `is_first_bot` 不再依赖字符串，用 `count` 派生。
- 跨租户 `(bot_id, 工号)` 在 tcauthmng 自然全局唯一，wire 不出现 tenant。
- 不新增数据库列；42 处 `default` sentinel 收敛到 count / ownership 语义；`default` 命名
  逻辑退役。
- ocb corp `ProdPassportPlugin` 不动（除非外部 gate 触发）。

---

## 2. 已确认的事实基础

| 事实 | 证据 |
|---|---|
| tcauthmng 复合键 `(bot_id, owner_workno)` | tcauthmng 团队确认；facade API `BotRequestDTO(bot_id, owner_workno)` |
| `owner_workno` = 工号 = `entity_id` = `owner_id` = `user_id` | `create_flow._apply_passport` 传 `owner_workno=user_id`；`bot_service.py:874 entity_id=owner_id`；`1132 resolved_entity_id = entity_id or f"staff_{user_id}"` |
| 工号全局唯一（跨租户同一人） | 业务确认 |
| `exists_by_owner_and_bot_id` 带 env + tenant 双作用域 | `bot_repository.py:527` 过滤 `is_delete=0 & owner_id & bot_id & env`，叠加 `with_loader_criteria(BotModel, avernet_tenant==current)` |
| openapi 当前照分 `default` | `openapi_v1/bots/router.py:247 generate_bot_id(owner_id, bot_repo)` |
| `is_first_bot` 是字符串判断 | `create_flow.py:329 is_first_bot = bot_id == "default"` |
| 后续 bot 本就全局唯一 | `generate_bot_id` 非 default 分支返回 `yyyymmdd_<8 随机>` |
| BotModel 已注册 tenant guard | `plugin_api/models.py:112 register_avernet_tenant_guard(BotModel)` |
| ocb corp 无 `generate_bot_id` / `create_flow` 覆盖层 | grep `src/agentclaw/corp` 为空 |

---

## 3. 发证策略（已对齐）

| 租户 | 是否产生 `default` | 首/非首判断 | tcauthmng RPC |
|---|---|---|---|
| 默认租户 `teamclaw`（内部 `/api`） | 否（新逻辑全局唯一） | `count_by_owner == 0` | 首 → `applyFirstAgentPassport`；非首 → `applyAgentPassport` |
| 其他租户（openapi） | 否 | 不判断 | 一律 `applyFirstAgentPassport`（跳过审批） |

**点 1 决议**：退役 `default` 命名；"首 bot"用 `count` 派生；**不新增 `is_default` 列**；
删除保护改为"至少保留一个"计数规则；collaborator / bot_chat 短路改 ownership/权限。

---

## 4. 整体设计

### 4.1 改动清单一览（均在 Avernet `community/`）

| # | 改动点 | 文件 |
|---|---|---|
| 1 | `generate_bot_id` 不再返回 `default`，始终全局唯一 | `core/bot_management/services/bot_service.py:243` |
| 2 | `is_first_bot` 改 `count_by_owner == 0` | `core/bot_management/create_flow.py:329` |
| 3 | 发证 RPC 按租户分流（默认租户首/非首，其他一律 `applyFirst`） | `core/bot_management/create_flow.py` `_apply_passport` 调用处 |
| 4 | 删除保护改 `count_by_owner <= 1` 拒 | `core/bot_management/services/bot_service.py:3160` |
| 5 | `_resolve_owner_id` 去 `default` 短路，改归属解析 | `core/bot_collaborator/interceptor/collaborator.py:436` |
| 6 | `_check_bot_access` 去 `default` 短路，统一走 `has_bot_access` | `core/bot_chat/service.py:360` |
| 7 | 退役 `create_bot_for_others_service._DEFAULT_BOT_ID` | `core/bot_management/services/create_bot_for_others_service.py`（+ `adapters/http/resources/router.py` 引用） |
| 8 | refresh-token 回调 handler 跨租户按 `(bot_id, owner_workno)` 解析 | `adapters/http/bot_management/router.py:1196` |
| 9 | openapi update 重开 `sync_to_bcn` | `adapters/http/openapi_v1/bots/router.py:401` |
| 10 | `_resolve_bot_name` 的首 bot 命名改 `count==0` | `core/bot_management/services/bot_service.py:795` `_is_first_bot` |

### 4.2 行为规则（改造后）

- **bot_id 生成**：`generate_bot_id(owner, repo)` 始终返回 `yyyymmdd_<8 随机>`（或等价
  全局唯一形式），删除 `"default"` 分支。内部 `/api` 与 openapi 共用同一生成器。
- **首 bot 判定**：`is_first_bot = (count_by_owner(owner) == 0)`，插入前查。`count_by_owner`
  走原 tenant guard，即"该 owner 在当前 tenant+env 下首 bot"——对默认租户语义正确；
  openapi 不使用此判定。
- **发证分流**（`create_flow` 内）：
  ```
  tenant = get_current_avernet_tenant()
  if tenant == DEFAULT_AVERNET_TENANT:
      apply = applyFirst if is_first_bot else applyAgent
  else:
      apply = applyFirst            # 其他租户一律首次发证,跳过审批
  ```
  分支在 passport seam 一处，`tenant` 取自 `get_current_avernet_tenant()`（ContextVar），
  不穿参、不进业务模块。
- **删除保护**：`delete_bot` 前查 `count_by_owner(owner)`；`<= 1` 则拒
  （`BotOperationNotAllowedError("至少保留一个 Bot")`）。不再针对 `default`。
- **collaborator 解析**：`_resolve_owner_id` 去掉 `bot_id == "default"` 短路；默认走
  `repo.get_by_id(bot_id).owner_id`；`bot_id` 缺失时仍返回 `user_id`（语义=我的 bot）。
- **bot_chat 鉴权**：`_check_bot_access` 去掉 `default`/`{user_id}_default` 短路，统一
  `return self._db_repo.has_bot_access(user_id, bot_id)`（已覆盖 owner ∪ collaborator）。
- **`create_bot_for_others`**：`_DEFAULT_BOT_ID` 常量退役；为他人开 bot 时用
  `generate_bot_id`（全局唯一 id）；语义从"给别人开 default bot"改为"确保该用户至少有一
  只 bot"。
- **token 回调**：`refresh_bot_passport_token` 按 `(bot_id, owner_workno)` 解析 bot，**跨
  租户直查**（`(bot_id, 工号)` 全局唯一，安全）；不依赖请求 tenant scope。详见 5.4。
- **BCN sync**：openapi update 路径 `sync_to_bcn=False` 改回 `True`（`((bot_id, 工号))`
  全局唯一后，public 路径 BCN sync 可重开）。

---

## 5. 逐点设计

### 5.1 `generate_bot_id`（改动 #1）

```python
def generate_bot_id(owner_id: str, bot_repository: BotRepository) -> str:
    """始终返回全局唯一 bot_id（yyyymmdd_<8 随机>）。不再分配 'default'。"""
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{date_part}_{random_part}"
```

调用点三处同步生效：`bot_service.py:1124`（内部 create）、`bot_management/router.py:923`
（内部 router）、`openapi_v1/bots/router.py:247`（openapi）。无需区分调用方。

> 碰撞兜底：8 位随机理论上存在极小碰撞概率。`count==2^36` 量级以下可忽略；若要绝对安全，
> 生成后 `exists_by_owner_and_bot_id(owner, candidate)` 校验重试（最多 3 次）。plan 阶段
> 决定是否加这层兜底。

### 5.2 `is_first_bot` + 发证分流（改动 #2、#3）

`create_flow.py` 内：

```python
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT, get_current_avernet_tenant,
)
...
is_first_bot = bot_repository.count_by_owner(user_id) == 0   # 替代 bot_id == "default"
...
tenant = get_current_avernet_tenant()
if tenant == DEFAULT_AVERNET_TENANT:
    apply = (passport_plugin.apply_first_agent_passport if is_first_bot
             else passport_plugin.apply_agent_passport)
else:
    apply = passport_plugin.apply_first_agent_passport
return apply(bot_id=bot_id, owner_workno=user_id, ...)
```

内部路径行为等价：原 `generate_bot_id` 在 owner 无 `default` 时返回 `default`、count 同时
为 0；新逻辑 count==0 → `is_first_bot=True`，bot_id 为全局唯一 id。发证 RPC 选择不变。

### 5.3 删除保护（改动 #4）

```python
# bot_service.delete_bot,替换 `if bot_id == "default":`
if self._repository.count_by_owner(owner_id) <= 1:
    raise BotOperationNotAllowedError("至少保留一个 Bot，不能全部删除")
```

`count_by_owner` 走 tenant guard，作用域 = 当前 tenant+env。

### 5.4 refresh-token 回调 tenant-scope 修复（改动 #8）

现状：`POST /api/bots/passport/refresh-token`（`router.py:1196`）Body `{bot_id,
owner_workno, token}`，调 `hot_update_passport_token_to_device(bot_id, user_id=owner_workno,
token)` → `get_by_id_and_owner(bot_id, user_id)`。该路径是 `/api`（非 `/openapi/v1`），
`AvernetTenantMiddleware` 置默认租户 `teamclaw` → tenant guard 把外部租户 bot 挡掉 →
`BotNotFoundError`。

修复：`(bot_id, owner_workno)` 全局唯一，回调解析应跨租户。在
`hot_update_passport_token_to_device`（或回调 handler 内）用
`skip_avernet_tenant_guard` 执行选项做一次跨租户直查：

```python
bot = self._repository.get_by_id_and_owner(
    bot_id, user_id,
    execution_options={"skip_avernet_tenant_guard": True},
)
```

（`get_by_id_and_owner` Protocol/impl 透传 `execution_options`；guard 已支持该选项，
  `_read_guard` 命中即 return。）或新增 `get_by_id_and_owner_cross_tenant` 仓库方法语义
  更显式。plan 阶段定。

> 安全性：回调由 tcauthmng 发起，`(bot_id, owner_workno)` 是 tcauthmng 主键，跨租户直查
> 不会泄漏（只能命中 tcauthmng 已签发的那一条）。回调入口仍需鉴权（tcauthmng 侧签名 /
> IP 白名单，非本 spec 范围）。

### 5.5 collaborator / bot_chat 短路（改动 #5、#6）

```python
# collaborator._resolve_owner_id
if not bot_id:
    return user_id
# 删除 `or bot_id == "default"` 分支,统一走 get_by_id 查 owner_id

# bot_chat._check_bot_access
def _check_bot_access(self, user_id, bot_id):
    return self._db_repo.has_bot_access(user_id, bot_id)   # 删除 default 短路
```

### 5.6 `_DEFAULT_BOT_ID` 退役（改动 #7）

`create_bot_for_others_service.py` 内 `_DEFAULT_BOT_ID = "default"` 及 ~20 处引用：改为
调 `generate_bot_id(target_user_id, repo)`；服务语义说明改为"为目标用户创建一只 bot（全局
唯一 id），若已存在则修复/重启"。`adapters/http/resources/router.py` 中对
`_DEFAULT_BOT_ID` 的引用同步改。

### 5.7 openapi BCN sync 重开（改动 #9）

`openapi_v1/bots/router.py:401` `sync_to_bcn=False` → 删除该参数透传（默认 `True`）。
`#556` 止血解除。

### 5.8 命名 `_is_first_bot`（改动 #10）

`bot_service.py:795`：`return self._repository.count_by_owner(user_id) == 0`（替代
`not exists_by_owner_and_bot_id(user_id, "default")`）。仅影响默认 bot 名（首 bot 用
nick_name，非首用 bot_id），行为等价。

---

## 6. 归属：Avernet vs ocb

| 仓 | 改动 |
|---|---|
| **Avernet**（`ocb-public/src/backend/src/agentclaw/community/`） | 改动 #1–#10 全部 |
| **ocb corp**（`src/backend/src/agentclaw/corp/plugins/prod/passport.py`） | **不动**。`ProdPassportPlugin.apply_first_agent_passport` / `apply_agent_passport` 现有 RPC 即可；`ApplyAgentPassportRequestDTO` 不加 tenant 字段 |
| ocb 网关 / 部署 | 无代码改动（openapi 路由已挂载） |

> 实施是 Avernet-only PR（对应 `inclusionAI/Avernet` 仓）。ocb 主仓本 spec 仅作为设计文档
> 存档；ocb corp 仅在 §7 外部 gate 触发时才改动。

---

## 7. 外部 gate 与待评估项

### 7.1 tcauthmng `applyFirst` 语义（外部 gate，必确认）

点 3 假设"其他租户一律 `applyFirstAgentPassport`，跳过审批、每次同步发证"。需 tcauthmng
团队确认：

1. 同一工号在其他租户建第 2、3 个 bot，每次调 `applyFirst`，是否因"此人已有护照"报错或
   反而触发审批？
2. `applyFirst` 对外部租户是否**始终同步返回 token**（不返回 pending）？

若 (1) 报错或 (2) 仍 pending → 点 3 不成立，需回退到"带 tenant 的边界键"方案（见 §10），
届时 ocb corp `ProdPassportPlugin` + DTO 要改。**gate 不触发则 ocb corp 全程不动。**

### 7.2 openapi 202 状态（连随 7.1）

若 7.1-(2) 成立（外部租户 `applyFirst` 始终返回 token）→ openapi 创建**永远 201，不再
202**。`BotAuthPending` / `auth-status` 端点对外部租户是否保留，plan 阶段定。顺带让 #559
（async create ≠ 已授权 bot）在外部租户自动失效。

### 7.3 桌面 bot 旁支（评估项，非阻塞）

`_apply_passport` 在 `core/desktop_bot/services/desktop_bot_service.py` 另有一份。桌面 bot
跑本机 VM、基本只 teamclaw。plan 阶段确认：桌面链路是否涉及外部租户 → 是否需要同样的
"其他租户一律 applyFirst"分支；大概率不涉及，按单租户保持现状。

### 7.4 前端复合 id（非阻塞，后续清理）

`src/frontend/src/utils/activeBotContext.ts:48` 因"default 不唯一"做了复合 id 打补丁。
`default` 退役后该补丁可简化——非本 spec 范围，单独跟进。

---

## 8. 行为变化清单

| 变化 | 旧 | 新 | 备注 |
|---|---|---|---|
| 新 bot 的 bot_id | 首 bot `"default"`，余 `yyyymmdd_随机` | 一律 `yyyymmdd_随机` | 全局唯一 |
| `default` bot 删除 | 永远拒 | `count<=1` 才拒（非最后一只可删） | 产品确认：保留≥1 |
| openapi 首个 bot | `default` | 全局唯一 id | 消除跨租户碰撞入口 |
| openapi 发证 | 走 `applyAgent`（非首） | 一律 `applyFirst`（跳过审批） | 依赖 §7.1 gate |
| openapi 创建返回 | 可能 202 | 大概率恒 201 | 依赖 §7.1 gate |
| openapi update BCN sync | `False`（止血） | `True` | #556 止血解除 |
| collaborator / bot_chat `default` 短路 | 命中即放行/自解析 | 统一走归属/权限 | 更正确 |

存量团队 teamclaw 已有 `default` bot：保留其 bot_id 字面值不变（不回填改名），新规则对它们
同样适用（删除保护按 count、collaborator/chat 按 ownership）。`default` 字面仅作为历史值
存在，不再有新成员。

---

## 9. 测试策略

- **`generate_bot_id`**：单测，连续生成 N 个 id 全唯一；无 `"default"` 返回。
- **`is_first_bot` / 发证分流**：单测，默认租户 count==0 → `applyFirst`、count>0 →
  `applyAgent`；其他租户 → 恒 `applyFirst`。mock `get_current_avernet_tenant`。
- **删除保护**：owner 1 只 bot → 拒；2 只 → 删 1 成功、剩 1 再删拒。
- **collaborator / bot_chat**：`bot_id="default"` 历史值不再走短路，走 `has_bot_access`/
  `get_by_id`；owner 匹配放行、collaborator 匹配放行、无匹配拒。
- **refresh-token 回调**：跨租户场景，tenant=B 的 bot 在回调（默认 teamclaw scope）下仍可
  解析（`skip_avernet_tenant_guard`）；解析不到 → 404。
- **架构守卫**：`tests/community/architecture/` 全绿（无新跨层 import；`create_flow` 引入
  `utils.avernet_tenant` 若为新依赖，需在 `bot_management` README Context Boundary 声明）。
- **内部套件不修改全绿**：`test_router.py`、`test_bot_passport.py` 等以行为等价保证。

---

## 10. 不在 scope / 备选方案

### YAGNI 掉的

- **`is_default` 布尔列**：曾考虑承载"主 bot"语义。因删除保护实为"保留≥1"计数规则、
  collaborator/chat 实为归属/权限，无一处真需"主 bot"标记 → 弃用，避免 DDL+回填+扫替换。
- **`(工号, bot_id)` 全局唯一约束**：曾考虑硬约束。`default` 退役后新 bot 本就全局唯一，
  外部租户不产生 `default`，存量 teamclaw `default` 在单租户内 `(工号, default)` 已唯一 →
  无新增碰撞，硬约束收益有限且与 tenant guard 哲学冲突 → 不加。
- **"主 bot = 最早存活 bot"派生**：曾考虑用于删除保护。被"保留≥1"计数规则取代，更简单。

### 回退方案（若 §7.1 gate 不通过）

点 3 不成立时，回退为"边界键带 tenant"：
- `ProdPassportPlugin` apply 时给 tcauthmng 传全局唯一边界键（含 tenant 消歧），存新列
  `agent_identity`；
- 回调按 `agent_identity` 解析；
- ocb corp DTO + `ProdPassportPlugin` 要改；
- 存量 teamclaw `default` 需 remap（tcauthmng 侧 reissue 或维护映射）。

代价显著高于本方案，仅在 gate 否决时启用。

---

## 11. 风险与回滚

| 风险 | 缓解 |
|---|---|
| §7.1 tcauthmng `applyFirst` 语义不符 | spec 标注为硬 gate；确认前不实施点 3 不符的分支；不符则走 §10 回退 |
| 存量 `default` bot 在新删除规则下被删（原永不可删） | 行为变化已与产品确认（保留≥1）；若需保全存量 `default`，plan 阶段可加"存量 default 视为不可删"过渡（YAGNI，暂不加） |
| `count==0` 与并发创建竞态 | 与原 `exists_by_owner_and_bot_id` 同级竞态；`generate_bot_id` 不再依赖此结果分配 id，仅影响 `is_first_bot`，偶发误判后果为发证 RPC 选错 → 依赖 tcauthmng 容错 |
| 回调 `skip_avernet_tenant_guard` 被滥用 | 仅限 `hot_update_passport_token_to_device` 一处；回调入口鉴权（tcauthmng 签名/IP 白名单）独立保障 |
| 桌面 bot 旁支漏改 | §7.3 评估项，plan 阶段确认 |

回滚：改动集中在 community 的 ~10 个文件，行为等价面大（内部路径）；出问题可逐点 revert。
`default` 退役不可逆（新 bot 不再产生 `default`），但存量不回填，故无数据迁移负担。