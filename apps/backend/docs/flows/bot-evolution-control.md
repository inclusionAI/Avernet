# Bot RSI 控制入口：复用系统身份，独立执行

Backend 只拥有用户侧协议适配和既有权限边界；
执行、队列、恢复、数据库仍由 [Evolution](../../../evolution/README.md) 拥有。
独立服务归属遵循 [ADR 0021](../../../../docs/adr/0021-evolution-control-plane-in-apps-evolution.md)。
本文不是已部署或已修复真实登录问题的证明。

## 触发与参与者

Bot 聊天页的 RSI 面板通过共享 `backendRequest` 请求同源 `/openapi`：

1. Gateway 执行原有登录并提供已验证 principal；没有第二套 RSI 登录。
2. Backend 既有 `UserIdDep` 将请求 user_id 与系统身份比对，既有 Bot admission 和
   collaborator gate 决定能否访问 Bot；浏览器参数不是身份凭据。
3. Backend 从相同的缓存 Bot 解析依赖获取稳定 entity、当前 owner；actor 从已验证
   caller 得到。三者不相互替代，不能使用对话中给定的工号或 Bot owner 冒充用户。
4. 薄适配验证 DTO/分页，只转发显式允许的控制操作；绑定 CAS、幂等键保持原义。
5. Backend 签发60秒内部授权回执，绑定租户、BotRef、actor 和精确请求指纹。
   Evolution 验证的是 Backend 已完成的授权结果，不再读取浏览器登录 Cookie。
6. Evolution 持久化 Binding/Run/Job，独立派发 Worker；Backend 发布重启不拥有其进程。
   Worker 的 claim/fencing 密钥是作业隔离协议，与用户登录完全不同。

读操作采用既有 MEMBER 门槛，保存和取消要求显式 MEMBER。启用内置数据通道后，
手动启动要求现有的显式 ADMIN（Owner 通过），与记录 Genome 候选的权限一致；旧应用通道
保留原启动权限。Space 合成的查看/聊天权限不等于 Bot 编辑/操作权限。RSI 不引入新的
Bot 角色，也不修改个人空间初始化、用户昵称或登录实现。

## 入口和部署配置

