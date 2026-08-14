# 依赖清单

[English](dependencies.md)

这份文档列出本地 quick start 需要的第三方工具。它是给人和 agent 在执行安装或启动前阅读的依赖说明。

适用平台：macOS、Debian / Ubuntu、Fedora。本文面向 `singlebox.sh` 本机隔离路径；Docker 源码构建路径只需要可选工具里的 Docker 依赖，详细流程见 [Docker Guide](docker.zh-CN.md)。

在仓库根目录先跑安全预检：

```bash
./scripts/singlebox.sh check
```

`check` 只打印将使用的路径，并检查 BCS / frontend 预检项：Cargo / `protoc`、Node.js 主版本、npm、源码目录和端口。除少量本地 runtime 目录初始化外，它不会安装依赖、构建代码、启动服务、杀进程或修改全局配置。`check` 不会提前校验 5bot 启动脚本里的 OpenClaw / `jq`，这些会在实际启动本地 bot stack 时检查。Rust 最低版本由 Cargo 在构建阶段按 workspace 的 `rust-version` 校验。如果失败，手动安装缺失项后重新跑同一个检查。

如果你希望脚本帮助检查并安装缺失工具，可以改用：

```bash
./scripts/singlebox.sh install-tools
```

`install-tools` 会先检查基础编译环境；如果编译器或 `make` 缺失，会输出适合当前系统的手动安装指引。它会在安装缺失的系统命令和开发库、OpenClaw、Rust/Cargo 以及 protobuf/protoc 前询问确认。Node.js 和 uv 是当前的例外：Node.js 22+ 缺失或版本过低时，脚本会自动通过 nvm 安装；uv 缺失时，会自动尝试 `pip`，然后尝试官方安装脚本。系统包安装被权限策略拒绝或执行失败时，脚本会输出可手动执行的命令。macOS 如果没有 Homebrew，会引导用户前往 [brew.sh](https://brew.sh/) 安装后重新运行。执行前请确认所有这些本机写入（包括 Node.js 和 uv 的自动安装路径）可以接受。

运行 `singlebox.sh` 时也会安装仓库级 pre-push hook，即设置 `core.hooksPath=.githooks`。如果某次命令需要跳过 hook 安装，可以设置 `OCB_SKIP_GIT_HOOKS=1`。

## 安全规则

- 除上文已说明的 `install-tools` Node.js / uv 自动安装路径外，执行 `sudo`、全局安装、`brew link --force`、`curl | sh` 或等价的系统级写入前，必须停下确认。
- 创建目录、软链、生成文件或日志前，先打印写入路径。
- 启动长期进程前，先检查端口。
- 任一步失败即停止；不要自动修复、自动换源或杀掉无关进程。

## 必需工具

| 工具 | 用途 | 检查命令 | 说明 |
| --- | --- | --- | --- |
| macOS 或 Linux | 本地 quick start | `uname -a` | Linux 需要编译工具、`pkg-config`、SQLite / OpenSSL 开发库。 |
| Rust 1.91+ | 构建 BCS | `rustc --version` | BCS workspace 声明了 `rust-version = "1.91"`。 |
| Cargo | 构建 BCS | `cargo --version` | 随 Rust toolchain 安装。 |
| `protoc` | BCS protobuf codegen | `protoc --version` | 需要安装 Protocol Buffers compiler。 |
| SQLite 开发库 | BCS 本地存储 | `pkg-config --modversion sqlite3` | macOS 通常自带；Linux 需要安装 dev 包。 |
| OpenSSL 开发库 | Rust TLS / native-tls 相关依赖编译 | `pkg-config --modversion openssl` | Linux 需要安装 `libssl-dev` / `openssl-devel`；macOS 建议使用 Homebrew 的 `openssl@3`。 |
| Node.js 22+ | 构建 BCN 插件和运行前端 | `node --version` | `singlebox.sh` 会拒绝更低的主版本。 |
| npm | 安装/构建 BCN 插件，以及安装前端依赖 | `npm --version` | 本机 singlebox 路径会用 npm 做插件和前端准备。 |
| OpenClaw `>= 2026.3.28` | 启动本地 5bot stack | `openclaw --version` | `install-tools` 检测到缺失或低于最低版本时，会询问是否安装指定版本。 |
| `jq` | 生成和复用 5bot OpenClaw JSON 配置 | `jq --version` | 本地 5bot stack 需要用它安全处理模型配置和 bot 配置。 |
| `curl` | 健康检查和手动下载 | `curl --version` | 脚本和手动安装命令都会用到。 |
| `lsof` | 端口检查 | `lsof -v` | 启动前用于检查本地端口是否被占用。 |

## 可选工具

| 工具 | 什么时候需要 | 说明 |
| --- | --- | --- |
| 通过 `corepack` 启用的 `pnpm` | 开发其他 plugin workspace 包 | quick start 不需要。 |
| Docker 和 Docker Compose | 使用 Docker 源码构建路径 | Docker Desktop 自带 Compose；Linux 可安装 Docker Engine 和 Compose plugin。详见 [Docker Guide](docker.zh-CN.md)。 |
| 模型 API endpoint 和 key | 测试 bot 真实调用模型回复，或自定义协作里使用 LLM judge 节点 | 不要提交 key，也不要写入 shell rc。 |
| `USE_CN_MIRROR=1` | 中国大陆网络加速 | 只有明确想把 npm、PyPI、nvm、rustup、corepack 等下载源切到公开镜像时使用。 |

## 安装指引

下面命令是给人手动执行的示例。agent 在执行任何系统级或全局写入命令前，必须先停下确认。

### macOS

```bash
brew install protobuf jq pkg-config openssl@3
protoc --version
jq --version
PKG_CONFIG_PATH="$(brew --prefix openssl@3)/lib/pkgconfig" pkg-config --modversion openssl
```

如果 OpenSSL 只有加上 `PKG_CONFIG_PATH` 才能被 `pkg-config` 找到，构建 BCS 前在当前 shell 中导出同一个变量。

如果本机没有 Rust 1.91+，可以通过 `rustup` 安装：

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
  | sh -s -- -y --profile minimal --default-toolchain 1.91.0
source ~/.cargo/env
rustc --version
```

用你常用的 Node 版本管理器安装或切换到 Node.js 22+，然后验证：

```bash
node --version
npm --version
```

确认可以接受全局安装后，再安装 OpenClaw CLI：

```bash
npm install -g "openclaw@>=2026.3.28"
openclaw --version
```

### Debian / Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential pkg-config perl \
  protobuf-compiler libssl-dev libsqlite3-dev \
  curl git jq lsof
protoc --version
pkg-config --modversion sqlite3
pkg-config --modversion openssl
jq --version
```

然后按上面的方式安装 Rust 1.91+、Node.js 22+、npm 和 OpenClaw。

### Fedora

```bash
sudo dnf install -y \
  gcc gcc-c++ make pkg-config perl \
  protobuf-compiler openssl-devel sqlite-devel \
  curl git jq lsof
protoc --version
pkg-config --modversion sqlite3
pkg-config --modversion openssl
jq --version
```

然后按上面的方式安装 Rust 1.91+、Node.js 22+、npm 和 OpenClaw。

## 验证

安装或升级依赖后，回到仓库根目录重新执行：

```bash
./scripts/singlebox.sh check
```

预检通过后，再继续主流程：

```bash
./scripts/singlebox.sh
```
