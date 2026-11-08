# Singlebox Profiles

项目后续新增的 singlebox Bot profile 统一放在 `singlebox/agents/`。每组 profile 使用一个稳定主题名的子目录，由该目录的 `bots.json` 定义要启动的 Bot。

## 目录约定

```text
singlebox/agents/
├── README.md
└── <name>/
    ├── README.md
    ├── bots.json
    └── <source>/
        ├── AGENTS.md
        ├── IDENTITY.md
        ├── KNOWLEDGE.md
        ├── TOOLS.md             # 按需提供
        └── skills/              # 该角色需要的 Skill
```

- `--profile-dir` 指向包含 `bots.json` 的具体子目录，不指向 `singlebox/agents/` 本身。
- 同一组新增 Bot 时，在该组目录下追加 source 子目录和 bots.json 条目；新增独立主题组时，在 `singlebox/agents/` 下追加子目录。
- 命名表达稳定职责，不包含开发阶段或 Bot 数量。
- 沿用 singlebox 现有 manifest、角色文件、端口和 Skill 复制规则。各角色只在自己的 `skills/` 中提供所需内容；bots.json 的 `skills` 字符串仅为能力描述。
- `bots.json` 条目的 `name` 同时用于 `botId` 和 `botName`。

## Avernet 使用帮助

已交付目录为 `singlebox/agents/avernet-help/`，一期只包含自定义协作助手 `avernet-help-collaboration`。该助手提供咨询、YAML 编写/校验和只读副屏预览，不自动建群或执行协作。设计与验收边界见 [spec](../../apps/bcs/specs/2026-09-23-collaboration-definition-preview/spec.md) 与 [tasks](../../apps/bcs/specs/2026-09-23-collaboration-definition-preview/tasks.md)。

交付完成后，在仓库根目录使用：

```bash
./singlebox/singlebox.sh start bots --profile-dir singlebox/agents/avernet-help
./singlebox/singlebox.sh status bots --profile-dir singlebox/agents/avernet-help
./singlebox/singlebox.sh restart bots --profile-dir singlebox/agents/avernet-help
```