协议详情、路由、错误码和恢复限制以 [Evolution Service API](../../../evolution/README.md#service-apiv1用户控制面)
为准。策略目录为 `/openapi/v1/bots/{bot_id}/evolution/strategies`，不代理旧无 Bot
目录或任何 Worker 路径。

Backend 配置（`user_config` 下的块）：

```yaml
evolution_control:
  upstream: http://evolution:8091
  timeout_s: 30
  builtin_data_tenant: "" # 配置为与 Evolution 匹配的系统租户后启用
```

默认 upstream 为空，返回503；仅允许部署配置的 HTTP(S) origin，不允许 path、query、
userinfo 或 HTTP 输入指定目标。timeout 范围 `(0, 120]` 秒。前端仅开启
`RSI_ENABLED=true`，继续使用已有 Gateway 路由，不配置独立 Evolution upstream。

服务回执复用平台现有 principal 签名材料。部署须在 Evolution 提供同一材料的受保护
只读文件 `EVOLUTION_BACKEND_PRINCIPAL_KEY_FILE` 以及匹配 Backend 的
`EVOLUTION_BACKEND_TENANT`；与 Worker key 分开管理。固定 issuer/backend、
audience/evolution、typ 和有效期只用于内部调用，不是 IAM Token 或新用户账号。
密钥泄露意味着服务信任失效，需部署侧限制读取者、网络和日志；不得暴露内部请求头。

Evolution 一套数据库只对应一个租户和 profile，重启校验控制面身份；未记录该身份的
旧库升级前须停写备份、离线核对租户，不能把首次固定声明当成历史归属证明。

### 内置手动 RSI 的数据权限

默认不开启。先部署支持回调的 Backend 和 Evolution，再在 Evolution 平台配置直接访问
Backend 的私有 origin（`EVOLUTION_BACKEND_DATA_ORIGIN`），最后把 Backend 的
`builtin_data_tenant` 配置为同一系统租户。该 origin 不经浏览器 Gateway；不要对外代理
内部路由。仅允许受信 Evolution 控制面持有签名材料，不给 Worker；回调使用从现有
principal 材料派生的独立用途密钥，并绑定租户、原操作人、稳定 Bot 范围和精确请求。

启用后，系统用户的内置手动 Run 不要求为 Bot 添加 Evolution 应用授权。Evolution 随
Run 保存实际发起人，恢复时不推断 owner；Backend 在每次读取 Genome／有限会话历史时
重新检查当前 MEMBER，记录该 Run 的候选时检查显式 ADMIN、当前 owner 与交接冻结。
通道不能捕获基线、发布或移动 `active`。权限失效不会回退到应用委托或开发登录。

应用调用、旧 Run 和其他不走内置通道的执行保留原正式服务委托
`PlatformCredential(headers, acting_user_id)`；旧 Run 不回填操作人。任何通道都不把
浏览器 Cookie 作为后台凭据。

## 失败与诊断边界

- 401/403/404：先查 Gateway 登录和原有 Bot 权限/选择器，不提供 RSI 登录绕行。
- 409 `no_genome_baseline`：Bot 可访问，但没有 `active` 基线；需通过独立 Genome 流程建立，不会自动捕获或提升。
- 409 `evolution_app_not_granted`：仅旧应用委托通道的 drift 不可见；不是缺基线。
- Worker 403 `bot_data_access_denied`：内置回调不再获准，例如协作者权限已撤销或 owner 已变更。
- 422：冲突选择器、分页或请求体不合法，不应执行上游写入。
- 503：Backend 未配置 origin/内部信任，或独立服务返回不可用；不是要求用户填写 IAM。
- 502：连接或响应异常；不回显 provider 地址/响应正文/凭据。
- 504：提交结果未知，不自动重试；沿用原幂等键对账，避免重复任务。
- 412/428：保留 Binding CAS；重新读取 ETag 再编辑，不覆盖其他人的更新。

适配器不转发 Cookie、客户端 Authorization、伪造的内部回执、Set-Cookie 或跳转；
不使用环境代理、不跟随重定向。请求上限512KiB、响应上限5MiB。仅保留 ETag 和
X-Trace-Id 等明确允许的响应头。共享访问审计记录实际 actor；Run evidence 提供执行
轨迹，但 source-only 测试不能证明真实沙箱/会话/候选已执行。

“姓名 admin”“missing login cookie”仍应沿原登录链路查证；本次没有真实环境根因
证据，不宣称修改已修复。验证和评审由 Evolution 平台决定；内置回调不启用 apply。

## 源码与验证

- [薄控制适配器](../../src/agentclaw/community/adapters/http/openapi_v1/evolution.py)、
  [系统权限表](../../src/agentclaw/community/adapters/http/openapi_v1/authorization_rules_evolution.py)、
  [配置](../../src/agentclaw/community/di/evolution_control_config.py)、
  [内置数据回调](../../src/agentclaw/community/adapters/http/openapi_v1/evolution_data.py)。
- [Backend 真实路由/权限测试](../../tests/community/adapters/http/openapi_v1/test_evolution.py)：
  共享 principal、跨 Bot 拒绝、actor/owner/entity 分离、不转发凭据、CAS/幂等/超时。
- [完整应用路由框架用例](../../tests/community/endpoints/test_openapi_evolution_control.py)：
  覆盖真实控制路由的授权成功和未登录拒绝；仅替换独立服务的网络边界，
  保留实际应用组装、DI、principal、Bot gates 和回执签发。
- [Evolution 回执/生产生命周期测试](../../../evolution/tests/test_backend_access.py)：
  签名、范围、请求绑定、过期拒绝、重启租户固定和无夹具鉴权回退。
- [本次迁移记录与缺口](../../../../docs/specs/2026-11-17-evolution-service-migration/report.md)。

## 执行所有权与旧路径退役

Backend 默认 container 不安装历史 EvolutionJobsModule/EvolutionRuntimeModule，HTTP
app 不挂载内部 Worker 路由。原 DI 模块移到显式 `di/evolution_acceptance/`，仅验收
脚本/测试主动装配；旧消费环境变量不能把它们重新接回生产默认启动。独立 Evolution
拥有控制库、Runner 和 Worker 协议，避免 Backend rollout 影响执行进程。

平台 GenomePort 适配在 `plugins/evolution/genome_registry.py`；纯领域/Worker 测试
和只读 monitor 在 `apps/evolution`。这些目录变化不改变系统用户身份、Bot 权限或现有
控制接口的对外路径。旧 core import 兼容层不得继续增长业务逻辑。

## 分层接线验收

新增[双服务接线测试](../../tests/community/adapters/http/openapi_v1/test_evolution_service_wiring.py)：
使用真实 Backend 默认权限路由、实际授权回执签发/验证、真实 Evolution HTTP 路由与
SQLite Binding 存储，仅替换系统身份/仓储/平台夹具及 HTTP 传输。
覆盖目录读取、Binding GET/PUT、428/412、actor 与 owner/entity 分离、关闭重开后读取，
以及未登录/无权限/身份不匹配时不访问 Evolution，真实接收端拒绝密钥/租户不匹配。内置通道用例还覆盖缺基线、显式 ADMIN 启动、重开运行库后认领作业、读取文件和会话、
候选写入与验证、实时撤权和 owner 变更；夹具主动禁止应用委托查找。这些测试使用临时
数据库及合成身份，不启动 consumer、申请真实沙箱或修改真实 Bot。

验收须分三层记录，不能互相替代：

1. **源码契约**：上述隔离测试通过。
2. **宿主接线**：Backend 镜像安装同一源码版本的共享 Genome 与 Evolution 包，配置
   内部 origin；前端 runtime-config 在应用初始化前启用 RSI；独立服务健康且拒绝无回执
   控制请求。安装 SDK 不会启动 Backend 内的 consumer。
3. **真实系统会话**：通过既有 Gateway 登录，以实际 Bot 权限读取策略与 Binding，
   再在专用测试 Bot 上按 ETag 保存并刷新确认。不得用手工签发 principal、用户 Cookie
   文件或聊天提供的工号代替。不要点运行或提交 `/runs`。

只重建 Backend/前端不能替代独立 Evolution 的 Platform、匹配的内部信任与租户、
持久化迁移，以及相应的内置回调或旧通道服务委托。宿主缺失平台组装时须明确阻塞；不能降低 production 校验、
覆盖历史 PVC/密钥，或把 public-local 夹具部署为真实 Bot 服务。
