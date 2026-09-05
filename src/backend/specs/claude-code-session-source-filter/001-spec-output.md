---
agent: tc-review
status: awaiting_user_confirmation
created: 2026-09-05T12:08:55+08:00
updated: 2026-09-05T12:15:38+08:00
iteration: 2
task_name: claude-code-session-source-filter
project_code: /Users/helloworld/Desktop/codes/teamclaw/Avernet
review_baseline: dev (local checkout; behind origin/dev by 3 commits, relevant files unchanged)
---

# 系分 Spec：Claude Code 会话列表 all_but_others 过滤与用户归属防冒充

## 1. 需求概述

为 `claude_code` Engine 的既有会话列表接口增加首期 `source=all_but_others` 过滤，并确保请求方不能通过传入其他工号冒充他人、读取他人会话或把无归属会话伪装成自己的会话。

用户提供的《多引擎会话列表 source 过滤方案》截图契约如下：

```http
GET /api/sessions?user_id=165137&source=all_but_others&offset=0&limit=20
```

- source 表中仅 `all_but_others` 有效；`mine` 和 `others` 均已被删除线划掉，不属于当前公开支持值。
- 不传 `source` 保持现有行为。
- 服务端先按 source 过滤，再执行 `offset/limit` 分页。
- 前端统一向各引擎传 `source`。
- TEClaw 首批支持，其他引擎后续逐步实现。
- 暂不支持 source 的引擎必须忽略合法参数，不因能力未实现或参数存在而报错。
- 对 TEClaw 链路，OCB Backend、Avernet Engine Adapter 与 ProxyPass 无需增加专门转发逻辑，原始 query 会透明透传。
- 本 Claude Code 需求首期只实现 `all_but_others`，不实现 `mine`、`others`。

## 2. 基线与工作区保护

- 当前 Avernet 主 checkout 为 `dev`，相对 `origin/dev` 落后 3 个提交；已核对这些提交未修改本需求涉及的 Session Router、AuthContext、Claude Code Session Adapter/Port。
- 用户未跟踪文件 `src/backend/bots.openapi.json` 必须保持原样，不得修改、删除、覆盖、暂存或通过生成脚本重写。
- 本阶段只修订本 Spec 与项目评审日志；禁止编码、创建 worktree、切换/rebase 分支或修改运行环境。

## 3. 源码现状与问题判断

### 3.1 扩展现有 GET /api/sessions，不新增 endpoint

现有主链路：

```text
Frontend
  -> CloudBrain/agentclawproxy ProxyPass
  -> Engine GET /api/sessions
  -> SessionService.list(SessionListRequest)
  -> ClaudeCodeSessionAdapter.list
  -> ClaudeCodeSessionPort.sessions_list
  -> claude_code relay RPC sessions.list
```

`GET /api/sessions` 已支持 `user_id`、`agent_id`、`session_key`、`offset`、`limit`。Claude Code Port 已在分页前执行 `agent_id/session_key` 过滤，所以 source 应沿该接口和列表请求模型扩展。

**接口决策：不新增 `/api/sessions/mine`、`/api/sessions/source` 等 endpoint。**

截图明确 ProxyPass 会透传 query，因此本次默认不修改 OCB Backend 或 ProxyPass。只有实测证明 Claude Code 实际调用链丢失 query 时，才允许补最小透传修复，并需先更新 Spec；不得预先扩张 Backend 范围。

### 3.2 Public OpenAPI 与本次范围

现有 Public OpenAPI `POST /openapi/v1/bots/{bot_id}/sessions` 已把 `UserIdDep` 校验后的 `user_id` 转发给 Engine `POST /api/sessions`，但不传 `agent_id`。这直接暴露当前 Claude Code 创建短 key 不带用户的问题，因此创建链路属于本次必要修复和回归范围。

Public OpenAPI GET 列表由 Backend Router 手工组装参数，并非截图所述透明 ProxyPass 主链路。**本期不默认扩展 Public OpenAPI GET 的 source 契约。** 若产品明确要求 Public OpenAPI 也公开 source，需另行确认并同步其 OpenAPI 文档，不能由本截图自动扩张外部 API。

### 3.3 当前认证缺口

