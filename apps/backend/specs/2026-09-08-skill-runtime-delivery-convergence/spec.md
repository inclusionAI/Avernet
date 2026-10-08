# Skill Runtime 日常交付收口（方案一）

## Problem Statement

Skill 模块 Owner 希望在 OpenClaw/Hermes 尚未全量切 Pool、Legacy 与 Pool 将继续共存的前提下，降低日常能力操作对布局迁移细节的依赖。

当前 PerDomainRuntimeProjection 同时处理 Skill/MCP 调度、布局状态、Legacy 分流、协议选择、设备推送验证和结果解释。迁移知识因此暴露在日常投影策略中，后续维护容易把迁移的严格约束与普通交付的降级语义混合。

本期需要隔离这些知识，而不是重新实现 Pool，也不是将所有历史设备强制切换到新协议。

## Solution

将日常文件型 Skill 交付收口到一个内部模块：接收已经解析好的 Plan 与明确退休映射，内部选择原有交付路线并解释结果；PerDomainRuntimeProjection 保留 Skill/MCP 调度、异常隔离和结果组合。

公开接口、控制面事实、设备协议、迁移流程及用户可观察行为保持兼容。复杂逻辑从旧位置迁出，不能新增一层透传后仍让调用方维护同样的判断。

## User Stories

1. 作为 Skill 模块 Owner，我希望日常投影策略不理解迁移阶段，以便布局兼容集中维护。
2. 作为 SkillSet 用户，我希望重构后激活与停用仍保持原有控制面行为，以便不影响产品操作。
3. 作为 Direct Skill 用户，我希望单项激活与停用的结果保持兼容，以便调用方无需适配。
4. 作为 Legacy Bot 用户，我希望纯 Local Skill 仍走原交付路线，以便旧设备无需支持新 Mapping endpoint。
5. 作为 Legacy Bot 用户，我希望空集合处理仍保持原有清理行为，以便重构不改变已有能力清理语义。
6. 作为 Pool Bot 用户，我希望逻辑映射仍使用正确的实际布局，以便切换过程不会读回已退休的 Legacy 目录。
7. 作为 Repo Skill 用户，我希望 Repo 映射继续使用现有协议与来源，以便不改变共享内容交付。
8. 作为 Center Skill 用户，我希望 UUID 和精确版本保持不变，以便一次交付不会重新解析到另一个 latest。
9. 作为用户，我希望已提交的 Installation 不因设备暂不可达被回滚，以便保存的能力意图仍然有效。
10. 作为用户，我希望保留逐 Skill 的错误、原因和建议，以便知道哪个能力尚未收敛。
11. 作为用户，我希望实体目录和外部软链仍按现有规则保留，以便不会因重构丢失容器内维护的内容。
12. 作为调用者，我希望明确退休的映射仍被处理，即使 Skill scope 未显式开启，以便不会留下应退出的受管链接。
13. 作为 MCP 用户，我希望 MCP-only 操作不新增 Skill I/O，以便保持现有操作成本。
14. 作为用户，我希望 Skill 交付异常不阻断原本应执行的 MCP 半链路，以便保留现有异常隔离。
15. 作为 Track Latest 消费者，我希望复用已有 Projector 路线，以便无需为本次重构新增调用方式。
16. 作为启动恢复链路维护者，我希望已接入 Projector 的入口自然复用新模块，以便不复制第二套交付逻辑。
17. 作为 Service Bot Owner，我希望发布前普通投影受益于收口，同时 Artifact 构建仍遵循原合同，以便历史版本不漂移。
18. 作为 Teclaw 用户，我希望继续使用 Whole Artifact，以便不会被强加文件型 probe 或布局要求。
19. 作为下游运行环境维护者，我希望继续通过既有设备运输层交付，以便保留认证、设备绑定和实例亲和性。
20. 作为 Pool 迁移维护者，我希望迁移与回滚继续使用原底层 Runtime，以便严格文件保护不与日常 best-effort 混合。
21. 作为维护者，我希望本次不增加有效态查询、设备调用和自动重试，以便纯职责收口不引入额外性能成本。
22. 作为实现者，我希望以同一套输入输出合同验证迁移前后行为，以便发现分流、状态及明细方面的回归。

