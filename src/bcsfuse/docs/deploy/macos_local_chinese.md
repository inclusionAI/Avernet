# BCSFuse Open-Core macOS 本地部署指南

**Phase 3.1 - OSS 可部署性 macOS 第一关**

本指南提供在 macOS 上部署 BCSFuse Open-Core 的详细步骤说明。

## 目录

- [前置要求](#前置要求)
- [快速开始](#快速开始)
- [详细步骤](#详细步骤)
- [脚本说明](#脚本说明)
- [数据持久化](#数据持久化)
- [故障排查](#故障排查)
- [危险操作](#危险操作)

## 前置要求

### 必需

- **macOS** 10.15+
- **Python** 3.12+
- **MySQL** 8.0+ (本地运行)
- **uv** 或 **pip** (用于依赖管理)

### 可选（推荐）

- **mysql** 客户端 (用于手动验证)

## 快速开始

```bash
# 1. 初始化环境（首次运行）
./scripts/deploy/macos/bootstrap_local.sh

# 2. 编辑环境配置文件，填入真实凭证
vi .runtime/env/.env.local

# 3. 启动服务
./scripts/deploy/macos/start_local.sh

# 4. 查看状态
./scripts/deploy/macos/status_local.sh

# 5. 运行冒烟测试
python -m pytest tests/smoke/ -v

# 6. 停止服务
./scripts/deploy/macos/stop_local.sh
```

## 详细步骤

### 1. 初始化环境（首次使用）

**脚本：** `scripts/deploy/macos/bootstrap_local.sh`

**用途：** 一键准备依赖环境

**它会做什么：**
- 检查 Python 3.12+
- 检查 bash、curl
- 创建或复用 `.venv`
- 安装依赖（uv sync 或 pip install）
- 创建 `.runtime/` 目录结构：
  - `.runtime/logs/`
  - `.runtime/pids/`
  - `.runtime/data/`
  - `.runtime/env/`
- 从 `.env.example` 生成 `.runtime/env/.env.local`（如不存在）
- 自动调用 `init_storage.sh`

**幂等性：**
- ✅ venv 存在：跳过创建
- ✅ env 存在：跳过生成，如有占位符则警告
- ✅ MySQL 表存在：跳过创建
- ✅ Qdrant 路径存在：跳过创建
- ❌ 不破坏数据

**示例：**

```bash
cd /path/to/bcsfuse
./scripts/deploy/macos/bootstrap_local.sh
```

**预期输出：**

```
========================================
BCSFUSE_OPEN_CORE_MACOS_BOOTSTRAP
========================================
- bcsfuse_root: /path/to/bcsfuse

========================================
PYTHON_CHECK
========================================
✓ python3: /path/to/python3
✓ python_version: 3.12.x (>= 3.12)

...

========================================
BOOTSTRAP_COMPLETE
========================================

下一步:
  1. 编辑 .runtime/env/.env.local 填入真实凭证
  2. 运行: ./scripts/deploy/macos/start_local.sh
```

### 2. 配置环境

**文件：** `.runtime/env/.env.local`

**关键字段：**

```bash
# MySQL 配置
export MYSQL_HOST="127.0.0.1"
export MYSQL_PORT="3306"
export MYSQL_USER="your_user"
export MYSQL_PASSWORD="your_password"
export MYSQL_DATABASE="bcsfuse_oss"

# LLM 配置
export LLM_BASE_URL="your_llm_endpoint"
export LLM_AUTH_TOKEN="your_llm_token"

# Embedding 配置
export EMBEDDING_BASE_URL="your_embedding_endpoint"
export EMBEDDING_AUTH_TOKEN="your_embedding_token"
export EMBEDDING_DIMENSION="4096"  # 关键：必须是 4096

# Qdrant（自动配置）
export QDRANT_LOCAL_PATH=".runtime/data/qdrant"
```

**重要提示：**
- 替换 `change_me` 占位符为真实凭证
- 确保 `EMBEDDING_DIMENSION="4096"`（用于 Qwen3-Embedding-8B）

### 3. 初始化存储

**脚本：** `scripts/deploy/macos/init_storage.sh`

**用途：** 初始化 MySQL 数据库表 和 Qdrant 向量存储

**它会做什么：**
- 加载 `.runtime/env/.env.local`
- 检查 MySQL 连接
- 创建数据库（如不存在）（`CREATE DATABASE IF NOT EXISTS`）
- 创建表（如不存在）（`CREATE TABLE IF NOT EXISTS`）
  - `workers`
  - `worker_runtime_state`
  - `worker_profile_content`
  - `worker_audit_log`
- 创建 `.runtime/data/qdrant/` 目录
- 记录日志到 `.runtime/logs/deploy.log`

**不会丢失数据：**
- ✅ 使用 `CREATE DATABASE IF NOT EXISTS`（不是 `DROP DATABASE`）
- ✅ 使用 `CREATE TABLE IF NOT EXISTS`（不是 `DROP TABLE`）
- ✅ 使用 `mkdir -p` 创建 Qdrant 目录（不是 `rm -rf`）
- ❌ 没有破坏性操作

**示例：**

```bash
./scripts/deploy/macos/init_storage.sh
```

**幂等性测试：**

```bash
# 运行两次 - 应该是安全的
./scripts/deploy/macos/init_storage.sh
./scripts/deploy/macos/init_storage.sh  # 应该显示 "tables_existing_before: 4"
```

### 4. 启动服务

**脚本：** `scripts/deploy/macos/start_local.sh`

**用途：** 使用固定路径启动 open-core 运行时

**固定路径：**
- 日志：`.runtime/logs/open_core_runtime.log`
- PID：`.runtime/pids/open_core.pid`
- Qdrant：`.runtime/data/qdrant`（除非外部设置）

**它会做什么：**
- 加载 `.runtime/env/.env.local`
- 检查是否已运行（健康检查并退出 0 如果健康）
- 清理过期 PID（如需要）
- 检查端口 8765 可用性
- 启动运行时：`nohup python3 main.py > $LOG_FILE 2>&1 &`
- 写入 PID 到 `.runtime/pids/open_core.pid`
- 等待 5 秒
- 验证进程存活
- 验证端口监听
- 健康检查（最多重试 10 次）

**重启保证：**
- ✅ 不清空 Qdrant
- ✅ 不清空 MySQL
- ✅ 不重新初始化表

**示例：**

```bash
./scripts/deploy/macos/start_local.sh
```

**预期输出：**

```
========================================
RUNTIME_STARTED_SUCCESSFULLY
========================================

PID: 12345
端口: 8765
日志: .runtime/logs/open_core_runtime.log
Qdrant: .runtime/data/qdrant

健康检查: curl http://localhost:8765/health
提供商: curl http://localhost:8765/providers

下一步: ./scripts/deploy/macos/status_local.sh
```

### 5. 查看状态

**脚本：** `scripts/deploy/macos/status_local.sh`

**用途：** 报告服务状态及数据位置信息

**输出包含：**
- 服务运行状态
- PID 和端口信息
- 健康检查结果
- 运行时日志位置
- Qdrant 路径
- MySQL 主机/数据库
- 表数量
- Qdrant collections

**示例：**

```bash
./scripts/deploy/macos/status_local.sh
```

**预期输出（健康）：**

```
========================================
STATUS_SUMMARY
========================================
- service_running: YES
- port_status: LISTEN
- health_status: PASS
- pid: 12345
- port: 8765
- log_file: .runtime/logs/open_core_runtime.log
- qdrant_path: .runtime/data/qdrant
- mysql_database: bcsfuse_oss
- mysql_tables_count: 4
- result: HEALTHY

运行时运行正常且健康
```

**退出码：**
- 0: 服务运行且健康
- 1: 服务未运行或不健康

### 6. 停止服务

**脚本：** `scripts/deploy/macos/stop_local.sh`

**用途：** 安全停止运行时而不丢失数据

**它会做什么：**
- 检查 PID 文件
- 优雅关闭（先 SIGTERM，10 秒超时后 SIGKILL）
- 基于端口的后备检测
- 清理 PID 文件
- 验证端口释放

**不丢失数据：**
- ✅ 不删除 Qdrant 数据
- ✅ 不删除 MySQL 数据
- ✅ 不删除日志
- ✅ 不删除 PID 文件（仅清空）

**示例：**

```bash
./scripts/deploy/macos/stop_local.sh
```

**预期输出：**

```
========================================
RUNTIME_STOPPED
========================================

数据已保留: Qdrant 和 MySQL 数据未删除
日志已保留: .runtime/logs/

重启命令: ./scripts/deploy/macos/start_local.sh
```

### 7. 重启服务

**脚本：** `scripts/deploy/macos/restart_local.sh`

**用途：** 安全重启（停止 + 启动）并保证数据保留

**它会做什么：**
- 调用 `stop_local.sh`
- 等待 2 秒
- 调用 `start_local.sh`
- 验证健康

**数据保留保证：**
- ✅ 不重新初始化存储
- ✅ 不清空 Qdrant
- ✅ 不清空 MySQL
- ✅ 复用现有 `.runtime/env/.env.local`
- ✅ 复用现有 `QDRANT_LOCAL_PATH`

**示例：**

```bash
./scripts/deploy/macos/restart_local.sh
```

## 脚本说明

| 脚本 | 用途 | 幂等性 | 数据丢失风险 |
|------|------|--------|-------------|
| `bootstrap_local.sh` | 依赖环境准备 | ✅ 是 | ❌ 无 |
| `init_storage.sh` | 初始化 MySQL + Qdrant | ✅ 是 | ❌ 无 |
| `start_local.sh` | 启动运行时 | ✅ 是 | ❌ 无 |
| `stop_local.sh` | 停止运行时 | ✅ 是 | ❌ 无 |
| `restart_local.sh` | 重启运行时 | ✅ 是 | ❌ 无 |
| `status_local.sh` | 报告状态 | N/A | ❌ 无 |
| `danger_reset_all_data.sh` | **危险：重置所有数据** | ❌ 否 | ⚠️ **高** |

## 数据持久化

### 重启后保留哪些数据？

| 数据 | 路径 | 持久化 |
|------|------|--------|
| MySQL 数据库 | 配置在 `MYSQL_DATABASE` | ✅ 保留 |
| MySQL 表 | `workers`, `worker_profile_content`, `worker_runtime_state`, `worker_audit_log` | ✅ 保留 |
| Qdrant 向量 | `.runtime/data/qdrant/` | ✅ 保留 |
| 运行时日志 | `.runtime/logs/` | ✅ 保留 |
| 环境配置 | `.runtime/env/.env.local` | ✅ 保留 |
| PID | `.runtime/pids/open_core.pid` | ❌ 清理（非数据） |

### 备份

**MySQL 备份：**

```bash
mysqldump -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE > backup.sql
```

**Qdrant 备份：**

```bash
cp -r .runtime/data/qdrant qdrant_backup_$(date +%Y%m%d)
```

## 故障排查

### 常见问题

#### 1. 端口 8765 已被占用

**症状：**

```
✗ port_available: NO
✗ port_listening: NO (after kill attempt)
```

**解决方案：**

```bash
# 查找占用端口的进程
lsof -iTCP:8765 -sTCP:LISTEN

# 杀掉进程
kill -9 <PID>

# 或使用停止脚本
./scripts/deploy/macos/stop_local.sh
```

#### 2. MySQL 连接失败

**症状：**

```
✗ mysql_connection: FAIL
```

**解决方案：**

```bash
# 检查 MySQL 是否运行
mysql.server status

# 启动 MySQL（如需要）
mysql.server start

# 验证数据库存在
mysql -h127.0.0.1 -P3306 -uroot -p -e "SHOW DATABASES LIKE 'bcsfuse_oss';"

# 创建数据库（如需要）
mysql -h127.0.0.1 -P3306 -uroot -p -e "CREATE DATABASE IF NOT EXISTS bcsfuse_oss CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

#### 3. Qdrant 锁文件检测

**症状：**

```
⚠ qdrant_lock_detected: YES
```

**解决方案：**

```bash
# 停止运行时
./scripts/deploy/macos/stop_local.sh

# 删除锁文件（仅在运行时停止后）
rm -f .runtime/data/qdrant/.lock

# 启动运行时
./scripts/deploy/macos/start_local.sh
```

#### 4. Embedding 维度不匹配

**症状：**

```
dimension error expected 4096 got 1024
```

**解决方案：**

```bash
# 编辑环境文件
vi .runtime/env/.env.local

# 确保 EMBEDDING_DIMENSION="4096"
export EMBEDDING_DIMENSION="4096"
```

#### 5. 运行时启动但健康检查失败

**症状：**

```
✗ health: FAIL
[WARN] Runtime started but health check failed
```

**解决方案：**

```bash
# 检查日志
tail -100 .runtime/logs/open_core_runtime.log

# 常见问题：
# - MySQL 连接被拒绝
# - Embedding 端点不可达
# - Qdrant 路径问题

# 验证环境
cat .runtime/env/.env.local | grep -E "(MYSQL|EMBEDDING|QDRANT)"

# 使用调试模式重启
export LOG_LEVEL=DEBUG
./scripts/deploy/macos/restart_local.sh
```

## 危险操作

### 重置所有数据

**⚠️ 警告：这会删除所有数据（Qdrant + MySQL 表 + 日志）**

**脚本：** `scripts/deploy/macos/danger_reset_all_data.sh`

**用法：**

```bash
./scripts/deploy/macos/danger_reset_all_data.sh --confirm-reset
```

**它会销毁：**
- 所有 Qdrant 向量数据（`.runtime/data/qdrant/`）
- 所有 MySQL 表数据（`workers`, `worker_profile_content`, `worker_runtime_state`, `worker_audit_log`）
- 所有运行时日志（`.runtime/logs/`）
- 所有 PID（`.runtime/pids/`）

**它会保留：**
- MySQL 数据库本身
- MySQL 用户/模式权限
- 环境文件（`.runtime/env/.env.local`）
- 代码

**何时使用：**
- 开发测试重置
- 完整数据清理
- 重新运行冒烟测试之前

**何时不使用：**
- 生产环境
- 有价值的测试数据
- 其他用户依赖此数据时

## 最佳实践

### 1. 定期检查状态

```bash
# 启动后
./scripts/deploy/macos/status_local.sh

# 启动后
./scripts/deploy/macos/status_local.sh

# 冒烟测试前
./scripts/deploy/macos/status_local.sh
```

### 2. 监控日志

```bash
# 实时查看运行时日志
tail -f .runtime/logs/open_core_runtime.log

# 检查部署日志
tail -f .runtime/logs/deploy.log
```

### 3. 破坏性操作前备份

```bash
# 备份 MySQL
mysqldump -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE > backup_$(date +%Y%m%d).sql

# 备份 Qdrant
cp -r .runtime/data/qdrant qdrant_backup_$(date +%Y%m%d)
```

### 4. 幂等性测试

```bash
# 测试 init_storage 是幂等的
./scripts/deploy/macos/init_storage.sh
./scripts/deploy/macos/init_storage.sh  # 应该是安全的

# 测试重启保留数据
./scripts/deploy/macos/start_local.sh
python -m pytest tests/smoke/ -v  # 假设全部通过
./scripts/deploy/macos/restart_local.sh
python -m pytest tests/smoke/ -v  # 应该仍然是全部通过
```

### 5. 修改后始终运行冒烟测试

任何配置修改或重启后：

```bash
python -m pytest tests/smoke/ -v
```

预期：**全部通过**

## 总结

BCSFuse Open-Core macOS 本地部署提供：

✅ **幂等的引导和初始化**
✅ **固定的日志/PID/Qdrant 路径**
✅ **重启后数据保留**
✅ **安全的启动/停止/重启脚本**
✅ **冒烟测试验证**
✅ **全面的故障排查指南**
✅ **隔离的危险数据重置脚本**

Docker 部署请参考：[docker.md](docker.md)

故障排查请参考：[troubleshooting.md](troubleshooting.md)