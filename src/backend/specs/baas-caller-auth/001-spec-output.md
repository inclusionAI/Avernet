---
agent: tc-review
status: completed
created: 2026-09-16T15:36:10+08:00
iteration: 1
---

# 系分 Spec: BaaS Caller 连接入口服务签名认证

## 需求概述

仅调整 `POST /api/v1/expert-chats/app-caller-connection`，按已确认的 BaaS JWT 签发协议验证 HS256、`iss=baas`、必填 `iat/exp` 及可选 `nbf`。不解析或校验 principals、app_id、tenant、aud，不使用这些字段切换数据租户。使用服务端默认租户，保留 Bot 存在、精确 Caller 已有有效实例检查和连接返回契约。用户已授权实现及 PR，本 spec 无额外确认门禁。

依据：`output/2026-09-16-avernet-principal-signing-contract.md`（TeamClaw 工作区）；基线 `origin/REL20260917@6da0472eed89ae76cea85a23184fb15f6ca5c3fa`。已核对 middleware、org/dependencies、expert_chat/router、gateway_principal/verifier、gateway_principal_config、expert_chat_instance_service 及现有 ASGI/endpoint/acceptance 测试。

## 编码 Spec

### 功能点

- [ ] 原始 JWT 经 `X-Avernet-Principal` 传输；缺失、空、损坏、Bearer 包装、无签名、错误签名或算法统一 HTTP 401 `{"detail":"Unauthorized"}`。
- [ ] 严格限定 issuer 为 `baas`；复用现有 5 秒时钟容差及 JWT 时间验证，不新增 TTL 上限、typ/kid 校验。
- [ ] 合法 JWT 无 principals 或带任意 JSON principals/app_id/tenant/aud 均不因此被拒绝；这些字段不进入身份或租户路由。
- [ ] Bot/owner/caller 参数与 force_upgrade 默认 false 保持不变；验证服务调用者后可访问默认租户中指定 Caller 的已有有效实例，无用户登录、grant 或成员校验。
- [ ] 不创建首个 Caller 实例；继续返回原 ApiResponse、connection、need_poll；业务错误与 401 分离。
- [ ] 其他 ordinary HTTP org、gateway 和 OpenAPI 的 issuer、principal 解析、tenant、audience 语义保持不变。

### 技术方案

现有 `AvernetTenantMiddleware` 对该路径提前调用 `resolve_ordinary_http_tenant`，会因旧 principal 校验影响 DI 仓储租户。删除此路径特例，使它与普通内部 HTTP 一样使用 `DEFAULT_AVERNET_TENANT`，保留 OpenAPI 分支与请求退出后的 ContextVar 恢复。

在 HTTP 认证依赖中为本入口使用专属方法（建议 `require_baas_caller`）。以 `dataclasses.replace(get_principal_verifier_config(), issuer="baas", verify_audience=False)` 派生配置，调用 `decode_principal_token` 后丢弃 claims，不调用 `verify_principal_token`/`caller_from_claims`，不构造伪造 VerifiedCaller 或 app_id。共享配置对象不可变更，ordinary issuer allowlist 不增加 baas。新依赖只绑定到此路由，认证完成返回 None 即可，无需新增身份 DTO。

密钥复用已由启动配置加载的 `SecretNamesConfig.gateway_principal_signing_key` 对应值，不增加部署配置或环境变量，不读取请求中的 key/kid。部署侧须确保 BaaS 签发进程持有匹配且受控的密钥；本 PR 不传输密钥。未配置密钥继续 fail closed。共享密钥意味着 issuer 是协议区分，不构成密钥持有者间的密码学隔离。

路由移除 VerifiedCaller、app_id/tenant 透传。保留 `get_application_caller_connection` 名称以最小修改其接口及实现，去掉 app_id/tenant 参数及旧 tenant mismatch 校验；从服务端当前上下文取租户用于诊断。保留 `_bot_repo.get_by_id_and_owner` 和 `get_authorized_caller_connection(operator_id=user_id, is_super_admin=False, ...)` 复用路径，后者检查精确 instance 及非空字符串 bot_uuid。禁止传入 super_admin=True。该赋值只是沿用已授权服务入口的业务执行方式，不声称完成终端用户身份校验。