## Implementation Decisions

1. **输入已定稿：**复用现有 ResolvedSkillPlan 与 retired_mappings，不新建专用输入 DTO，不要求调用方新增 source_layout、probe 或目录参数。
2. Plan 携带的 Bot 身份、元数据、已解析资产、逻辑映射和 Legacy Runtime capability 可在内部使用；只读消费，不修改 Plan 或其 Bot 元数据。
3. 新模块不重查 Installation/SkillSet，不重新解析 latest。上游 Reader、VersionResolver 和 Resolver 的事实所有权不变。
4. 允许新模块内部读取现有 layout state，并沿用 runtime_uses_pool_paths 对中间阶段的判断。配置不是实际布局事实，不能仅用 active_layout 替代既有选择规则。
5. **分流已定稿：**Legacy、仅 Local 或空集合且无 retired 时，保留原 project_skills/DeviceSync 路线；Pool、包含 Repo/Center 或存在 retired 时，保留原 Mapping 路线。当前实际 Engine 身份解析不变。
6. 协议协商、Center probe/ensure 前置步骤、Mapping publish/verify、既有顺序、请求形状、超时及 BEST_EFFORT 选择保持不变。批次 ensure 与逐项降级的差异不在本期修改。
7. **输出已定稿：**返回现有 RuntimeProjectionResult，仅表达 Skill 分量。Legacy bool、Mapping publish/verify 结果及其明细由新模块解释，不暴露给 PerDomain 再次解析。
8. 沿用现有状态、错误码、逐项信息、中文文案和建议。未知异常保留日志与 PerDomain 现有兜底，不新增自动重试、Installation 回滚或响应 schema。
9. PerDomain 保留 Skill/MCP scope、retired 强制交付条件、MCP claimed/released 过滤、两条半链路异常隔离及结果组合。它不再直接读取 layout Repository或编排、解释 Skill 设备协议。
10. **复用范围已定稿：**新模块仅用于 PerDomain 的日常 Skill 投影。已通过 Projector 的 SkillSet、Direct、Track Latest、启动恢复和发布前投影自然受益，不逐入口新增接线。
11. Teclaw 继续经 Registry 使用 WholeArtifactRuntimeProjection；Pool cutover/rollback/recovery 继续使用原 SkillsPoolRuntimeProtocol；Artifact 独立 probe、capture 和验证不改。
12. 优先提取职责明确的内部模块，复用现有 Runtime/Repository 和 composition root。不新建插件框架、通用 DTO 或额外配置开关。具体命名与内部拆分按现有惯例决定。
13. 原位置的相关实现必须删除或迁出，不能保留第二份布局选择和结果归一逻辑。永久模型属地的大规模整理不夹带在本期。
14. 下游实现继续通过 DeviceAdapterTransport 绑定交付。保留 Legacy DeviceSync 和延迟 composer 装配；不直接依赖专有 concrete、不自行拼设备 URL、不绕过认证或实例亲和性。
15. 不修改公开 HTTP、Engine wire、底层 Runtime Protocol、数据库 schema、持久 locator 或历史数据。Gateway 生成合同不应因本期发生变化。

## Testing Decisions

