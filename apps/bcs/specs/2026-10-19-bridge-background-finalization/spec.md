# Bridge 后台任务收尾 Spec：Result + Idle + Stop 快照

> 日期：2026-10-19
> 状态更新（2026-10-22）：按用户要求，Claude-compatible driver 未配置模式时默认 `hook_idle`，保留显式 `legacy` / `observe`。仅验证 cfuse 2.6.44 → Claude Code 2.1.199 的有限能力范围；本次默认值调整不扩大能力认证。下文原 opt-in 推广计划及未测边界的历史背景见 validation.md。
> Owner：公共 BCS `bcs-bridge-core` 引擎适配边界；内部 launcher 由发行 composition 接线。
> 公共源码基线：`7385c8323439081a4022b0623e13788d7f816e35`。
> [问题与实验证据](research.md) · [执行计划](plan.md) · [实现验证](validation.md)

## 1. 决策与范围

在已验证能力的 Claude stream-json 适配路径中，以**同一执行阶段的最新成功 result、
新到达的原生 idle 和有效 Stop 后台快照**共同判定 BCS run 收尾。不读取 transcript，
不解析运行日志，不再以 bridge 启发式消费 pending 作为 final 的硬门槛。

当前实现已通过真实 launcher 的普通回复、恢复会话、后台 Bash、执行中消费及通知入队验证。原始交付采用隔离研究验证用的有限范围 opt-in；2026-10-22 的默认值调整不等于全部任务类别和企业策略已验收。
如果无法证明同轮关联或快照覆盖，停止启用此路径并记录缺少的协议能力，不用时间窗、
文本相似度、计数猜测补洞。

### 1.1 边界与相关契约

- 遵守 [架构规则](../../../../docs/arch/arch.rules.md) 和
  [协议测试规范](../../../../docs/arch/protocol-contract-tests.md)。没有发现直接规定
  本项 bridge 完成判定的既有 ADR；本草案不替换任何 accepted ADR。
- 保持 BCS Plugin V2/V3、Gateway SSE 的现有终态语义；一个 run 至多一个成功/错误/
  取消终态，已终态的 run 不因迟到信号重新开启。
- Hook 使用 engine adapter 的原生进程控制协议，不是新的 BCS 公共 endpoint；
  注册与 ACK 在 driver 中，纯判定逻辑不依赖 socket、环境变量或文件读取。
- 可复用实现留在公共 BCS；内部 launcher 注册、包装配置和兼容验证留在发行 overlay。
  公共 crate 不硬编码内部 launcher、模型、服务地址或凭据。
- OCB = 业务/API adapter；Relay = AICoding runtime。本变更仅涉及现有 BCS bridge
  的 Claude 协议适配器，不在 OCB legacy `src/engine` 新增 AICoding 运行时行为。
- retention 修复已有独立 [spec](../../docs/superpowers/specs/2026-10-19-bridge-plugin-retention-reclamation-design.md)，不作为本方案成立前提或替代品。

### 1.2 非目标

- 不迁移到 Paseo 式持久 session/autonomous 多 turn 产品语义，不新增 BCS run 路由。
- 不修改事件保留上限、重放、发送 cursor、token 消耗统计或增量合并。
- 不通过延长/缩短 600 秒 idle watchdog 掩盖完成证据不足。
- 不修改用户/项目的永久 settings，不让业务手工安装或填写 Hook。
- 不用 Stop hook 阻止模型停止或注入汇总提示；回调不读取 transcript。
- 不停止长驻后台任务来制造空快照，不承诺任意未来 cron/监控任务都会自然结束。

## 2. 术语与问题

