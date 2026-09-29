# OpenClaw Pool-native：独立开发任务 Handoff

## 授权与交付

用户已授权一个独立 Codex 任务使用 **gpt-5.6-sol / high**，按 `$implement` 开发，
目标为三个仓库的 **dev** PR。主任务继续统筹和验收，不在此开发任务中自动切流。

必须完整读取 `/Users/freddie/.agents/skills/implement/SKILL.md`，按既有测试边界尽量
TDD，完成后用 `/Users/freddie/.agents/skills/code-review/SKILL.md` 做双轴评审。
不要重新询问已经定稿的产品取舍；若代码事实要求扩展公开契约或交付范围，再报告。

交付预期：4 个功能 PR；必要时另加 1 个纯 OCB gitlink 集成 PR。
每个功能 PR 包含对应测试与合同文档，不另拆一个只有测试的 PR。
不要只交方案或仅完成 Avernet 即宣称整个任务完成。

## 文档入口（先读）

本目录内：

1. `2026-09-18-openclaw-pool-native-startup-spec.md`：正式需求，优先级最高。
2. `2026-09-18-openclaw-pool-native-evidence.md`：跨仓基线、代码路径与证据边界。
3. `2026-09-18-pool-native-design-review.md`：决策历史，旧候选已被正式 Spec 覆盖。

来源 worktree：`/tmp/avernet-pool-native-design.EmAmLF`。
上述文档和该 worktree 的 CONTEXT.md 补充尚未提交；主任务此前只做了文档工作。
请读取这些文件后，将必要 Spec/证据与术语补充纳入自己的提交，保持路径链接有效。
不要批量带入来源 worktree 或主 checkout 的其他未提交修改。

## 已确认、不可漂移的边界

- 命中 Rollout 的新 OpenClaw personal/Service Draft/desktop：创建即选择 Pool；
  不认领 migration、不 Cutover、不搬迁 Local、不构造迁移 generation。
- 复用 `ac_bot_skill_layout_state`；创建事务内写 `active_layout=pool`、
  `phase=pool_initializing`、`target_layout=NULL`、`migration_generation=NULL`。
  不新增来源列/新表。新建与 layout 行原子提交，之后再分配外部实例。
- 历史无 layout row 仍是 Legacy。缺 generation 不是 Native 身份判定。
- 存量 Legacy 重启仍执行原迁移；已 Pool 重启保持 `pool_active`，不重置初始化阶段。
- 新增 `pool_initializing` 的读兼容要先部署；当前 owner Rollout 已打开，不能一上
  Backend 新代码就不受控地给未更新 Engine 派 Native 请求。
- Backend 决定逻辑 layout，Engine 决定物理路径；云端复用现有 layout env/contract。
- 启动最早分流，不创建 Legacy roots/bridge；后续 setup/default 初始化也不能重建。
- Native 与已迁移稳态共用最小 active marker：engine/contract/activation_state。
  不新增 `initialization=native`；不再依赖 ready/preparation/generation 作为稳态前置。
- finalizing 和 DB 未提交的迁移仍按原严格恢复；迁移身份、quarantine 历史保留。
- 可信 Pool 启动且 marker 缺失，根合法时幂等补写；坏 marker/冲突保留现场并报错。
  普通 probe 只读，不自动修复。
- 完成条件仅根级布局；逐 Skill、Scanner/hash、MCP/default 全成功不作为新门禁。
  Repo/Center 检查真实 mount 事实并诊断，但 non-critical 语义不变。
- 复用 status callback，增量可选布局证据；原 alive/SUCCEEDED/后台派发不能假装布局完成。
  不新增 ACK 持久重试/轮询/投递任务；回报丢失接受初始化未确认，正常重启恢复。
- Published Service 使用冻结 Artifact/layout/image，不能按最新 Rollout 改写；
  Desktop 是独立本地启动链路，不能假定云镜像升级等于桌面更新。
- 不引入旧 batch，不重开已暂停的逻辑文件接口/ZIP 大重构，不扩大其他 Engine 切流范围。

## PR 组织

