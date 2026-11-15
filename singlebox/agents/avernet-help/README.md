# Avernet 使用帮助 profile

该 profile 提供唯一的“协作助手”，用于解答自定义协作问题、编写和校验
BCS `state_machine` YAML，并生成可在聊天副屏打开的只读快照。它不会自动建群、绑定
真实 Bot 或启动 Run。

## 启动依赖

- singlebox 的公共依赖、模型和 BCS 服务已经按项目说明准备完毕。
- `bcs-cli` 在 Bot Runtime 的 `PATH` 中；在线校验沿用当前 Bot 的 BCS 身份。
- Runtime 同时提供公共 `bcs-coordination` Skill，帮助 Skill 会按需读取其中的 schema
  和诊断参考，不依赖开发机绝对路径。

在仓库根目录运行：

```bash
./singlebox/singlebox.sh start bots --profile-dir singlebox/agents/avernet-help
./singlebox/singlebox.sh status bots --profile-dir singlebox/agents/avernet-help
./singlebox/singlebox.sh restart bots --profile-dir singlebox/agents/avernet-help
```

`bots.json` 中的 `skills` 只是能力描述；实际注入内容来自
`collaboration/skills/`。新增其他帮助角色时，应新增独立 source 目录和 manifest 条目，
不要把本 Skill 注入普通 Bot。

该 Bot 的 `name` 使用“协作助手”，沿用 Singlebox 的名称配置方式。