| 术语 | 本文含义 |
| --- | --- |
| BCS run | 一次上游请求及其唯一终态，可覆盖多个原生主循环阶段 |
| 原生执行阶段（epoch） | 当前 CLI 主循环的一次结果生成阶段；具体边界由能力验证确定 |
| 执行结束 | 后台任务自身已完成/失败/停止 |
| 已消费 | 后台结果进入主 agent 后续上下文；不等于仅收到完成通知 |
| 候选 result | 当前最新成功结果，尚未满足 BCS run 收尾条件 |
| 有效快照 | 来源、覆盖范围、session/进程/epoch 关联与新鲜度都已通过校验的 Stop 快照 |
| Unknown | 没有可靠证据；不能等同于空集合、idle 或成功 |

故障中，bridge 已收到成功 result 和全部已观察任务的终态通知，却因未看到某种消费
信号留下 3 个 pending，最终 600 秒超时。新方案解决这一误等待，同时保护“任务已完成
但通知尚未处理”的中间 result。详见 [research](research.md)。

## 3. 上线前硬门槛

| Gate | 必须证明 | 未通过的处理 |
| --- | --- | --- |
| G1 注入 | 临时 Stop hook 经实际 launcher 透传/合并，原 hooks、权限、环境和工具策略不被覆盖；遵守 managed policy | 不启用，报告 capability unavailable |
| G2 关联 | Hook invocation、进程代次、当前 epoch、result、idle 可强关联；乱序/重复/迟到/Stop 被阻止不会误配 | 不采用“最近一次 Stop”；要求补协议或调整方案 |
| G3 覆盖 | 支持的任务类型、嵌套/恢复任务、快照时点及空集合语义已实测；缺字段/截断均 Unknown | 未验证类别不能被过滤成空，保持明确不支持 |
| G4 收敛 | 多任务、queued notification、重入、error/abort、IPC 丢失、取消竞态和跨版本契约回归通过 | 只允许研究/旁路，不允许推广 |

普通 CLI idle 实验已完成，不能代替上述 Gates。快照只是某一时点的观察，需同时验证
快照之后的任务变化与原生 idle 的关系；一次 `background_tasks=[]` 不是永久无工作证明。

## 4. 目标契约

- **FR-1**：最终 result 已产生且组合证据有效时正常完成，不等待 600 秒超时。
- **FR-2**：中间 result、后台仍运行的 idle、旧空快照均不得提前结束 run。
- **FR-3**：不凭 `task_notification=completed` 声称主 agent 已消费结果。
- **FR-4**：不因旧消费 pending 残留而否决新路径的有效完成证据。
- **FR-5**：只保留最新结果；新阶段、取消、错误或重启后不能补发旧候选。
- **FR-6**：Hook 自动注入/清理且不改变业务配置或模型行为；失败不是空快照。
- **FR-7**：单一终态与取消优先；沿既有 TurnOutcome→run 收尾路径发送，不从 Hook 直接发 final。
- **FR-8**：未知能力显式可诊断，运行中不能偷偷切换判定策略。
- **FR-9**：保留所有合法 delta/tool/interaction 的既有内容与归属，不为兼容修改工具结果名称。

## 5. 自动 Hook 注入与接收

### 5.1 原生控制协议（替代最初的外部 helper/socket 设计）

实测直接追加 `--settings` 与 cfuse 自有设置存在覆盖问题；原生 SDK 注册能够工作。
因此不新增 command helper、socket、临时 settings 或全局配置：

1. 每个 run 的新 CLI 进程生成独立 initialize request ID 和 Stop callback ID。
2. 设置子进程 `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`，发送
   `control_request/initialize`，注册 `hooks.Stop[].hookCallbackIds`。
3. 收到匹配的 success control_response 后才发送唯一业务输入。握手上界 30 秒
   （若 run idle override 更短，取更短值）；取消优先。
4. CLI 从同一条有序 stdout 发送 `control_request/hook_callback`，包含独立
   request_id、已注册 callback_id 和 Stop input。bridge 校验后立即回复空对象 ACK；
   回调写入上界 5 秒。此 ACK 不修改模型输入、不 block、不注入上下文。
5. 检查原生版本 `2.1.199`；其他版本明确 capability unavailable，不猜兼容性。
6. Hook 随进程结束自动释放，继续沿已有 CliSession 进程树清理路径结束。

