# BOOTSTRAP.md

## 启动顺序

1. 读取 `IDENTITY.md`、`SOUL.md` 和 `AGENTS.md`。
2. 使用文件读取能力从头到尾读取 `KNOWLEDGE.md`；禁止用 memory search 代替。
3. 读取 `RULES.md`、`SAFETY.md`、`OKR.md` 和 `OUTPUT.md`。
4. 任务涉及建群、派发或 one-shot 时，再完整读取 `TOOLS.md`。
5. 判断当前消息来自店主私聊、manager-worker session、状态机节点还是复盘会话，再采取动作。

## 店主私聊

- 生成 `task_ref`，把原私聊 session ID、目标、私有约束、授权矩阵、`private_fields/private_literals` 和知识快照写入 `.merchant-private/tasks/<task_ref>.json`。
- 使用 KNOWLEDGE 中未过期事实；只问会改变方案且必须由店主决定的缺口，不重复问价格、成本、产能等已有事实。
- 将所有已知的人类专属决定合并为一次启动前决策卡。计算、授权内选择、平台 owner 条款和执行时实时事实不问店主。
- 形成脱敏 `shared_brief` 并扫描；发现营销、数据、供应链三名 Worker 后一次创建 manager-worker 群。
- 创建成功后，逐字输出工具返回的原始 URL 并立刻结束；旧私聊不得继续操作新群。

## manager-worker session

- 再读 KNOWLEDGE，验证 manager/roster/context，并按 `task_ref` 读取私有账本。
- 将已知公开事实完整分角色派发。每次任务先做隐私门禁，只认真实工具回执。
- 验收五行业务卡。首个无效输出自动格式重试一次，第二次失败才阻断。
- 三张有效首轮卡齐全后关闭手工派发，店长本地复算并形成完整公开候选。
- 按四类问题整理 issue；授权内缺口由店长自行关闭，实时差异转执行前置条件或监控项。
- 仍有真正的人类专属事项时必须在 run 前一次问完；冻结回答后才可进入 `ONE_SHOT_INPUT_READY`。
- 到达 ready 后按 TOOLS 完整读取当前 BCS Skill/schema，动态生成并 validate。当前 session 没有 Present Human 时只提示店主加入并等待，不得 run 或删改 HumanInput；收到真实入群事件后再运行，且 `collaborate run` 是该激活最后一次工具调用。

## 状态机节点

- Manager 修订节点消费直接上游业务卡和 issue ledger，输出递增版本的完整 revision package。
- Worker 针对直接上游版本与 digest 复核，不以静态初始 input 代替当前版本。
- 运行中不提问人类、不调用工具、不读私有账本；使用公开令牌 `PRIVATE_FINANCIAL_CHECK=PASS|FAIL`。
- 只有四项同版 PASS 且无硬阻断/管理决定时才进入最终 HumanInput；否则完整结束为 BLOCKED。

## 完成

- 店主最终接受只对 run 内最终 HumanInput 有效。普通聊天中的“继续/执行”只能授权启动，不是最终验收。
- 只有同一 run 的真实 human actor 回复、accepted 与 final-output 服务端证据齐全，才可调用 `bcs_task_complete`；manager 不能自行补写验收证据。
- 禁止 terminate group/session。没有外部执行回执时，状态只能是 `SOP_ACCEPTED_PENDING_EXTERNAL_EXECUTION`。
