# Bug：主持人自派状态机入口任务收到 ACK 后未执行，游戏停在第一轮开场

- 报告日期：2026-09-05
- 建议优先级：P1（阻断游戏流程，偶发，当前没有可靠的局内恢复手段）
- 疑似涉及：OpenClaw 同会话任务队列 / 活动任务清理，以及 BCN 插件的运行生命周期衔接
- 状态：已多次观察到相同卡点；具体代码根因未定位，未确认修复
- 时间口径：以下时间均为北京时间 UTC+8；原始 session JSONL 使用 UTC

## 问题与影响

“谁是卧底”主持人在处理当前会话消息时，通过 `undercover.py open-round` 或 `open-vote` 同步提交 BCS 状态机。状态机立即把入口 `speak_open` 或 `vote_open` 派回同一主持人、同一会话。

BCS 记录投递成功且 Bot 返回 ACK，主持人原激活随后发出 final/end，但入口任务没有开始执行。状态机等待该入口完成，后续玩家节点无法启动，副屏一直停在发言或投票阶段。

这是偶发现象：用户曾在新建另一个 session 后正常通过相同位置。尚无可靠复现概率或失败率统计。

## 环境与流程

- 本地 Avernet standalone 环境，BCS + OpenClaw BCN channel 插件。
- 游戏配置：1 个主持人 Bot、5 个玩家 Bot、1 名人类玩家。
- 外层会话为 `manager_worker`；发言和投票通过独立自定义 `state_machine` 运行。
- 日志显示模型为 `GLM-5.2`、`openai-completions`。没有证据表明故障由模型服务报错触发。
- 原投票流程：`vote_open（主持人） → 所有 vote_N（并行） → tally（主持人，final_output）`。
- 原发言流程：`speak_open（主持人） → 按顺序发言 → collect（主持人，final_output）`。各发言节点连接其后所有发言节点及 collect，使后手获得完整前序发言。
- 主持人入口超时为 900000 ms，节点 `max_attempts=1`。

版本说明：排障前 referee 源码基准为 `16146b671`；报告生成时仓库 HEAD 为 `38c047d5dd0bbec039738ae262d544c19b1518e5`。排障期间有未提交改动及 profile 重载，不能把任一 HEAD 直接当作所有故障现场的完整运行版本。

## 复现步骤

1. 启动该游戏 profile，新建游戏 session，并让人类加入。
2. 向主持人发送“开始”，主持人发牌并提交第一轮发言状态机。
3. 当提交期间入口任务被立即派回同一活动主持人会话时，观察原激活结束后是否开始入口任务。
4. 若发言轮正常完成，继续观察主持人在汇总回灌后执行 `open-vote` 的同一交接位置。
5. 对比 BCS 的入口投递/ACK、Bot 的实际运行开始/结束事件和引擎队列诊断。

此步骤描述已观察到的触发路径，不保证每次复现。不需要玩家输入错误，也不依赖重复点击或主动重试。

**预期：** 入口可以排队等待原激活结束；结束后应被调度执行，并以入口任务对应的运行标识返回完成事件，进而启动玩家节点。

**实际：** 入口已 ACK，但没有可见的实际执行或完成记录；原聊天 final 只完成原聊天运行，不能完成状态机入口。

## 三次故障证据

### A. 第一轮发言入口卡住

- BCS session：`bcs_grp_331d309a1252421f945201d4327cc1f0:5514f523`
- 状态机 run：`sm-d6e349b6-0a85-43ac-bfa9-973a93eff719`
- 原聊天 run：`47b7be14-ecea-45c8-a1d0-d8a086662a54`
- OpenClaw session：`6007268b-7120-4eb2-a292-cc81ded5c39e`

| 时间 | 观察 |
| --- | --- |
| 18:52:03 | 状态机创建成功，`speak_open` 投递并 ACK；原主持人激活此时尚未结束。 |
| 18:52:12 | 原主持人激活输出开场文字并 final/end，入口没有随后执行。 |
| 18:56:24 | 引擎报告队列有任务但没有活动运行。 |
| 18:56:24 起 | 恢复逻辑认为仍有活动回复工作，反复跳过恢复。 |

诊断日志关键字段摘录（省略会话标识等字段，保留原字段值）：

```text
stuck session:
state=processing age=130s queueDepth=2
reason=queued_work_without_active_run
classification=stale_session_state
lastProgress=run:completed lastProgressAge=253s
terminalProgressStale=true recovery=checking

stuck session recovery outcome:
status=skipped action=keep_lane
activeWorkKind=embedded_run reason=active_reply_work
```

两个判断之间存在值得排查的不一致：进展诊断认为没有活动运行，但恢复检查仍认为存在活动回复工作。现有证据无法判定具体是哪一份状态没有清理，或是否有尚未完成的内部工作。

### B. 已采用短收尾提示，第一轮发言仍卡住

- BCS session：`bcs_grp_7b347538b3254c8dbaafcc773caad7e3:a9b0d3e8`
- 状态机 run：`sm-55744192-f3e3-4650-814e-7c02eec9ed0a`
- 原聊天 run：`601885a6-50f6-43bb-89ea-b090f00e65fe`
- OpenClaw session：`8808cb1a-5a6f-4c86-b1e3-b7c06f058f4b`

| 时间 | 观察 |
| --- | --- |
| 19:13:01 | `speak_open` 投递成功且 ACK。 |
| 19:13:05 | 主持人严格按新提示仅回复“好。”，原运行发出 final/end，BCS 注销原运行 channel。 |
| 截至 19:15:19 | 没有入口开始执行或完成记录，也未启动玩家节点。 |

