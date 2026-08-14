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

然后直接启动；`start hybrid` 会自动完成项目 setup：

```bash
./scripts/singlebox.sh start hybrid \
  --profile-dir scripts/4bots_merchant_operations_profile \
  --exclusive-profile-dir platform-data \
  --claude-profile-dir scripts/4bots_merchant_operations_profile_for_claude
```