| PR | 仓库 | 边界 |
| --- | --- | --- |
| 1 | Avernet | 新 phase/回报读兼容、Engine 最小 marker 与稳态消费者、Native 确认能力；尚不启用创建生产方 |
| 2 | agentclaw-daas-scripts | 目录 setup 遵守 layout、实际完成证据回报、保留旧回报兼容 |
| 3 | OCB | 云端/Desktop 初始化、启动分流、mount 诊断、镜像与公共实现装配 |
| 4 | Avernet | 创建事务与 Native 生产方、部署参数接通、不入迁移任务、端到端合同收口 |

全部目标 dev。依赖还没合并时不要把 PR base 改成 feature branch；可以使用隔离的
集成测试 worktree 验证整套组合，并明确 PR 依赖。PR 4 若因 PR 1 未合并不能形成
纯自身 diff，先交付前置 PR 并报告依赖，待用户合并后继续，不自动合并绕过依赖。

OCB 最终 gitlink 必须指向已知精确公共提交；若功能 PR 无法同时携带最终 gitlink，
额外纯集成 PR，不复制公共实现到 corp。遵守仓库 PR/AntCode 关联工作项要求；
若必须补 Dima 且没有可验证的适用工作项，报告缺口，不能绑定历史无关 Bug。

## 仓库与基线

- Avernet 主 checkout `/Users/freddie/Documents/Avernet`，用户当前分支/未提交改动不得触碰。
- OCB `/Users/freddie/Documents/codebase/ocb`，主 checkout 可能 dirty；使用隔离 worktree。
- daas `/Users/freddie/Documents/codebase/agentclaw-daas-scripts`，同样使用隔离 worktree。
- 读取对应 AGENTS.md/架构文件，fetch 每仓 dev，记录 SHA 和 OCB `ocb-public` gitlink。
  正式附录记录的是先前调研快照，不是最新远端或部署证明。
- 不要在独立任务默认 worktree 上继续历史 REL；目标统一 dev，分支使用 `codex/`。
- Avernet/OCB push 始终 `git push --no-verify`；必须 force 时用精确 force-with-lease。

## 实现前优先核验四处接线

1. 现有回报身份：尤其 BaaS 原地重启复用 binding/device 时，如何排除旧启动回报。
   复用现有实例/publish 身份，不自造另一套租约系统。
2. BaaS 主动 alive 与容器 status 的顺序；不能把命令派发成功当 READY，不能循环等待
   只能在 ACTIVE 后发生的 Skill 投影，不重做全局生命周期。
3. Desktop 的独立 payload/本地最早初始化/回报；所需改动是否都在三仓可覆盖。
4. 已启用 owner Rollout 下读兼容、新镜像、写生产方的分阶段部署顺序。

这些是正式 Spec 标注的尚未验证的实施细节，请先核验并写入 implementation note；
无需再次 grill 已确认目标，若必须新增身份持久化/扩仓/产品限制才向用户报告决策点。

## 质量与交付证据

- 执行正式 Spec 的 27 项测试要求；按 consumer seam 做测试，不用 fake READY 代替真实初始化。
- 窄测/类型/合同/DI/架构门禁先行；Standards/Spec 双轴 review，修复高优 findings；
  然后创建 PR 跑 CI，本地受影响全量只在最后跑一轮，不在开发早期反复跑全量。
- 审查文件行数，不为过门禁任意放宽 allowlist；检查公共仓库无内部凭证/机器私有配置。
- 跟进 PR CI 和有效 reviewer comments；提交报告必须区分本地验证、CI、Review、
  合并、部署、运行验证。没有现场验证不要称 rollout 已验证。
- 当前授权是开发并提出 PR，不自行手工合并、部署、切流、重启或修改 DB。
  若平台自动合并规则与该交付边界有冲突，先明确报告，不隐式扩大授权。
- 每个 PR 创建后附加到开发任务。向主任务汇报 PR URL/base/head、测试、CI、依赖与剩余风险。

主任务 ID：`01a00ebc-3e6b-73f2-862a-f03b47ab33e0`。
