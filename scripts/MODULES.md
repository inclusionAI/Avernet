# scripts/ 模块化架构规范

## 概述

`singlebox.sh` 是本地开发环境入口，通过约定式分发调用各模块。`local_setup.sh` 仅保留为兼容包装，公开本地流程会转发到 `singlebox.sh`，不再承载内部 dev 环境逻辑。

## 文件结构

```
scripts/
├── singlebox.sh            # 入口: 常量 + 参数解析 + 编排 + 分发
├── local_setup.sh          # (兼容) 转发公开本地流程到 singlebox.sh
├── utils.sh                # 纯工具函数 (无状态)
├── toolchain.sh            # 工具链: 安装/检查/升级
└── modules/
    ├── frontend.sh         # 前端
    ├── bcs.sh              # BCS 协调服务
    ├── all.sh              # 组合: 当前 all 组
    └── bcs_frontend.sh     # 组合: BCS + 前端
```

## 模块接口

每个模块实现可选接口：

```bash
<name>_prereqs()   # 声明依赖 (只检不装，按服务粒度)
<name>_setup()     # 检查 + 编译/sync (不装工具, 失败提示 $0 setup)
<name>_start()     # 启动进程
<name>_stop()      # 停止进程
<name>_restart()   # (可选) 重启进程; 未定义时 fallback 为 stop + sleep 2 + start
<name>_clean()     # (可选) 清理本地运行数据; 不存在则跳过
<name>_status()    # 查状态
<name>_help()      # 一行帮助文本
```

入口通过 `type -t "${svc}_prereqs"` / `type -t "${svc}_start"` 等检测，不存在则跳过或报错。
`_restart()` 可选；未定义时 `restart_service()` 自动 fallback 为 `_stop() + sleep 2 + _start()`。

## `_prereqs()` 约定

每个服务模块**可选**定义 `<name>_prereqs()`，声明该服务启动所需的外部依赖。
`check_prereqs_for_services()` 按服务列表收集并执行，只检查与目标服务相关的依赖。

### 职责

- **只检不装** — 检查工具、hosts、端口、目录是否存在/可用，不自动安装
- **按需检查** — 由编排函数按服务列表调用，不全局检查
- **条件检查** — 可根据运行模式 (`LOCAL_MODE`, `BCS_SERVER_ENV` 等) 调整检查项

### 输出格式

```bash
foo_prereqs() {
    local has_error=false

    echo -e "${CYAN}[foo] Prerequisites${NC}"

    # 工具检查: 用 prereq_ok / prereq_warn / prereq_error
    if check_command some_tool; then
        prereq_ok "some_tool found"
    else
        prereq_error "some_tool not found. Install: ..."
        has_error=true
    fi

    # 目录检查
    if [ -d "${FOO_DIR}" ]; then
        prereq_ok "directory: ${FOO_DIR}"
    else
        prereq_error "directory not found: ${FOO_DIR}"
        has_error=true
    fi

    # 端口检查 (端口被占用是 warn 不是 error)
    if check_port_available "${FOO_PORT}"; then
        prereq_ok "Port ${FOO_PORT} available"
    else
        prereq_warn "Port ${FOO_PORT} is in use"
    fi

    # 返回
    if [ "$has_error" = true ]; then
        return 1
    fi
    return 0
}
```

### 可复用的检查工具

#### utils.sh

| 函数 | 用途 | 返回值 |
|------|------|--------|
| `check_command <cmd>` | 命令是否存在 | 0=存在 |
| `check_directory_exists <dir>` | 目录是否存在 | 0=存在 |
| `check_python3_version` | Python ≥ 3.12 | 0=满足 |
| `check_uv_installed` | uv 是否安装 | 0=安装 (静默) |
| `check_openclaw_installed` | openclaw CLI | 0=安装 |
| `check_rust_installed` | cargo 是否安装 | 0=安装 |
| `check_protobuf_installed` | protoc 是否安装 | 0=安装 |
| `check_bcs_binary` | bcs 二进制是否存在 | 0=存在 |
| `check_bcs_cli_binary` | bcs-cli 二进制是否存在 | 0=存在 |
| `check_port_available <port>` | 端口未被占用 | 0=可用 |
| `port_is_listening <port>` | 端口正在监听 | 0=监听中 |
| `check_prereqs_for_services` | 批量执行 prereqs | 0=全通过 |
| `prereq_ok <msg>` | 打印 ✓ 通过 | — |
| `prereq_warn <msg>` | 打印 ⚠ 警告 + 记入 PREREQ_WARNINGS | — |
| `prereq_error <msg>` | 打印 ✗ 错误 + 记入 PREREQ_ERRORS | — |