已有 command/plugin hooks 仍由 CLI 执行；bridge 不写任何用户/项目设置。
企业 managed-policy 场景尚无实机验收，不声明支持；禁止绕过策略。

### 5.2 证据与资源约束

状态机区分 `None`（Unknown）、有效空快照和有效非空快照；格式错、缺字段、
未知任务类型、非 running 描述、超限或非空 session_crons 均不当作空集合。
验证过的快照类型为 shell/subagent，task_started 类型为 local_bash/local_agent。

输入行最多 2 MiB，在逐块读取时执行上限；候选 result 最多 1 MiB；快照最多
512 条；每进程最多 2048 个 callback request ID 和 8192 个原生 init/result/state UUID，标识符最多 256 字节。
不截断后放行。Hook/control_response 和坏 JSON 在 raw trace 前脱敏，
不记录 Hook 中的回答、transcript_path、cwd、任务命令或凭据。

业务仅有一条输入；同一进程内没有 bridge 新输入并发。因此每次 init 建立新阶段，
后续主 agent 输出、用户帧、任务生命周期变化使旧证据失效。
成功 result 后最多等待 5 秒的新 idle（较短 run idle override 优先）；
该等待上界只触发失败，绝不作为成功证据；回调不能刷新 watchdog。

## 6. 强关联，而非“同 session 内最近一次 Hook”

- BCS run ID 标识业务请求；process generation 区分重启；engine session ID 可跨 run 复用。
- 原生 idle 只有自身 uuid 和 session_id，没有 result_uuid/turn_id；不得用它直接连接任意 Stop。
- `prompt_id`、`stop_hook_active` 或回答文本即使存在，也不是已证明的唯一 epoch 键。
- 新路径必须使用 G2 验证出的原生 Hook 生命周期 ID/控制回调标识及输出边界，或由
  launcher/CLI 提供明确的相关标识。仅给 helper 加一个自增计数并不能解决配对。
- 同步回调 ACK 只证明本地快照已被接收；它本身不证明后来哪个 result 属于该快照。
- 当前使用同一 stdout 的原生顺序，而非两通道到达时间。只接受当前阶段、result 之前的同步 Stop 回调；晚到、重复 request ID、无继续活动却重复 Stop 均明确拒绝。
- `system/init` 是实验中观测到的边界之一；新的 init 使旧候选失效。不能把所有
  后台帧视为新主阶段，也不能以一次 init 消费一条通知来“修正”集合。
- 其他 Stop hook 可阻止停止、触发重复 Stop；必须证明选中的快照属于实际完成轮，
  不认第一条 Stop 即最终停止，也不接受已被否决的快照。

若 G2 无法成立，需要新增引擎侧明确关联/settled 事件；不能以延迟 N 毫秒作为替代。
这是实施前的技术决策点，不留给上线后的异常处理来掩盖。

## 7. 收尾状态机

### 7.1 分离观测与权威证据

- 生命周期表保留 `task_id/tool_use_id`、别名、类型、归属和观察到的执行状态，供展示与诊断。
- 该表为空仅表示观察到的任务都终态，不等于全局为空；不能独立批准 final。
- Stop 快照是受验证覆盖范围约束的执行态证据；原生 idle 是本阶段已停止续处理的证据。
- 生命周期事件若揭示快照之后产生新工作，标记证据过期/矛盾，需要新 epoch 的证据；
  这不是恢复旧的“必须收到 user 通知才减 pending”规则。

建议纯状态机输入为 `EpochStarted`、`HookObserved`、`ResultObserved`、`IdleObserved`、
`EvidenceInvalidated`、`Failed`、`Canceled`；输入均由适配层规范化并携带已验证身份。

### 7.2 决策表

