# OpenClaw/Hermes Pool 启动与兼容桥生命周期收敛（方案二）

## Problem Statement

Skill 模块 Owner 希望在既有 Pool 迁移机制上，可靠支持 OpenClaw/Hermes 新建及重启后的渐进切流，并降低不再被使用的兼容桥对正常运行的干扰。

当前准备期 Repo mount 已可能位于 Pool，但旧引用尚未切换，因此需要 Legacy Repo 地址桥。切换完成后正常映射直达 Pool，Hermes 外部旧 Repo 桥却仍被多处校验强制要求存在。另一个已复现的问题是 Hermes 启动先执行 H0，重建了切换后已退休的 Local 目录桥，使后续 Pool 准备校验失败。

这些问题需要按阶段收敛，而不是全局删除软链、放宽所有文件安全校验或重做迁移平台。

## Solution

准备期保持必要兼容；完成 Pool 切换后，平台映射直接使用 Pool，不再长期依赖 Hermes 外部旧 Repo 地址。升级后的启动/Engine 实现按阶段保护文件现状、幂等退休平台旧桥，并一致更新准备、探测、切换和恢复规则。

Bot 启动与 Pool 迁移继续异步；保留现有任务准入、重试、在途恢复和低峰切流方式。不建设新的状态表、队列或协议版本。Hermes Service Bot 不在产品支持范围，本期不新增相关能力。

## User Stories

1. 作为 Legacy Bot 用户，我希望 Pool 准备期间旧 Repo 引用保持可读，以便尚未切流时仍能正常使用 Skill。
2. 作为 OpenClaw 用户，我希望既有准备与切换行为保持兼容，以便 Hermes 修复不影响我的 Bot。
3. 作为 Hermes 用户，我希望切换后的逐 Skill 入口直接指向 Pool，以便不再依赖旧 Repo 地址。
4. 作为 Hermes 用户，我希望 Pool 状态下再次重启不会重建旧 Local 桥，以便重启不会破坏已经完成的布局。
5. 作为用户，我希望准备失败不新增同步启动门禁，以便 Bot 启动不被额外阻塞。
6. 作为用户，我希望启动成功与迁移完成分别可观察，以便不会将 ACTIVE 误认为 POOL_ACTIVE。
7. 作为已有 Pool Bot 用户，我希望下次启动或收敛能处理平台旧桥，以便不需要集中清理容器。
8. 作为已有 Pool Bot 用户，我希望旧桥已不存在时能够正常通过校验，以便重复收敛是幂等的。
9. 作为用户，我希望退休平台旧桥只删除链接、不删除 Pool 内容，以便共享仓库保持安全。
10. 作为用户，我希望旧地址被我改成实体目录时平台保留它，以便不误删容器内数据。
11. 作为用户，我希望不再使用的旧 Repo 地址指向意外位置时不会卡住正常 Pool，以便旧地址不成为无关阻断项。
12. 作为维护者，我希望此类非预期占位有可定位的诊断，以便仍能解释保留原因。
13. 作为迁移维护者，我希望正在完成切换的启动过程不重建 Legacy，以便已有数据面切换不会被覆盖。
14. 作为用户，我希望 Pool 标记损坏或不可读时文件现状被保护，以便平台不会凭猜测创建另一套 Legacy 数据。
15. 作为维护者，我希望异常标记不会被假报为 READY，以便真正的迁移问题仍能被检测和恢复。
16. 作为运维人员，我希望已认领任务继续按原阶段恢复，以便关闭准入配置不会遗留半完成迁移。
17. 作为运维人员，我希望保留当前任务执行时准入和显式 wake 能力，以便不为极少数延迟事件建设新机制。
18. 作为模块 Owner，我接受低峰切流时少量旧启动任务延迟执行，以便优先保持实现简单。
19. 作为旧 Engine 用户，我希望 Backend 不单方面删除旧 Engine 仍要求的桥，以便配套升级前保持正常工作。
20. 作为运维人员，我希望布局 rollback 能按需重建 Legacy view，以便取消长期桥不等于删除回滚能力。
21. 作为 OpenClaw Service Bot 用户，我希望历史发布物继续按冻结布局恢复，以便当前 Pool 默认策略不会改写历史版本。
22. 作为实现者，我希望共享 Engine 逻辑的修改明确限定到目标引擎与阶段，以便不改变 Claude Code/AICoding 兼容行为。
23. 作为模块 Owner，我希望完整启动顺序被测试，以便不再出现单个 prepare 测试通过、真实调用顺序失败的问题。
24. 作为发布维护者，我希望 Avernet、OCB 和 daas 依赖链共同核验，以便代码通过不被误当成企业部署已验证。

## Implementation Decisions

