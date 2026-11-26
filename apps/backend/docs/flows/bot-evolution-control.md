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

### Mutation Scope 的冻结与回调校验

新策略版本可声明 `mutation_capability`，Binding 用 `mutation_scope` 选择 Persona、
Local Skill 或联合更新。Evolution 在提交 Run 时取能力、请求、父版本既有内容/归属和
平台/父版本 policy 的交集并冻结；空范围在预留预算前拒绝。历史版本与 Run 不回填。
Evolution 提交前、Backend Registry 写入前各自校验真实操作及每个 Skill 成员，新增/删除
整包不在新范围中。内置候选回调的签名 DataProof 同时绑定冻结 scope 摘要；丢弃或扩大
范围返回403，不能只靠重新签名请求指纹扩大权限。现有逐次权限检查仍执行。
详见 [Run 契约](../../../evolution/src/avernet_evolution/core/evolution_run/README.md#frozen-mutation-scope)。

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

同一测试文件的 `test_rsi_local_skill_candidate_has_file_diff_and_requires_review` 用合成快照验证
Local Skill 的候选链路：显式创建含 Skill 的基线，绑定权限仅为 `skills/quality-check`，由 Worker
执行测试策略，提交带会话证据的 `skill.update`。报告指向 Genome Diff；包下只有 `SKILL.md`
有文件级变化。独立文件规则验证候选新增内容和回归项，随后产生 `verdict.finalized`、
`gate.decided` 与关联同一 Run 的评审单；批准只记评审结果，不触发 Apply，`active` 不变。
这是文件内容验证，不是 Bot 行为评测或真实沙箱验收。测试策略与 profile 不注册到默认平台，
也不改变 Skill 激活、上传或捕获逻辑（实时捕获仍只纳入已激活的直绑 Skill，不捕获 SkillSet）。

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

新增[三范围接线矩阵](../../tests/community/adapters/http/openapi_v1/test_evolution_mutation_scope.py)
使用同一通用策略分别授权 Persona、Local Skill、两者联合，覆盖候选、成员 Diff、配对验证、
门禁、人工批准及 scope 篡改拒绝。模型/质量方法是确定性测试夹具；批准不创建 promotion，
不调用 Apply，active 不变。部署模型、真实行为评测和 Worker 镜像不在该测试的证明范围内。


## Bot model completion callback

The private `POST /api/internal/evolution-data/v1/bots/{bot_id}/models:complete`
callback is excluded from public OpenAPI. It uses the existing signed data-proof
channel; it is not a new browser endpoint or an application-credential export.
A complete persisted Run scope is required. Backend verifies the request
fingerprint, tenant/Bot/entity/current owner, actual operator's live explicit
ADMIN permission, mutability and runtime readiness before every inference.

The strict [wire DTOs](../../src/agentclaw/community/adapters/http/openapi_v1/schemas_evolution_models.py)
accept only messages, an optional model and output-token limit; URLs, keys,
headers, sessions and tools are refused. Backend forwards to the selected
runtime's `POST /api/engine/models:complete` (200s relay timeout), validates the
text/model/positive token result and rejects an explicit-model mismatch. It
never reads Bot configuration files or receives the provider credential.
The [Engine contract](../../../../engine/adapter/docs/model-completion-api.md)
owns live routing, authentication support and stateless behavior.

The [bridge tests](../../tests/community/adapters/http/openapi_v1/test_evolution_models.py)
exercise real Backend admission plus signed Evolution callbacks, persisted
operator scope, revocation/owner changes, strict input/output and charge/token
accounting. The Engine transport is replaced; this is not live Bot deployment
or real model/quality acceptance evidence. Existing Bot images without the new
capability must be upgraded independently; no automatic fallback is performed.

### 策略最终输出

`GET /openapi/v1/bots/{bot_id}/evolution/runs/{run_id}/strategy-output` 沿用已登录
principal、Bot MEMBER 读取权限、内部请求签名和 Evolution 的 Run 归属检查，返回
`{output: object | null}`，并禁止响应缓存。它不是任意 artifact 下载入口。
完整 schema 和空值语义由 [Evolution Run 契约](../../../evolution/src/avernet_evolution/core/evolution_run/README.md#protected-final-strategy-output) 维护。

RSI 侧栏选中 Run 后独立读取策略输出：显示模型修改或不修改的最终理由、建议文件内容、
可展开的脱敏最终返回文本及截断标记；纯文本渲染，不执行模型 HTML。运行中刷新，终态停止
周期下载；读取失败不冒充“没有修改”。缺失历史输出明确显示无法回溯。不额外记录隐藏推理、
提示词或原始输入会话；最终返回可能复述输入，基础脱敏不等于完整 DLP，仍应视为敏感内容。
元数据事件接口不携带此正文。生效需要新版本 Worker 和控制面、
Backend、前端一起部署；旧策略版本和已有 Binding 不自动迁移。

## Explicit human authorization and separate Apply

[ADR 0027](../../../../docs/adr/0027-human-promotion-authorization-is-not-evaluation.md)
adds an intervention path independent of the record-only risk-review approval above.
Current ADMIN requests `POST /candidates/{candidate_id}:authorize-promotion` with
`Idempotency-Key`, exact revision/parent and a reason. Evolution atomically saves
`basis=human` and `promotion.human_authorized`; neither Bot nor refs change.
The original authorizer separately requests `POST /candidates/{candidate_id}:apply`
with `authorization_id`. Backend admission and the purpose-bound callback recheck
current access, provenance, parent and drift. An ordinary Run proof cannot Apply.
Only an explicitly assembled `Platform.manual_delivery` enables this path; Workers
never receive it. Read the candidate report to recover authorization and authoritative
Backend delivery status; retry the same request after an unknown result, not a new key.
Known reject/safety failures, incident, freeze/kill and stale baselines remain blocked.
Existing databases require the nullable `ac_genome_promotion.authorization`
column; follow the [one-time schema upgrade](../../src/agentclaw/community/core/bot_genome/README.md#explicit-human-rsi-promotion).
Fresh schemas already include it. Automated benchmark evaluation is still
a separate integration, not provided or simulated by this human authorization.


### Delivery recovery and receipt interpretation

- A lost authorization response must reuse its exact key and body, or recover by
  reading the candidate report. A lost Apply response must reuse the same
  authorization ID. The page never automatically retries a write.
- An existing delivery receipt is recovered before checking a parent/drift that
  successful delivery itself changed. This is not a second Apply or a permission
  bypass: current Backend admission still applies.
- `applied` and `applied_partial` are historical settlement receipts, not assertions
  about the current active ref or live content. Check current refs and drift.
  Partial advances refs but is not complete acceptance. Failure does not advance
  refs, but may still require inspecting live partial effects.
- A crash after the durable promotion claim can leave `pending` without an Apply
  ID. Replaying that authorization returns the claim and does not restart Apply.
  Inspect the actual Apply records/lock and service logs first. Existing stale
  pending cleanup occurs when a later promotion successfully starts after the
  five-minute threshold; it is not a watchdog or a guaranteed recovery deadline.
  Do not change keys, delete claims or clear locks to force an uncertain write.
- Once an authoritative `apply_failed` receipt exists, repair the cause and
  recheck current refs/drift before requesting a new human authorization. The
  previous authorization remains immutable and only recovers its own failure.

## Fresh-Bot Local Skill checklist

This sequence is generic; it must not depend on a particular Bot/Skill name or
synthetic acceptance prompt. The cold-start path is covered by the
[signed HTTP flow tests](../../tests/community/adapters/http/openapi_v1/test_evolution_manual_promotion.py),
not by a newly deployed live Bot in this closeout.

1. **Assembly:** deploy matching Backend, Evolution controller, digest-pinned
   Worker/registered strategy and frontend versions. Enable the RSI UI, internal
   signed callbacks and the explicit `manual_delivery` adapter. Preserve the
   Evolution database, signing keys and tenant identity across restart. Upgrade
   existing Backend schemas before exposing manual delivery.
2. **Runtime:** ensure the actual selected Bot device can read files, export
   sessions and Apply; a healthy Backend or a Bot metadata readiness flag alone
   is insufficient. Bot-owned inference requires an Engine image supporting
   `models.complete` and compatible configured routing/authentication. Supported
   OpenClaw inference is currently HTTPS OpenAI-compatible `openai-completions`
   with literal API key/Bearer auth; other forms fail explicitly. See the
   [Engine limitations](../../../../engine/adapter/docs/model-completion-api.md).
   Configure an explicit model/provider through the deployment/provider seam;
   never put credentials or provider URLs in Binding params.
3. **Content before baseline:** upload and activate an existing direct Local
   Skill package through the normal Skill APIs. Confirm it appears in captured
   inventory as local-origin; SkillSet, repository and Center packages are not
   editable Local Skill targets in this flow. Complete first-chat initialization
   before capturing the baseline where possible. `BOOTSTRAP.md` may legitimately
   disappear during initialization; that remains real drift, not an exemption.
4. **Initial baseline:** `POST /openapi/v1/bots/{bot_id}/genome/baseline:initialize`
   captures the current Bot and creates initial refs without Apply. With no
   baseline, the RSI page retries configuration/submission once only after the
   specific `no_genome_baseline` response. It does not overwrite an existing
   baseline. For later uploads or changes, inspect the Diff and explicitly
   reconcile the baseline; do not silently ignore `out_of_band_change`.
5. **Registration and Binding:** use an enabled registered NEW strategy version
   whose `mutation_capability` allows the intended local-origin Markdown update.
   The page's default manual example grants **persona only**, not local Skills.
   Read existing bindings and save with the current ETag; do not replace other
   entries blindly. The following is one Binding object for a hypothetical
   existing `example-local` package; replace the strategy/profile with actual
   registered versions and align budget with deployment ceilings/model charge.

   ```json
   {
     "id": "local-skill-manual",
     "strategy": "example/behavior-refinement@1.0.0",
     "trigger": {"manual": true},
     "parent": "active",
     "allowed_genes": ["skills/example-local"],
     "mutation_scope": [
       {"gene": "skills", "origin": "local", "include": ["example-local"], "operations": ["update"]}
     ],
     "verification_profile": "rules-file@1",
     "budget": {"max_usd": 0.5, "max_wall_clock_s": 300, "max_tokens": 24000},
     "params": {},
     "auto_promote_ceiling": null
   }
   ```

   The registered capability, Binding and captured inventory intersect to freeze
   scope at Run submission; configuration cannot grant broader capability.
   `params` must satisfy that strategy's schema; it is not a universal model
   override field. Selecting only Skill scope is intentional: it does not grant
   Persona edits, new files, package add/remove, publication or activation.
6. **Evidence and candidate:** create ordinary conversations exercising the
   Skill, then submit the Binding. Inspect the final strategy output and member
   Diff. No recent exported session evidence, no authorized Markdown, or a
   justified model no-change decision can legitimately produce no candidate.
   Do not modify production behavior to force a candidate for acceptance.
7. **Human intervention:** inspect exact candidate/parent/revision and reason,
   authorize without Apply, refresh and verify the Bot is unchanged, then
   separately confirm Apply as the original authorizer with current ADMIN access.
   Authorization and Apply both refuse known safety/evaluation failure, stale
   parent and drift. Missing scanner evidence stays `not_run`, not `pass`.
8. **Post-delivery:** inspect the authoritative receipt, current active/previous,
   live Skill contents and drift; run independent behavioral checks. Record
   partial/failure honestly. The original `pending`/`inconclusive` verdict remains
   unchanged. This proves delivery, not benchmark quality or automated RSI.

Next production milestone: evaluate frozen parent and candidate in isolated
sandboxes using versioned public/business benchmarks, collect independent
regression/safety feedback, and pass the normal platform promotion gate. Human
intervention must remain a separate audit basis rather than the normal evaluator.
