# 任务指令执行前的 task-loop 按需就绪保障

- 日期：2026-10-12
- 状态：DRAFT，推荐方案，尚未获批准或实现
- 参与模块：Backend Task、Skill Center、Devices，BaaS/BCS 执行入口，Engine adapter、AICoding Relay、Teclaw
- 本地代码：merge_to_dev，HEAD 275c4cb7524afd26f324cf12acc2e1a48d1ea0a9，包含用户已有未提交任务链路变更
- 输入：Desktop/2026-10-12-default-skill-on-demand-loading-handoff.md
- 约束：AGENTS.md、CONTEXT-MAP.md、CONTEXT.md、docs/arch/ 四份强制文档、Skill Center/AGENTS.md
- 关联决定：ADR 0002（Track Latest 不修改已发布 Artifact）、ADR 0015 event-driven-desktop-skill-recovery
- 验证状态：本地只读源码核对；无运行时、线上、Corp DI、Relay 或 Teclaw 服务端验证

## 1. 问题与目标

平台默认集合新增 task-loop 后，长期运行的历史 Bot 的 Desired State、运行时激活入口和会话技能快照可能不一致。重启全量 Bot 或要求用户手动更新均不能作为正常业务前置条件。

目标：每次真正执行依赖 task-loop 的任务指令前，确认本次命中的运行实例和本次会话可使用兼容的 task-loop；明确缺失或可修复漂移时按需交付；确认就绪才开始业务执行。不触及没有任务的 Bot。

“就绪”不是保证 LLM 必定遵循全部 Skill 规则。平台可以保证依赖内容、发现入口、启用状态、会话快照和必要脚本/工具满足执行条件；业务协议仍通过执行、上报和验收验证。

非目标：全量扫 Bot、每次重投影全部配置、复制 task-loop 到所有本地资产、另建 Installation 写入口、自动取消用户 exclusion、业务模块拼 Engine 目录、自动迁移 Pool、绕过正式发布约束。

## 2. 必须区分的支持范围

| Runtime | 读取依据 | 允许修复 | 本次建议 |
| --- | --- | --- | --- |
| 生产环境 personal/current workspace | 当前有效能力集合 | 当前绑定下 canonical projector/delivery | 第一阶段支持 |
| service draft | 草稿有效能力集合 | 草稿 projector/delivery | 第一阶段支持，必须携带正确 stage |
| service verify/online | 该发布版本的不可变 Artifact | 仅批准的发布契约允许的操作 | 可探测；不得用 draft activate 自动补线上 |
| 缺内容挂载/无会话刷新能力的旧 Engine | 实际 capability/evidence | 不得虚构热更新 | 明确阻塞与平台修复建议 |

service online 自动吸收新默认能力违反当前 ADR 0002。若需求包括这种实例，必须另提 ADR 和受限平台默认能力更新合同。可能方案是冻结基础 Artifact 加有版本的默认能力 overlay，要求明确白名单、权限、审计、精确内容、重建/扩容/回滚和会话刷新语义。它不是一个新增 stage 参数即可完成的工作。

第一阶段的限制不等于整体需求已完成。如正式发布 Bot 在目标人群内，online 合同必须进入交付范围，不能作为隐含例外上线。

## 3. 备选方案与推荐

### A. 在单 Bot 派发前查文件，缺失则 activate 默认 Set

最小 diff、可做现场验证；复用已有 activate。缺点：遗漏建群/BBS/规划；可能改变集合激活状态；投影完整集合；不能证明同实例、同会话；不支持 online。只作为验证手段，不作为“保证执行前生效”的生产验收方案。

### B. 统一按需就绪服务 + 任务准入门禁 + 执行入口最后校验（推荐）

任务只声明 task-loop 依赖；Skill Center 校验当前授权有效态、探测、必要时调用原交付链路、回读；实际执行入口核验目标实例和会话证据再启动。多类引擎共用协调流程，差异留在运行时适配器中。

成本为中等，依赖执行传输与各 Engine 的会话语义补齐。避免另建技能管理系统，可分切片实施。

### C. 发布时全量推送历史 Bot