- Public OpenAPI `require_user_id` 已有明确契约：人类认证主体传入其他 `user_id` 时返回 403，响应固定为 `Forbidden`，不回显目标用户。
- Engine HTTP `GET/POST /api/sessions` 当前没有可信身份依赖：
  - `AuthContext` 只有 `token`；
  - HTTP Router 调用 SessionService 时没有传 `auth`；
  - Query/Body `user_id` 由调用方控制。
- WebSocket frame 中的 `user_id` 未形成可复用的 HTTP 认证事实，不能作为当前用户。
- 容器 owner ID 代表 Bot/运行时所有者，不一定是发起当前请求的用户，不能替代当前认证用户。

结论：**source 过滤不能直接信任 query user_id。Engine 必须从 ProxyPass/企业认证提供的安全上下文得到当前认证工号，并校验请求 user_id 与其一致。**

### 3.4 Claude Code SessionKey 归属缺口

当前 `ClaudeCodeSessionAdapter.create`：

- 同时有 `agent_id`、`user_id`：`agent:<agent>:session:<uuid>:user:<uid>`；
- 否则：`session:<uuid>`。

Public OpenAPI 创建会话不传 agent_id，因此通常生成不含 user 的 `session:<uuid>`。

当前 `_parse_session_key` 对短 key 返回无归属，随后 `_relay_session_to_session(..., user_id=request.user_id)` 又把请求工号作为 fallback。调用方传谁的工号，旧短 key 就可能被标成谁的会话，无法可靠判断“他人发起”，并存在逻辑数据泄露风险。

## 4. 推荐方案

### 4.1 总体方案

采用“**现有 GET 扩展 + 单一有效 source + 可信 actor + 新 key 带 user + 旧短 key 保留 + 过滤后分页**”：

1. 在通用 `SessionListRequest` 增加可选 source，首期公开合法值仅 `all_but_others`；不传为 None。
2. Engine `GET /api/sessions` 增加同名 Query，沿现有 ProxyPass query 透传链路接收。
3. 暂未支持 source 的其他引擎忽略合法 `all_but_others`，返回与不传 source 相同的结果。
4. Engine HTTP 认证适配层将可信当前工号写入 `AuthContext.user_id`；Session Router 校验请求 `user_id` 必须等于该值。
5. Claude Code 新建 key：
   - 有 agent_id：保持 `agent:<agent>:session:<uuid>:user:<uid>`；
   - 无 agent_id 但有可信 user_id：改为 `session:<uuid>:user:<uid>`。
6. SessionKey parser 支持：
   - `agent:<a>:session:<s>:user:<u>`；
   - `user:<u>:session:<s>:agent:<a>`；
   - `session:<s>:user:<u>`；
   - `session:<s>` 和畸形 key 归类为 `unknown_legacy`，不使用请求工号补归属。
7. Claude Code Port 在 offset/limit 之前完成 source 过滤。

### 4.2 invalid source 契约

#### 推荐契约

Engine Query 和 `SessionListRequest.source` 定义为：

```python
Literal["all_but_others"] | None
```

| 输入 | Claude Code | 暂未支持 source 的其他引擎 |
|---|---|---|
| 不传 source | 保持现有行为 | 保持现有行为 |
| `all_but_others` | 执行过滤 | 接收后忽略，保持现有行为 |
| `mine` | 422，不是公开支持值 | 422，参数值本身无效 |
| `others` | 422，不是公开支持值 | 422，参数值本身无效 |
| 其他未知值 | 422 | 422 |

#### 推荐依据

1. 截图中 `mine`、`others` 已被明确划掉，不能擅自定义为公开支持值。
2. “未支持 source 的引擎忽略参数”应解释为忽略**合法的 `all_but_others` 能力请求**，而不是接受任意字符串。
3. 非法值若静默忽略，会把调用方本想过滤的请求降级为未过滤列表，可能扩大可见数据范围。
4. 仓库 FastAPI 已使用 Query pattern/模型闭集校验并返回 422，符合既有参数风格。
5. 422 能暴露旧前端仍发送 `mine/others` 或拼写错误，避免“HTTP 成功但权限过滤未生效”的假成功。

因此本期推荐 **非法值 422，合法但引擎未实现则忽略**。不推荐将 `mine/others` 兼容映射为 None 或 `all_but_others`。

