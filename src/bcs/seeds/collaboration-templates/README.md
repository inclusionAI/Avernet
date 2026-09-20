# 结构化协同模板 Seed

这是 BCS 内置结构化协同模板的 canonical seed source。

- local file mode 直接从这里读取模板。
- `bcs-admin template seed` 默认从这里生成 DB seed 数据。
- 非 local / 生产部署不应在运行时依赖这个目录；应先把这些模板 seed 到 mysql-backed catalog。

## 目录结构

```
collaboration-templates/
├── zh-CN/               # 简体中文
│   ├── write-and-review.yaml
│   ├── write-review-loop.yaml
│   ├── research-writing-loops.yaml
│   ├── world-cup-preview-content-production.yaml
│   ├── micro-merchant-event-orchestration.yaml
│   ├── parallel-expert-review.yaml
│   ├── solution-and-risk-review.yaml
│   └── single-bot-guided-answer.yaml
├── en-US/               # 美式英文
│   ├── write-and-review.yaml
│   ├── write-review-loop.yaml
│   ├── research-writing-loops.yaml
│   ├── world-cup-preview-content-production.yaml
│   ├── micro-merchant-event-orchestration.yaml
│   ├── parallel-expert-review.yaml
│   ├── solution-and-risk-review.yaml
│   └── single-bot-guided-answer.yaml
└── README.md
```

## 命名规范

- 目录名使用 BCP 47 locale tag：`zh-CN`、`en-US`、`ja-JP` 等
- 文件名是模板 ID（kebab-case），不带 locale 后缀
- 每个语言目录下的文件集合建议一致；允许后续按 active 内容行表达部分语言可用

## 模板清单

| id | 中文模板 | English template |
| --- | --- | --- |
| write-and-review | [写作质检协同](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/write-and-review.yaml) | [Write & Review](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/write-and-review.yaml) |
| write-review-loop | [写作评审循环](zh-CN/write-review-loop.yaml) | [Writing Review Loop](en-US/write-review-loop.yaml) |
| research-writing-loops | [资料与写作双循环](zh-CN/research-writing-loops.yaml) | [Research and Writing Loops](en-US/research-writing-loops.yaml) |
| world-cup-preview-content-production | [世界杯比赛前瞻内容生产](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/world-cup-preview-content-production.yaml) | [World Cup Preview Content Production](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/world-cup-preview-content-production.yaml) |
| micro-merchant-event-orchestration | [小微商家活动协同](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/micro-merchant-event-orchestration.yaml) | [Micro-Merchant Event Orchestration](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/micro-merchant-event-orchestration.yaml) |
| parallel-expert-review | [多专家并行协同](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/parallel-expert-review.yaml) | [Parallel Expert Review](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/parallel-expert-review.yaml) |
| solution-and-risk-review | [方案与风险评审](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/solution-and-risk-review.yaml) | [Solution & Risk Review](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/solution-and-risk-review.yaml) |
| single-bot-guided-answer | [单 Bot 引导回答](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/single-bot-guided-answer.yaml) | [Guided Single Answer](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/single-bot-guided-answer.yaml) |

## Loop 模板

`write-review-loop` 包含三个必选角色 `writer` / `reviewer` / `polisher`，使用 v2 hierarchical
状态机。先写作/修订，再由主编评审；Judge 根据完整稿件和主编的明确结论选择
`approved` 或 `revise`，达标即可离开循环，无需执行满三次。

- `approved → polish → finalize`：润色编辑可独立绑定 Bot，保留通过稿件的事实与结论并改善表达。
- `exhausted → rewrite → finalize`：持续返修达到 `max_iterations: 3` 后，写作者依据必改问题重写，披露未解决问题且不宣称已通过。
- `revise`：未达到上限时继续写作和评审；主编的输出保留完整稿件、评审结论和必改问题。

两条出口互斥，最终汇总消费实际执行分支的结果，保持其评审状态。
模板需要可用的 LLM Judge；未配置时列表沿用“需要启用 LLM”提示，校验也会明确指出依赖。

测试部署可在 `[collaboration]` 下设置 `loop_execution_enabled = true`，重启 BCS 后即可
使用 v2 Loop 执行；运行本模板还需配置 LLM Judge。此开关同时允许 Loop 恢复；普通工作流恢复随服务启动，无需独立开关。仓库的 `bcs-config-local.toml` 已为本地测试开启。
其他部署默认关闭，关闭时校验仍返回 `VALIDATION_ONLY_FEATURE`，页面禁止创建。
实验开放不代表完整 Singlebox、多实例强杀和生产 FO 发布验收已完成。
模板不会改变运行时开关或替用户配置 LLM 凭证。

local file mode 在进程内缓存模板索引，新增文件后需重启 BCS，再刷新模板列表。
DB catalog 部署仍通过 `bcs-admin template seed --dry-run` 检查，
再按已有 seed 流程生成并应用数据，无需 schema migration。

## 多个 Loop 模板

Loop 模板同时示范自定义连线名称：`loop.continue_display_name` 标注回边，
`transitions.<outcome>.display_name` 标注普通边与 approved/exhausted 出口。
这些字段只控制预览和副屏文案；Judge outcome、状态判断及 target 不变。
省略时保留原有标签；已存在的 Run 和 rerun 使用保存的快照文案。
更新后的模板需要支持这些字段的 BCS 服务，不能直接导入尚未升级的旧实例。

`research-writing-loops`（资料与写作双循环）使用四个必选角色：`researcher`、`writer`、`reviewer`、`polisher`。
两个 Loop 位于外层同一级；主编分别评审资料与稿件，Judge 分别选择 `approved` / `revise`。

```text
research_loop ── approved ──> writing_loop ── approved ──> polish ──> finalize
      │                            │
   exhausted                    exhausted
      │                            │
      v                            v
research_gaps ──> finalize       rewrite ──> finalize
```

- 每个 Loop 的 `revise` 只回到自己的入口，执行次数与上一结果相互独立。
- `research_loop` 的 `approved` 可以直接指向外层 `writing_loop`，BCS 自动进入其 `draft`；不能跨 Loop 指向内部 `draft` 或 `review`。
- 两个 body 都有 `review` 节点，逻辑作用域和执行 ID 各自独立；这是多个同级 Loop，不是嵌套。
- `writing_loop` 首次执行的 previous_result 为空，已通过的资料评审结果通过普通 upstream output 传入。后续执行只自动携带本 Loop 上一次评审结果，因此评审输出保留完整资料依据与稿件。
- 资料 exhausted 时生成缺口报告，整个写作 Loop 被跳过；写作 exhausted 时走 rewrite，最终汇总保留未通过状态。

## 新增语言

1. 创建目录 `{locale}/`
2. 翻译所有模板文件，保持相同文件名
3. 确保 YAML 结构（节点拓扑、transitions、participant slot key）与其他语言一致，仅 name/description/display_name/instruction/criteria 等展示文案不同