不能保证离线、重建和旧会话的执行前状态；大规模写入与恢复负载高。与按需目标及 event-driven recovery 决定不符，不选。

## 4. 职责与新增合同（均为拟议名称）

### 4.1 任务侧：TaskSkillRequirement 与准入调用

明确 task-loop 的稳定资产身份或受治理平台引用、所需协议版本和执行 purpose，不以展示 name 作为唯一身份。task-loop 的 UUID/code 必须从发布配置取得，本地 specs 中的 SKILL.md 不是生产资产注册事实。

入口按结构化业务调用声明依赖，不以任意用户消息包含 `[task-execute]` 作为授权或依赖开关：

- 中心化 owner `[planning]`、启用 Skill 搜推时的 `[search]`。
- single_bot 执行、singlebot_2_group 旁路。
- form_coop_group 自动初始执行，state_machine 显式 start。
- BBS 胜出 Bot 正式执行；竞价只有在其实现依赖 task-loop 时才声明，不能为广播候选做无条件补载。
- Relay 每一棒实际接棒 Bot；不只第一棒 owner。
- intake、discovery、轨迹分析等调用按其实际依赖决定，不能仅因调用了 Bot 就强加 task-loop。

至少 task-loop Relay S1-S6 需要相应协议内容与 scripts/relay_http.py 等必需文件可用。现有 Skill frontmatter 仍为 1.0.0，不能只凭这个字段判断新增 Relay 功能存在。使用不可变版本/content digest 或明确的协议 capability 证据。Repo 源若没有完整包 digest，须补供给侧精确 revision/hash 合同，而不是业务解析全文猜版本。

### 4.2 Skill Center：BotRuntimeSkillReadinessProtocol.ensure_ready

这是 Task 调用 Core 的 Service API；同进程注入，不走 Backend 自身 HTTP。不强制增加公开 endpoint。将来其他调用方需要时再增加薄 HTTP adapter，并复用 authorization/admission/envelope。

请求表达已认证 actor、owner、bot、明确的 runtime intent/stage、依赖身份/协议、deadline、执行请求身份；涉及已有会话时传真实 session identity。owner 不能用任务提交人的身份代替，也不能由不可信 metadata 自行授予写权限。

服务负责：授权/准入、阶段和实例定位、有效态判断、精确依赖解析、协调并发、probe、必要修复、回读、生成可核验的执行就绪证据。

复用 BotCapabilityStateReader 和 SkillVersionResolver，不从展示成员表重建 Effective Skill Set。Reader 的 Installation flush 是现有受治理路径，不能描述成完全无写查询。默认 exclusion/inactive/不适用时返回 NOT_EFFECTIVE，不自动 activate/un-exclude；若普通 active Set 已合法供应同一技能，不应仅因 Default exclusion 而误拒绝。

### 4.3 Runtime probe/session capability

RuntimeSkillProbeProtocol 是 Core 调用可替换运行时 provider 的 Plugin API。优先扩展实际已有 contract seam，避免重建另一套 transport。

探测证据至少包括：稳定技能身份、精确版本/content revision、激活入口匹配、必需包文件可读、启用/可发现状态、目标 instance generation、执行会话采用的 skill snapshot revision、所需工具能力。对工具不支持或证据未知，不能伪装 READY。

各 Engine 在同一 consumer-conformance suite 中覆盖真实成功/失败分支；本地 fake 必须证明 consumer 调用了插件。Corp 实现与部署需另行验证。

### 4.4 Projector/Delivery 的 target-bound 演进

已有 BotRuntimeProjector.project/resolve_plan/apply_plan 只有 bot/owner/scope，没有明确 stage/binding/instance；SkillRuntimeDelivery 内部再次 resolve_for_bot。

新增 readiness 不能只在 probe 时 pin 实例，然后仍调用会重新选目标的 project。需要向 target-bound 投影 seam 显式传递已解析 RuntimeTarget，并保证逻辑 mapping、新旧 fallback、MCP 配置和 Teclaw 整份 Artifact 都使用同一目标。保留既有入口兼容，由 composition root 装配演进后的实现。