### 4.3 all_but_others 精确规则

内部可使用归属分类名，但不公开 mine/others source 参数：

| 内部分类 | 判定 | `source=all_but_others` | 不传 source |
|---|---|---:|---:|
| current-user | owner 可解析且等于认证用户 | 返回 | 保持现有集合行为 |
| other-user | owner 可解析且不等于认证用户 | 排除 | 保持现有集合行为 |
| non-user/system | 项目已有可信元数据明确为非用户发起 | 返回 | 保持现有集合行为 |
| other-format | 无尾部 `:user:<user_id>` 的其他格式或旧短 key | 返回 | 继续兼容可见，但不得伪造 user_id |

关键规则：

- `all_but_others` 只排除能够从 Key 尾部明确识别为其他用户的会话。
- 没有尾部 `:user:<user_id>` 的其他格式、旧短 key 和畸形 key 均保留，不将其归属伪造为当前用户。
- 禁止通过标题、cwd、连接 ID、query user_id 或其他启发式推断 SessionKey 用户。
- 不传 source 时保持列表集合和顺序兼容，但 DTO 中未知归属不得继续填成请求 user_id。

过滤顺序：

```text
sessions.list 全量结果
  -> 丢弃非 dict 和既有 BCS group 隐藏项
  -> agent_id 过滤（如有）
  -> session_key 过滤（如有）
  -> 解析可信 owner / 内部归属分类
  -> all_but_others 过滤（如 source 有效）
  -> offset/limit 分页
  -> DTO 转换
```

### 4.4 身份与错误语义

- `AuthContext.user_id` 只能来自安全认证上下文：已验证 Gateway Principal、企业认证插件，或 ProxyPass 注入且外部调用者不可覆盖的可信身份字段。
- 禁止从 Query、Body、普通自定义 Header、未经验证的 WS frame 或“只解码不验签”的 IAM Token 构造可信用户。
- `user_id != AuthContext.user_id`：HTTP 403 `Forbidden`，不得调用 relay。
- `source=all_but_others` 但没有可信 `AuthContext.user_id`：401 或认证组件既有拒绝状态，绝不降级为不筛选。
- 不传 source但显式传 user_id：仍必须做 mismatch 校验，不能保留冒充入口。
- Public OpenAPI 继续沿用现有 403 固定响应；按具体 session id 访问无权资源时继续遵循既有隐藏 404，本列表需求不改变它。

### 4.5 必须修复创建 key

若继续生成 `session:<uuid>`：

- 新会话无法被识别为当前用户，`all_but_others` 无法准确过滤；
- 使用 query user_id fallback 会继续允许冒充；
- 无归属旧格式会持续增长。

因此 SessionKey 修复是 all_but_others 正确工作的必要组成，不是额外功能。

## 5. 编码 Spec

### 5.1 功能点

- [ ] 只扩展现有 Engine `GET /api/sessions`，支持可选 `source=all_but_others`。
- [ ] 不实现 `source=mine/others`，非法或未知值返回 422。
- [ ] 未实现 source 的引擎忽略合法 `all_but_others`，不报能力错误。
- [ ] 从可信认证上下文创建 `AuthContext.user_id`，拒绝请求工号与认证身份不一致。
- [ ] 修复 Claude Code 无 agent 新建 key 为 `session:<uuid>:user:<uid>`。
- [ ] 扩展 key parser，去掉旧短 key 的请求工号 fallback。
- [ ] all_but_others 只排除尾部 user_id 明确为其他用户的 Session。
- [ ] source 过滤先于 offset/limit。
- [ ] 保持不传 source 的列表集合兼容。
- [ ] 增加结构化 request/success/denied/failure 日志与敏感字段脱敏测试。

### 5.2 关键方法抽象

