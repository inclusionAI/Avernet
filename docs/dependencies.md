# Dependencies

[简体中文](dependencies.zh-CN.md)

This document lists the third-party tools required for the local BCS + OpenClaw
quick start. It is written for humans and agents to read before installing
dependencies or starting services.

Supported platforms: macOS, Debian / Ubuntu, and Fedora. This page covers the
native `singlebox.sh --local` and `singlebox.sh --standalone` paths. The Docker
source-build path only needs the optional Docker dependency below; see the
[Docker Guide](docker.md) for the full flow.

Run the safety preflight from the repository root first:

```bash
./scripts/singlebox.sh check
```

`check` only prints the paths it will use and checks the BCS / frontend
preflight items: Cargo / `protoc`, the Node.js major version, npm, source
directories, and ports. Except for initializing a few local runtime
directories, it does not install dependencies, build code, start services, kill
processes, or change global configuration. `check` does not preflight OpenClaw
or `jq` for the 5-bot startup script; those are checked when the local bot
stack actually starts. Cargo verifies the minimum Rust version during the build
from the workspace `rust-version`. If `check` fails, install the missing items
manually and run the same check again.

If you want the script to help check and install missing tools, use:

```bash
./scripts/singlebox.sh install-tools
```

`install-tools` may install Node.js, uv, OpenClaw, Rust/Cargo, and
protobuf/protoc, and may write to the user directory or call the local package
manager. The current script asks for confirmation before installing OpenClaw,
Rust/Cargo, and protobuf/protoc. When Node.js 22+ is missing or too old, it
installs Node.js through nvm; when uv is missing, it tries `pip` or the official
installer. Run it only after confirming those local writes are acceptable.

Running `singlebox.sh` also installs the repo-local pre-push hook by setting
`core.hooksPath=.githooks`. Set `OCB_SKIP_GIT_HOOKS=1` if you need to skip hook
installation for a one-off command.

## Safety rules

- Stop for confirmation before running `sudo`, global installs,
  `brew link --force`, `curl | sh`, or any equivalent system-level write.
- Print the target path before creating directories, symlinks, generated files,
  or logs.
- Check ports before starting long-running processes.
- Stop on any failure. Do not automatically fix, switch mirrors, or kill
  unrelated processes.

## Required tools

| Tool | Used for | Check command | Notes |
| --- | --- | --- | --- |
| macOS or Linux | Local quick start | `uname -a` | Linux needs build tools, `pkg-config`, and SQLite / OpenSSL development libraries. |
| Rust 1.91+ | Building BCS | `rustc --version` | The BCS workspace declares `rust-version = "1.91"`. |
| Cargo | Building BCS | `cargo --version` | Installed with the Rust toolchain. |
| `protoc` | BCS protobuf codegen | `protoc --version` | Requires the Protocol Buffers compiler. |
| SQLite development libraries | BCS local storage | `pkg-config --modversion sqlite3` | Usually present on macOS; Linux needs a dev package. |
| OpenSSL development libraries | Building Rust TLS / native-tls dependencies | `pkg-config --modversion openssl` | Linux needs `libssl-dev` / `openssl-devel`; on macOS, prefer Homebrew `openssl@3`. |
| Node.js 22+ | Building the BCN plugin and running the frontend | `node --version` | `singlebox.sh` rejects lower major versions. |
| npm | Installing/building the BCN plugin and installing frontend dependencies | `npm --version` | Both local and standalone paths use npm for plugin and frontend setup. |
| OpenClaw `>= 2026.3.28` | Starting the local 5-bot stack | `openclaw --version` | `install-tools` asks whether to install the requested version when OpenClaw is missing or below the minimum. |
| `jq` | Generating and reusing 5-bot OpenClaw JSON configuration | `jq --version` | The local 5-bot stack uses it to safely handle model and bot configuration. |
| `curl` | Health checks and manual downloads | `curl --version` | Used by scripts and manual installation commands. |
| `lsof` | Port checks | `lsof -v` | Used to check whether local ports are occupied before startup. |

## Optional tools

| Tool | When you need it | Notes |
| --- | --- | --- |
| `pnpm` enabled through `corepack` | Developing other plugin workspace packages | Not required by quick start. |
| Docker and Docker Compose | Using the Docker source-build path | Docker Desktop includes Compose; Linux can install Docker Engine and the Compose plugin. See the [Docker Guide](docker.md). |
| Model API endpoint and key | Letting test bots make real model calls, or using LLM judge nodes in structured collaboration | Do not commit keys and do not write them into shell rc files. |
| `USE_CN_MIRROR=1` | Network acceleration in mainland China | Use only when you explicitly want npm, PyPI, nvm, rustup, corepack, and similar downloads to use public mirrors. |

## Installation guide

The commands below are examples for manual execution. Agents must stop for
confirmation before running any system-level or global write command.

### macOS

```bash
brew install protobuf jq pkg-config openssl@3
protoc --version
jq --version
PKG_CONFIG_PATH="$(brew --prefix openssl@3)/lib/pkgconfig" pkg-config --modversion openssl
```

If OpenSSL is visible to `pkg-config` only with `PKG_CONFIG_PATH`, export the
same variable in the current shell before building BCS.

If Rust 1.91+ is not installed locally, install it through `rustup`:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
  | sh -s -- -y --profile minimal --default-toolchain 1.91.0
source ~/.cargo/env
rustc --version
```

Use your preferred Node version manager to install or switch to Node.js 22+,
then verify it:

```bash
node --version
npm --version
```

After you confirm that a global install is acceptable, install the OpenClaw CLI:

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

Then install Rust 1.91+, Node.js 22+, npm, and OpenClaw as described above.

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

Then install Rust 1.91+, Node.js 22+, npm, and OpenClaw as described above.

## Verification

After installing or upgrading dependencies, return to the repository root and
run:

```bash
./scripts/singlebox.sh check
```

After the preflight passes, continue with the main flow:

```bash
./scripts/singlebox.sh --local
```

If you want to use the repo-local isolated BCS runtime and OpenClaw root:

```bash
./scripts/singlebox.sh --standalone
```
