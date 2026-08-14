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
│   ├── world-cup-preview-content-production.yaml
│   ├── micro-merchant-event-orchestration.yaml
│   ├── parallel-expert-review.yaml
│   ├── solution-and-risk-review.yaml
│   └── single-bot-guided-answer.yaml
├── en-US/               # 美式英文
│   ├── write-and-review.yaml
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
| world-cup-preview-content-production | [世界杯比赛前瞻内容生产](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/world-cup-preview-content-production.yaml) | [World Cup Preview Content Production](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/world-cup-preview-content-production.yaml) |
| micro-merchant-event-orchestration | [小微商家活动协同](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/micro-merchant-event-orchestration.yaml) | [Micro-Merchant Event Orchestration](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/micro-merchant-event-orchestration.yaml) |
| parallel-expert-review | [多专家并行协同](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/parallel-expert-review.yaml) | [Parallel Expert Review](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/parallel-expert-review.yaml) |
| solution-and-risk-review | [方案与风险评审](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/solution-and-risk-review.yaml) | [Solution & Risk Review](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/solution-and-risk-review.yaml) |
| single-bot-guided-answer | [单 Bot 引导回答](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/zh-CN/single-bot-guided-answer.yaml) | [Guided Single Answer](https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/seeds/collaboration-templates/en-US/single-bot-guided-answer.yaml) |

## 新增语言

1. 创建目录 `{locale}/`
2. 翻译所有模板文件，保持相同文件名
3. 确保 YAML 结构（节点拓扑、transitions、participant slot key）与其他语言一致，仅 name/description/display_name/instruction/criteria 等展示文案不同