1. **主要测试 seam：**优先通过现有 BotRuntimeProjector 的公开调用验证已装配的 PerDomain 行为，用现有 Runtime/Legacy capability fake 观察交付请求和结果。上层 command 测试确认 Installation 与公开响应未回归；不要以私有方法名或文件移动作为行为测试目标。
2. 内部交付模块需要隔离测试时，经其交付入口验证结果，不为测试访问内部辅助方法另建接口。架构门禁可独立检查 PerDomain 不再直接依赖 layout/协议编排，这是职责验收，不代替行为测试。
3. 覆盖 Legacy Local-only、空集合、Pool、Legacy Repo/Center、混合 corpus、retired 非空与空、缺失布局记录及 cutover 中间阶段。断言原分流和 source_layout/wire 保持兼容。
4. 覆盖不变 scope、MCP-only、Skill-only、retired 强制交付和完整投影；断言无新增设备调用或重复有效态查询，Skill 异常不跳过应执行的 MCP。
5. 覆盖 Center 精确版本、v3 能力、ensure 失败、设备不可达、publish/verify 逐项结果、用户实体与外部链接、合法源缺失以及异常兜底。验证状态、逐项错误、文案、重试属性和结果组合保持既有兼容性。
6. 覆盖 Teclaw 路线隔离，无文件型模块调用、无新增 probe。保留现有 Whole Artifact 行为。
7. 下游集成测试通过公共 Consumer/Runtime 与替代 `DeviceAdapterTransport` 绑定、mock 网络验证 method/path/body、调用顺序、owner+bot、超时及设备亲和性不丢失。检查 Legacy clean/bindpath 与 lazy composer 兼容。
8. Prior art：现有 SkillSetManagement/Projector 行为测试、Runtime projection message 测试、SkillsPoolRuntime 测试、下游 DeviceAdapterTransport/desktop 测试、Engine Mapping/Router 合同测试。优先复用其 fixtures，避免新增同义测试框架。
9. 先补当前行为的缺失测试，再提取实现；跑贴近改动的单测、合同、DI、架构与兼容门禁，之后 Standards/Spec 双轴 review，修复高优问题后执行最终门禁。完整 Backend 套件不在每个增量反复运行。
10. 新发现的既有缺陷单列证据与影响，不悄悄改变产品语义，也不把错误行为提升为长期领域合同；若影响本期验收，明确报告并解决范围判断。
11. 合并与企业 gitlink 集成后，预发验证 Legacy/Pool 正常交付和错误明细；区分本地、CI、评审、集成、部署和真实 Runtime 证据。

## Out of Scope

- 开启全量 rollout、修改名单、强制重启或对历史 Bot 执行数据迁移。
- 重写 Pool claim、cutover、rollback、quarantine、cleanup 或取消必要文件保护。
- 为所有 Legacy 设备强制启用 Mapping endpoint。
- 改变 Center ensure 的整批阻断方式、增加重试或性能优化策略。
- Local 逻辑 locator 演进、git_path 数据转换、上传/删除内容模块重构。
- Artifact/共享目录排除、挂载脚本、路径规划协议和镜像改造。
- 新增 AICoding/Claude Code Runtime 能力或推进其我方 Pool 迁移。
- 改动 Installation 事实、SkillSet 规则、MCP 策略或 Teclaw Whole Artifact。
- 承诺第一步后整个 Backend 已完全不感知目录；Factory、内容与 Artifact 的耦合仍为后续工作。

## Further Notes

- 2026-09-08 用户完成四轮设计确认，并要求转为 Spec。本 Spec 取代先前范围草案中未决措辞；研究报告和 HTML 是解释材料，不能扩大本期范围。
- 目标分支为 Avernet `dev`。实施前重新刷新基线并复核相关变更；下游以精确集成提交为准，不能以分支状态代替集成或部署验证。
- 下游专有组装与外部 Runtime 的真实部署行为不由本公开仓源码证明；需在对应集成与运行环境中独立验证。
- 后续的 Pool rollout 与本 Spec 的日常交付职责收口分属不同阶段；本变更不开启切流、不执行迁移。
- 与 [Avernet #455](https://github.com/inclusionAI/Avernet/issues/455) 相关，但只实现日常交付收口，不能据此关闭整个路径权威重构议题。
- 预计一个 Avernet 主 PR；下游按实际补测及集成需要配套。不拆多个 stacked PR，不预设下游零改动。
- 验收完成的结构性标准：PerDomain 只调度和组合；布局兼容及 Skill 设备结果解释有唯一维护位置；所有相关用户可观察合同保持兼容。
