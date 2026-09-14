---
agent: tc-review
status: completed
created: 2026-09-14
iteration: 2
task_name: app-caller-connection
---
# 应用身份 Caller connection 实现 Spec

## 已收敛需求
用户已批准以下方案并授权实现、测试、rebase、push 和 PR。BaaS 直连 Backend，原样透传受信签发方的 X-Avernet-Principal。新增 POST /api/v1/expert-chats/app-caller-connection，原 expert_chat/router.py 平放 endpoint。query 参数 bot_id、owner_id、user_id 必填，force_upgrade 默认 false；成功沿用 ApiResponse(data={instance, connection, need_poll})。不依赖 AuthenticatedUser，不要求 Cookie；user_id 是目标 Caller，不是认证用户。APP+USER 混合主体按 APP 授权，不回退用户/管理员分支。透传 app_id 是签名声明的原应用，并非自动变成 BaaS。

代码基线 GitHub inclusionAI/Avernet REL20260915 @ 5e41bc0d42720b68c1b18c2b3ae5a320c99d33f2。已比较本地 dev 与该 base：org/dependencies、middleware、expert_chat router/instance_service、bot_app_grant、bot_collaborator 的相关源码没有差异。

## 编码 Spec

### 既有链路与最小接线
1. org/dependencies.py 新增 require_app_caller(connection)->VerifiedCaller，await require_org_user_caller(connection)，强制 caller.app_id is not None，否则 MissingPrincipalError。复用缓存的 ordinary verifier；沿用 verify_audience=False，但不弱化签名、issuer、exp、身份结构、tenant 一致性验证。缺失/伪造/过期/无 APP 均真实 HTTP 401。
2. middleware.py 的 AvernetTenantMiddleware 对新精确路径使用 ordinary caller 缓存解析可信 tenant，置于现有 avernet_tenant_scope；无有效身份仍失败关闭于认证依赖。不要使用 OpenAPI 的 audience-sensitive resolve_caller，也不要放开所有 /api/v1。可公开普通 HTTP tenant seam，由 middleware 读、require_app_caller 复用缓存。绑定发生在任何仓储访问之前，并在异常/完成后 reset。保留原路径租户行为。
3. 原 expert_chat/router.py 新 endpoint 负责参数、可信上下文、service 调用、原 ApiResponse 映射和日志。HTTP 身份错误 401 由统一 handler 处理；ChatPermissionError 维持 HTTP 200 + success=false,error_code=403,data=null；意外业务失败 5999，安全固定消息。
4. ExpertChatInstanceService 新增 get_application_caller_connection，Protocol 同步方法签名。输入可信 app_id/tenant 和原 query。先校验当前租户和可信 tenant 一致，才查精确 grant。BotAppGrantServiceProtocol.find(bot_id,owner_id,user_id,app_id) 在请求 tenant 下查询。查不到/撤销均拒绝，不能先读实例凭据。
5. grant.find 的现有契约明确：grant 不替代被委托用户的当前访问权。应用 service 再按现有 chat 规则检查 owner/public='1'/collaborator MEMBER；参考 ExpertChatSessionRuntimeMixin._check_chat_access。使用 CollaboratorServiceProtocol 现有方法，不从 core 依赖 HTTP admission，不用 super_admin，失败关闭。随后 get_authorized_caller_connection(operator_id=user_id,is_super_admin=False,...) 复用已有实例限制和原生命周期；无实例/ext 非 dict/缺失或空 bot_uuid 都拒绝首次创建。
6. 不修改 BaaS、Engine、Relay、Cron、WebSocket、Gateway 路由配置或数据库 schema；无部署任务。原有 force_upgrade/need_poll 行为不变，不产生 session_key。

### 关键方法与模型
| 方法 | 职责/输入输出/副作用 |
|---|---|
| require_app_caller | ordinary JWT 校验后强制 APP，返回 VerifiedCaller 或统一 401；不读取业务仓储 |
| ordinary tenant resolution seam | 从同一个缓存验证结果读 tenant，供 middleware 建立并 reset 上下文；不采用未验证 header/query |
| get_application_caller_connection | app grant + 当前 Caller 访问权 + 已有实例规则，允许后续既有生命周期副作用；任何授权失败先于连接读取/升级 |
| get_caller_connection_for_application | HTTP 参数与响应，应用 service 编排留在 core |

不新增领域持久化模型。VerifiedCaller.app_id:int 来源签名 APP；tenant:str 来源签名且跨主体一致；grant 绑定 (tenant,app_id,bot_id,owner_id,user_id)；instance 仍归属用户 caller_user_id。不得把 app_id 当 staffId。可信 tenant 与运行 tenant 必须一致；grant 和用户权限每次重新检查。