| 抽象/方法 | 模块 | 职责与边界 | 输入/输出 |
|---|---|---|---|
| `resolve_http_auth_context(...)`（最终名称按既有认证设施） | Engine API auth | 从可信认证结果构造 AuthContext；不信任业务参数 | Request/认证结果 → AuthContext |
| `require_session_actor(auth, requested_user_id)` | Engine Session API | 比对认证工号与请求工号；mismatch fail closed | AuthContext + user_id → verified user_id / 401/403 |
| `_parse_session_key(key)` | Claude Code ACL | 解析三种带 user key；短/畸形 key 标未知 | key → owner、agent、kind |
| `_classify_session_owner(key, actor)` | Claude Code ACL/Port | 判定 current/other/non-user/unknown | key + actor → 内部分类 |
| `sessions_list(..., source, actor_user_id, offset, limit)` | Claude Code Port | relay 拉取后先过滤再分页 | filters → raw page |
| `ClaudeCodeSessionAdapter.create(...)` | Claude Code ACL | 用 verified user 生成带 user key | request + auth → Session |
| `ClaudeCodeSessionAdapter.list(...)` | Claude Code ACL | 传递 verified actor/source并构造 DTO | request + auth → Session list |

### 5.3 关键领域模型

#### AuthContext 扩展

| 字段 | 类型 | 必填 | 来源/约束 | 用途 |
|---|---|---:|---|---|
| token | `str | None` | 否 | 已验证认证层；永不明文日志 | 保持现有连接路由 |
| user_id | `str | None` | source 模式是 | 只能来自可信认证结果 | 会话 owner 比对 |
| trace_id | `str | None` 或既有 ContextVar | 否 | 请求链路 | 日志关联 |

不变量：Query/Body 不得覆盖 AuthContext.user_id；source 模式缺 actor 必须拒绝。

#### SessionSourceFilter

| 值 | 状态 | 语义 |
|---|---|---|
| `all_but_others` | 本期唯一公开有效值 | 排除 other-user 与 other-format |
| None | 默认 | 保持现有行为 |
| `mine` / `others` / 其他 | 不支持 | FastAPI/Pydantic 422 |

#### SessionOwnership（内部）

| 字段 | 类型 | 说明 |
|---|---|---|
| kind | current-user / other-user / other-format | 决定可见性 |
| owner_user_id | `str | None` | 禁止从请求 fallback |
| agent_id | `str | None` | 保持 agent 过滤 |

### 5.4 预计文件范围

| 路径 | 预计改动 |
|---|---|
| `src/engine/src/engine/community/core/engine/context.py` | AuthContext 增加可信 user_id |
| `src/engine/src/engine/community/core/session/models.py` | source 仅允许 all_but_others/None |
| `src/engine/src/engine/community/api/session/router.py` | 现有 GET source、HTTP auth、identity mismatch；POST 复用 actor 校验 |
| `src/engine/src/engine/community/core/adapters/claude_code/session.py` | key parse/create、去 fallback、source 传递 |
| `src/engine/src/engine/community/plugin_api/claude_code/session.py` | Port source/actor 契约 |
| `src/engine/src/engine/community/plugins/claude_code/_session.py` | 分页前过滤 |
| 对应 Engine Router、Adapter、Port、其他引擎兼容测试 | TDD 覆盖 |

默认不修改：OCB Backend、Public OpenAPI GET、ProxyPass、Frontend、其他引擎过滤实现、`src/backend/bots.openapi.json`。若可信 actor 传播缺失确需跨层修改，必须先回到 Spec 确认，不能编码时自行扩张。

### 5.5 外部边界结构化日志

| 事件 | 必备字段 |
|---|---|
| `engine.sessions.list.request` | system、direction=inbound、operation、method、route、authenticated_actor、requested_user_present、source、offset、limit、trace/request id |
| `engine.sessions.list.denied` | reason=identity_mismatch/missing_actor/invalid_source、status、actor、安全编码后的 requested user、trace id |
| `claude_code.sessions.list.request` | direction=outbound、operation=sessions.list、source、actor、filter presence、offset、limit、trace id |
| `claude_code.sessions.list.success` | raw/current/other/non-user/unknown/matched/returned count、duration、status/result |
| `claude_code.sessions.list.failure` | error_type、safe_message、duration、status、trace id |
| `engine.sessions.create.request/success/denied/failure` | actor、requested_user_present、agent_present、generated_key_format 枚举、duration、status、trace id |

递归脱敏：Authorization、Cookie、IAM/MCP/proxypass token、password、secret、API/private key、credential、session credential。原始 SessionKey 只记录不可逆 hash 和格式枚举；禁止完整 headers、request、raw session dict、原始异常响应入日志。测试必须断言成功、拒绝、失败事件存在，并断言植入的凭据/SessionKey 原值不出现。

