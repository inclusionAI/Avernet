# OpenClaw Pool-native 创建与 Pool 稳态启动

日期：2026-09-18  
状态：设计决策已确认；实现尚未开始。跨端 wire 映射核验项见 §8。  
适用范围：Avernet、OCB、agentclaw-daas-scripts 的 OpenClaw 链路。

本文是本轮评审的正式需求合同，取代决策记录中被否决或被后续结论覆盖的建议。
代码定位与调研基线独立记录于 [实现核验附录](2026-09-18-openclaw-pool-native-evidence.md)。
逐轮讨论保留于 [决策记录](2026-09-18-pool-native-design-review.md)。

## 1. Problem Statement

Rollout 命中的新 Bot 目前仍可能先进入 Legacy 准备与迁移认领流程，停在
`pool_activating_pre_cutover` 等中间态。新用户没有需要迁移的历史 Skill 数据，
却要承担 Cutover 等待与失败；Service Draft 构建还可能因布局未终态而被阻断。

另一方面，已经完成迁移的 Pool Bot 在普通启动和布局读取时，仍可能被要求提供
历史 `.pool-ready`、`preparation_id`、`migration_generation`。
这把一次性的迁移恢复证据变成长期运行依赖，也使原生 Pool 无法自然复用稳态合同。

本次要同时解决“新建即 Pool”和“Pool 稳态不依赖迁移历史”。
不把存量 Legacy 文件迁移问题伪装成原生初始化，也不重构整个迁移状态机。

## 2. Solution

Backend 在新 Bot 创建时确定并持久化布局；Engine 在首次准备目录之前获得该选择，
直接初始化 Pool。初始化成功后，通过既有启动回报确认布局终态。
整个过程没有 Skills migration claim、Cutover、Local 文件搬迁或迁移任务轮询。

| 对象 | 启动方式 | 结果 |
| --- | --- | --- |
| 命中准入的新 OpenClaw Bot | Pool-native 初始化 | `pool_initializing → pool_active` |
| 未命中新 Bot | 原 Legacy 启动 | 保持既有 Legacy 合同 |
| 存量 Legacy 可编辑 Bot 重启并命中准入 | 原迁移流程 | 原有 Cutover 完成后 Pool |
| 已完成 Pool 的 Bot 重启 | Pool 稳态启动 | 保持 `pool_active`，不重跑迁移 |
| 未重启的存量容器 | 不主动改变 | 不假定它已升级或已切 Pool |
| 历史 Service Published 运行、扩容、回滚 | 冻结 Artifact 合同 | 不被当前 Rollout 改写 |

覆盖 OpenClaw personal、Service Draft 和 desktop。云端镜像升级不能代替 Desktop
本地 Engine 的配置/版本交接；Desktop 是必须交付的独立路径，不能仅靠云端验证宣称完成。

## 3. User Stories

1. 作为命中 Rollout 的用户，我希望新建 Bot 直接使用 Pool，而不是等待历史数据迁移。
2. 作为用户，我希望新建且没有 Skill 的 Bot 也能完成 Pool 初始化，不依赖默认技能先到达。
3. 作为用户，我希望重试创建时沿用首次持久化选择，而不是因配置变化得到不同布局。
4. 作为未命中准入的用户，我希望保持原有 Legacy 行为。
5. 作为存量 Legacy 用户，我希望正常重启后安全迁移已有文件，而非把目录当空白新建。
6. 作为尚未重启的用户，我希望旧容器继续正常使用，不被新合同强制升级。
7. 作为已完成 Pool 的用户，我希望重启不再依赖历史准备文件仍然存在。
8. 作为用户，我希望重启期间保留已有 Pool 内容和布局选择。
9. 作为用户，我希望初始化失败被明确报告，而不是静默回退 Legacy。
10. 作为用户，我希望初始化期间已有产品操作权限和限制不被额外扩大。
11. 作为用户，我希望允许提前写入的 Local Skill 始终写向所选 Pool 权威位置。
12. 作为用户，我希望个别 Skill 源文件、软链或 MCP 异常不阻断布局初始化。
13. 作为用户，我希望共享仓库挂载异常有诊断信息，但保持现有 non-critical 启动策略。
14. 作为 Service Draft 用户，我希望布局确认后可按现有流程构建 Artifact，不要求迁移身份。
15. 作为已发布 Service Bot 的用户，我希望重启、扩容、回滚仍消费该版本冻结的布局。
16. 作为 Desktop 用户，我希望得到同样的 Pool 目录语义，而不是照搬云端 OSS mount 假设。
17. 作为运维人员，我希望重复启动回报幂等，不重复认领迁移或修改历史迁移信息。
18. 作为运维人员，我希望过期实例的回报不能推进当前实例的布局状态。
19. 作为运维人员，我希望初始化成功但回报丢失时，可通过正常重启重新确认。
20. 作为运维人员，我希望关闭 Rollout 只停止新增准入，不撤销已经持久化的 Pool 选择。
21. 作为维护者，我希望 Native 和已迁移 Pool 使用同一个稳态标记合同，不新增来源字段。
22. 作为维护者，我希望 finalizing 或 DB 未完成提交的迁移继续使用原恢复机制。
23. 作为维护者，我希望通过增量部署兼容旧回报和旧标记，不要求全量重启或批量修数据。

