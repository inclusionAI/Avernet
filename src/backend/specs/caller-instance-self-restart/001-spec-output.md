---
agent: tc-review
status: approved-design
created: 2026-09-04
baseline: github/REL20260904@3c3aeaf109638ea5e1d917a31dd0fd38d971e5ac
repository: inclusionAI/Avernet
---

# Caller 实例本人重启权限升级 Spec

## 1. 需求背景

现有 `POST /api/v1/expert-chats/caller-connection` 是会创建、复用或升级 Caller 独立容器的写接口。当前只允许 `super_admin` 调用，管理员可通过 `bot_id + owner_id + user_id` 为任意 Caller 管理实例。

本次在不破坏管理员旧逻辑的前提下，允许普通登录用户管理**自己已经存在的 Caller 容器**：只有当 `ac_expert_chat_instance` 在当前环境中精确匹配请求的 `bot_id + owner_id + user_id`，且记录已有有效 `ext.bot_uuid` 时，普通用户才可获取连接或通过 `force_upgrade=true` 重启/升级该指定容器。

普通用户不得通过此接口首次创建 Caller 实例；公开 Bot、协作者关系或普通聊天权限不构成本接口的授权依据。

## 2. 现有链路与边界

### 2.1 既有链路

1. Router `get_caller_connection_for_other` 从 `get_current_user()` 获取可信 `user.staffId`。
2. Router 当前要求 `user.staffId in super_admin()`。
3. Router 调用 `ExpertChatInstanceService.get_caller_connection()`。
4. Service 按 `(bot_id, owner_id, user_id, env)` 查建 `ac_expert_chat_instance`。
5. Service 解析服务 Bot 的成功发布单，按实例状态复用、创建或升级 BaaS 容器，并返回连接。

### 2.2 允许新增点

- 在 `ExpertChatInstanceServiceProtocol` 与 `ExpertChatInstanceService` 中新增 actor-aware 的接口入口，承载管理员兼容和普通用户既有实例匹配规则。
- Router 改为调用 actor-aware 入口，只负责传递认证身份和 HTTP 参数、映射业务异常。
- 增补 Endpoint、Service 和 Acceptance 测试。
- 为本次修改的 HTTP 入站边界补齐最小、结构化、可关联且不包含连接凭据的请求、成功和拒绝/失败日志。

### 2.3 禁止触碰点

- 不修改 `get_caller_connection()` 现有创建、复用、升级和进度状态机。
- 不修改 `_create_container()`、`_upgrade_container()`、BaaS、Relay、WebSocket、Cron 或通用 HTTP Client。
- 不修改 `ac_expert_chat_instance` 表结构、唯一键和 Repository 查询语义。
- 不把 public/collaborator/互动列表权限引入本管理型接口。
- 不新增 GET 路由；保留 POST，避免浏览器预取或代理重试意外触发生命周期操作。
- 不修改 Caller binding 现有归属语义。

## 3. 编码 Spec

### 3.1 权限决策表

| 操作者 | 条件 | 结果 |
|---|---|---|
| 匿名/空身份 | `operator_id` 为空或 `anonymous` | 拒绝，`error_code=400` |
| 超级管理员 | `operator_id in super_admin()` | 保留旧逻辑；允许目标实例不存在并首次创建，也允许为任意 `user_id` 复用/升级 |
| 普通用户本人 | `operator_id == requested_user_id`，且当前 env 下精确存在实例，且 `ext.bot_uuid` 为非空字符串 | 允许复用连接；`force_upgrade=true` 时允许升级/重启该实例 |
| 普通用户跨用户 | `operator_id != requested_user_id` | 拒绝，`error_code=403` |
| 普通用户无匹配实例 | 精确查询返回 `None` | 拒绝，`error_code=403`；不得首次创建 |
| 普通用户实例无容器 | 记录存在但 `ext.bot_uuid` 缺失、为空或非字符串 | 拒绝，`error_code=403`；不得首次创建 |

授权拒绝必须发生在 `_resolve_build_artifact()`、`release_async()`、`upgrade_async()`、`get_publish_progress()` 和 `get_ws_info_by_bot_uuid()` 之前。

### 3.2 关键方法抽象

#### `ExpertChatInstanceServiceProtocol.get_authorized_caller_connection`

建议签名：

