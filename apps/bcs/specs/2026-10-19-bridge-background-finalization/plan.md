# Bridge 后台任务收尾执行计划

> 日期：2026-10-19
> 状态：隔离 opt-in 实现及公共回归完成；完整兼容性验收与内部启动检查尚未通过，不能推广。实际证据见 [validation.md](validation.md)。原计划的外部 helper/socket 由原生 SDK 控制协议替代。
> 基线：GitHub `dev` 的 `7385c8323439081a4022b0623e13788d7f816e35`。
> [Spec](spec.md) · [故障与已有实验](research.md)

> 2026-10-20 交付指令：用户已明确授权 commit、push 和面向 dev 的 PR，并要求不再运行验证。下文未授权提交的记录描述此前实现阶段；本次交付沿用 validation.md 中的既有测试证据。

## 当前执行结果

以下原始检查表保留设计要求；本表和 validation.md 记录本次实际完成范围，未完成项不视为通过。

| 阶段 | 实际结果 |
| --- | --- |
| P0 | 基线 53 个 Claude 测试通过；完成 mapper、权限及 trace 依赖盘点 |
| P1 | 验证原生回调顺序、重复 Stop、既有 Hook 共存及 shell/subagent 有限范围；完整 G3/G4 未通过 |
| P2 | 12 个确定性驱动测试通过，包含多个负例表及红绿记录 |
| P3 | 受控拆分后旧测试通过；实现有限大小的候选、快照、去重和终态判定 |
| P4 | 原生 initialize/hook_callback 代替 helper/socket；有界且可取消的 ACK，未修改用户设置 |
| P5 | legacy/observe/hook_idle 接线；新模式不依赖旧消费 pending；唯一 TurnOutcome 出口 |
| P6 | 公共 336 通过、2 忽略；另行实机测试 5 场景通过；架构检查通过；内部 --locked 启动测试被基线锁文件不一致阻塞 |
| P7 | 新模式移除消费启发式，保留默认 legacy 和兼容说明；生产回滚演练、完整兼容矩阵及发布未执行 |

一次独立审查发现的 3 项 Important 均已复现并修复；无遗留 minor。
本次仅交付隔离研究验证用的显式 opt-in。managed policy、完整嵌套/恢复后台任务、
MCP/workflow/监控及跨版本支持仍需补证据；未授权 commit/push/发布，工作区保持未提交。

## 0. 执行约束与工作区

本计划顺序执行，不要求子代理。它是后续实现的工作清单，不授权提交、推送或发布。

- 当前功能分支：`fix_cfuse_background_finalization`。
- 公共变更在 Avernet 的 `apps/bcs`；内部发行只负责 wrapper/capability 接线，保持公共依赖方向。
- OCB worktree 基于拉取后的 OCB remote dev；其 Avernet linked worktree 单独基于
  GitHub remote dev，可能新于 OCB 原 gitlink。外层 submodule modified 是基线差异，
  不是本计划修改 retention 或自动提交了指针。
- 不切换/清理原工作区，不覆盖并发修改，不自动 commit/push，不改生产配置或重启服务。
- 读根与 BCS 的 AGENTS/CLAUDE、架构规则和协议测试规范。不运行 cargo fmt/all 或全局格式化。
- 新增/修改源码最终不超过 1000 行；现有 `claude_code.rs` 2330 行，需要在受控步骤中
  按 parser/driver/测试职责拆分，不能直接继续堆叠新逻辑。
- 原始 trace、会话内容、凭据不入库；用合成 fixtures 固化序列，现场摘要见 research。
- 新实验必须全新 cwd/session、短任务、明确版本，记录设置隔离与真实配置合并实验的区别。

路径以 `apps/bcs` 为根；命令从该目录执行。OCB 中进入方式：

```bash
cd submodules/avernet/apps/bcs
```

## 1. 依赖顺序与停止条件

```text
P0 源码/契约基线
  → P1 Hook + idle 联合验证（G1/G2/G3；不通过则修订 spec，禁止启用）
  → P2 可复现合成 fixtures 与失败测试
  → P3 受控拆分 + 纯收尾状态机
  → P4 自动注入 / helper / 本地回调
  → P5 driver 接线 + 单一终态 + 兼容模式
  → P6 全部回归、能力矩阵与小范围验证（G4）
  → P7 删除新路径计数依赖、发布准备与回滚验证
```