## 4. Implementation Decisions

### 4.1 职责与模块边界

| 模块 | 本次责任 | 不承担 |
| --- | --- | --- |
| Bot 创建与布局 Repository | 创建事务内保存布局选择、复用准入决策、幂等回读 | Engine 物理路径与目录操作 |
| Skills Pool 领域服务 | 初始化状态确认、CAS、迁移与稳态分流 | HTTP 解析、镜像发布 |
| 生命周期与部署适配器 | 透传逻辑布局、接收当前实例完成证据 | 猜测路径、伪造完成证据 |
| Engine 布局实现 | 物理根选择、初始化、统一稳态标记、布局读取 | Rollout 准入与 DB 业务决策 |
| OCB 启动与镜像装配 | 最早启动分流、mount 诊断、避免创建 Legacy 入口 | 重复定义 Backend 状态机 |
| daas 启动脚本 | 后续目录准备遵守布局、既有回报携带实际完成证据 | 新可靠投递系统 |
| Service Artifact 消费者 | 接受 Native 终态、不要求迁移身份、保持冻结语义 | 用当前 Rollout 改写历史 Artifact |
| Desktop 启动适配 | 本地配置及实际初始化证据交接 | 依赖云端镜像或必须有 OSS mount |

继续使用已有 Service API / Plugin API 边界与 DI。HTTP adapter 仅翻译回报，
状态判断属于领域服务；物理文件操作属于 Engine/启动实现。
共享代码修改不得顺带改变 Hermes、AICoding、Claude Code、Teclaw 的业务合同。

### 4.2 创建时的持久化合同

复用 `ac_bot_skill_layout_state`，不新增表、`layout_origin` 或 `provisioning_mode` 列。

| 字段 | 新建命中 Pool 时 |
| --- | --- |
| scope | 沿用当前 env/entity/Bot 隔离范围 |
| `active_layout` | `pool`，表示已选定的内容权威布局，不代表启动完成 |
| `phase` | 新增枚举值 `pool_initializing` |
| `target_layout` | `NULL`，不是迁移目标 |
| `migration_generation` / `preparation_id` | 不生成迁移身份 |
| `layout_contract_version` | 当前受支持的布局合同版本 |
| `rollout_evidence` | 复用现有准入证据，不引入已废弃 batch 依赖 |
| `data_plane_cutover_committed` | 不作为 Native 完成条件，不伪造 Cutover |

Bot 与 layout row 必须在同一数据库事务中提交，成功后才分配外部运行实例。
不能先提交 Bot、后补布局行，否则重试可能把新 Bot 当历史 Legacy。
失败必须回滚；不要用 TaskQueue 的非事务 enqueue 冒充这个原子性保证。

准入配置明确不存在/未命中时走 Legacy；读取失败、解析失败或无法可靠作决定时，
创建失败且不启动实例。已保存选择的重试不重新决定布局。
历史无 layout row 保持 Legacy 默认解释，不能靠缺行或缺 generation 推断 Native。

### 4.3 启动选择与初始化

云端复用 `AGENTCLAW_SKILLS_LAYOUT` 及已有布局合同版本参数，不新增物理路径 wire。
Backend 必须在首次相关目录准备或 mount 选择之前传入；只在启动后补 env 不满足合同。
Desktop 在现有本地部署/启动配置中承载同一逻辑选择。

Native 初始化必须：

1. 在 Legacy 准备、bridge、本地内容复制前分流。
2. 按 Engine resolver 初始化 active root、Pool Local 等必要根。
3. 不生成平台 Legacy Local/Repo 根和兼容 bridge；后续 setup/default 初始化也不能重建它们。
4. 不拷贝或迁移已有用户内容，不创建迁移 quarantine，不调用 Cutover。
5. 原子持久化统一完成标记，随后产生布局完成证据。