### 5.6 编码验收标准

- [ ] 接口示例可用：`GET /api/sessions?user_id=165137&source=all_but_others&offset=0&limit=20`。
- [ ] 没有新增 endpoint。
- [ ] mine、others、未知 source 返回 422；不被公开或静默映射。
- [ ] 未支持 source 的引擎对合法 all_but_others 返回与未传 source 相同结果。
- [ ] 请求 user_id 与可信认证用户不一致返回 403，relay 未被调用。
- [ ] source 模式缺可信 actor fail closed。
- [ ] 无 agent 的新 Claude Code key 为 `session:<uuid>:user:<uid>`。
- [ ] all_but_others 排除明确他人和旧短/未知 key。
- [ ] 不传 source 的集合/顺序兼容，旧短 key 不再被请求工号伪造归属。
- [ ] source 过滤先于分页，跨页无漏项/重复。
- [ ] 结构化日志完整，原始凭据和 SessionKey 不落日志。
- [ ] 单测通过；改动文件行覆盖率 >90%，变更行覆盖率 >=90%，远端 ACI case pass 100%、总行覆盖率 >=70%。
- [ ] Ruff/linter、unused import/variable、E203/E265、whitespace、block comment 检查通过。

## 6. Review Spec

### 6.1 必查项

- [ ] 公开合法 source 只有 all_but_others/None。
- [ ] “未支持引擎忽略合法参数”和“非法值 422”没有混淆。
- [ ] Engine actor 来自可信 AuthContext，不来自 query/body/frame/env owner。
- [ ] GET/POST 共用身份校验 seam，无绕过路径。
- [ ] 新 key 带 user；旧短 key不再使用 request fallback。
- [ ] all_but_others 对 unknown legacy fail closed。
- [ ] source 过滤位于 `sessions[offset:offset+limit]` 前。
- [ ] 不修改 OCB Backend/ProxyPass，除非有 query 丢失的真实证据并先修订 Spec。
- [ ] 日志覆盖 inbound/outbound/success/denied/failure、duration/status/trace，且无 credential 泄漏。
- [ ] 无新 endpoint、无其他引擎业务扩张、无无关重构。

### 6.2 REJECT 模式

- 把 mine/others 继续作为公开支持值。
- 对非法 source 静默当成不传，导致返回未过滤数据。
- 只在 Public API 校验，Engine 直连仍可传任意工号。
- 从未验证 token、普通 header、query/body 构造 actor。
- 旧短 key 使用 request user/default 作为真实 owner。
- 在已分页结果上过滤。
- all_but_others 返回 unknown legacy。
- 无证据修改 Backend/ProxyPass 或新建列表 endpoint。
- 日志打印完整 request/headers/raw session/SessionKey/token/cookie。

## 7. QA Spec

| 编号 | 用例 | 预期 |
|---|---|---|
| QA-01 | U 请求 all_but_others，集合含 U/V/旧短 key | 返回 U 和无尾部 user 的其他格式；排除 V |
| QA-02 | U 的认证上下文传 user_id=V | 403；relay 未调用；响应不回显工号 |
| QA-03 | 无可信 actor 请求 all_but_others | 401/既有认证拒绝；不降级为全量列表 |
| QA-04 | 不传 source | 集合与改动前兼容，unknown 不被标成请求用户 |
| QA-05 | source=mine | 422 |
| QA-06 | source=others | 422 |
| QA-07 | source=random | 422 |
| QA-08 | 未支持引擎收到 all_but_others | 既有成功；结果等同不传 source |
| QA-09 | Public POST U、不传 agent_id | 返回 `session:<uuid>:user:U` |
| QA-10 | Engine POST AuthContext=U、body user_id=V | 403；不创建 session |
| QA-11 | 前 20 条为 other，后 5 条 current，limit=5 offset=0 | 返回 5 条 current，不是空页 |
| QA-12 | 12 条 current 混合 other，limit=5 offset=5 | 返回过滤后的第 6-10 条，无重复/漏项 |
| QA-13 | agent_id + all_but_others | 同时满足 agent 和 owner 条件 |
| QA-14 | exact session_key 属于 V，actor U | 空列表，不越权 |
| QA-15 | relay 超时/失败 | 保持既有安全失败语义，failure 日志齐全 |
| QA-16 | 日志注入 token/cookie/proxypass/session key | 关键事件存在，原始敏感值不存在 |
| QA-17 | ProxyPass 实际请求抓取 | source/user_id/offset/limit 原样到达 Engine，无额外 Backend 转发代码 |
| QA-18 | 旧 Session API 回归 | create/list/get/update/delete/history/favorite 无非预期回归 |