P1 的输出必须包含准确的 Hook↔epoch↔result↔idle 关联算法及字段来源；
未找到可靠算法时，不进入强制判定实现。可提出引擎侧协议增量，不能借任务计数或时间窗代替。
本计划不以完成文件数衡量成功，以 AC-01～AC-14 的用户可见行为验收。

## P0. 固定基线与可运行测试入口

- [x] 拉取公共 GitHub dev 并建立独立分支；未携带旧工作区修改。
- [x] 阅读当前 `claude_code.rs`、run outcome/Terminate 清理路径和发行注册入口。
- [x] 确认 retention 回收已在当前 baseline，和本修复分开。
- [x] 归档既有八组 stdout 实验的脱敏结论；没有宣称 Hook 验证已完成。
- [x] 跑现有公共测试并记录实际结果；基线 53 通过，最终全套 336 通过，详见 validation.md。
- [x] 盘点 mapper 的名称关联、工具结果、HITL、trace、session observer 依赖，确定最小拆分边界。

```bash
cargo test --locked -p bcs-bridge-core --lib engine::claude_code::tests::
cargo test --locked -p bcs-bridge-core --test encoder_contract
cargo test --locked -p bcs-bridge-core --test plugin_websocket
cargo test --locked -p bcs-bridge-core --test e2e_webhook
```

**交付：** 后续新增 `validation.md`，基线测试实际结果与既有实验证据分列。
依赖/权限/宿主限制不是产品测试通过；不得改锁文件绕过错误。

## P1. 在真实 launcher 上完成能力验证（前置阻断项）

### P1a. 自动注入与配置合并（G1）

- [ ] 使用隔离测试脚本，通过真实 launcher 注入临时、无副作用 Stop helper。
- [ ] 单独测试无原有配置、有用户/project hooks、包装层自有 settings 与企业禁用策略。
- [ ] 验证是否允许追加配置、是否覆盖原 hooks/env/permission/MCP；检查最终生效配置，不只看 argv。
- [ ] 每个 Stop 只调用一次自带 helper，重复注入不重复执行；helper 自身不递归。
- [ ] helper 超时/退出非零/不可执行不阻塞 CLI 无限等待，bridge 记录证据不可用。

### P1b. 精确关联与快照覆盖（G2/G3）

- [ ] 同时捕获有序 stdout 与 helper 结构化回调，保留字段白名单、接收时间及序号。
- [ ] 查证 native hook_started/hook_response/控制回调等是否提供可用 invocation 身份；
  不假定所有 hook 都有该字段。`prompt_id` 复用、重复 Stop、阻断 Stop 必须单独覆盖。
- [ ] 验证同步 helper ACK 与 result/idle 的原生顺序，定义迟到、重复、冲突的拒绝规则。
- [ ] 测试普通无后台、Agent、Bash、前台工具、后台 MCP、嵌套/恢复任务、长驻任务。
- [ ] 明确哪些任务纳入 Stop 快照，何时采集，已完成未投递是否保留，字段缺失/截断如何表示。
- [ ] 专门构造快照后 task_started、通知到达、新输入折叠/排队和旧 idle，证明新鲜度边界。
- [ ] 重放 research 的两个反例：活后台时 idle；完成通知到达后中间 result 仍需续处理。
- [ ] 给出版本能力表和可执行的关联算法；更新 spec 中的未决字段和错误等待上界。

**Go/No-Go：** G1/G2/G3 全通过才能进入新模式接线。只证明 `background_tasks=[]`
存在不算通过；只观察接收时间接近也不算通过。如果需要额外引擎字段，先明确协议与支持版本。

**交付：** `validation.md` 中记录可复现步骤、正反例序列、字段来源和未支持类别；
原始试验日志只保留本机，不提交真实模型回答或任何凭据。

## P2. 构建确定性失败测试与合同 fixtures

建议新增测试目标（实施时创建，不代表当前已存在）：