```python
async def get_authorized_caller_connection(
    self,
    *,
    operator_id: str,
    user_id: str,
    bot_id: str,
    owner_id: str,
    is_super_admin: bool,
    force_upgrade: bool = False,
) -> Dict[str, Any]:
    ...
```

职责：

- 以认证上下文中的 `operator_id` 作 actor，并接收 Router 从可信认证配置解析出的 `is_super_admin` 角色事实。
- `is_super_admin=True` 时直接委托现有 `get_caller_connection()`，保持旧行为。
- 普通用户先校验 `operator_id == user_id`。
- 普通用户通过现有 `ExpertChatInstanceRepository.get_instance(user_id, bot_id, owner_id)` 在当前 env 精确匹配实例。
- 普通用户校验实例 `ext.bot_uuid` 为非空字符串后，才委托现有 `get_caller_connection()`。
- 权限失败抛出 `ChatPermissionError`，由 Router 映射为 `error_code=403`。

不承担：

- 不实现 BaaS 生命周期。
- 不检查 public/collaborator 聊天权限。
- 不修改实例状态或字段。
- 不解析 HTTP Request、Cookie 或 Header。

#### `get_caller_connection_for_other` Router

职责：

- 保留空身份/anonymous 的现有 400 行为。
- 使用既有 `super_admin()` 将认证身份解析为 `is_super_admin`，并调用 actor-aware Service 入口。
- 将 `ChatPermissionError` 映射为 `ApiResponse(success=False, error_code=403)`。
- 记录入站请求、成功、权限拒绝和意外失败的结构化事件。

不承担：

- 不直接查询 Repository。
- 不实现管理员/实例归属规则。
- 不修改 query 中的 `user_id` 后再透传；由 Service 使用 actor 明确判断。

### 3.3 关键领域模型

#### `CallerInstanceAccessRequest`（概念命令，不新增持久化 DTO）

表达一次 Caller 实例访问/重启请求。为保持最小改动，使用方法参数承载，不新增 class。

| 字段 | 类型 | 必填 | 来源/所有者 | 业务含义与约束 |
|---|---|---:|---|---|
| `operator_id` | `str` | 是 | `get_current_user().staffId` | 可信操作者身份；普通用户不得由 query 覆盖 |
| `is_super_admin` | `bool` | 是 | Router 根据既有 `super_admin()` 解析 | 仅表达认证角色事实；Service 仍负责全部领域授权不变量 |
| `user_id` | `str` | 是 | query | 目标 Caller；普通用户必须与 `operator_id` 相等 |
| `bot_id` | `str` | 是 | query | 服务 Bot 标识；参与实例精确键匹配 |
| `owner_id` | `str` | 是 | query | 服务 Bot Owner；参与实例精确键匹配，不等同于 Caller |
| `force_upgrade` | `bool` | 否 | query，默认 `False` | `True` 时跳过版本快路径；仍必须先通过权限检查 |

#### `ac_expert_chat_instance`

本次不变更模型，只把其既有记录作为普通用户管理权限的服务端证明。

| 字段 | 约束 | 本次用途 |
|---|---|---|
| `bot_id` | 唯一键成员 | 必须与请求精确匹配 |
| `owner_id` | 唯一键成员 | 必须与请求精确匹配 |
| `user_id` | 唯一键成员 | 必须等于请求 `user_id`，普通用户同时必须等于 `operator_id` |
| `env` | 唯一键成员，由 Repository 使用当前环境 | 防止跨环境实例命中 |
| `status` | `init/success/failed` | 不作为授权依据；失败或 init 的既有容器仍可按原生命周期处理 |
| `ext.bot_uuid` | 普通用户路径要求非空字符串 | 证明已有指定 Caller 容器；缺失时禁止普通用户首次创建 |

关键不变量：

1. 普通用户的授权必须同时满足“可信身份等于目标 Caller”和“当前环境精确实例存在且已有 bot_uuid”。
2. 管理员旧路径不得因新增普通用户规则而要求实例预先存在。
3. 权限拒绝不得产生 BaaS、发布进度、连接获取或持久化副作用。

### 3.4 业务异常与 HTTP 映射

- `ChatPermissionError`：普通用户跨用户、无实例或无有效 bot_uuid；HTTP envelope `error_code=403`。
- 现有其他异常保持 catch-all 行为和返回契约，不在本次扩张修改。
- 错误消息不返回实例是否属于其他用户的详细信息，避免暴露非公开实例状态。