1. **职责与范围：**Avernet 负责公共 Engine 布局/探测/切换/恢复合同及相关 Backend 消费；OCB 负责企业 Engine 装配和镜像启动准备；daas 的既有 Service 恢复行为纳入兼容检查。按实际必要性修改，不预设每仓都必须有生产代码 PR。
2. **准备期：**保留 OpenClaw/Hermes 所需的 Legacy Repo → Pool Repo 地址桥，不提前退休尚有 Legacy 引用的桥，不改为双挂载或推迟整个挂载流程。Local 准备复制与 Repo 地址桥不是同一语义。
3. **切换完成：**正常平台映射直达 canonical Pool。Hermes active root 外的旧 Repo 地址不再作为长期可用性保证，相关 probe/prepare/finalize 不再强制要求该桥存在。
4. 首次切换完成、已有 Pool Bot 后续启动或正常布局收敛时，由配套执行端幂等退休平台确认的旧 Repo 桥。不存在即无须处理；删除链接本身，不递归操作链接目标。
5. 旧 Repo 地址为用户实体目录、非预期软链或无法安全退休的占位时，保留并记录诊断；对于已经生效且不再依赖该旧地址的 Pool，不因此判定整体布局无效。
6. 上述放宽仅适用于无用的外部旧 Repo 地址。canonical Pool 目录、实际挂载、必要内容、Local 迁移保护以及 active root 内整库入口规则保持原有安全要求。准备期实际使用的 Legacy 引用不适用该放宽。
7. probe 保持只读；桥退休在启动/收敛等执行路径进行。新校验须兼容历史 Pool 状态中仍有预期桥，以及新状态中桥已退休，避免依赖“先删桥才能通过probe、先通过probe才能删桥”的循环。
8. **H0 修复：**在 Hermes 启动准备修改 Legacy Local 或桥之前检查已有激活标记。active/finalizing 不再进入 Legacy 重建；分别按已有完成/恢复阶段处理，不将 finalizing 直接视为完成。
9. 标记存在但损坏、不可读、对象形态异常时，保留文件现状并记录问题，不默认当作首次 Legacy、不自动重建旧内容、不假报 READY。标记缺失时走原 Legacy 准备及其安全校验。
10. 启动判断须贯穿 H0、mount 选择和完整 prepare，避免仅跳过 H0 后又因错误的前置结果选择 Legacy 挂载或重建旧目录。复用现有标记与布局能力，不新增并行事实源，也不取消原挂载失败保护。
11. **异步语义：**Bot ACTIVE 与 POOL_ACTIVE 独立；不新增“迁移成功才允许启动”的同步门禁。迁移失败按原阶段保护与恢复，不在已经 cutover 后擅自回退 Legacy。
12. **首次准入：**保留执行时读取准入配置的现状，不增加启动时间或新启动周期校验。接受有限 deadline 内开放前启动任务延迟执行的极端情况，计划低峰切流；不是严格的“配置开放后必须再次重启”时间保证。
13. 普通 ACTIVE 心跳不新建迁移任务、已 claim 继续恢复、人工 wake 等原有逻辑保持。关闭开关阻止新准入，不取消半完成迁移。本期不重构 TaskQueue。
14. **旧 Engine：**由已升级的配套启动/Engine 实现执行桥退休，不由 Backend 远程单方面清理仍使用旧合同的容器。先具备兼容校验与启动逻辑，再允许退休；沿用镜像发布、切流和版本核验，不新增双布局协议版本或状态表。
15. **回滚：**显式布局 rollback 按需恢复必要 Legacy view。代码/镜像降级与布局 rollback 是不同操作；不承诺删除桥后任意旧镜像直接可用。降级到仍要求桥的版本前，必须恢复其必要结构或使用已验证恢复流程。
16. **产品范围：**只推进 OpenClaw/Hermes 的既有受支持形态。Hermes 不支持 Service Bot；不新增 Hermes Service 路径解析、安装位置、Artifact 或恢复能力，也不为此新增产品门禁。
17. OpenClaw 已有 Service Artifact 冻结语义保持；不批量重打历史发布物，不按当前配置重解释历史布局。daas 未实现 Hermes Pool Service 不作为本期缺陷或阻塞。
18. Claude Code/AICoding 的 Engine 与切流不扩展；修改共享函数时按明确引擎/阶段保持原兼容。Aix 上游实现不纳入开发范围。
19. 不改 Installation 生效事实、SkillSet/Direct 公开行为、Local locator 格式或 API/Gateway schema。不重复方案一的 SkillRuntimeDelivery 重构。
20. 结构诊断可使用现有日志/内部证据，须能定位阶段与保留原因；不为诊断新增表、用户界面或公开响应模型。

## Testing Decisions

