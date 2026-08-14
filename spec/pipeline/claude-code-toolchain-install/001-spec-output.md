# Claude Code toolchain 安装检查

## 问题

`./scripts/singlebox.sh install-tools` 会检查并引导安装 OpenClaw、Rust 等依赖，
但不会检查 Claude Code CLI。启用 Claude relay 的 hybrid 环境因此可能直到启动时才发现
缺少 `claude` 命令。

## 范围

- `install-tools` 增加 Claude Code CLI 检查。
- 已有可执行的 `CLAUDE_CODE_PATH` 或 PATH 中的 `claude` 时跳过安装。
- 缺失时，经现有交互确认后用固定包名和
  `https://registry.npmmirror.com` 安装 `@anthropic-ai/claude-code`。
- 安装后解析并校验实际 CLI 路径，在当前脚本进程中导出
  `CLAUDE_CODE_PATH`，同时打印可复制到调用终端的 export 命令。

不修改 `~/.zshrc`、`~/.bashrc` 或任何密钥配置；不接受动态包名、registry 或 shell
命令内容。

## 实施计划

1. 为 toolchain 增加红灯测试：已有 CLI 跳过 npm、缺失时使用固定 npm 参数并导出
   实际路径、安装后仍无法解析 CLI 时失败。
2. 在 `scripts/toolchain.sh` 增加最小 Claude CLI 解析、安装和 setup 函数，并把它接入
   `toolchain_setup`。
3. 更新 toolchain 帮助摘要，执行 focused shell 测试、语法检查与 diff 检查。

## 验收标准

- 已有 `CLAUDE_CODE_PATH` 的可执行文件时不会调用 npm。
- 缺失 CLI 时 npm 仅收到
  `install -g @anthropic-ai/claude-code --registry=https://registry.npmmirror.com`。
- 成功后当前进程的 `CLAUDE_CODE_PATH` 是一个可执行路径；日志不含凭据。
- 安装后仍没有可执行 CLI 时返回非零并输出可人工执行的固定安装命令。
- `scripts/test_singlebox_toolchain.sh`、Bash 语法检查和 diff 检查通过。