仅有启动参数 `pool` 不能证明磁盘是空白：已迁移稳态、未完成迁移及冻结 Artifact
恢复各有合同，不能把它们无差别当 Native 初始化。遇到矛盾的保留入口不自动删除内容。
启动失败沿用生命周期失败表达，不静默降级 Legacy。

### 4.4 最小布局完成条件

布局完成仅证明：实际 layout/engine 与持久化选择一致；Engine 管理的必要根初始化成功；
固定 Legacy 保留入口不存在；完成标记已持久化。根级检查不递归检查 Skill 内容。

以下不是晋升 `pool_active` 的阻断项：逐 Skill 是否存在/是否可读、软链是否悬空、
Scanner、SKILL.md、hash、默认技能投影全成功、MCP 权限/配置/同步。
Repo/Center 检查真实挂载事实并报告诊断，但仍 non-critical，不新增挂载成功硬门禁。
不以“目录存在”冒充已挂载，不递归列举共享仓库。Desktop 按实际交付类型诊断。

### 4.5 启动回报与数据库确认

增量扩展现有 status callback，不新增 URL。新的可选完成证据语义包含：
实际 engine、实际 layout、layout contract version、根级初始化完成；并关联当前启动实例。
不得包含 Backend 用来重新判断目录正确性的物理路径清单或迁移身份。
精确字段名和现有实例标识的 wire 映射须通过 §8 核验，不能假称现有 DTO 已支持。

Backend 必须先验证回报鉴权及当前 Bot/绑定/启动实例，再做条件更新：

- 预期 `active_layout=pool` 且 `phase=pool_initializing`，证据匹配：CAS 到 `pool_active`。
- 当前实例重复成功：幂等，不重新迁移、不覆盖历史证据。
- 过期/释放实例、scope/engine/contract 不匹配：不能推进布局。
- 旧 alive、旧 SUCCEEDED、BaaS 发布 ACTIVE、后台命令派发成功：不构成布局证据。
- 存量迁移阶段：继续原恢复流程，不能借 Native 确认跳过迁移提交。

布局状态与 Bot/Binding 生命周期不是同一个事实。旧回报仍沿用原生命周期合同，
不得仅据 Bot ACTIVE 推断布局完成，也不得把本次改造成全局健康门禁。
BaaS 主动 alive 快路径须遵守相同原则。布局确认不等待只能在 Bot ACTIVE 后触发的
Skill/MCP 投影，避免循环等待。回报先后顺序的具体接线在 §8 核验。

沿用现有发送方式，不增加 ACK 等待/持久重试文件、补偿队列、确认轮询或新任务模块。
Engine 已完成但回报网络/DB失败时，允许 DB 暂留 `pool_initializing`；Service Build
可能因此仍不能进行。通过正常重启重走确认恢复，已有 marker 不能使本次回报被跳过。
DB 写失败不能吞掉并声称完成。本期不承诺新增可靠投递或自动最终收敛 SLA。

### 4.6 Native 与已迁移 Pool 共用稳态合同

统一 `.pool-active` 的稳态最小语义为：

- `engine` 与实际引擎一致。
- `layout_contract_version` 受支持且匹配。
- `activation_state=active`。
- 同时满足已确认根级结构合同。

不新增 `initialization=native` 字段。稳态有效性不要求 `.pool-ready`、
`preparation_id`、`migration_generation` 或历史 mappings。
旧 marker 的额外字段继续兼容，无批量回填、主动重写或迁移历史删除。

| 状态 | 处理 |
| --- | --- |
| 可信 Pool 启动且 marker 不存在、根级结构可成立 | 幂等初始化或补写最小 marker，保留已有 Pool 内容 |
| marker 损坏、engine/contract 冲突 | 明确失败、保留现场，不覆盖、不退回 Legacy |
| marker 为 `finalizing` | 原迁移恢复合同，继续校验原身份和恢复证据 |
| marker 已 active，但 DB 仍处于迁移阶段 | 完成原 DB 迁移事务，不能直接走 Native 晋升 |
| 普通 probe/读取 | 只读，不顺手修复 marker |

已 `pool_active` 的 Bot 重启不退回 `pool_initializing`，启动中/失败由既有生命周期表达。
迁移中的 lease/generation、quarantine 与原恢复检查保留。
不能把所有现有 probe 放宽：稳态布局判断与迁移完整性校验需要按用途区分。

### 4.7 Consumer 传播与 Service Artifact