#### toolchain.sh

| 函数 | 用途 | 返回值 |
|------|------|--------|
| `check_node_available` | Node ≥ 22 | 0=满足 |

### 各服务 prereqs 明细

| 服务 | Tools | Hosts | Ports | Dirs | 条件 |
|------|-------|-------|-------|------|------|
| **frontend** | node≥22, npm | — | FRONTEND_PORT (默认 8000) | FRONTEND_DIR | — |
| **bcs** | cargo, protoc | — | BCS_PORT | BCS_DIR | database selected by BCS config |


## 模块模板

```bash
#!/usr/bin/env bash
# scripts/modules/<name>.sh — <描述>
[[ -n "${_<NAME>_SH_LOADED:-}" ]] && return 0
_<NAME>_SH_LOADED=1

# 服务专属常量 (端口、数据目录等)
DEFAULT_FOO_PORT="<service-port>"
FOO_PORT="${FOO_PORT:-${DEFAULT_FOO_PORT}}"
FOO_LOG="${LOG_DIR}/foo.log"

foo_prereqs() {
    local has_error=false
    echo -e "${CYAN}[foo] Prerequisites${NC}"

    if check_command some_tool; then
        prereq_ok "some_tool found"
    else
        prereq_error "some_tool not found. Install: ..."
        has_error=true
    fi

    if [ -d "${FOO_DIR}" ]; then
        prereq_ok "directory: ${FOO_DIR}"
    else
        prereq_error "directory not found: ${FOO_DIR}"
        has_error=true
    fi

    if check_port_available "${FOO_PORT}"; then
        prereq_ok "Port ${FOO_PORT} available"
    else
        prereq_warn "Port ${FOO_PORT} is in use"
    fi

    if [ "$has_error" = true ]; then return 1; fi
    return 0
}

foo_setup() {
    check_依赖 || { log_error "xxx not found. Run: $0 setup"; return 1; }
    # 编译/sync 依赖
}

foo_start() {
    mkdir -p "${LOG_DIR}"
    kill_port_process ${FOO_PORT}
    # 启动进程
}

foo_stop() {
    kill_port_process ${FOO_PORT}
    # 停止进程
}

# foo_restart()  # 可选; 未定义时自动 fallback 为 stop + sleep 2 + start

foo_status() {
    local pid=$(lsof -ti :${FOO_PORT} 2>/dev/null | head -1)
    if [ -n "$pid" ]; then
        echo "  Foo:       Running (PID: $pid, port: ${FOO_PORT})"
    else
        echo "  Foo:       Stopped"
    fi
}

foo_help() {
    echo "foo - Foo service (port ${FOO_PORT})"
}
```

## 命令规范

```
./singlebox.sh                              # 当前默认流程 (setup 当前 all 组 + start 当前 all 组)
./singlebox.sh install-tools                # 安装/升级工具链 (幂等)
./singlebox.sh setup [service|group|all]    # Setup (default: 当前 all 组)
./singlebox.sh start [service|group|all]    # Start (default: 当前 all 组)
./singlebox.sh stop [service|group|all]     # Stop (default: 当前 all 组)
./singlebox.sh restart [service|group|all]  # Restart (default: 当前 all 组)
./singlebox.sh clean [service|group|all]    # Clean local runtime data
./singlebox.sh status [group|service|all]   # Show status (default: 当前 all 组)
./singlebox.sh check [service|group|all]    # Check prerequisites (default: 当前 all 组)
```

