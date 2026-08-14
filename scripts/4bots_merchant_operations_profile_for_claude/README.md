# 平台数据分析 Claude Code Profile

此 profile 用于 `hybrid` 的 Claude Code 数据分析 Bot。默认从仓库根目录
`.env.local` 读取独立的 Anthropic-compatible 模型配置，不复用 OpenClaw 的
OpenAI-compatible endpoint。Claude Code 的本机认证配置不会写入该目录。
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
Claude Code，但仍会检测本机安装。Claude Code 的模型配置默认直接读取
`.env.local`：

```bash
./scripts/singlebox.sh start hybrid \
  --profile-dir scripts/4bots_merchant_operations_profile \
  --exclusive-profile-dir platform-data \
  --claude-profile-dir scripts/4bots_merchant_operations_profile_for_claude
```

如果本机没有 `claude` 命令，启动流程会询问是否通过 npm 安装；拒绝安装会取消
本次启动。OpenClaw 使用 `OPENCLAW_OPENAI_*`。Claude Code 的
`ANTHROPIC_MODEL` 和 `ANTHROPIC_AUTH_TOKEN` 未配置时，分别沿用 OpenClaw 的模型
和 API key；已有 `ANTHROPIC_API_KEY` 时不会被覆盖。`ANTHROPIC_BASE_URL` 未配置时，
交互式启动会展示当前 OpenAI URL，并允许输入一次 Anthropic-compatible URL；直接
回车则沿用展示值。该 URL 必须支持 Anthropic Messages API，输入仅对本次启动有效，
不会写回 `.env.local`。非交互启动必须显式配置 `ANTHROPIC_BASE_URL`。

如需显式使用用户自己的 Claude Code 配置，可设置
`HYBRID_CLAUDE_CONFIG_MODE=user`；脚本不会改写 `~/.claude/settings.json`。

不带 profile 参数执行 `restart hybrid` 时，脚本直接恢复上次成功 `start hybrid`
保存的运行模式、profiles、模型配置模式和 Anthropic base URL，不再询问以上选项。
如果没有可恢复状态，会要求先执行一次 `start hybrid`，而不是重新猜测配置。

非交互环境可以显式设置：

```bash
HYBRID_USE_CLAUDE_CODE=yes \
HYBRID_INSTALL_CLAUDE_CODE=yes \
./scripts/singlebox.sh start hybrid
```

其中 `HYBRID_CLAUDE_CONFIG_MODE` 可选 `env-local` 或 `user`。
