# Merchant hybrid 店庆流程三轮稳定性验收

## 目标

在同一代码与配置基线上，连续执行三轮相互独立的店庆协作流程，验证
`3 OpenClaw + 1 Claude Code` 拓扑能否稳定完成拉群、Worker 协作、
one-shot 状态机、店主 HumanInput 接受以及最终店庆方案交付。

## 固定输入

每轮在新的店长私聊群中发送相同任务：

> 今年要做18周年店庆。下周开始，活动为期一个月。
>
> 原则只有一条：品质不变。第一目标是多来客人，第二目标是提高转化率。
> 老客主推护理套餐，新客用王牌剪发引流。活动贡献毛利率不能低于10%。
>
> 请你协调平台营销、平台数据和平台供应链，协商出一套可执行、可验收的
> 周年庆方案和SOP。

HumanInput 固定回复：`接受当前版本`。

## 每轮步骤

1. 新建独立自由聊天群，仅包含店长日常运营与测试用户。
2. 发送固定任务，等待店长创建新的 manager-worker 群。
3. 确认 manager-worker 群同时包含店长、平台营销方案、平台数据分析（当前）
   和平台供应链，且三位 Worker 都产生本轮回复。
4. 确认店长创建 one-shot 状态机；等待 HumanInput 后提交固定接受文本。
5. 等待状态机终态，并区分官方状态机输出与后续普通 chat 补发输出。
6. 保存本轮最终店庆方案、群组/会话/run 追踪信息与关键断言。

## PASS 标准

单轮必须同时满足：

- 新建私聊群和新的 manager-worker 群，不能复用上一轮会话。
- 3 个 OpenClaw bot 与 1 个 Claude Code bot 在线且参与正确。
- 营销、数据、供应链均产生本轮回复；平台数据分析回复来自 Provider 路径。
- one-shot 图包含三类 Worker 复核、LLM Judge、最多三轮反馈与 HumanInput。
- HumanInput 接受后，官方状态机输出直接包含
  `DELIVERY_DECISION=ACCEPTED`、版本、run ID、Plan A/B、四项检查和待执行外部动作，
  不能依赖状态机结束后的普通 chat 补发。
- 最终方案保存为独立 Markdown，且不包含凭据。

三轮均 PASS 才能判定流程稳定；否则报告真实通过率与每轮失败阶段。

## 产物

- `output/merchant-hybrid/stability-3x-20260811/run-01-plan.md`
- `output/merchant-hybrid/stability-3x-20260811/run-02-plan.md`
- `output/merchant-hybrid/stability-3x-20260811/run-03-plan.md`
- `output/merchant-hybrid/stability-3x-20260811/run-*-trace.json`
- `spec/pipeline/merchant-hybrid-stability-3x/005-qa-report.md`

群、会话和状态机数据保留，服务在三轮结束后停止。
