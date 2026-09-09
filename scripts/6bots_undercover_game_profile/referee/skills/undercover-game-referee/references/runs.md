# 两个临时自定义协作的形状

## 持久副屏与公开/私有边界

每个游戏 session 只有一个 `undercover-game-<session>` 标签，提交时固定 `--panel-tab-closable false`。每次发言、投票、后续轮次或 `--retry` 仍生成独立 YAML/input/panel-params 文件，但用同一标签更新到新的 run；标题继续显示轮次与阶段。

`public_panel_projection` 是公开参数的唯一入口：发布完整原始座位 `seatOrder`、存活行动顺序 `turnOrder`、显式座位号、主持节点映射、脱敏 `publicHistory`、公开规则和人类可投候选人。它不发布词、身份、原始发言、原始票面、未公开票向或私有推理。

人类发言/投票节点在自然语言 instruction 末尾追加 `UNDERCOVER_UI_CONTEXT_V1` JSON 块。这个块只属于该 HumanInput 的认证读取边界，用于副屏显示本人词、轮次、座位和输入约束；Bot 节点 instruction、运行 input、公开参数与普通历史不复制该块。人类投票提交 `{"kind":"vote","target_actor_id":"<actor-id>"}` 或 `{"kind":"vote","abstain":true}`，事实层先严格解析并校验存活/非自己目标，再对非结构化 Bot 文本使用原有票号/名字解析。

副屏 nodeActorMap 将 speak_N / vote_N 映射到玩家，collect / tally 映射到主持人。
vote_start 是预备确认，不映射到玩家展示或投票进度；主持人 actor 保留。

## 主持人授权开轮，玩家执行，主持人裁决

YAML 只由脚本生成。主持人仍决定开轮、开投、汇总、计票和终局。
BCS 要求恰好一个零入度入口和一个 final_output 末节点，不能直接生成多个并行入口。

发言：首位存活玩家为入口，可以是 Bot 或 human_input。每个发言节点连向其后所有
发言节点和 collect，后手能收到本轮全部前序原话，collect 收到全员原话。

投票顺序：
1. 主持人 collect 汇总发言并结束；普通回灌激活调用 open-vote，检查阶段和协作槽位。
2. 主持人提交运行即授权开投，成功后立即结束当前激活，不追加模型播报或工具调用。
3. 唯一入口 vote_start 交给按座位排序的首位存活玩家 Bot，原样播报「投票开始，请大家投出一票，等待主持人公布结果。」。这不是
   选票，不携带词或判断，也不授权该 Bot 调用脚本或决定何时开投。
4. vote_start 完成后扇出全部存活玩家的 vote_N；各投票节点之间无边，全部连接 tally。
   预备确认者也必须执行自己的独立投票节点。Human 出局后仍使用存活 Bot 预备入口。
5. 主持人 tally 等所有票完成后计票裁决；继续游戏由结果回灌安排遗言或下一轮，
   终局按 SKILL.md 在当前 tally 内收尾。

发言公告写入 Input.opening，提交成功后返回主持人播报。
投票公告由脚本写入 Input.opening 与公开副屏参数 openingAnnouncement；副屏只有在
本次运行 running 且 vote_start completed 时才展示，准备期间显示等待提示，失败时
显示失败状态。重开公告包含旧票作废说明。投票公告不含名字、词、身份、票向或倾向。
完整公开发言历史仍通过 Input.history 传入；预备确认不是投票判断依据。
Bot 的词只放本人 instruction；Human 的词仍只在本人认证节点上下文中。

人类节点超时 15 分钟，普通 Bot（含预备确认）7 分钟，主持人汇总/计票 10 分钟，max_attempts=1。

## 修复范围与验证边界

2026-09-09 的投票运行在提交者仍运行时给同一主持人派 vote_open，缺失该节点完成
回执后整轮无法启动。当前取消主持人自派入口，使用已有玩家的中性预备确认满足单入口契约。
发言阶段早已取消 speak_open；现在两个阶段均不在提交后立即给主持人自派入口。

这是游戏层避让，不是 OpenClaw 通道队列修复。快速收齐时 tally 仍可能与尚未结束的
主持人激活重叠；不以延时、提示词或玩家响应速度作为顺序保证。预备确认本身也可能失败，
按运行状态走恢复流程。拓扑测试只能证明入口与主持人分离及计票依赖全员，不能证明线上
引擎交接可靠；部署验收应覆盖快速收齐、人类出局和重开。

## 派任务会打断我自己

bcs_assign_task 的派单状态与回执都可能回灌并干扰主持人会话。
只在开票稿回灌后、pending_ping 非空时派遗言，且必须是最后一个工具调用。
提交运行后不派看门狗、不 sleep、不轮询，不用背景进程延迟提交。
collect/tally 内不启动下一运行，必须结束后等待回灌，否则会占着自己需要的协作槽位。
IN_COLLECT_NODE / IN_TALLY_NODE 立即结束；RUN_SLOT_BUSY 只说明服务端仍有活跃运行。
卡住时按 phase-machine.md 的 SX 检查，不能凭未见开场消息推断运行失败。
同一轮最多重开两次；仍失败则请人类新建会话，不猜测推进。

副屏组件统一使用 `--panel-component bcsPanel.UndercoverGamePanel`，由 BCS manifest 中的 `bcsPanel` 包提供；提交必须使用本局 session ID。