1. **主要 seam：**以现有启动准备调用链和 Engine 布局 lifecycle 入口验证完整行为，用临时文件系统与可控 mount 检查替身模拟环境；观察文件、链接目标、标记和阶段结果。不是只测试私有 helper 或某一个独立 prepare 调用。
2. 复现测试先固定 Hermes 真实 H0 → mount选择 → prepare 顺序：有效 Pool-active fixture 直接 prepare 原本成功，前置 H0 会重建退休 Local 桥。修复后完整顺序不得再重建。
3. 覆盖 Legacy 初次准备、READY 尚未cutover、首次cutover、active再次重启、finalizing恢复、标记缺失/损坏/不可读/异常对象形态。每项断言实际路径选择和原内容未被破坏。
4. 准备期验证旧 Repo 单 Skill 引用仍可解析，不将准备成功当成 Pool 已提交；cutover 后验证映射直达 Pool、active内退休入口不重现。
5. 对 Pool 外部旧 Repo 地址覆盖：缺失、预期绝对/等价相对链接（按既有安全识别合同）、意外目标、非空实体、其他对象与退休失败。验证不误删、不跟随链接删除 Pool，非预期占位不阻断本来有效的Pool；关键Pool错误仍被识别。
6. 验证 probe 全程无文件副作用；启动/收敛重复执行幂等，不因桥可选而误接受损坏激活标记或非法实际内容库。
7. 验证显式 rollback 恢复所需 Legacy view；错误阶段不擅自rollback。区分旧镜像兼容和布局回滚，不以单纯存在目录作为成功证明。
8. 保留 Backend 新建/重启唤醒、普通ACTIVE心跳不唤醒、旧任务延迟准入、sticky claim和异步恢复的既有测试；不把现有行为改成新时间门禁。
9. 对 OpenClaw 跑原有prepare/probe/cutover/restart回归；对受支持的OpenClaw Service恢复，验证冻结链接及桥退休行为不变。Claude Code/AICoding共享代码的既有测试用于防回归，不作为新增产品能力验收。
10. Prior art：现有 OCB Pool preparation/entrypoint 测试、公共 Hermes Pool layout/probe/activation 测试、Desktop bridge bootstrap测试、Backend reconcile/claim/task测试、daas Service layout/transition测试。优先复用这些真实入口与fixtures，避免新增测试框架。
11. 三仓按实际变更运行相关窄门禁；公共与Corp装配、旧记录兼容、源/目标发布物处理均需核验。先完成Standards/Spec双轴review及高优问题修复，再执行最终模块门禁，不反复提前跑Backend全量。
12. 后续预发验收须有具体镜像/脚本/gitlink证据，覆盖至少一次首次切换及Pool再次重启；本地临时目录验证不代表真实mount、部署或全量完成。

## Out of Scope

- 实际开启全量配置、修改名单、强制重启、批量DB订正或集中容器清理。
- 新建迁移平台、表、队列、协议版本、启动epoch或严格事件时间门禁。
- 重做mount策略、引入双挂载或统一全引擎物理目录。
- 放宽Local内容迁移安全、忽略损坏Pool标记、允许任意删除用户实体或未知链接。
- Hermes Service Bot、Aix上游实现、Claude Code/AICoding具体切流或新增产品能力。
- 改变历史Artifact合同、重打发布物、承诺任意旧镜像无条件降级兼容。
- 方案一日常交付收口、Local locator演进、Installation/MCP规则或公开API修改。

## Further Notes

- Q1–Q8已逐项确认；用户要求转Spec，访谈结束。本文为方案二执行合同，研究建议中已被用户排除的Hermes Service扩展不得带回实现。
- 目标以 Avernet/OCB `dev` 为准；daas作为相关第三仓核验其`dev`。研究基线为 Avernet `c6a862959538b084649eb8f61d04a187896c5fc0`、OCB `12f8383630ee2aa7bee8091469a00c51d2427572`（gitlink `4fecbb141e1baafd95dce27e2fc960c8c0a6b58b`）、daas `43331d1364090df9703567f4c46e1631bc2832ac`。桥研究当轮Avernet fetch失败，故不能把该固定基线称作实施时最新dev。
- 方案一 [#1999](https://github.com/inclusionAI/Avernet/issues/1999) 的 [PR #2001](https://github.com/inclusionAI/Avernet/pull/2001) 据执行任务回报已合入，merge `e006e5144d36edff8f45b7d98d8b0cc76f608e4d`。实施前刷新各仓，核对该合并及最新gitlink，不复制旧PerDomain逻辑。
- 与较大架构议题 [#455](https://github.com/inclusionAI/Avernet/issues/455) 相关；本期完成不等于整个路径权威重构完成。
- 相关领域约束采用仓库现行术语与ADR；尤其维持已发布Service Artifact的冻结语义。桥退休的兼容边界以本Spec明确取舍为准，不把“旧地址永久可用”重新当成隐含要求。
- 已有研究证据：真实OCB函数组合在临时目录复现H0问题，Pool内容保持；daas纯路径函数验证不支持Hermes Pool Service，后者已按产品范围排除。未在本Spec生成阶段运行真实迁移、修改配置或验证部署。
- 预期Avernet/OCB按实际实现配套提出PR；daas先检查和回归，不预设必须修改。部署必须记录真实gitlink与包/镜像版本，代码合入不等于企业已集成或已切流。