RuntimeTarget 的必需事实：tenant/env/owner/bot、runtime kind/stage、binding、instance/device identity 与 generation、必要时 Artifact revision。个人工作区不能默认冒充 service online；binding 不足以区分同 binding 下容器重建。

## 5. 就绪等级与结果

分三层证据，不能互相替代：

1. Desired：当前合法配置允许并供应目标身份/协议。
2. Runtime：本次实例准确内容和激活/启用入口存在，依赖脚本和工具可用。
3. Session：本次运行的会话/新 run 确认使用目标版本或满足所需协议的快照。

只有三层一致才执行。建议内部结果：

| 结果 | 业务执行 | 后续 |
| --- | --- | --- |
| READY | 可开始 | 已存在且会话就绪 |
| REPAIRED_READY | 可开始 | 修复后在同实例回读并确认会话 |
| PENDING | 不开始 | 有界重试/事件恢复 |
| NOT_EFFECTIVE | 不开始 | 依赖缺失或用户排除，配置侧解决 |
| UNSUPPORTED | 不开始 | capability/阶段合同不支持，平台升级 |
| FAILED | 不开始 | 权限、内容冲突、无效配置等明确错误 |

另有 probe 内部 MISSING、MISMATCH、SESSION_STALE、UNKNOWN 分类。权限错误、连接超时、代理路由 404、500 不得转成 MISSING。repairable 必须显式，读不到不等于可创建或覆盖。

结果包括 retryable、脱敏 reason/code、目标/内容/会话证据、实际是否执行过修复、原 runtime_projection diagnostics。复用原 PENDING/DEGRADED 与 issue code，不另造一套同义投影状态或公开错误码。

目标 task-loop 就绪与整体投影状态分开：无关 skill 有 PENDING 可在明确的目标级证据下不阻断，但不能丢失诊断；目标本身、所需 MCP/工具、全局布局或整份 Artifact 原子失败一律阻断。第一版无法可靠归因的整体异常按保守拒绝处理。

## 6. 推荐执行流程

1. 任务指令经过现有权限准入，生成 TaskSkillRequirement 和稳定 execution_request_id。
2. 在有路由决策权的执行层解析真实 RuntimeTarget；Backend preflight 不能代表下游最终命中的实例。
3. 通过 canonical reader 取得该阶段合法有效态，冻结本次精确依赖内容/协议；NOT_EFFECTIVE 不修复。
4. 对选定实例 probe。全部满足则进入会话准入；明确修复需求才进入写入路径。
5. 按真实写入范围取得跨 worker 的投影租约；锁内重读有效态、版本、binding/generation 并再次 probe。
6. 复用 canonical target-bound projector/delivery。完整 resolver-owned skills snapshot，不通过单项旧 bindpath 清目录；按 target Skill 的 MCP 依赖选择 ProjectionScope，不调用 everything()。
7. 同实例回读内容、入口、启用状态和必要工具。异步 apply 只表示已受理时，deadline 内等待 applied revision；不得把 2xx 当完成。
8. 准备本次会话：新会话先 ensure 再创建；旧会话由 Engine 原生 refresh/重建可用快照。活跃 run 不强行刷新、结束、清空历史或重置 session。
9. 实际 run start 前再次校验 instance generation、内容/快照证据和有效态变更。目标替换或 exclusion 变化使旧证据失效，重走准入。
10. 启动同目标业务 run，收到有效 run/group acknowledgment 后才标记 delivery accepted；回收依原 callback/poller 互斥合同。

工程上推荐新增明确版本的 required-runtime-skill/admission 合同，让 BaaS/BCS 到 Engine 的实际启动入口消费依赖并进行最后校验。字段名/endpoint 待消费者和 provider 联合确定；在 metadata 随便塞字段但无人验证，不构成已实现合同。

若旧执行 API 无法 pin 或在实际 run start 处校验，第一阶段只能承诺 best-effort preflight，不能作为“保证同实例同会话执行”的验收通过。必须补执行层能力或缩小到经验证的稳定单实例、新会话路径并明确范围。

## 7. 多模态落点与异常传播