### 3.5 外部边界结构化日志

本次修改的是入站 HTTP 边界，不新增出站调用。日志使用项目现有 logger，不记录 `connection`、token、Cookie、Header 或完整 `instance.ext`。

稳定事件：

| 事件名 | 时机 | 非敏感字段 |
|---|---|---|
| `expert_chat.caller_connection.request` | 认证完成、调用 Service 前 | `direction=inbound`、`operation=caller_connection`、`method=POST`、`route`、`operator_id`、`bot_id`、`owner_id`、`user_id`、`force_upgrade` |
| `expert_chat.caller_connection.success` | Service 成功返回 | 上述关联字段、`authorized_as=admin/self`、`need_poll`、`duration_ms` |
| `expert_chat.caller_connection.denied` | 匿名或权限失败 | 关联字段、`reason=missing_operator/cross_user/instance_not_found/instance_missing_bot_uuid`、`duration_ms` |
| `expert_chat.caller_connection.failed` | 意外异常 | 关联字段、`exception_type`、`duration_ms`；异常栈沿用项目设施，但不得附请求凭据 |

脱敏要求：

- 禁止记录 `connection.token`、`IAM_TOKEN`、Authorization、Cookie、secret、key、credential、session 内容。
- 不使用 `str(request)`、headers dump 或原始响应序列化。
- 测试断言请求、成功、拒绝事件存在，并断言注入的敏感哨兵值不出现在日志文本。

### 3.6 TDD 实现顺序

1. RED：普通用户本人有精确实例且 bot_uuid 有效时应成功；当前代码因非管理员返回 403。
2. RED：普通用户无实例时应 403，且 lifecycle 方法零调用。
3. RED：普通用户跨 user_id 时应 403，即使目标实例存在。
4. RED：普通用户实例缺少/无效 bot_uuid 时应 403。
5. RED：超级管理员在实例不存在时仍可成功进入旧创建逻辑。
6. RED：权限拒绝发生在 BaaS 调用前。
7. RED：入站 request/success/denied 日志存在且敏感哨兵不落日志。
8. GREEN：实现最小 actor-aware Service 入口和 Router 映射。
9. REFACTOR：只消除本次新增重复，保持行为不变。

## 4. Review Spec

Reviewer 必须检查：

- [ ] 仅修改 actor-aware Service API、实现、Router、对应测试和 Spec/报告。
- [ ] Router 不直接访问 Repository，不承载领域权限判断。
- [ ] 普通用户必须同时满足 `operator_id == user_id`、实例精确存在、`ext.bot_uuid` 有效。
- [ ] 普通用户不能首次创建实例。
- [ ] 管理员可在实例不存在时继续走原逻辑。
- [ ] 所有拒绝路径均在任何 BaaS/发布/连接副作用之前返回。
- [ ] 不引入 public/collaborator 权限。
- [ ] 不新增 GET，不修改数据库 schema 和 lifecycle 状态机。
- [ ] 日志事件覆盖 request/success/denied/failed，不包含 token、Cookie、Header、connection 或完整 ext。
- [ ] 测试具有行为断言，非纯 mock 存在性检查；TDD RED 证据写入编码报告。
- [ ] Python 无未使用 import/变量、无无关格式化 diff。
- [ ] Avernet 源码改动不提交到 `mirrors/Avernet`，后续源码 PR 走 GitHub `inclusionAI/Avernet`。

本地门禁：

- 相关 Endpoint + Service tests 100% 通过。
- 相关 Acceptance tests 在可用的 Singlebox 环境中通过；若环境不可用，必须明确记录未运行原因。
- 远端 ACI：casePassRate 100%、lineCoverage >=70%、changeLineCoverage >=90%；PR 前为 PENDING，不得虚报 PASS。

## 5. QA Spec

### 5.1 本地/自动化用例

1. 管理员、owner、caller 三者不同：仍可创建/复用/升级。
2. 普通用户 `operator=user_id`，精确实例存在且有 bot_uuid：可返回连接。
3. 同上且 `force_upgrade=true`：进入既有升级流程。
4. 普通用户实例不存在：403，不新增实例，不调用 BaaS。
5. 普通用户实例存在但无 bot_uuid：403，不调用 BaaS。
6. 普通用户 `operator != user_id`：403，即使目标记录存在。
7. 相同 user_id 但 bot_id 或 owner_id 不匹配：403。
8. 匿名：维持 400。
9. 拒绝日志包含非敏感业务键和 reason；不出现 token/Cookie/secret 哨兵。
10. 管理员旧用例和 force_upgrade 用例不回归。