同步 Service API/protocol、调用方和文档。无数据库迁移，不新增资源级授权、全租户扫描或缓存。

### 外部系统边界日志

| 外部系统/方向 | 操作/事件 | 非敏感输入输出、关联及状态 |
|---|---|---|
| BaaS → Backend | authentication_request / denied / authenticated（或后续 request） | method、route、request_id、bot_id、owner_id、user_id、force_upgrade；失败分类枚举、HTTP 401、duration_ms；不得记录 JWT、claims、JOSE header 或完整异常文本 |
| BaaS → Backend | app_caller_connection.request / success / denied / failed | 服务端 tenant、业务参数、request_id；递归脱敏 ApiResponse 的非敏感结构和数据、HTTP 状态、业务 error_code、exception_type、duration_ms |
| Backend → 已有实例连接服务 | 沿用现有连接业务 | 保留现有调用与日志，不新增外部请求；connection 返回给授权调用方但日志递归脱敏 |

递归脱敏 token、认证头、Cookie、密码、secret、key、credential、session 及嵌套数组字段；URL query 中凭据也不得出现在日志。仅记录允许列表输入字段，不枚举 JWT claims。错误日志不使用 str(exc)、repr(exc) 或包含认证内容的 traceback。成功、失败都验证无原始凭据；继续复用 `_application_log_value`，必要修复仅针对已证实缺口。

### 关键方法抽象

| 抽象/方法 | 所在层或模块 | 职责与边界 | 输入与输出 | 协作对象与副作用 |
|---|---|---|---|---|
| `require_baas_caller`（建议名） | HTTP 认证依赖 | 仅认证 BaaS JWT 签名和时间，不解释身份 | HTTPConnection → None；失败 MissingPrincipalError | 配置 getter、decode_principal_token；安全日志，无数据库访问 |
| `AvernetTenantMiddleware.__call__` | HTTP 中间件 | 本路径使用服务端默认 tenant；保留 OpenAPI 路由 | ASGI scope → 下游响应 | ContextVar 设置与恢复；DI 构建前生效 |
| `get_caller_connection_for_application` | HTTP adapter | 参数和错误映射，调用已经完成认证的用例 | 既有 query → ApiResponse | Service API、脱敏边界日志 |
| `get_application_caller_connection` | Service API + core 实现 | Bot 存在及已有实例的连接用例，无 JWT 处理 | user_id/bot_id/owner_id/force_upgrade → 原 connection 字典；ChatPermissionError | Bot/instance 仓储及已有连接生命周期，禁止首次创建 |

认证依赖把受信任服务身份与可选 payload 分离，调用方不得读取返回 claims。中间件在 DI 前建立唯一服务端租户；下游不得从参数覆盖。adapter 保持 401 与业务失败边界。core 用例只接受业务目标标识，服务调用方负责执行 BaaS 认证，core 负责 Bot 和既有实例不变量；core 不导入 HTTP/JWT adapter。

### 关键领域模型设计

不适用：无新实体、数据库字段或持久化状态。既有 Caller 实例模型已表达 `(user_id, bot_id, owner_id)` 及 `ext.bot_uuid`。移除的是服务方法中不再有效的身份参数，不是持久化字段。JWT 为边界输入，不建立业务 Principal 模型。

| 现有字段 | 类型/格式 | 必填 | 默认值或约束 | 来源/所有者 | 说明与兼容性 |
|---|---|---|---|---|---|
| bot_id / owner_id / user_id | string | 是 | 保持现有 query 约束 | 请求业务目标参数 | 不等同认证身份，无序列化变化 |
| force_upgrade | boolean | 否 | false | 请求 | 透传已有升级流程，不授予首次创建权限 |
| instance.ext.bot_uuid | 非空 string | 已有实例复用时是 | 拒绝缺失、空白、非字符串 | 已持久化实例 | 保持已有有效实例规则 |
| tenant 上下文 | string | 是 | DEFAULT_AVERNET_TENANT | 服务端 middleware | 不从 JWT 或 query 解析，不跨租户遍历 |