- `crates/tools/bcs-bridge/core/tests/claude_completion.rs`
- `crates/tools/bcs-bridge/core/tests/fixtures/mock_cc_hook_idle.py`
- 纯状态机同级 unit test 模块；fixture 通过受控同步点产生两通道事件，不依赖随机 sleep。

- [ ] 用合成数据重现 3 Agent 已完成、无 user 通知/新 init、成功 result 被旧 pending 卡住（AC-01/04）。
- [ ] 覆盖 task_started→result→idle→稍后完成→新 init/result/idle，不得提前杀进程（AC-02）。
- [ ] 覆盖完成通知→中间 result→init→新 result→idle，无中间 idle（AC-03）。
- [ ] 建立 scope 错误、旧 empty、旧 idle、晚回调、阻断 Stop、重复 Stop 的表驱动测试（AC-05/06）。
- [ ] 缺失、未知类型、不可读取、认证失败、超限、部分数据都不能被反序列化为 empty（AC-07/08/11）。
- [ ] 断言当前实现的具体失败原因和新测试数量；零测试、条件跳过不算通过。

**交付：** 可审查的红灯记录，包含“卡死”和“提前完成”的双向保护。
不得删除旧 `result_landing_between_enqueue_and_delivery_stays_intermediate` 等行为保护。

## P3. 拆分适配器与实现纯判定器

建议文件：

- `core/src/engine/claude_completion.rs`：候选、scope、证据状态、决策。
- `core/src/engine/claude_completion_tests.rs`：确定性状态迁移、幂等与取消竞态。
- `core/src/engine/claude_code.rs` 及聚焦 parser/driver/test 模块：按实际体积拆分。

- [ ] 先做保持旧行为的局部拆分，跑 P0 基线；不改事件内容、工具名或权限逻辑。
- [ ] 使用显式 `Unknown/Unsupported/Known`，不以 `Option<Vec<_>>::unwrap_or_default()` 表达快照。
- [ ] candidate 只保留当前最新成功结果，新 epoch/abort/error/restart 时失效。
- [ ] 为每次评估保留证据来源和拒绝原因，不用旧消费 pending 做 AND 条件。
- [ ] 两通道输入统一到纯状态机；只有 G2 强关联的迟到证据允许重评估。
- [ ] 实现显式终态锁定；已 terminal 后无事件可重新完成，取消/错误保持优先契约。
- [ ] 记录资源上限：候选大小、去重索引、快照条数及容量超限的失败行为。

**完成条件：** AC-01～09/14 纯逻辑通过；生产接线前仍默认 legacy。

## P4. 自动 Hook 注册（实现修订：原生控制通道）

实施采用 spec §5 原生 initialize/hook_callback，不再新增下面最初规划的 helper/socket 文件。
两者目的相同：进程隔离、主动结构化报告、原设置保留、有界 ACK；实际实现与测试见 validation.md。

建议新增/修改：

- `core/src/engine/claude_hook.rs`：协议验证与受控接收，纯判定器不读环境/文件。
- `app/src/` 的 CLI/helper/config 模块：隐藏 helper 子命令、临时 settings、端点装配。
- `core/src/engine/cli.rs`：仅必要的进程配置/资源所有权扩展。
- `cli/tests/`：实际二进制启动 helper 与资源清理测试。

- [ ] helper 能独立处理 stdin JSON；不启动 daemon/模型，不改返回语义，不输出通知正文。
- [ ] endpoint 在引擎前就绪，per-run 目录/权限/代次隔离，凭据不出现在 argv/日志。
- [ ] 原 hooks 和 launcher 配置完整合并；支持空格/引号路径，不拼接未转义 shell 文本。
- [ ] policy 禁用时显式不可用，不绕过限制；业务无需手动修改 settings。
- [ ] callback 有大小、连接、队列、等待上界；Unknown 保持 Unknown。
- [ ] 接收 ACK 不等待 result；回调重试/心跳不能延长 stdout idle watchdog。
- [ ] 正常、spawn 失败、abort、process crash、daemon shutdown 都只清理自己的资源。

**完成条件：** AC-06/07/10/11 实际 IPC/进程测试通过，配置文件前后内容一致。

## P5. driver 接线、终态与模式切换

