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
│   ├── parallel-expert-review.yaml
│   ├── solution-and-risk-review.yaml
│   └── single-bot-guided-answer.yaml
├── en-US/               # 美式英文
│   ├── write-and-review.yaml
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

| id                               | 中文名         | 英文名                       |
|----------------------------------|----------------|------------------------------|
| write-and-review                 | 写作质检协同   | Write & Review               |
| parallel-expert-review           | 多专家并行协同 | Parallel Expert Review       |
| solution-and-risk-review         | 方案与风险评审 | Solution & Risk Review        |
| single-bot-guided-answer         | 单 Bot 引导回答 | Guided Single Answer         |

## 新增语言

1. 创建目录 `{locale}/`
2. 翻译所有模板文件，保持相同文件名
3. 确保 YAML 结构（节点拓扑、transitions、participant slot key）与其他语言一致，仅 name/description/display_name/instruction/criteria 等展示文案不同