前置条件：两个认证用户 U/V、同一 Bot 上的带 user 会话、至少一个 `session:<uuid>` 旧短 key、可控 relay fake、真实 ProxyPass 黑盒环境。QA 报告必须区分单测、Engine HTTP、ProxyPass 请求和部署环境证据。

## 8. Ship Spec

- 编码前从用户指定的最新 GitHub `inclusionAI/Avernet` 目标分支创建隔离 worktree；本轮不创建。
- 当前主 checkout 落后且有用户未跟踪文件，禁止直接编码/rebase/清理。
- 后续目标分支、ARCA/Bot、PR 均由用户明确提供，不猜测。
- 发布顺序：TDD → Reviewer + Engine regression → 用户代码确认 → deliver/ARCA → QA → GitHub PR/ACI → pre/prod QA。
- 回滚以同一提交组回退 source、actor wiring、key 生成；不迁移或批量改写旧 SessionKey。

## 9. 权限控制类安全约束设计与风险识别

>/*数字支付需求安全分析要求*/

### 9.1 权限控制类安全约束设计

- 会话列表涉及非公开数据会话对象，仅限当前认证用户通过 `source=all_but_others` 查询能够证明为当前用户或可信非他人来源的数据；归属为其他用户或无法证明归属的数据均不可见。
- 会话创建涉及非公开数据会话对象，创建者工号必须来自安全认证上下文；请求传入工号与当前认证用户不一致时必须拒绝，新会话归属必须固化在带 userId 的 SessionKey 或等价可信元数据中。
- 不传 source 的兼容查询不得把请求工号写成旧短 key 的真实归属；显式请求工号与认证主体不一致时仍必须拒绝。

### 9.2 技术安全风险识别

- **技术安全风险描述**：旧短 key 使用请求工号 fallback，可把未知会话伪装成当前用户并泄露他人会话。**安全设计要求**：短 key 标记未知，all_but_others 下不可见。
- **技术安全风险描述**：非法 source 若被静默忽略，会把过滤请求降级为全量查询。**安全设计要求**：非法/已删除值返回 422。
- **技术安全风险描述**：source 若在分页后过滤，会造成漏筛或跨页暴露。**安全设计要求**：归属过滤必须先于 offset/limit。

**注意** 1、在进行权限安全校验时使用到的用户身份信息（包括用户类型和用户Id）和业务中需要消费当前用户身份信息时，必须从安全的上下文中获取的（用户登录态或其他用户不可控身份组件）2、代码编写中确保所有校验逻辑代码都已完全实现，如果保留todo则该函数示例返回应该默认校验不通过

## 10. 编码前硬门禁

1. 确认实际 ProxyPass/企业认证链路提供的可信当前用户 carrier；必须证明外部请求不能伪造或覆盖。
2. 若当前链路没有可信 actor，必须先修订 Spec 并确认最小传播方案，不能直接信任 query user_id。
3. 确认 relay 是否有可信 non-user/system 标记；没有时 all_but_others 只返回当前用户会话。
4. 确认最新目标分支和 worktree 创建授权后才能编码。

## 11. 评审结论

**推荐只在现有 GET `/api/sessions` 首期支持 `source=all_but_others`；mine、others 和未知值返回 422。未实现 source 的引擎只忽略合法 all_but_others。Claude Code 必须同时建立可信 AuthContext.user_id、拒绝工号冒充、生成带 userId 的新 SessionKey，并对旧短 key 保留但不补写 user_id；source 过滤必须先于分页。**

当前状态继续为 `awaiting_user_confirmation`。未解决可信 actor 和短 key 归属前，不得进入编码验收或声称权限隔离完成。