### 文件范围
相对 src/backend：src/agentclaw/community/adapters/http/org/dependencies.py、adapters/http/middleware.py、adapters/http/expert_chat/router.py、core/expert_chat/services/expert_chat_instance_service.py、core/expert_chat/expert_chat_instance_service_protocol.py、core/expert_chat/README.md；必要测试与 spec/report。核心跨域依赖从 owning core/bot_app_grant/bot_app_grant_service_protocol.py / core/bot_collaborator/collaborator_service_protocol.py 导入同一 Protocol 对象；HTTP adapter 才使用 community.api 转导出（遵守 core 不导入 api 的现有架构门禁）；README internal_dependencies 增加 bot_app_grant。现有 DI 自动构造 service；新增必需构造参数时同步全部手工测试 fixture，避免引入可缺省授权绕过。

### 边界日志
新 event=expert_chat.app_caller_connection.request/success/denied/failed。记录 system、direction=inbound、operation、method、route、可信 app_id、tenant、bot_id、owner_id、user_id、force_upgrade、存在的 request/trace ID、HTTP/业务 status、duration_ms。成功记录递归脱敏后的完整非敏感 result，异常记录类别及安全摘要。鉴权依赖拒绝也须有拒绝事件。不要记录 principal、完整 headers、原始异常 str。嵌套 token/Authorization/Cookie/password/secret/key/credential/session 和 URL 查询凭据都不得明文。复用已有日志脱敏设施，不新建大而全框架。

## Review Spec
- 同 router 平放；adapter 不承载 grant/实例编排，core 不导入 HTTP；Protocol 签名严格一致，README 声明跨域依赖。
- 只有用户 token 拒绝；混合 token 不获得 super_admin；BaaS audience 合法 JWT 按 ordinary 策略通过。
- 应用准确授权和实时 Caller 权限检查先于实例访问、BaaS 或连接数据读取；既有实例不豁免 grant。
- 可信 tenant 在仓储读取前生效，完成/错误后不泄漏，下个请求不继承；签名只验证一次。
- 不新增 unused import/变量、孤儿函数、Python colon/comment 风格问题。不降低覆盖阈值或通过排除逃避验证。
- 相关改动文件行覆盖率 >90% 使用 pytest --cov --cov-report=term-missing 实测，记录真实覆盖证据；PR 远端状态独立，不以本地测试替代。

## QA Spec
- 真 ASGI 路由 + 真实签名测试凭据：APP 无 Cookie 成功；APP+USER 成功；USER-only/缺失/伪造/过期/错误 issuer/错误结构/租户不一致 401，无业务副作用；合法 BaaS audience 通过。
- 精确 grant：更换 app/user/bot/owner/tenant，各自拒绝，grant 撤销立即生效；owner/public/collaborator 授权和 collaborator 撤销/异常失败关闭。
- 实例边界：无实例、非 dict ext、缺失/空/空白 bot_uuid 拒绝，无创建；有效实例复用、轮询、强制升级保留原结果与参数。
- 参数：缺必填、非法 bool 422；force_upgrade 默认 false；不接受自报 app_id 作身份。
- 日志：请求/成功/拒绝/失败与非敏感字段都有断言；复杂嵌套 response、URL、异常消息植入测试 secret，捕获全日志验证原值不存在。
- 测试定位：tests/community/endpoints/test_expert_chat_caller_connection.py、adapters/http/org/test_org_user.py、adapters/http/test_avernet_tenant_middleware.py、core/expert_chat/services/test_expert_chat_instance_service.py、acceptance/expert_chat/test_caller_connection_api.py。保留旧 caller endpoint 管理员/本人/跨用户/匿名回归。
- 架构：tests/community/architecture/test_service_api_conformance.py、test_module_boundaries.py、test_http_adapter_layer_is_http_only.py、test_no_fastapi_in_core.py；新增构造依赖时跑 DI 自测与手工 fixture 使用范围。

## Ship Spec
仅 GitHub 源码 PR，无部署/合并/OCB gitlink。worktree 位于 /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/feat-app-caller-connection-rel20260915。完成本地验证和独立 review/regression 后重新 fetch，rebase 最新 origin/REL20260915 为底，核对仅本任务 commits，再 push topic 并创建目标 REL20260915 PR。按 tc-pr 英文 title/Problem/Solution/Validation；读取实际远端 checks 与评论，修复合理失败；pending 不写 PASS。无 schema 迁移，回滚本次 commit 恢复旧行为。