必须审计布局 Repository/枚举、Runtime 路由、Local 读写、reconcile 任务与事件监听、
Engine probe、OCB startup、Service Build/Artifact 验证、Desktop 启动。

Native 不入 migration reconcile；队列中已存在的消息也不能要求 Native 伪造 generation。
已迁移 Bot 的历史清理工作不因此被无条件丢弃。
Native 在初始化期间已选择 Pool：所有原合同允许的文件操作使用该权威布局，不回写 Legacy。

Service Build 继续要求布局终态和既有 Artifact 合同，但 `pool_active` 不得因缺少
迁移证据而被拒绝。只有 `pool_initializing` 时，不伪造终态让构建通过。
本次不取消必要的 Snapshot exclusion/冻结 exact 引用，也不增加新的逐 Skill 完整性门禁。
Published restart/scale/rollback 继续使用目标 Artifact 的冻结 layout/image，不读当前
Rollout 来改写它；Repo/Center 的 OCB mount ownership 不变。

### 4.8 发布顺序与兼容

1. 先部署能够读取新 phase、旧/精简 marker、接收可选回报字段的兼容消费者。
2. 发布并核验新 Engine/OCB/启动脚本；Desktop 单独核验其实际版本和配置链路。
3. 再使 Native 创建生产方生效，先测指定 owner，再扩大环境范围。

已开启的 owner Rollout 不等于 Native 已安全就绪。实现交付需明确生产方在哪个部署
阶段生效；可拆读写部署，不默认新增永久开关，也不能依靠混部碰运气。
未重启旧容器继续兼容；新建/产品重启使用新镜像的前提是模板确已更新且无旧 image 覆盖。
普通进程重启不能当作镜像升级。

故障止损停止新增准入/Native 写入，保留已持久化 Pool 选择与内容。
不把回退到不认识新 phase/精简 marker 的旧 Backend/旧镜像作为恢复方案。
关闭 Rollout 不自动回滚已经 Pool 的 Bot。部署、切流、数据订正均需单独授权。

## 5. Testing Decisions

优先使用现有高层边界验证可观察行为：创建 Service API + Repository 事务、
启动配置 consumer、既有回报入口、Engine 布局操作、Service Artifact 构建。
不要仅测试内部 helper 或以 mock 返回 READY 代替真实初始化链路。
不新增专用测试接口，不把私有目录规则复制进 Backend 的断言实现。

| 编号 | 用例 | 必须观察到的结果 |
| --- | --- | --- |
| T01 | 命中新建 personal/Service Draft/desktop | Bot 与 Pool initializing 同事务；无迁移任务 |
| T02 | Bot/layout 任一步 DB 失败 | 原子回滚、无外部实例分配 |
| T03 | 准入缺失、未命中、读失败、坏配置 | 前两者 Legacy；后两者明确创建失败 |
| T04 | 创建重试时 Rollout 已变化 | 复用原布局，不重复创建或重新决定 |
| T05 | 历史无 layout row | 仍解释 Legacy，不补造 Native |
| T06 | 全空 Native 第一次启动 | 直接 Pool；无 Legacy 根/bridge/copy/cutover |
| T07 | 后续 setup/default 初始化运行 | 不重新创建 Legacy 入口 |
| T08 | 初始化期允许的 Local 操作 | 使用 Pool 权威根，不依赖先完成默认投影 |
| T09 | Repo/Center mount 失败、单 Skill/MCP 异常 | 有诊断，布局不因此失败 |
| T10 | 保留根被异常内容占用、marker 损坏 | 明确失败且保留内容，不静默清理/降级 |
| T11 | 当前实例有效成功回报/重复回报 | 幂等 CAS；迁移 generation 仍为空 |
| T12 | 旧实例、释放绑定、错 scope/engine/contract | 不推进布局，权限边界不放宽 |
| T13 | 旧 alive/SUCCEEDED、BaaS 主动 alive | 不伪造布局完成，不破坏旧生命周期 |
| T14 | callback 与 alive 不同到达顺序 | 无投影循环等待，布局只由有效证据确认 |
| T15 | Engine 成功后回报丢失/DB 失败再重启 | 内容保留，重新回报；不因已有 marker 跳过确认 |
| T16 | 旧 active marker，无 ready/preparation 历史 | 已完成 Pool 正常稳态运行 |
| T17 | marker 缺失，可信 Pool 启动且根合法 | 幂等补写；普通 probe 不补写 |
| T18 | finalizing 或 DB 未提交迁移 | 原严格恢复，不误走 Native/稳态旁路 |
| T19 | 已 Pool 重启/重启失败 | 布局 phase 不重置；生命周期正确表达可用性 |
| T20 | Native 收到历史 reconcile 消息 | 安全分流；不生成迁移身份 |
| T21 | 历史迁移清理任务 | 原职责保留，不被稳态简化无条件跳过 |
| T22 | Native Service Draft 构建与预发/正式发布 | 终态可构建，不因无 generation 失败 |
| T23 | Published restart/scale/rollback | 冻结 layout/exact refs/image 合同不漂移 |
| T24 | Desktop 新建/重启/Repo 下载方式 | 同样 Pool 语义；不要求云端 mount |
| T25 | 存量 Legacy 重启且有 Local/Repo/Center | 原迁移保留内容与投影，最终 Pool |
| T26 | 旧 Engine/旧回报/其他 Engine | 原合同兼容；共享 helper 不引入误识别 |
| T27 | 关闭准入与分阶段部署 | 已选 Pool 不回退，未兼容消费者不接收新写入 |