关系与不变量：先 Bot 存在，再获取当前租户精确三元组 Caller 实例，bot_uuid 有效才进入连接流程；JWT 任意扩展字段不能影响仓储上下文或这些约束。

### 文件改动范围

路径均相对 `src/backend/`，测试按实际引用收敛，禁止无关扩散。

| 文件路径 | 改动类型 | 改动说明 |
|---|---|---|
| src/agentclaw/community/adapters/http/org/dependencies.py | 修改 | 替换本入口专属认证依赖；其他依赖不变 |
| src/agentclaw/community/adapters/http/middleware.py | 修改 | 删除旧精确路径 principal tenant 特例及过时说明 |
| src/agentclaw/community/adapters/http/expert_chat/router.py | 修改 | 依赖与服务参数、可信 tenant 日志及说明 |
| src/agentclaw/community/api/expert_chat_instance_service.py | 修改 | Service API 契约 |
| src/agentclaw/community/core/expert_chat/expert_chat_instance_service_protocol.py | 按实际定义修改 | 同步 protocol，避免孤立旧签名 |
| src/agentclaw/community/core/expert_chat/services/expert_chat_instance_service.py | 修改 | 移除 app_id/tenant 参数，保留业务保护 |
| tests/community/api/expert_chat/test_app_caller_connection.py | 修改 | 真实签名 ASGI、默认租户、日志 |
| tests/community/core/expert_chat/services/test_expert_chat_instance_service.py | 修改 | 用例参数、Bot/instance 约束 |
| tests/community/endpoints/test_expert_chat_caller_connection.py | 修改 | endpoint happy/error 注册用例 |
| tests/community/acceptance/expert_chat/test_caller_connection_api.py | 修改 | live BaaS JWT 成功与拒绝首次创建 |
| 相关 README/协议说明、middleware/auth regression tests | 按需修改 | 准确说明本入口范围及兼容性；不全量覆盖 gateway schema |

### 验收标准

- [ ] QA 表中的行为全部获得真实 JWT/业务结果证据；不能 mock 掉 verifier 后宣称认证通过。
- [ ] 单测改动行覆盖率 >90%；用 `pytest --cov --cov-report=term-missing` 及仓库 changed-line coverage 门禁实测。已有大模块不能用排除新增路径或降低阈值满足要求；报告同时提供改动文件覆盖与增量分母。
- [ ] Backend SAST/lint、单测、架构/协议检查、endpoint 注册、Singlebox coverage 按仓库门禁通过，无新增 unused import/变量和孤儿方法。

## Review Spec

### 关注点

- endpoint 专属配置拷贝；共享 issuer/audience/解析路径无变化。
- 服务端默认 tenant 覆盖 DI、仓储和异常退出恢复，而非只改 route 参数。
- 无 principals 的 JWT 是授权 BaaS 服务，不伪造应用/终端用户身份；仅此入口允许访问指定 Caller。

### 检查项

- [ ] 关键方法职责、输入输出、错误和副作用与上表一致。
- [ ] 既有模型不变量、序列化、默认值保持；无数据库迁移。
- [ ] >90% 改动行单测覆盖，未达标 REJECT；保留既有模块门禁。
- [ ] 真 JWT 覆盖缺字段/任意字段/坏签名与时间，不使用固定过期时间作为成功凭据。
- [ ] 成功/拒绝日志不泄露 JWT、签名密钥、连接 token、URL 凭据或异常输入。

### 不可接受的模式

- 修改共享 `_ISSUER`、普通 issuer allowlist 或共享解码器来全局接受 baas。
- 调用 caller_from_claims、构造假 app_id、信任 token tenant 或扫描全部 tenant。
- 绕过 Bot/已有实例检查、授予 super_admin 或让未认证请求进入生命周期。
- 新增无需求依据的部署密钥配置、TTL 限制、typ/kid/app_id 校验。
- 未使用 import/变量、本次变动遗留孤儿代码、Python `:` 前空格、block comment 非 `# ` 开头。

## QA Spec

### 测试用例

