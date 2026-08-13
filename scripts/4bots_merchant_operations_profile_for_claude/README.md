# 商家经营协作队 Claude Code Profile

本目录只定义替换 OpenClaw `platform-data` 的 Claude Code 平台数据分析
角色。认证和 provider 设置不在本目录；relay 使用
`~/.claude-platform-data`，当其中没有 `settings.json` 时沿用本机
`~/.claude/settings.json` 的 provider 设置。本 profile 显式指定
`Kimi-K2.6`，由 relay 作为每轮 Claude Code 调用的 model 参数传入。

启动完整协作队：

```bash
SINGLEBOX_MODEL_CONFIG_MODE=home \
./scripts/singlebox.sh start merchant_hybrid \
  --profile-dir scripts/4bots_merchant_operations_profile \
  --exclusive-profile-dir platform-data \
  --claude-profile-dir scripts/4bots_merchant_operations_profile_for_claude
```

`--exclusive-profile-dir platform-data` 是原 OpenClaw `bots.json` 的
`source` 选择器，不是目录路径。它保留三个 OpenClaw bot，并由本目录的
Claude Code bot 接替平台数据分析角色。

BCS 中活跃 Provider 卡片显示为 `平台数据分析（当前）`，以便与旧运行留下的
同名历史卡片区分。通过纯文本 `@` 点名时请使用这个完整显示名；从前端 Bot
选择器加入群聊时会自动使用当前卡片身份。

`platform-data/CLAUDE.md` 是角色 prompt 根文件。它通过 `@` 导入其余
Markdown；singlebox 在 relay 启动时安全展开这些引用，并作为静态 system
prompt 传给 Claude Code。不要将 token、Cookie、OAuth 文件或本机模型配置
提交到本目录。

修改角色文档后执行 `restart merchant_hybrid`，已有 relay 不会热加载文档。