- [ ] `ClaudeCode::run_turn` 接收原生 session state 和新 Hook 证据，保留候选 result。
- [ ] 固定 legacy/observe/hook_idle 模式；能力由 factory/composition 显式注入。
- [ ] observe 不改变 legacy 终态/清理时点；它没有观察到的后续不能被声称已验证。
- [ ] hook_idle 中停用启发式消费计数对 final 的 veto；保留事件和任务身份关联用途。
- [ ] success 返回正常 TurnOutcome，不由 helper 或 IPC task 调用上游 final sender。
- [ ] 接入原 run 的取消/deadline/watchdog、session/inject 清理及错误映射。
- [ ] 不支持/证据矛盾通过既有 runtime_error 输出脱敏诊断；不在同 run 偷偷回退 legacy。
- [ ] 内部发行只做 capability/launcher 合并接线；实现前另读该 overlay 的 AGENTS/CLAUDE。

**完成条件：** AC-09/12/13/14 通过，两个传输模式都不会重复 terminal 或丢工具结果。

## P6. 完整回归与受控实机验收（G4）

公共 BCS 命令（新增测试 target 完成后才执行）：

```bash
cargo check --locked -p bcs-bridge-core -p bcs-bridge-app -p bcs-bridge-cli
cargo test --locked -p bcs-bridge-core --test claude_completion
cargo test --locked -p bcs-bridge-core --test encoder_contract
cargo test --locked -p bcs-bridge-core --test plugin_websocket
cargo test --locked -p bcs-bridge-core --test e2e_webhook
cargo test --locked -p bcs-bridge-core --test plugin_retention
cargo test --locked -p bcs-bridge-core -p bcs-bridge-app -p bcs-bridge-cli
```

从 OCB 根执行内部发行检查（仅适用实际存在的 overlay，公共 CI 不依赖它）：

```bash
cargo test --locked --manifest-path apps/bcs-internal/Cargo.toml -p bcs-bridge --test start
./apps/bcs-internal/scripts/check-architecture.sh
```

- [ ] P0 的旧行为回归与新模式双向保护都通过，记录真实执行数量而不是只贴命令。
- [ ] 全新隔离会话复现八个原实验，并加入 G2/G3 的 hook、竞态、错误、恢复和嵌套矩阵。
- [ ] 无 pending 漂移造成的 600 秒等待；也无“更快结束但后台被杀”的伪修复。
- [ ] 对明示的 callback/快照异常测试错误终态，不依赖整段 600 秒 wall clock 等待。
- [ ] final 内容属于正确候选；普通空文本成功仍合法；工具/交互事件语义保持。
- [ ] 敏感信息扫描、临时文件权限、进程/FD 泄漏、资源上限和 shadow 日志审查通过。
- [ ] 所有修改源码 ≤1000 行；必要重构仅限本任务，禁止全局格式化。

**交付：** `validation.md` 列出 AC-01～AC-14 的实际结果、支持版本、未测边界和 G4 结论。
明确区分合成 fixture 通过、真实 wrapper 验证通过、发布后确认；三者不可互相替代。

## P7. 清理启发式依赖与发布准备

- [ ] 在已启用的新路径删除 init-pop/user 通知/receipt 修补对 final 的控制作用。
- [ ] 若 legacy 尚需相同 parser，保留其独立模式并注明迁移范围，不跨模式混用状态。
- [ ] 支持版本 opt-in 前先留观察记录；不支持的组合保持旧模式或明确拒绝，业务无手工 Hook 步骤。
- [ ] 回滚演练：只改变新 run 的配置，活跃 run 不换算法，不主动杀整机 engine。
- [ ] 更新用户可见兼容说明与 helper 配置说明；保留“不读 transcript”的约束。
- [ ] 公共变更先经 GitHub dev 的 PR/CI；内部发行后更新 OCB gitlink。提交/推送/发布需要后续明确授权。

**最终交付定义：** 有支持范围、有成功与提前结束反例测试、有可回滚开关；不是“加入 Hook”
或“减少了 pending”就宣称修复完成。用户后续要求继续实现；本次实现与有限验证结果见上表，完整发布验收尚未完成。