### `check` 命令

按需依赖检查，受启动的服务影响：

```bash
./singlebox.sh check              # 检查当前 all 组
./singlebox.sh check bcs          # 只检查 bcs 依赖
./singlebox.sh check frontend     # 只检查 frontend 依赖
./singlebox.sh check all          # 检查当前 all 组
```

实现机制：
- `resolve_services()` 将组名 (`all`, `bcs_frontend`) 展开为服务列表，普通服务名原样返回
- `check_prereqs_for_services()` 逐个调用 `<svc>_prereqs`，汇总 PREREQ_ERRORS/PREREQ_WARNINGS
- 无 `_prereqs` 函数的服务跳过并提示
- `start_service()` 启动前自动调用 `_prereqs`，失败则中止

## 常量归属

| 归属       | 内容                                          | 位置           |
|------------|-----------------------------------------------|----------------|
| 全局常量   | PROJECT_ROOT, DEP_DIR, LOG_DIR, LOCAL_MODE... | singlebox.sh   |
| 服务常量   | 端口、数据目录、配置路径                       | 各 modules/*.sh |
| 工具函数   | log_*, kill_port_*, check_*, prereq_*...       | utils.sh       |
| 工具链检查 | check_node_available              | toolchain.sh   |

## 关键规则

1. **模块只 check 不装** — `_setup()` 检查工具是否存在，失败时提示 `$0 install-tools`，不自动安装
2. **`_prereqs()` 只检不装** — 声明服务外部依赖，按服务粒度执行，不自动安装
3. **start 是纯启动** — `_start()` 不含隐式 setup/check/依赖安装，依赖由 `start_service()` 自动调用 `_prereqs()` 守护。不要在 `_start()` 中调用 `_setup()` 或 `uv sync`/`cargo build`
4. **toolchain 负责安装** — `install-tools` 命令安装/升级工具链，幂等；`setup [service|group|all]` 检查+编译依赖，默认当前 all 组
5. **模块是集成层** — 委托服务自身脚本 (`src/*/scripts/`) 或自己实现
6. **utils.sh 纯函数** — 无状态，不被 ≥2 个模块用则留在模块内
7. **双 source 守卫** — 每个文件开头 `_XXX_SH_LOADED` 防重复加载
8. **组合模块** — `all`, `bcs_frontend` 组合其他模块，不直接管理进程
9. **新增服务** — 创建 `modules/<name>.sh` + 在 `singlebox.sh` 添加 source + 修改 ORDER 数组
10. **hosts 检查归服务** — 如服务确实需要 hosts 绑定，由对应服务的 `<name>_prereqs()` 按需实现

## 启动顺序

ORDER 数组定义在各 group module 中，不在 singlebox.sh 中：

```bash
# all.sh
SETUP_ORDER=(bcs frontend)
START_ORDER=(bcs frontend)
STOP_ORDER=(frontend bcs)


```

> **注意**: `toolchain` 不在任何 ORDER 数组中 — 安装工具链使用 `install-tools` 命令，`setup` 默认走 `all_setup`。
> 当前 `START_ORDER` 包含 `bcs frontend`，本地默认流程会同时启动 BCS 和前端。

## 添加新服务

```bash
# 1. 创建模块
# 参考 scripts/modules/bcs.sh 或 scripts/modules/frontend.sh 新建 scripts/modules/foo.sh
# 编辑: 定义函数名、常量、逻辑

# 2. 定义 prereqs (可选但推荐)
foo_prereqs() {
    local has_error=false
    echo -e "${CYAN}[foo] Prerequisites${NC}"
    # ... 检查工具、目录、端口 ...
    if [ "$has_error" = true ]; then return 1; fi
    return 0
}

# 3. 在 singlebox.sh 添加 source (Service modules 区域)
source "${SCRIPT_DIR}/modules/foo.sh"

# 4. 在 group module 的 ORDER 数组中加入服务名
#    - all.sh: SETUP_ORDER, START_ORDER, STOP_ORDER

# 5. 完成。start/stop/restart/status/help/prereqs 自动工作。
```