| 调用点 | 门禁位置 | 重点 |
| --- | --- | --- |
| GapBasedPlanStrategy.apply | owner 规划指令发送前 | 规划先于 worker 运行 |
| SearchBasedDispatchStrategy.apply | use_skill 分支发送前 | 纯规则/catalog 查询不补载候选 |
| TaskExecutor._dispatch_single_bot | 每个实际发送/旁路建群启动入口 | 下游最终 target/session 校验 |
| TaskExecutor.form_coop_group | create_group 自动初始 run 之前 | 所有会执行依赖逻辑的成员，不含 human/observer |
| TaskExecutor._dispatch_state_machine | start run 之前 | 已有 group 不能复用陈旧进程内成员证据 |
| bbs_modal_executor | 胜出 Bot 执行/群启动前 | 别让不就绪 Bot 消耗执行 SLA |
| TaskServiceDeliveryMixin.dispatch_node | delivery intent 准入阶段 | 每一棒，代际和租约共同校验 |

任务级公共 helper 负责 requirement/result-to-error 映射；可按 DI 使用受门禁保护的 Task 专用端口/decorator，避免每处复制 Engine 判断。不要改变系统全局 OpenApiBotAdapter，让普通聊天也被强制门禁。

建群 create_group 可能立即执行，而 session_id 是响应后才拿到；因此不能“建群后再 ensure”。应在 BCS 实际初始 run 入口按每个参与者解析其 Engine session 并校验。不能仅设置 state_machine start_initial_run=false 就宣称覆盖 chat/manager_worker。

已有 singlebot_2_group 和 BBS group 执行的宽泛 except 会回退直发。Readiness 拒绝必须是可识别错误，禁止被回退链吞掉；换 transport 仍必须经过准入。不确定建群/发送结果也不能盲目回退制造第二次执行。

中心化已有 HIT_GROUP/retry 场景必须从 BCS 权威 group/membership/session 状态解析，不能依赖 _group_meta 内存。Relay 分布式下一棒需要通过正式 /dispatch 和受门禁 transport，不指望上游 Bot 自觉检查技能。

## 8. 三类 Engine 的具体策略

### OpenClaw

通过现有 layout/mapping/file 合同检查 active 单 Skill 入口；路径由 Engine/layout resolver 决定。补载继续 SkillRuntimeDelivery，新 logical mapping 优先，旧兼容留在原 seam；禁止业务新写 symlink/fallback。

Center ensure 是内容挂载/版本检查，不会安装挂载。MANAGED_SOURCE_MISSING/CENTER_MOUNT_NOT_READY 保留真实诊断。旧实例根本没有内容挂载、旧镜像没有必要 API 时，纯 Backend 无法保证免重启修复。

会话旧快照必须由 OpenClaw/workflow plugin 的权威能力刷新或创建可用新 run；尚未验证会话接口，因此不能承诺既有会话已热更新。

### Claude Code / AICoding

识别实际 Relay vs adapter 部署，复用已认证连接。skills.list/get、installed、mirror 都不足以单独放行；确认 enabled、内容/版本、必要 Claude mirror 与本次会话权限/快照。

AICoding Relay 的会话实现由其维护者交付，不能只改 Avernet 遗留 adapter。部署接口矩阵和镜像与本地源码须对应。

### Teclaw

沿 WholeArtifactRuntimeProjection/ConfigComposer 的完整 apply，不发送只包含 task-loop 的局部 bot_config。非 404 读取错误应保留，读取路径经 path_mapper。

新增 target-bound 应用必须记录正确 runtime bot identity/stage/Artifact revision。明确 apply 是否异步、如何取 applied revision、会话如何刷新。目前这些服务端事实未验证；缺证据则 PENDING/UNSUPPORTED，不假 CONVERGED_READY。

## 9. 并发、配置竞态与缓存

现有 CachePlugin 有 acquire_lock_strict/renew_lock_strict/release_lock，可复用，不把 asyncio.Lock 当跨实例锁，不借用无租期的用户编辑锁。

