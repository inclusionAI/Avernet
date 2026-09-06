# 两个临时自定义协作的形状

## 持久副屏与公开/私有边界

每个游戏 session 只有一个 `undercover-game-<session>` 标签，提交时固定 `--panel-tab-closable false`。每次发言、投票、后续轮次或 `--retry` 仍生成独立 YAML/input/panel-params 文件，但用同一标签更新到新的 run；标题继续显示轮次与阶段。

`public_panel_projection` 是公开参数的唯一入口：发布完整原始座位 `seatOrder`、存活行动顺序 `turnOrder`、显式座位号、主持节点映射、脱敏 `publicHistory`、公开规则和人类可投候选人。它不发布词、身份、原始发言、原始票面、未公开票向或私有推理。

人类发言/投票节点在自然语言 instruction 末尾追加 `UNDERCOVER_UI_CONTEXT_V1` JSON 块。这个块只属于该 HumanInput 的认证读取边界，用于副屏显示本人词、轮次、座位和输入约束；Bot 节点 instruction、运行 input、公开参数与普通历史不复制该块。人类投票提交 `{"kind":"vote","target_actor_id":"<actor-id>"}` 或 `{"kind":"vote","abstain":true}`，事实层先严格解析并校验存活/非自己目标，再对非结构化 Bot 文本使用原有票号/名字解析。

副屏 nodeActorMap 将 speak_N / vote_N 映射到玩家，vote_open / collect / tally 映射到主持人。
不再生成 speak_open；投票保留 vote_open 及其主持人映射。主持人 actor 本身保留。

## 发言直接启动，投票由主持人开场

YAML 只由脚本生成，运行中的主持人不要手写或改图。
主持人仍控制开轮、开投、汇总、计票和终局；玩家只执行自己的发言/投票。

发言：首位存活玩家为入口，可以是 Bot 或 human_input。每个发言节点连向其后所有
发言节点和 collect，后手能收到本轮全部前序原话，collect 收到全员原话。
投票：vote_open 为唯一入口，由主持人播报开投，再扇出全部 vote_N；
投票节点彼此无边，全部连接 tally。不生成玩家准备节点。
collect / tally 保持 final_output，阶段推进仍依赖其结束后的回灌。

发言开场由脚本生成 announcement，写入共享 Input.opening，并在提交成功后返回主持人播报。
开场只有公开轮次、行动提示、发言顺序；重开时包含旧发言/票作废说明。
投票开场由 vote_open 主持人节点产出，不包含个人名字、词、身份、票向或倾向。完整历史仍通过 Input.history 传入。
Bot 的词只放本人 instruction；human 的词仍只在本人认证节点上下文中。

发言玩家可能在主持人播报结束前开始行动，这是有意接受的展示时序变化；不再让行动依赖开场播报。
人类节点超时 15 分钟，普通 Bot 7 分钟，主持人汇总/计票 10 分钟，max_attempts=1。
投票主持人入口仍保留 15 分钟超时。

## 为什么只移除发言主持人入口

2026-09-05 两次第一轮都停在已 ACK 的 speak_open。第二次已加载短收尾提示，
主持人确实只回复「好。」，仍未启动开场任务。缩短回复不足以解决交接问题。
当前仅在发言阶段移除同步自派开场任务。投票已完整恢复到本次排障前的仓库版本（16146b671），包括入口、超时、提交与重开提示；
提交后立即结束激活，仍可能遇到之前的队列交接问题。
这是应用层避让，并非引擎队列修复：后续汇总、计票、回灌仍可能遇到引擎调度故障。

## 派任务会打断我自己

bcs_assign_task 的派单状态与回执都可能回灌并干扰主持人会话。
只在开票稿回灌后、pending_ping 非空时派遗言，且必须是最后一个工具调用。
提交运行后不派看门狗、不 sleep、不轮询，不用背景进程延迟提交。
collect/tally 内不启动下一运行，必须结束后等待回灌，否则会占着自己需要的协作槽位。
IN_COLLECT_NODE / IN_TALLY_NODE 立即结束；RUN_SLOT_BUSY 只说明服务端仍有活跃运行。
卡住时按 phase-machine.md 的 SX 检查，不能凭未见开场消息推断运行失败。
同一轮最多重开两次；仍失败则请人类新建会话，不猜测推进。

副屏组件统一使用 `--panel-component bcsPanel.UndercoverGamePanel`，由 BCS manifest 中的 `bcsPanel` 包提供；提交必须使用本局 session ID。
