# 平台数据分析 Claude Code Profile

此 profile 用于 `hybrid` 的 Claude Code 数据分析 Bot。模型沿用 Singlebox
本次选择的配置：`manual` 模式读取仓库根目录 `.env.local`，`home` 模式读取
本机 OpenClaw 配置。Claude Code 的本机认证配置不会写入该目录。
`CLAUDE.md` 是受控角色 prompt 的入口。

## 本地启动

第一次使用先安装工具：

```bash
./scripts/singlebox.sh install-tools
```

然后直接启动；不加 profile 参数时，`hybrid` 和兼容别名
`merchant_hybrid` 会先询问是否使用 Claude Code。选择使用时启动
3 个 OpenClaw Bot + 1 个 Claude Code Bot；选择不使用时，4 个 Bot
全部使用 OpenClaw：

```bash
./scripts/singlebox.sh start hybrid
```

只传 `--profile-dir` 时，该 profile 中的 Bot 全部按 OpenClaw 启动：

```bash
./scripts/singlebox.sh start hybrid \
  --profile-dir scripts/4bots_merchant_operations_profile
```

三个 profile 参数全部传入时，OpenClaw profile 中被排除的一个 source
由 Claude profile 中同 source 的唯一一个 Bot 替代。此时不再询问是否使用
Claude Code，但仍会检测本机安装，并询问是否用 `.env.local` 替换 Claude Code
的模型配置：

```bash
./scripts/singlebox.sh start hybrid \
  --profile-dir scripts/4bots_merchant_operations_profile \
  --exclusive-profile-dir platform-data \
  --claude-profile-dir scripts/4bots_merchant_operations_profile_for_claude
```

如果本机没有 `claude` 命令，启动流程会询问是否通过 npm 安装；拒绝安装会取消
本次启动。选择不使用 `.env.local` 时，只读取用户自己的 Claude Code 模型配置，
不会改写 `~/.claude/settings.json`。

非交互环境可以显式设置：

```bash
HYBRID_USE_CLAUDE_CODE=yes \
HYBRID_INSTALL_CLAUDE_CODE=yes \
HYBRID_CLAUDE_CONFIG_MODE=env-local \
./scripts/singlebox.sh start hybrid
```

其中 `HYBRID_CLAUDE_CONFIG_MODE` 可选 `env-local` 或 `user`。