| 输入/条件 | 行为 |
| --- | --- |
| 成功 result | 保存当前 epoch 最新候选，不立即丢弃、不单独 final |
| 新 idle + 当前快照非空 | 保留“本阶段结束但业务未完”状态，继续接收后台及自动唤醒 |
| 新 idle + 同 epoch 有效空快照 + 最新成功候选 | 满足其余一致性条件后返回 TurnOutcome |
| result 后有新 init，但尚无 idle | 旧候选失效，进入下一阶段 |
| 已 idle 后收到完成通知，观察表归零 | 不复用旧 idle，不释放旧候选；等后续阶段证据 |
| callback 晚于 result/idle | 当前原生同步协议不支持这种顺序，明确拒绝 |
| 快照 Unknown、关联冲突、必要能力消失 | 不成功；执行有界诊断/错误路径 |
| engine error、abort、deadline、retention 终止 | 错误/取消优先，禁止晚到成功覆写 |
| 重复 result/hook/idle | 幂等，不产生第二个终态 |

形式化的必要条件（不是三个布尔字段的无身份 AND）：

```text
current_scope == scope(candidate) == scope(snapshot) == scope(idle)
&& candidate.is_success
&& snapshot.is_known_complete_for_supported_scope
&& snapshot.live_tasks.is_empty()
&& idle.follows_current_candidate_under_verified_protocol
&& no_newer_epoch_or_invalidating_activity
&& !terminal_or_cancel_requested
```

`scope` 包含 run、进程代次、session 及 G2 确认的阶段身份。
不能把跨阶段的 empty 与 idle 拼接。多个后台任务、恢复/嵌套任务和晚到消息的处理
必须由 G3/G4 证明；不能仅因为“没有观察到新事件”就宣布协议覆盖完整。

### 7.3 单一终态出口

成功仍返回 `TurnOutcome { engine_session_id, final_text }`，由现有 run 循环完成
inject/session 持久化、`chat_final` 和清理。Hook 不接触上游 sender 或 RunHandle。
保留 run 级 deadline、watchdog、取消与进程树清理；未知证据不降级为成功。
确认 gate 已失败时可提前结束为既有 `runtime_error`，使用脱敏原因标签
`completion_capability_unavailable` / `completion_evidence_unavailable` / `completion_evidence_conflict`。
这些是建议诊断标签，不新增对外 errorKind；具体错误消息与等待上界在 G2 验收后固定并测试。
有界等待只控制失败时机，超时不能作为成功证据。Hook 回调/重试/心跳不能重置
stdout idle watchdog，不能把永远没有新引擎输出的 run 维持为活跃。

## 8. 旧逻辑的退出与兼容

启动前固定模式，建议命名为 `legacy` / `observe` / `hook_idle`，作为显式受校验的
engine adapter 配置/发行策略，非散落的环境判断。默认 legacy；当前 hook_idle 只用于
明示版本的隔离研究验证，完成 G1–G4 后才能推广。若明确选择而不支持，应拒绝或显式失败，不静默回退。

| 逻辑 | 新模式下的职责 |
| --- | --- |
| `background_pending()>0` 扣住 result | 退出 final 决策 |
| init 弹出一条 queued notice | 不再作为消费完成依据 |
| user/XML 通知释放 pending | 可保留规范化/展示，不再决定能否 final |
| launch receipt / inline result 修补消费计数 | 保留真实工具事件，删除对 final 的启发式影响 |
| task/tool ID 映射、名称回填、幂等、错误标识 | 保留；不要因删除计数而破坏工具协议 |
| legacy 路径与历史回归 | 未支持版本继续明确使用，后续单独评估移除 |

禁止写成 `hook_idle_ready && old_pending == 0`。observe 模式只记录可比时刻的判定，
不能为了收集新证据改变 legacy 的结束或杀进程时点；其截断观察也不能当完整验收。

## 9. 模块、性能与可观测性

实现位置相对 `apps/bcs`：