既有测试先例包括布局 Repository/租户隔离、Skills Pool DI、Engine layout probe/
activation 合同、Desktop layout activation、Service Build fresh layout 与 OCB prepare 测试。
新增 callback wire 需覆盖真实 schema/鉴权入口；跨模块测试必须验证实际 provider 被调用。

本地顺序：相关窄测/合同/架构/DI → Standards/Spec 双轴评审 → PR CI 与本地相关全量。
预发验证另行执行：owner 小范围中新建 personal、新建 Service Draft、Desktop；
存量 Legacy 重启、已有 Pool 重启；Local/Repo/Center 添加移除；Service 构建、发布、回滚。
每次留存代码/gitlink/Engine 基线、布局 DB、marker、目录及实际接口结果。
不能用 CI 通过替代跨仓集成、部署或运行验收。本文件没有宣称这些用例已经执行。

## 6. Out of Scope

- Hermes/AICoding/Claude Code 的 Native 切流或 Teclaw StoreRef 改造。
- 删除存量 Legacy 迁移能力、整体重写迁移状态机、数据批量订正。
- 新 layout 来源列、新迁移 batch、Native 专用来源 marker。
- Skills 专用可靠回报、后台确认轮询、投递补偿队列。
- 全局 Bot 健康模型、全部生命周期重排、额外产品编辑限制。
- 新的逐 Skill 内容安全/完整性检查或 mount 成功硬门禁。
- 强制全容器重启、自动环境全量 Rollout、镜像固定策略大改。
- 本轮暂停的逻辑文件接口/ZIP 传输重构及 Backend 全面去路径化。

## 7. Further Notes

接受的取舍：不新增可靠回报成本，低概率投递失败由正常重启恢复；布局终态不等于
所有 Skill/MCP 已生效；新版本兼容旧记录，不承诺旧二进制能读取新写入。

无需新增数据表/列，但 phase 枚举、DTO、consumer、生成合同及相关文档仍须同步。
数据库物理约束需在实现时复核，不能把“不新增列”误写成“不涉及持久化合同变更”。
旧字段和旧标记按兼容读取处理，不要求用户先订正数据或完成全量重启。

## 8. 实现前必须闭环的接线核验项

这些是尚未完成的代码合同映射，不是重开用户已确认的取舍。
实现者应先提交接口/调用关系核验与测试，再据此接线；如只能靠扩大范围实现，需反馈。

1. **当前实例身份**：现有 device_id/token/绑定对 ARCA 与复用 binding 的 BaaS restart
   分别能证明什么；复用现有 sandbox/publish 等身份，验证过期回报拒绝，不能假设 device_id
   天然代表一次启动。具体 wire 字段尚未确定，不新建一套身份系统。
2. **BaaS/回报顺序**：后台命令、主动 alive、容器 status 之间如何传递证据；现有
   SUCCEEDED 不携带布局信息。接线不得用 ACTIVE 代替证据或等待激活后投影才能确认。
3. **Desktop**：部署配置如何在最早本地初始化前到达、完成证据如何经既有通道回传；
   当前独立 payload/credentials 不是已实现的 Pool-native 合同。缺客户端能力需纳入
   明确交付依赖，不可静默缩成仅云端实现。
4. **分阶段生效**：已开启 owner Rollout 时，明确读兼容、新 Engine 与 Native 写入
   的部署边界和实际版本，不依赖同时上线的隐含假设。

以上四项未闭环前，可以进行实现准备，但不能宣称端到端合同已实现或可以直接全量切流。
