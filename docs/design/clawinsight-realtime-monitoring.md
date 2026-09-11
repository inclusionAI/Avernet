# ClawInsight 监控自愈独立模块方案

## 1. 与 Avernet 真实界面的关系

本 Demo 参考了 Avernet 当前前端代码，而不是只依据截图构造。

AgentEvolve 顶层导航来自：

- `src/evolverun/clawweb/public/app/clawweb/web/main.tsx`

当前真实栏目为：

```text
AgentEvolve
├── 任务护航
├── 效果中心
└── Claw进化
```

因此 Demo 已补充相同的顶层栏目，并将“效果中心”设置为选中状态。

效果中心内部再增加两个并列功能入口：

```text
效果中心
├── Agent 治理
└── 监控自愈
```

- **Agent 治理**：继续承载效果概览、问题证据、我的待办，面向历史会话统计、badcase 总结和 Agent 优化；
- **监控自愈**：面向指定 Bot 的运行状态和实时诊断记录，不把监控内容叠加到 Agent 治理的三个 Tab 中。

## 2. Bot 名称展示策略

Avernet 现有代码中确实存在 Bot 名称：

- `clawinsight` 的历史分析接口返回 `botId` 和 `botName`；
- `sessionAnalysis.bots()` 返回的 Bot 元信息也包含 `botName`；
- 服务端 `ac_bots` 表保存 `bot_name`。

但这些能力只能说明：**已登记且当前用户有权访问的 Bot，通常可以查到名称**。用户直接输入一个任意 Bot ID 时，不能假定名称一定存在或一定能被当前接口解析。

因此页面采用以下降级规则：

1. Bot ID 始终作为主标识；
2. 成功解析到 Bot 名称时，将名称作为次级信息展示；
3. 无法解析名称时，只显示 Bot ID，不显示“自定义 Bot”“未知 Bot”等虚构名称。

Demo 中第二个 Bot 专门展示了“只有 Bot ID、没有名称”的真实降级状态。

## 3. 页面信息结构

### 第一块：监控对象

- 支持用户或管理员输入业务 `bot_id`；
- 已添加的 Bot 以可切换项展示；
- Bot ID 是主要文本，名称仅在可获得时补充；
- 点击后切换当前 Bot，并刷新其运行状态和诊断记录。

### 第二块：当前 Bot 运行状态

只保留实时监控所需信息：

- 当前 Bot ID；
- 可选的 Bot 名称；
- 运行中或已暂停；
- 最近更新时间；
- 当前 Bot 的诊断记录数量。

已删除“数据源”和监控链路健康等非核心信息。页面主文案调整为面向用户的短句：

> 实时监控 Bot 运行状态，快速定位异常会话。

页面不再用首屏文案解释开发阶段或强调自愈尚未接入。

### 第三块：诊断记录

- 每条记录代表一次完整诊断结果；
- 摘要显示时间、问题类型、Trace / Session、诊断置信度和 TC 结果；
- 点击记录后展开处理人、Session Key、会话时间、TC 故障标签、诊断置信度、业务问题类型 / 子类型、系统诊断文本、业务诊断文本和通知状态；
- 不展示数据源标签；
- 不提供故障处理或自动修复按钮；
- 支持结果筛选、Session / Trace 搜索、时间范围筛选；
- 支持上一页、页码、下一页和每页 10 / 20 / 50 条切换；
- 切换 Bot、筛选条件或每页条数时自动回到第一页。

## 4. 告警记录示例

Demo 首条展开记录保留钉钉群示例信息：

- 处理人：仲彦；
- Session Key：`agent:main:bcs-cli:20260514_6ln39h2j:461514:b0dad5f7`；
- 会话时间：`2026-09-07 17:46:17（北京时间）`；
- TC 故障标签：`TC.MCP.DATA`；
- 诊断置信度：`80.0%`；
- 业务问题类型：外部服务异常；
- 业务问题子类型：数据获取失败；
- 系统诊断文本：MCP 服务 `risk_evaluation_toolkit` 返回空响应，脚本 JSON 解析失败，海豚活动数据未能获取，分发任务以关单结束；
- 业务诊断文本：用户发起海豚评审流水线（id:23800115），脚本执行过程中 MCP 返回内容为空导致活动数据获取失败，任务未成功提交，最终以关单收尾。

## 5. 数据历史与分页接入建议

当前 `claw-validation` 代码检查未发现每天清空最终诊断表 `diagnosis_result` 的逻辑。`hot_retention_seconds` 控制的是热会话复查窗口，不等于删除诊断历史。

正式接入时建议后端查询接口至少支持：

```text
bot_id
page
page_size
result_type
from
until
query
```

并返回：

```text
items
total
page
page_size
total_pages
```

前端分页应使用服务端分页，不一次性加载某个 Bot 的全部历史记录。

## 6. Demo 文件

- HTML：`clawinsight-realtime-monitoring-demo.html`；
- 设计说明：`clawinsight-realtime-monitoring.md`。

页面使用 mock 数据，没有修改 Avernet 的生产 React 页面，也没有直接复用真实 API。