- `crates/tools/bcs-bridge/core/src/engine/claude_code.rs`：接线与现有 mapper 兼容。
- `engine/claude_completion.rs` 执行证据校验与判定；`engine/claude_code/`
  分离 mapper、权限和测试，所有修改源码均不超过 1000 行。
- 不增加 app helper/IPC；公共 CLI 与内部 cfuse factory 共用 core 的原生控制协议实现。
- `engine/cli.rs`：仅在需要时增加可测试的子进程配置/资源所有权接线。
- 不直接修改 run buffer/plugin 发送逻辑；内部发行已有 factory 共用 core，未增加内部运行逻辑。

每条 delta 不做文件/数据库/IPC 访问；每次 Stop 至多一次有界本地报告，不按 token 回调。
候选只保留最新一个、限定字节数；identity/快照去重索引有容量与生命周期上限。
不增加 DB round trip，不扫描 HOME，不解析历史 transcript，不读取 hook 日志。

结构化诊断至少包含 run/session 的受控标识、process generation、epoch、来源、
候选结果是否存在、快照 Unknown/empty/nonempty、拒绝原因、耗时与判定模式。
记录 `candidate_deferred`、`completion_evidence_matched`、`completion_evidence_rejected`；
不记录正文、工具参数、思考、环境、auth 或回调凭据。trace 与日志使用同一脱敏规则。

## 10. 验收标准

| ID | 场景 | 必须断言 |
| --- | --- | --- |
| AC-01 | 本次 3 Agent + 4 Bash 的合成回放，消费信号缺失 | 有效组合证据后成功且仅一个 final，不等 watchdog |
| AC-02 | result + idle 时仍有活后台任务 | 非空快照阻止提前完成，进程存活继续接收 |
| AC-03 | 通知在长回答中入队，中间无 idle | 中间 result 不完成，最后处理后的 result 才完成 |
| AC-04 | 消费发生在工具循环，无新 init/user 投递帧 | 新路径不需要补消费计数 |
| AC-05 | 新阶段/新输入/重启/旧 callback/旧 idle | 不能跨 scope 配对或补发旧答案 |
| AC-06 | Hook 重复、乱序、迟到、阻止 Stop、同 prompt 多 Stop | 强关联或明确拒绝，不按“最后一个”猜测 |
| AC-07 | Hook 缺失、policy 禁用、字段缺失、坏 JSON、部分覆盖 | Unknown/unsupported，不当作空 |
| AC-08 | 嵌套、恢复、别名、MCP、长驻任务 | 明确覆盖或不支持，不过滤后假成功 |
| AC-09 | final 与 abort/error/deadline/retention 竞态 | 一个终态，保持已有取消/错误与清理契约 |
| AC-10 | 配置含原有 hooks/权限/MCP/env，路径含空格 | 临时注入正确合并、不修改原配置，无 shell 注入 |
| AC-11 | IPC 伪造/重放/超限/挂起、helper 或引擎崩溃 | 有界处理、隔离资源清理，不影响其他 run |
| AC-12 | Plugin/Gateway 重连、重放、session 与 inject 持久化 | 现有协议回归不退化，工具结果不丢失 |
| AC-13 | legacy/observe/hook_idle 模式与不支持版本 | hook_idle 模式固定无 fallback；observe 仅在发送业务输入前允许放弃观察并重启为 legacy，且显式诊断 |
| AC-14 | 普通无后台、空文本成功、执行失败、权限交互 | 不新增内容丢失或重复，错误不伪装成功 |

## 11. 发布与停止条件

先完成 Gates 与确定性回归，再对验证过的版本组合进行小范围 opt-in；业务不需手工配 Hook。
模式回滚只影响新 run，活跃 run 不切换算法；回滚不等于主动杀所有进程。
发布记录必须包含实际 launcher/CLI 版本、能力 profile、测试结果及不支持范围。
若出现早结束、跨轮关联、旧候选补发，停止新模式推广，保留脱敏证据并恢复新 run 的 legacy 配置。
不把“这次 Stop 快照为空”或“未出现超时”当作完成验收。