| 编号 | 用例名称 | 操作步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | 最小 BaaS JWT | 动态生成 HS256 iss=baas iat/exp；省略 principals/app_id/tenant/aud，命中有效实例 | HTTP 200 success=true；原 connection/need_poll |
| TC-02 | 忽略扩展字段 | 对 principals/app_id/tenant 参数化 null、空数组、字符串、数字、畸形嵌套对象；变更/省略 aud | 均可通过认证，DI/仓储一直默认 tenant，无 schema 解析 |
| TC-03 | Header/签名/算法 | 缺空 header、JWT 错段、Bearer 前缀、错误密钥、tamper、none/HS512 | 401 固定响应；无 service 调用 |
| TC-04 | issuer | 缺 iss、gateway、bcs、其他值、错误类型 | 401；仅 baas 可通过 |
| TC-05 | 时间 | 缺 iat/exp、过期、未来 iat/nbf 超 5 秒；有效 nbf、容差内样本 | 错误 401；合法时间通过；不新增 TTL 上限 |
| TC-06 | 未配置密钥 | 清空测试 verifier 配置后请求 | 401，绝不 fallback key |
| TC-07 | tenant 隔离与恢复 | JWT/query 带其他 tenant，DI factory/仓储记录上下文，连续成功和异常请求 | 使用 DEFAULT_AVERNET_TENANT，退出恢复，无跨请求泄漏 |
| TC-08 | Bot 不存在 | 认证合法，Bot lookup 无结果 | 原业务拒绝，未进入连接生命周期 |
| TC-09 | 实例无效 | 实例缺失；ext 缺失/非字典；bot_uuid 缺失/null/数字/空白 | 业务拒绝且不创建；force_upgrade=true 同样拒绝 |
| TC-10 | 已有实例 | 合法实例分别 force_upgrade=false/true | 保留原连接/升级结果、need_poll 与参数透传 |
| TC-11 | 安全日志 | 成功连接带嵌套 token/auth/cookie/secret/session/key 和 URL query；认证/业务异常带敏感 marker | 事件、ID、状态、耗时可定位；所有敏感 marker 不落日志 |
| TC-12 | 其他接口回归 | org gateway/bcs 既有 principal；OpenAPI gateway+aud+tenant；其错误 principal/bad aud/baas | 完全沿用原有允许与拒绝行为 |
| TC-13 | 框架及 live | endpoint happy/error + Singlebox 真实 JWT 已有实例和拒绝创建 | 登记覆盖通过；live 正确，未降低覆盖基线 |

### 前置条件

隔离测试配置和动态测试密钥，固定/可控时间避免边界抖动；默认及另一 tenant 的测试数据；Bot、有效与无效 Caller 实例；配置 reset fixture；测试日志捕获。真实部署验证只使用已授权测试实例，不在报告写入 JWT/连接 token。

## Ship Spec

### 部署目标环境

- [ ] 线下环境：本地测试与 Singlebox 门禁。
- [ ] 预发环境：合并发布后的协议验证；本任务交付 PR 不等于已部署。

### 分支策略

- 开发分支：`feat/baas-caller-auth-rel20260917`。
- 目标分支：`REL20260917`；门禁 base 为该目标 merge-base，不使用 dev。
- PR 描述明确协议变化、消费者 BaaS、密钥配置来源、测试证据及未部署项。其他 gateway/org/OpenAPI 契约无迁移。

### 回滚方案

回滚本 PR 对应提交/发布包，恢复旧应用 Principal 入口；BaaS 调用方同步退回旧协议或暂缓调用。无 DB 迁移，无密钥轮换操作。回滚后 baas-only JWT 将被旧入口拒绝，必须协调调用方版本。

## 2026-09-16 Follow-up: allow first provisioning
User explicitly removes the existing-instance prerequisite for the BaaS endpoint. After JWT authentication and Bot existence validation, call get_caller_connection directly, allowing its normal instance upsert and container creation. Preserve get_authorized_caller_connection guards for ordinary users. This supersedes the existing-instance-only requirements above.
Validation: unit tests for missing instance and missing bot_uuid entering the lifecycle; live acceptance creates the first instance through the BaaS endpoint and then reuses it; retain ordinary-user denial tests. Run affected tests, static/secret checks, push to PR2256 and inspect current-head CI.
