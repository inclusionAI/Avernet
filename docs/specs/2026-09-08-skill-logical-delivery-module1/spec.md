# #455 模块一：日常 Skill 逻辑交付

状态：设计已确认，待实现。日期：2026-09-08。

关联 Issue：[inclusionAI/Avernet #455](https://github.com/inclusionAI/Avernet/issues/455)。
执行子 Ticket：[#2025](https://github.com/inclusionAI/Avernet/issues/2025)，已内附定稿Spec快照，可脱离本地工作目录领取。
Owner：Skill Runtime；参与模块：Avernet Backend/Engine、OCB 企业适配与启动脚本。
三仓目标分支均为 `dev`，按实际依赖决定是否修改 daas。

## 1. 文档权威与范围

本文汇总 Q1–Q15 的最终决定，供后续实现与验收使用。
[决策账本](review-decisions.md)保留讨论、反例和被替代建议；发生措辞冲突时，以本文的最终合同为准。
先前 HTML/研究稿中的“Backend 最终不感知 layout”与“Legacy Local-only 永久使用 DeviceSync”不再是目标。

遵守[架构规则](../../arch/arch.rules.md)、[CI 规则](../../arch/ci.enforce.md)、
[Context Boundary](../../arch/context-boundary-format.md)及[Protocol 测试规则](../../arch/protocol-contract-tests.md)。
领域用语见[根词汇表](../../../CONTEXT.md)。保留
[稳定 Skill 身份](../../adr/0001-stable-skill-identity-across-versions.md)与
[Track Latest / 历史 Artifact 冻结](../../adr/0002-market-and-space-skills-track-latest.md)的既有决定。

本模块只收敛日常文件型 Skill 交付。用户最新决定暂缓Hermes启动/桥生命周期配套，该配套不再纳入#2025范围或验收依赖；Hermes日常Apply能力与兼容测试仍保留。
不以本文授权自动编码、合并、部署、强制重启或 Pool 切流。

## 2. 核验基线

| 仓库/集成点 | 固定提交 |
| --- | --- |
| Avernet `github/dev` | `3d41e45170e23dbc27295bcfec53925a1932a589` |
| 本轮详细代码研究版本 | `dd8a8d139240a14a47a3e02ef587c4568bea7fd9` |
| OCB `origin/dev` | `e7fabd281daddffba88dc0c938dc8444868ff952` |
| 上述 OCB 的 `ocb-public` gitlink | `dd8a8d139240a14a47a3e02ef587c4568bea7fd9` |
| daas `origin/dev` | `43331d1364090df9703567f4c46e1631bc2832ac` |

已核对关注的 Skill Center、Skills Pool、Engine Mapping、布局和传输文件，最新公共基线相对研究版本无差异。
这不是部署证明。实现启动时重新获取三仓最新 `dev`，审计有关差异，不把旧 feature gitlink 覆盖到新基线上。

## 3. 问题与目标

当前 `SkillRuntimeDelivery` 已集中日常交付，但仍有三个问题：

1. `_build_skill_plan` 提前构造路径型 Service，即使最终使用逻辑 Mapping。
2. 新旧结果仍主要按物理 target 关联；截取 basename 会丢失精确内容与退出操作的区别，空 items 还可能掩盖整体失败。
3. 普通 Mapping 通常有 publish/verify 两次调用；含 desired Center 时通常还包含 probe 和 center/ensure。日常最佳努力交付不需要长期固定成四步远程流水线。

本模块目标：

- 同一 Reader、同一有效态计划、同一对外 RuntimeProjectionResult。
- 新 Runtime 正常日常交付只需一次 Backend→Engine 业务 Adapter 调用。
- Backend 表达逻辑 Skill 及布局策略；Engine 解析物理目录、执行并报告逻辑结果。
- 旧 Runtime 无需整体重启；历史 DB 无需订正或迁移。
- 明确部分失败，不以文件漂移阻止全部正常条目执行，不因结果未知切换写协议。

一次调用不表示整个系统只有一次网络/文件 I/O，不包含 MCP、设备连接解析等独立成本。
它也不表示全部文件瞬时原子更新、永久收敛或用户无法在调用后修改容器。

## 4. 长期所有权

| 职责 | Owner |
| --- | --- |
| 有效 Skill、精确版本选择 | Backend 的 Installation Reader / 既有 Version Resolver |
| layout 配置、目标布局、迁移状态、本次交付布局选择 | Backend |
| 各 Engine 的物理根目录及逻辑引用到目录的解析 | Engine |
| 软链执行、文件保护、现场观测 | Engine |
| Runtime 结果对业务的归一化和安全展示 | Backend Delivery |

**Backend 物理路径无感，不等于 Backend layout 配置无感。**
布局选择不是模块四将来要删除的控制面职责。业务层不散落 layout 分支；Delivery 使用统一规则。

本模块继续使用 `runtime_uses_pool_paths` 选择 `source_layout`，包括现有 cutover 中间态和 `data_plane_cutover_committed`。
不能仅使用 `active_layout`，也不能直接把 `target_layout` 当作当前已生效布局。
Engine 不因看到 marker、目录或 Center mount 就擅自切流或重写控制面配置。

## 5. 目标调用结构

```text
Command / Listener / Track Latest
  → BotRuntimeProjector
  → Installation Reader → Version Resolver → RuntimeProjectionResolver
  → 完整逻辑 Skill Plan
  → SkillRuntimeDelivery
      ├─ 新内部 apply → Engine 检查、解析、应用、返回结果
      └─ 集中兼容 Adapter → 既有 Mapping 或 DeviceSync
  ← 统一 RuntimeProjectionResult

MCP / Passport：保持现有 scope、前置校验和交付边界
Teclaw：保持 Whole Artifact / StoreRef
Pool STRICT 与 Service Artifact：保持各自原合同
```

### 5.1 计划与依赖

- 纯 `ResolvedSkillPlan` 不携带路径型 service；构造该计划不无条件调用 `SkillSetServiceFactory.create`。
- 新文件型 apply 和已有逻辑 Mapping 不需要旧路径型 service。
- 旧 DeviceSync 兼容分支确实执行时，才准备所需 service。
- `scope.mcp=True` 时，计划阶段仍按需准备现有 MCP consumer；身份模式、Effective MCP、Default CLI 及完整计划校验必须先于任何 Engine 写入。
- Teclaw 所需 Whole Artifact service 继续存在；不承诺所有引擎的 Skill-only 请求都零 Factory。
- 保留共享 Factory 的 Local 内容、参数、Artifact 等其它消费者，不为此重写它们。
- 实现选择与装配在 Composition Root；不新增通用 Service Locator，不用可空 service 隐藏必需依赖。

### 5.2 合同类型

- Projector/Delivery 的业务调用为库式 Service 边界，不依赖 HTTP 类型。
- Backend→Engine 为外部能力的 Plugin/传输合同；HTTP 状态及旧端点识别在传输适配中归一，不散落到业务 Command。
- Engine HTTP Router 是薄适配；Engine 插件合同与实现负责应用能力。复用既有 planner、Center 检查和软链 helper，不另造一套执行算法。
- 相关 Protocol、DTO、DI、Context Boundary 和 conformance tests 随实现一起更新。

## 6. 新日常应用合同

独立内部入口采用 `POST /api/skills/mappings/apply`，不增加产品 OpenAPI。
旧 `publish / verify / probe / center/ensure` 接口继续保留原消费者合同。
新入口专用于本轮 BEST_EFFORT；不能把 STRICT 迁移顺带切到它。

### 6.1 请求语义

请求包含完整 `mappings`、`retired_mappings` 及控制面选定的 `source_layout`。
一次完整快照选择一条写路线，不把 Local/Repo/Center 拆成多条独立清理 active root 的请求。

| Mapping 类型 | 逻辑字段 |
| --- | --- |
| Local / Repo | `corpus`、`relative_path`、`link_name` |
| Center | `corpus`、`skill_uuid`、精确 `sc_version_number`、`link_name` |

复用现有字段含义与路径安全约束；新入口不接受靠绝对 `source/target` 驱动的日常主合同。
`relative_path` 是内容相对引用，不等于运行时名称。Center 使用内部 UUID 与精确版本，不用外部 skillCode 或 latest/current 目录。

“待退出旧映射”是某个旧运行时入口的精确退出意图，不是资产 Offline/Retirement。
不能只凭 link_name 删除与本次精确引用无关的内容。

历史 Local locator 必须保持兼容，不重写 DB，不以搬文件解决本次重构。
兼容选择需要的信息不得预先不可逆丢弃；历史数据差分测试发现真实反例时修正适配，不以订正数据绕开。

### 6.2 Engine 执行

在一次 Engine 操作内完成：请求支持与格式校验、布局解析、必要 Center 检查、BEST_EFFORT 应用、必要局部确认及结果汇总。
内部复用函数，不通过 HTTP 调用自己；不强制再跑一遍完整 verify 扫描。

Center ensure 的独有检查不能删除：挂载状态、精确版本和可读 `SKILL.md`。
这些是只读检查，不在 apply 中下载、Scanner 扫描或物化整个版本。

- Center 挂载/精确内容不可用：对应项 PENDING，其它正常条目继续。
- 用户实体、外部链接冲突：保护原内容，按既有原因返回 DEGRADED。
- 真正的独立 retired 项：只处理匹配的受管旧入口。
- 非法请求和路径安全约束不放松，不能把所有异常都包装成成功。
- 按现有幂等语义重复应用同一意图，不新增分布式锁、能力表、幂等任务表或新的重试调度系统。

### 6.3 Center 同名替换

V1→V2 且 V2 未通过必要检查时：

```text
DB 仍期望 V2
V2 APPLY → PENDING
V1 旧同名入口 → 保留，不先独立 unlink
其它条目 → 继续处理
```

V1 的退出交给 V2 替换处理；不能在过滤掉失败的 desired 后仍让对应 retired 删除 V1。
也不能把未发生的退出报告为成功。保留旧入口不承诺其内容当前一定可读。
这是新 apply 的显式策略，现 helper 并不自动保证；不顺带改变 Local/Repo 既有缺源悬空软链策略。

### 6.4 结果语义

结果包含整体 status、逐 Mapping 结果及不能关联请求成员的 Runtime 问题。
逐项回显逻辑 Mapping、`action=APPLY/RETIRE`、`status`、`code`、`retryable`。
Backend 根据请求侧关联找到 Skill ID/名称，不从物理 target 截取 basename。
无需新增持久 Mapping ID，不让 Engine 查询 Skill 数据库。

- 同名 V1 RETIRE 与 V2 APPLY 可区分；由替换覆盖的退出需明确关联该 APPLY 结果，不补造独立删除成功。
- 同一逻辑输入去重与结果覆盖必须一致；不能用任意少于输入数量的 items 宣称完成。
- 不属于请求的现场漂移按 Runtime issue 表达，不伪造 Skill 身份。
- 物理路径若保留，仅作诊断 evidence，不作为新主链业务匹配键，也不直接透出敏感设备信息。
- 最终 wire DTO/内部类型命名在实现中保持一个权威定义并同步合同测试，不额外建设结果能力协商流程。

| 情况 | 整体状态 |
| --- | --- |
| 存在降级项 | `DEGRADED` |
| 无降级项，但存在暂时不可用或结果未知 | `PENDING` |
| 应有结果完整，处理完成且无异常 | `CONVERGED` |

沿用 `DEGRADED > PENDING > CONVERGED`；保留全部问题，整体 DEGRADED 不表示没有待重试项。
HTTP/envelope 成功不能替代上述判断。非空请求缺项、非法结果、响应丢失不得因 items 为空而归为成功。

`mappings=[]` 且无 retired 是正常可表达的空意图，但不是清空整个 active root 的命令。
有明确 retired 时仍须执行。旧健康受管残留、未知实体和外链的处理保持既有保护合同，不扩大清理范围。

## 7. 旧 Runtime 兼容

### 7.1 有界回退

新入口正常完成时不额外 probe/verify。只在以下明确兼容分支中增加调用：

1. apply 返回可识别的标准路由缺失响应，而非任意 HTTP 404。
2. 使用同目标设备上下文做 health 确认，检查状态及期望 Engine。
3. 符合条件后进入旧兼容 Adapter。

传输需保留结构化状态和响应信息，不能仅凭 `DeviceAdapterEndpointNotFoundError` 类名或搜索异常字符串判断。
新 handler 的源缺失、权限和执行失败不得伪装成 route-not-found。
如公共 Router 存在而具体 Corp 插件尚未支持 apply，必须明确在任何应用副作用前返回可识别的不支持结果；普通 501 不自动代表可回退。

代理/HTML/非标准 404、health 不符、参数拒绝、401/403、5xx、超时及畸形响应不自动换写协议。
执行结果未知保留 PENDING/诊断；非法请求仍按原错误合同处理，不一律吞成成功。

已接受的取舍：标准路由缺失与 health 是正常可信路由下的兼容旁证，不是严格的历史未写证明。
同设备上下文也不等于已证明同进程实例；本期不解决代理伪装标准响应或请求间实例更换的极端场景。
不新增能力 DB/cache，不把 `/openapi.json` 读取加入默认兼容依赖。

### 7.2 兼容路线选择

- apply 缺失仅表示不支持新日常命令，不等于不支持 Mapping v2/v3。
- 已有可信且覆盖完整请求的 Mapping 能力证据时继续 Mapping，包括 Local-only。
- 缺少积极旁证时保留既有可用分流：原 Mapping 不降级为不能表达它的 DeviceSync；原 Local-only 兼容行为不新增 Pool READY 强制门禁。
- 旧 Center 路线仍保留必要的 probe/ensure/publish/verify；不能因新命令已内聚检查就删除旧检查。
- 旧结果仅在兼容 Parser 中处理。保留 published/valid/整体失败信息，不以默认空 items 覆盖失败，也不伪造旧 Runtime 没给的逐项证据。
- 已开始执行或结果未知后，不通过切换协议重投来“试着成功”。后续正常重试仍按当前有效意图与现有机制处理。

兼容代码的退出需另行证明目标 Runtime 覆盖与历史消费者退出；本期不删除旧接口，不设置强制重启期限。

## 8. 不变边界与非目标

- Installation/Reader 的有效态语义、Owner/Bot/tenant/env 隔离、Direct/SkillSet 规则不变。
- 日常 Runtime PENDING/DEGRADED 不回滚已提交 Desired State；本轮不重建 mutation guard 或补偿机制。
- Skill-only 不额外执行 MCP/Passport；MCP scope 的前置校验不能推迟到 Skill 写入之后。
- Teclaw 保持 Whole Artifact/StoreRef，不调用文件型 apply，不建设 Hermes Service。
- Pool STRICT cutover/rollback/recovery 与 Artifact fresh observation/验证保留，不能用日常单次调用目标删除它们。
- 不改 Service Artifact schema、冻结版本、挂载与恢复合同。
- 不改 Local 内容 CRUD、参数路径、资产发布/物化、巡检和下线语义。
- 不新增 Aix/Claude Code 切流；已有企业实现必须兼容，但不替其重构外部 Engine。
- Backend layout 配置权威永久保留；Engine 内重复路径与启动消费者的进一步治理属于模块四，不转移配置所有权。

## 9. Hermes 启动配套暂缓（#2002独立跟踪）

公共 #2003已合入；核验时OCB gitlink已包含它，但OCB主仓启动脚本配套仍有缺口。这不等于#2003需要重新开发，也不只是一个gitlink更新问题。

按用户最新范围决定，本轮不修复、集成或验收这部分启动/桥生命周期问题，不把它作为#2025完成门禁。后续继续由#2002跟踪，届时复用已有 `codex/issue-2002-pool-bridge-lifecycle` 分支并刷新基线，不盲目照搬旧gitlink。

以下为后续保留事项，非本任务验收项：

- ARCA 完整 H0→mount→prepare 顺序，active/finalizing 不重建已退出的 Local 桥。
- active 后 Hermes Repo 桥退出；finalizing 不错误提前退休，保持既有阶段合同。
- Desktop Pool 稳态不重新建立已退出的 Repo 桥。
- Legacy 准备保持兼容；坏 marker、用户实体、未知链接保留并诊断；probe 只读。
- 显式 rollback 保持原合同；不以日常 apply 替代严格迁移。

本轮仍验证Hermes日常逻辑Apply和既有支持合同，不新增Hermes Service、不改变既有启动/迁移流程。已知启动配套问题如影响现场用例，单列为外部限制，不冒充日常Apply缺陷或已完成验收。

## 10. 交付与依赖传播

| 仓库 | 必要工作 |
| --- | --- |
| Avernet | 逻辑 Plan/Delivery、兼容解析、按需依赖、公共 Engine apply、合同/错误信息及测试文档 |
| OCB | 日常Apply必要的企业传输/Corp/DI兼容；不纳入Hermes启动配套，gitlink部署集成另行安排 |
| daas | 核验实际启动/挂载调用链；仅有明确必需缺口才修改源码 |

预计Avernet/OCB按日常Apply实际依赖需要代码交付，不为三仓各有一个PR制造改动；不与#2002的剩余配套捆绑。用户已将OCB gitlink后续更新/部署集成留待后续安排。
旧 Backend→新 Engine、以及新 Backend→旧 Engine 均须验证。
公共 API 保留使版本演进可分步，但不能凭公共 PR 合入就声称企业装配、镜像、gitlink 已一致。

所有 PR 目标为 dev；代码合并、OCB gitlink、镜像部署、真实运行证明分别记录。
不得自动执行 Pool 全量切流或为了测试重启用户容器。预发运行操作另按授权推进。

## 11. 验收标准

以下是待执行标准，不是已通过记录。

| ID | 覆盖 | 必须断言 |
| --- | --- | --- |
| A01 | 新 OpenClaw/Hermes × Legacy/Pool × Local/Repo/Center/混合 | 正常文件型日常 apply 一次业务调用；无独立 probe/ensure/verify |
| A02 | 旧 Backend + 新 Engine | 原 DeviceSync/Mapping v2/v3/STRICT 接口与字段不变 |
| A03 | 新 Backend + 旧 Engine | 安全兼容条件、旧 Mapping 与 DeviceSync 区分、旧 Center 检查保留 |
| A04 | 回退负例 | 非标准404、health不符、401/403、400、5xx、超时/丢失/畸形结果不改协议重复写 |
| A05 | 公共新 Router + 尚无新能力的 Corp 插件 | 写前明确拒绝、无副作用；不能因普通501盲目回退 |
| A06 | 历史数据与作用域 | 不改DB；真实locator形状差分、name≠包名、Owner/Bot/tenant/env隔离 |
| A07 | 清理与替换 | 空意图、最后一个Local退出、exact retired、同名新旧引用正确关联 |
| A08 | Center V2不可用 | V2 PENDING、V1入口不先删、其它条目执行；不虚报旧版可读或退出成功 |
| A09 | 非受管内容 | 用户实体、外部链接保护；不误清整个active root |
| A10 | 结果合同 | 缺项/重复/额外/矛盾逻辑结果不冒充成功；空items不覆盖整体失败；完整保留两类问题 |
| A11 | Factory/前置校验 | 文件型Skill-only新apply/旧Mapping不调用旧Factory；MCP身份/有效集合/CLI校验失败时所有Engine写入为零 |
| A12 | Scope/消费者 | MCP-only不推Skill；Teclaw保留Whole Artifact；历史Artifact不跟随latest |
| A13 | 严格迁移 | cutover/rollback/recovery原合同保留，日常结果不得替代其验证证据 |
| A14（暂缓） | Hermes启动/桥生命周期 | 转由#2002后续处理，不属于#2025验收门禁；日常Apply的Hermes覆盖仍按A01–A13执行 |
| A15 | 三仓装配 | public/Corp DI、传输状态与结果序列化、gitlink和镜像脚本版本实际一致 |

先运行贴近变更的 UT、真实 helper/临时文件系统测试、consumer↔Protocol 合同、架构和 DI 窄门禁。
不能只测手动构造 Service 或返回固定成功的 Fake，必须断言实际调用及未调用边界。
Standards/Spec 双轴 review 并修复高优问题后，按仓库规范运行最终门禁与 CI；Backend 全量避免在 review 前重复执行。

预发再抽测新旧真实容器、准确软链目标和可读内容及逐项失败诊断；Hermes完整启动/桥生命周期验收暂缓，不要求先修复#2002才能交付#2025。
已有 Owner 范围 Local 数据审计只支持样本的路径等价，不替代全量历史数据、Hermes样本或真实Runtime验证。
公开测试使用脱敏 fixture，不提交原始 DB 记录、私有日志、Token 或容器现场文件。

## 12. 实现导航与当前证据

以下为已有源码入口，便于后续实现；不表示新 apply 已存在。

| 入口 | 关注点 |
| --- | --- |
| [Backend Projector](../../../src/backend/src/agentclaw/community/core/skill_center/services/bot_runtime_projector.py) | Reader-backed计划、提前Factory、MCP前置校验 |
| [Skill Delivery](../../../src/backend/src/agentclaw/community/core/skill_center/services/runtime_projections/skill_runtime_delivery.py) | 日常分流、物理结果关联、兼容结果聚合 |
| [Runtime合同](../../../src/backend/src/agentclaw/community/core/skill_center/runtime_projection_contract.py) | Plan、scope、结果与消费者边界 |
| [旧Mapping Adapter](../../../src/backend/src/agentclaw/community/core/skills_pool/runtime.py) | probe/ensure/publish/verify与传输 |
| [布局状态判断](../../../src/backend/src/agentclaw/community/core/skills_pool/types.py) | 本次source_layout选择 |
| [Engine Mapping合同](../../../src/engine/src/engine/community/plugins/skills_pool/mapping_contract.py) | 逻辑请求及写前校验 |
| [Engine执行](../../../src/engine/src/engine/community/plugins/skills_pool/layout_activation.py) | BEST_EFFORT、retired、实体保护、STRICT分离 |
| [Center只读检查](../../../src/engine/src/engine/community/plugins/skills_pool/center_mount.py) | mount、精确版本及SKILL.md |
| [Engine Router](../../../src/engine/src/engine/community/api/skills/router.py) | 新入口及旧接口兼容 |

优先扩展现有 `test_skill_runtime_delivery.py`、`test_runtime.py`、`test_bot_runtime_projector.py`、
`test_mapping_contract.py`、`test_mapping_retirement.py`、`test_layout_activation.py`、
`test_device_adapter_transport.py` 和相关 DI/Corp/启动测试。
旧测试中“Local-only永不Mapping”的固定假设须改为按能力判断；timing测试不能替代MCP失败前零写断言。

本轮完成设计、源码核验及执行Ticket创建；未实现业务代码、未运行新合同测试、未创建实现PR，也未验证部署或执行切流。
实施完成后补记实际PR/提交、测试结果和未验证边界，不以本文的设计状态代替交付状态。
