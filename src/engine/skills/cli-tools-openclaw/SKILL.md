---
name: cli-tools-openclaw
description: "查找并调用平台为本 Bot 安装的命令行工具（manifest 的 cli_tools）。这些工具**不在 PATH 上**，必须用绝对路径 /home/admin/.openclaw/cli/<name> 调用。任何时候需要「某个命令行工具/CLI/二进制」、或直接敲命令报 command not found、或要确认本 Bot 装了哪些工具时，先读本技能。触发词：cli 工具、命令行工具、装了什么工具、工具目录、command not found、cli_tools、bcs-cli、run a CLI, which tools are installed。"
---

# 本 Bot 的命令行工具（openclaw）

平台会按本 Bot manifest 里的 `cli_tools` 声明，把工具安装到：

```
/home/admin/.openclaw/cli
```

**这个目录不在 `PATH` 上。** 直接敲 `mytool` 会报 `command not found`，这不代表工具没装——必须用绝对路径调用。

## 1. 先看有哪些工具

```bash
ls -1 /home/admin/.openclaw/cli 2>/dev/null
```

- 目录不存在或为空 = 本 Bot 没有安装任何 CLI 工具。这是正常状态，不是故障。
- `ls -1` 默认不显示以 `.` 开头的文件，这正是想要的：`.<name>.XXXXXXXX.part` 是安装过程中的临时文件，不是工具，**绝不要调用它们**。
- 这份清单是**每个 Bot 各不相同、并且会变的**——每次 manifest 下发都可能增删。所以每次要用工具时现场 `ls`，不要凭记忆假设某个工具存在。

## 2. 用绝对路径调用

```bash
/home/admin/.openclaw/cli/<name> [args...]
```

不确定用法时，先问工具自己，同样用绝对路径：

```bash
/home/admin/.openclaw/cli/<name> --help
```

不要猜参数。`--help` 失败时试 `-h` 或 `help` 子命令；都不行就把这个情况如实告诉用户，不要凭空编造命令行。

## 3. 需要在脚本里用时，临时把目录加进 PATH

工具本身是自包含的可执行文件，但**它启动的子进程不会自动找到同目录的其他工具**；你写的脚本里如果要按裸名字调用，就在那一次调用里补上 PATH：

```bash
PATH="/home/admin/.openclaw/cli:$PATH" bash ./my-script.sh
```

只在需要的那条命令上这么做，不要去改 shell 配置文件或全局环境。

## 4. 这个目录是平台管的，不要动它

- **不要**往里面写文件、改名、删文件、改权限。
- **不要**自己去下载、编译、`pip install`/`npm install` 一个工具来「补上」缺的命令。
- 一次 manifest 下发是**整集覆盖**：你手工放进去的东西会被删掉，而且不会有任何提示。

用户要的工具不在目录里时，正确的回答是：**告诉用户这个 Bot 当前没有安装该工具，需要在 Bot 的 manifest `cli_tools` 里声明后重新下发**，然后用现有工具或其他办法继续把事情做完。

## 5. 已知边界

- 一个条目 = 一个命令 = 一个自包含的可执行文件，仅 `linux/amd64`。没有随附的库、配置或补充文件。
- 工具是按名字安装的；目录里的文件名就是命令名。
- 工具运行失败（非 0 退出码）是工具自己的问题，**不是**「没装」。把它的 stderr 原样报给用户，不要退回去重装或换路径重试。