- 锁 key 至少 tenant/env/owner/bot/stage/binding；具体实例映射可按 instance 区分。Teclaw 若完整更新共享 Bot 配置，锁要覆盖整个 Artifact 写范围，不得只锁 skill_id 或单实例。
- 同一投影并发合并；锁内重新探测；锁丢失/基础设施故障 fail closed。
- 新 ensure 锁若只有自身使用，不能阻止既有 activate、Track Latest、编辑等 writer。所有重叠写入需接同一投影协调 seam，或运行时接受 desired revision 的 CAS/fencing 拒绝陈旧 apply。
- MVP 不引入全局 desired revision 表。重读+目标运行准入可以检测变化，但不能当作严格跨系统原子事务。需要强并发顺序的路径必须补 CAS/fencing 合同，特别是整份 Artifact 更新。
- 第一版不依赖成功缓存：每次实际启动需轻量核验；只缓存同一次并发协调结果或明确“不支持”的短期能力结果。
- 后续若缓存证据，key 包含实例 generation、精确依赖版本、实际会话快照 revision。默认成员/exclusion/版本/binding/session 变化必须失效。TTL 只是优化，不是正确性保证。
- 修复超时后状态未知：下次先回读，不切换旧协议盲重发；原 new-contract unsupported 才允许合同规定的兼容路径。

## 10. 任务状态、恢复、预算与幂等

建议 readiness deadline 默认 30 秒，可配；与既有 60 秒 delivery lease 留裕量。超时后台不继续无约束写入；更长下载交由原恢复能力，当前任务暂停等待。若需要长等待，必须续租 delivery 与 projection 两层租约，不仅延长 HTTP timeout。

等待 readiness 不消耗业务执行重试计数，也不触发执行 SLA/HUNG/BBS 升级。用 delivery carrier 和节点 extend_props 标记阻塞原因，不直接新增业务 Status；准备就绪时才开始 run timing。Relay 状态机若必须提前 RUNNING，至少区分 NOT_STARTED 与执行中，禁止 watchdog 错误回收棒。

当前 dispatch_node 提前 PENDING->RUNNING，且 mark_delivery_intent(delivered=False) 立即回 PENDING；后台每 10 秒 redrive。新增门禁必须处理该语义，不能把所有不可重试错误都放回可无限重试的 PENDING。

拟议增补投递策略：

- 可重试临时故障记录 readiness-specific attempt、next_retry_at、总 deadline；建议 2/5/10 秒带 jitter 的有界退避，不复制永久恢复队列。
- desktop OFFLINE：当前尝试停止，等待 Runtime Ready 事件或明确用户再次派发；retryable=true 不授权每 10 秒对离线实例反复写入。
- NOT_EFFECTIVE/权限拒绝/UNSUPPORTED/冲突：挂起明确 blocker，直到相关配置/运行时变化或显式重新调度，禁止 redrive 忙循环。
- 已成功的 readiness 后业务发送超时：按 execution_request_id 查询实际 receipt/run；不知道是否已执行不能盲目再发。

readiness 幂等不等于业务执行幂等。single Bot 的 message_id 已有可选 seam，应向 send-and-wait 链路透传；确定性 key 由 task/node/run generation/delivery purpose 派生。同一棒 retry 使用同 key，新业务 run generation 使用新 key。BCS 群/initial run/显式 start 也必须有消费者确认的幂等语义，不能仅保证 UUID 稳定。

## 11. 实施切片与责任

1. Task+Skill+Engine 共同现场验证三个未重启测试 Bot，冻结 engine build、runtime kind、stage、绑定、内容版本、会话刷新与源挂载证据。确定 task-loop 的生产身份和协议要求。
2. Skill 团队实现统一 readiness Service API、probe 分类、精确依赖读取、target-bound 投影；复用锁、reader、resolver、delivery。更新 Skill Center README/AGENTS 和 conformance tests。
3. Runtime/BaaS/BCS/Relay/Teclaw 团队补实际启动准入：路由实例、会话 snapshot/readiness evidence、幂等与异步 apply receipt；先交付可验证路径，不虚构统一 reload endpoint。
4. Task 团队按结构化依赖接入规划/搜推/单 Bot/群/BBS/Relay，修复拒绝被 fallback 绕过、启动前 RUNNING/SLA、readrive 无界重试问题。保持结果回收合同。
5. 增加按环境/引擎/精确 Bot 的 validated rollout config，observe-only 先测，不视为保障已开启；小流量 enforce 后扩展。gate 关闭回旧行为只是回退机制，不再承诺 readiness。
6. 若包括 service online，独立 ADR 与发布/overlay 合同及多实例验证，未完成不宣称全部历史 Bot 已支持。