已核对运行 workspace 内的 `undercover.py`、`SKILL.md` 与当时修改后的源文件完全一致；工具返回也明确包含新短收尾提示。因此不能解释为“修改没有加载”或“主持人未遵守短收尾要求”。

这次检查时尚未出现 A 中的自动 stuck-session 诊断，能确认相同卡点，不能仅凭外部表现证明内部原因完全一致。

### C. 发言成功完成，投票入口卡住

- BCS session：`bcs_grp_bbcdf0be80a748bca91bcac9e2096d44:2b199077`
- 已完成的发言 run：`sm-724188d3-1b3c-42b5-ac77-9447c870046c`
- 卡住的投票 run：`sm-193ec1b6-21b9-4260-b488-8d6dea9381c3`
- 提交投票的聊天 run：`07441c30-e274-430d-9080-5a77481fe0da`
- OpenClaw session：`ea91bec8-a2e0-468f-866d-91865f47d4e1`

| 时间 | 观察 |
| --- | --- |
| 19:44–19:47 | 发言节点依次推进，人类及 Bot 发言后进入 collect。此局发言已使用“直接从玩家开始”的实验方案。 |
| 19:47:50 | collect 产物 final；后续回灌触发开投。 |
| 19:47:59 | 主持人执行 `open-vote`。 |
| 19:48:00 | `vote_open` 投递成功并 ACK；工具返回 `VOTE_RUNNING`、`submitted=true`。 |
| 19:48:04 | 主持人仅回复“好。”并 final；随后检查未见投票入口执行。 |

此局说明发言链及 collect/回灌可以成功，卡点出现在再次向活动主持人会话派发投票入口时。此现场尚无已采集的同类自动恢复诊断。

## 已尝试方案与结果

| 方案 | 结果 / 当前处理 |
| --- | --- |
| 把成功提交后的回复固定为“好。”，发牌说明移到提交前 | B 明确加载并遵守，仍卡住，不能作为有效修复。 |
| 移除发言的主持人入口，直接从首位玩家开始 | C 中发言及汇总成功通过，但没有足够重复对照证明总体故障率下降。 |
| 投票增加玩家 `vote_ready`，回复“准备完成”后扇出 | 未验证可靠性；因流程推进必须由主持人承担，产品明确否决，已移除。 |
| 恢复主持人 `vote_open` | C 再次卡在入口。 |
| 完整回滚投票相关实现到排障前基准 | 4 个投票相关函数与基准一致，本地 15 项测试通过；尚无回滚后的实局结果。原版也有自派主持人入口，不能宣称回滚修复了引擎问题。 |

上述测试验证的是生成图、数据边界和命令行为，未覆盖真实 OpenClaw 会话队列交接。修改 profile 源码不会修改已提交的运行；需重载到 Bot workspace 后在新局验证。

## 当前判断与建议排查

**已确认：** 故障发生在“入口投递并 ACK”到“入口实际执行”之间；BCS 能收到原运行 final，但没有对应入口完成产物。短收尾无法消除问题。

**待验证假设：** 同会话重入时，原回复任务已对外结束，但内部活动工作记录、dispatcher promise 或 session lane 未释放，导致后续入口无法启动；自动恢复又受残留活动状态阻挡。这不是已定位到具体代码行的根因结论。

建议优先检查：

1. BCN 插件 `src/bcs/crates/plugins/openclaw-channel-bcn/src/inbound-handler.ts` 中 `recordInboundSession`、`dispatchReplyWithBufferedBlockDispatcher`、`onAgentRunStart`、终态事件处理与 `cleanupRunContext` 的先后顺序。
2. 原运行发出 lifecycle end 后，SDK dispatcher 是否完成，session lane 和活动回复工作是否仍被占用。BCS 的 channel 注销不等于 OpenClaw 内部队列已释放。
3. 排队任务是否真正入队，何时开始，以及 BCS delivery ID 与 OpenClaw agent run ID 的绑定是否完整。ACK 不能等同于实际执行成功。
4. `queued_work_without_active_run` 与恢复分支 `active_reply_work` 为什么同时成立；应记录两者各自的状态来源、拥有者和清理时机，避免盲目强制释放真实工作。
5. 对同一会话“工具调用中自派任务”的交接增加确定性并发测试，不依赖 LLM 输出速度制造竞态。

## 修复验收建议

- 在原运行执行工具期间、即将结束时和结束后分别投递同会话入口，入口均应最终执行一次。
- 原运行 final 与入口 final 必须正确关联各自运行，不丢失、不串用，不重复执行节点。
- 正常结束、异常结束及取消均应正确清理内部工作状态；stale-session 恢复不能持续被无主的活动标记阻挡。
- 覆盖主持人发言入口、投票入口、collect/tally 后回灌，以及多 session 交替运行。
- 保留主持人开投职责、玩家并行独立投票及词语隔离，不通过玩家准备任务或无限重试绕过验收。

## 日志与证据保存说明

本报告依据本次排障中实际读取的日志整理。最初读取位置：

- `scripts/.dependencies/logs/bcs.log`
- `scripts/.dependencies/logs/undercover-referee.log`
- `.standalone-openclaw/profiles/undercover-referee/agents/main/sessions/<OpenClaw session ID>.jsonl`

报告生成时再次检查，`undercover-referee.log` 已不存在，因此上述摘录是此前读取记录，未附当前仍可下载的完整原始日志。请开发团队按以上 session/run ID 对照已有归档；后续复现时应在重启或清理环境前保存 BCS、Bot 和 session 日志，并记录实际运行版本。

完整日志可能含玩家词语、内部模型地址或认证信息；分享前应脱敏。本报告未包含游戏词语、身份分配、模型内部推理、凭据或私有服务地址。