### 5.2 预发验证

- 使用 POST，不通过浏览器地址栏 GET 调用。
- 首先对普通用户已有实例执行 `force_upgrade=false` 只读式连接复用验证。
- 经用户确认具体 Bot 后，再执行 `force_upgrade=true` 的真实重启验证。
- 核对同一 `(bot_id, owner_id, user_id, env)` 的实例 `bot_uuid` 与发布进度，禁止输出连接 token。
- 验证普通用户跨 user_id 和无实例均返回 403，且后端日志无 BaaS 出站事件。
- 验证超级管理员代其他 Caller 的旧路径仍成功。

## 6. Ship Spec

本次源码位于 Avernet 子模块，采用双仓交付：

1. 在独立 Avernet worktree 分支 `rebase/caller-instance-self-restart-on-REL20260904` 完成源码和测试。
2. 源码必须推送到 GitHub `inclusionAI/Avernet` 并以 `REL20260904` 为 base 创建 Avernet PR；不得向 `mirrors/Avernet` 推送源码。
3. Avernet PR/CI 通过且提交已同步到 `.gitmodules` 配置的镜像后，才在 OCB 独立 worktree 更新 `ocb-public` git gitlink。
4. OCB PR 只包含 gitlink 和确有必要的 Corp 回归，不复制 Avernet 源文件。
5. 部署/预发 QA 必须在用户审核代码后进行；本轮编码完成不自动触发真实 `force_upgrade=true`。
6. 回滚：Avernet 回退该权限提交；OCB gitlink 回退到升级前 SHA。无数据库迁移。

# 权限控制类安全约束设计与风险识别

>/*数字支付需求安全分析要求*/

## 权限控制类安全约束设计

涉及到非公开数据 Caller 容器、Caller 实例记录和连接信息，仅限超级管理员操作任意目标 Caller；普通用户仅可操作 `ac_expert_chat_instance` 中 `bot_id`、`owner_id`、`user_id` 与请求精确匹配、`user_id` 为当前登录用户且 `ext.bot_uuid` 有效的既有 Caller 容器。

**注意** 1、在进行权限安全校验时使用到的用户身份信息（包括用户类型和用户Id）和业务中需要消费当前用户身份信息时，必须从安全的上下文中获取的（用户登录态或其他用户不可控身份组件）2、代码编写中确保所有校验逻辑代码都已完全实现，如果保留todo则该函数示例返回应该默认校验不通过

## 技术安全风险识别

- **技术安全风险描述**：仅校验实例存在但不校验登录身份与 `user_id` 相等，会允许重启他人的 Caller 容器。**安全设计要求**：普通用户必须使用认证上下文身份与实例 `user_id` 双重匹配。
- **技术安全风险描述**：普通用户命中无 `bot_uuid` 的占位记录后进入原流程，会产生首次创建副作用。**安全设计要求**：普通用户路径必须要求有效 `ext.bot_uuid`，否则先于生命周期调用拒绝。
- **技术安全风险描述**：仅在 Router 做权限校验，其他入口可直接调用生命周期方法绕过。**安全设计要求**：为 HTTP 用例提供 Service actor-aware 入口并集中落实权限不变量。

## 7. Iteration 2 架构修订

- `ExpertChatInstanceServiceProtocol` 的唯一权威定义位于 `core/expert_chat/expert_chat_instance_service_protocol.py`；`api/expert_chat_instance_service.py` 仅直接 re-export 同一 Protocol 对象。
- HTTP Router 采用既有 `super_admin()` 解析认证角色，并以显式 `is_super_admin` 参数传入 core Service；core Service 不依赖 `core.access`，但继续集中执行管理员兼容、`operator_id == user_id`、精确既有实例和有效 `bot_uuid` 四项授权规则。
- Protocol/Concrete pair 纳入 `tests/community/architecture/test_service_api_conformance.py`，同时锁定 API re-export 与 owning core Protocol 的对象同一性和方法签名。
- `tests/community/endpoints/` 只保留真实 world/DI 用例；使用 mock/patch 的 Router 日志及敏感哨兵测试位于 `tests/community/api/expert_chat/test_router.py`。