当前 TaskExecutor 文件 983 行，接入以独立 task_skill_readiness/helper 文件和有限调用改动完成，不把新逻辑继续塞入该文件。每个源码文件保持 <=1000 行。

第一版可用的未来文件落点（不是已实现 API）：

- core/skill_center/runtime_skill_readiness_contract.py
- core/skill_center/services/bot_runtime_skill_readiness.py
- 既有 Devices/Engine 插件合同下的 probe/session 适配
- core/task/task_runner/task_skill_readiness.py 或 Task 专用 guarded port
- task_module、skill_center module 和实际 Corp composition root 装配
- task persistence 的有界 retry/blocker carrier 与相关 SQL migration（如需增加查询字段）

## 12. 验收和发布门禁

必须留下“长期运行未重启 Bot -> 本次请求 -> 缺失 -> 修复 -> 同实例回读 -> 会话确认 -> 真正执行 -> 正确任务协议上报”的证据。

| 测试 | 必须证明 |
| --- | --- |
| 已就绪目标 | 零 repair write；继续实际业务 |
| 历史 Bot 缺入口、断链、旧内容 | 按需收敛到合法内容，其他 Skill/MCP 不丢 |
| Default exclusion/inactive/不适用 | 不自动恢复；普通 active Set 合法供应不误拒绝 |
| 代理 404、403、5xx、timeout | 零误判缺失、零越权覆盖 |
| 源内容/挂载缺失、用户实体占位 | 准确阻塞，不删除未知内容 |
| 旧会话/活跃会话 | 正确刷新或明确等待，不清历史/打断 run |
| 20 个跨 worker 并发首次请求 | 同写范围协调；不会旧快照覆盖用户新 exclusion |
| ensure 与普通 activate/Track Latest 并发 | 实際共享协调或 CAS/fencing 生效 |
| probe 后换实例/扩容/重建 | 旧证据不放行新实例 |
| singlebot_2_group/群/BBS fallback | readiness 拒绝不被直发绕过 |
| 规划/search/每棒 Relay | owner 和新接棒者均被覆盖，不只 worker |
| 新 API 写入超时 | 回读/receipt 收敛；不盲 fallback 或重复业务 |
| Teclaw 2xx但异步/失败 | applied revision 与目标回读确认才放行 |
| readiness 等待与持久 intent | 不抢跑 RUNNING/SLA；不可重试错误不 10 秒循环 |
| 同名伪 Skill/旧 1.0.0 内容 | 不以 name/frontmatter 代替精确身份/功能证据 |
| service stages | 不跨阶段，不用草稿完整覆盖 online |

先跑贴近改动的 Task、Skill、Devices 单测、consumer-conformance 与完整 DI smoke；再 architecture、coverage gate、显式 user_id/schema docs、handler-only checks；公开合同若改同步 gateway schema。Corp 装配、OCB 匹配 gitlink、三类实际镜像和现场业务 E2E 单独报告。当前文档工作未执行这些测试。

不采用交接文档末尾建议的 git push --no-verify；实施交付仍遵守仓库现有 secrets/pre-push 门禁，不弱化检查。

## 13. 待确认决策与下一步

1. 历史 Bot 是否包含正式发布 service online？如包含，优先确认默认技能动态附加政策。
2. 三类实际 Engine 的 build、内容挂载和 session refresh/snapshot 能力是什么？纯 Backend 能否实现免重启取决于这些事实。
3. task-loop 的生产稳定身份、精确内容 revision，以及新 Relay 协议的最低兼容要求是什么？

下一步不是先改 send_message，而是由任务、Skill、Engine 三方各选一个未重启 Bot，验证同一次路由实例和会话 readiness 的最小闭环；据结果批准 B 方案的支持范围和合同，再进入实现。
