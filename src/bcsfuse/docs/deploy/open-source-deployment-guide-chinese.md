# BCSFuse Open-Core：完整部署与运维指南

**版本：** 3.4
**最后更新：** 2026-07-06
**目标用户：** 开源贡献者和开发者

---

## 目录

- [概述](#概述)
- [前置要求](#前置要求)
- [快速开始](#快速开始)
- [分步安装](#分步安装)
- [配置指南](#配置指南)
- [运行时操作](#运行时操作)
- [测试与验证](#测试与验证)
- [API 使用示例](#api-使用示例)
- [监控与日志](#监控与日志)
- [故障排查](#故障排查)
- [高级操作](#高级操作)
- [最佳实践](#最佳实践)

---

## 概述

BCSFuse Open-Core 是一个多机器人 AI 工作台，支持：

- **机器人生命周期管理**：创建、部署和监控 AI 机器人
- **多机器人协作**：通过 BCS（机器人协调服务）协调多个机器人
- **智能路由**：语义搜索和推荐用于机器人选择
- **专家会诊**：G2（群体会诊）和 G5（风险评估）用例

### 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    BCSFuse Open-Core                        │
│                   (端口 8765)                               │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Worker     │  │   Search &   │  │   Fusion     │     │
│  │  Registry    │  │  Recommend   │  │   Engine     │     │
│  │   (MySQL)    │  │  (Qdrant)    │  │   (LLM)      │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Profile    │  │   Embedding  │  │   LLM        │     │
│  │   Manager    │  │   Service    │  │   Client     │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
       │                      │                      │
   MySQL 8.0+           Qdrant Local           LLM Provider
                                                 (GLM-4/GLE-5)
```

### 组件

| 组件 | 用途 | 存储 |
|-----------|---------|---------|
| **Worker Registry** | 机器人元数据和状态管理 | MySQL 8.0+ |
| **Vector Store** | Profile 语义搜索 | Qdrant（本地模式） |
| **Embedding Service** | 文本向量化 | 外部 API（Qwen3-Embedding-8B） |
| **LLM Client** | Fusion 和会诊 | 外部 API（GLM-4/5） |
| **Profile Manager** | 机器人能力描述 | MySQL + Qdrant |

---

## 前置要求

### 系统要求

| 要求 | 最低配置 | 推荐配置 |
|-------------|---------|------------|
| **操作系统** | macOS 10.15+ | macOS 12+ |
| **Python** | 3.12+ | 3.12.x |
| **MySQL** | 8.0+ | 8.0.30+ |
| **内存** | 4 GB | 8 GB+ |
| **磁盘** | 2 GB | 5 GB+ |

### 必需工具

```bash
# 检查 Python 版本
python3 --version  # 应该 >= 3.12

# 检查 MySQL
mysql --version  # 应该 >= 8.0

# 检查 bash
bash --version  # 应该 >= 3.2

# 检查 curl
curl --version
```

### 外部服务

您需要访问：

1. **LLM 服务**（兼容 Anthropic API）：
   - Fast model（例如 GLM-4.7-Flash）
   - Reasoning model（例如 GLM-5）
   - 需要 Base URL 和 auth token

2. **Embedding 服务**（兼容 OpenAI API）：
   - 模型：Qwen3-Embedding-8B（维度：4096）
   - 需要 Base URL 和 auth token
   - ⚠️ **关键**：必须使用 4096 维度的 embeddings

---

## 快速开始

### 1. 克隆和初始化（5 分钟）

```bash
# 克隆仓库
cd /path/to/your/workspace
git clone <repository-url> bcsfuse
cd bcsfuse

# 初始化（一次性设置）
./scripts/deploy/macos/bootstrap_local.sh
```

### 2. 配置环境（2 分钟）

```bash
# 编辑环境配置
vi .runtime/env/.env.local

# 替换以下占位符：
# - MYSQL_USER, MYSQL_PASSWORD（您的 MySQL 凭据）
# - LLM_BASE_URL, LLM_AUTH_TOKEN（您的 LLM 服务）
# - EMBEDDING_BASE_URL, EMBEDDING_AUTH_TOKEN（您的 Embedding 服务）
```

### 3. 启动和验证（3 分钟）

```bash
# 启动运行时
./scripts/deploy/macos/start_local.sh

# 检查状态
./scripts/deploy/macos/status_local.sh

# 运行冒烟测试（验证核心功能）
python -m pytest tests/smoke/ -v
```

**总时间：约 10 分钟**

---

## 分步安装

### 步骤 1：克隆仓库

```bash
# 选择您的安装目录
cd /path/to/your/workspace

# 克隆
git clone <repository-url> bcsfuse
cd bcsfuse

# 验证
ls -la scripts/deploy/macos/
# 应该看到：bootstrap_local.sh, start_local.sh 等
```

### 步骤 2：初始化环境

**脚本：** `scripts/deploy/macos/bootstrap_local.sh`

此脚本执行一次性设置：

```bash
./scripts/deploy/macos/bootstrap_local.sh
```

**执行内容：**

1. ✅ 检查 Python 3.12+
2. ✅ 检查 bash, curl
3. ✅ 创建 Python 虚拟环境（`.venv/`）
4. ✅ 安装依赖（使用 `uv` 或 `pip`）
5. ✅ 创建 `.runtime/` 目录结构：
   - `.runtime/logs/` - 运行时和部署日志
   - `.runtime/pids/` - 进程 ID 文件
   - `.runtime/data/` - Qdrant 向量数据
   - `.runtime/env/` - 环境配置
6. ✅ 从 `.env.example` 生成 `.runtime/env/.env.local`
7. ✅ 自动调用 `init_storage.sh`

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

========================================
DEPENDENCIES_CHECK
========================================
✓ bash: /bin/bash
✓ curl: /usr/bin/curl

========================================
VENV_SETUP
========================================
✓ venv_created: .venv
✓ dependencies_installed: YES

========================================
RUNTIME_DIRECTORY_STRUCTURE
========================================
✓ logs: .runtime/logs
✓ pids: .runtime/pids
✓ data: .runtime/data
✓ env: .runtime/env

========================================
ENV_FILE_SETUP
========================================
✓ env_file: .runtime/env/.env.local
✓ action: GENERATED_FROM_EXAMPLE
⚠ WARNING: Please edit .runtime/env/.env.local with real credentials

========================================
STORAGE_INITIALIZATION
========================================
Calling init_storage.sh...
[... init_storage.sh 输出 ...]

========================================
BOOTSTRAP_COMPLETE
========================================

Next steps:
  1. Edit .runtime/env/.env.local with your credentials
  2. Run: ./scripts/deploy/macos/start_local.sh
```

**幂等性：** 此脚本可以安全地多次运行。它会跳过已存在的组件。

### 步骤 3：配置 MySQL

#### 3.1 创建 MySQL 数据库

```bash
# 连接到 MySQL
mysql -u root -p

# 创建数据库
CREATE DATABASE IF NOT EXISTS bcsfuse_oss CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

# 创建用户（可选）
CREATE USER 'bcsfuse_user'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON bcsfuse_oss.* TO 'bcsfuse_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;

# 验证
mysql -u bcsfuse_user -p -e "SHOW DATABASES LIKE 'bcsfuse_oss';"
```

#### 3.2 验证 MySQL 连接

```bash
# 设置环境变量
export MYSQL_HOST="127.0.0.1"
export MYSQL_PORT="3306"
export MYSQL_USER="bcsfuse_user"
export MYSQL_PASSWORD="your_password"
export MYSQL_DATABASE="bcsfuse_oss"

# 测试连接
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD -e "SELECT VERSION();"
```

### 步骤 4：配置 LLM 和 Embedding 服务

#### 4.1 配置 LLM 服务

编辑 `.runtime/env/.env.local`：

```bash
# LLM 配置
export LLM_ENABLED="true"
export ENABLE_REAL_LLM="true"

# 替换为您的 LLM 端点（兼容 Anthropic）
export LLM_BASE_URL="https://your-llm-endpoint.com/api/anthropic"
export LLM_AUTH_TOKEN="your_llm_token_here"

# 模型配置
export LLM_FAST_MODEL="GLM-4.7-Flash"          # 快速响应
export LLM_BALANCED_MODEL="GLM-4.7-Flash"       # 平衡模式
export LLM_REASONING_MODEL="GLM-5"              # 复杂推理
export LLM_LONG_CONTEXT_MODEL="GLM-4.7-Flash"  # 长上下文
export LLM_EXTRACTION_MODEL="GLM-4.7-Flash"    # 信息抽取

# 超时（根据您的网络调整）
export LLM_DEFAULT_TIMEOUT_MS="600000"          # 10 分钟
export LLM_REASONING_TIMEOUT_MS="600000"        # 10 分钟
```

**测试 LLM 连接：**

```bash
# 加载环境变量
source .runtime/env/.env.local

# 测试 LLM 端点
curl -X POST "$LLM_BASE_URL/v1/messages" \
  -H "Authorization: Bearer $LLM_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "GLM-4.7-Flash",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Hello, world!"}]
  }'
```

#### 4.2 配置 Embedding 服务

编辑 `.runtime/env/.env.local`：

```bash
# Embedding 配置
export ENABLE_REAL_EMBEDDING="true"
export EMBEDDING_ENABLED="true"

# 替换为您的 Embedding 端点（兼容 OpenAI）
export EMBEDDING_BASE_URL="https://your-embedding-endpoint.com/v1"
export EMBEDDING_AUTH_TOKEN="your_embedding_token_here"

# 模型配置
export EMBEDDING_MODEL="Qwen3-Embedding-8B"
export EMBEDDING_DIMENSION="4096"  # ⚠️ 关键：必须是 4096
export EMBEDDING_TIMEOUT_MS="30000"
```

**⚠️ 关键：Embedding 维度**

- Qwen3-Embedding-8B 生成 4096 维向量
- **不要**更改 `EMBEDDING_DIMENSION` 为其他值（1024, 768 等）
- 维度不匹配会导致运行时错误

**测试 Embedding 连接：**

```bash
# 加载环境变量
source .runtime/env/.env.local

# 测试 Embedding 端点
curl -X POST "$EMBEDDING_BASE_URL/embeddings" \
  -H "Authorization: Bearer $EMBEDDING_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-Embedding-8B",
    "input": "Hello, world!"
  }'

# 验证响应中的维度（应该是 4096）
```

#### 4.3 验证完整配置

```bash
# 加载环境变量
source .runtime/env/.env.local

# 验证所有变量
echo "MySQL 配置："
echo "  Host: $MYSQL_HOST"
echo "  Port: $MYSQL_PORT"
echo "  User: $MYSQL_USER"
echo "  Database: $MYSQL_DATABASE"

echo ""
echo "LLM 配置："
echo "  Enabled: $ENABLE_REAL_LLM"
echo "  Base URL: $LLM_BASE_URL"
echo "  Fast Model: $LLM_FAST_MODEL"
echo "  Reasoning Model: $LLM_REASONING_MODEL"

echo ""
echo "Embedding 配置："
echo "  Enabled: $ENABLE_REAL_EMBEDDING"
echo "  Base URL: $EMBEDDING_BASE_URL"
echo "  Model: $EMBEDDING_MODEL"
echo "  Dimension: $EMBEDDING_DIMENSION"  # 应该是 4096

echo ""
echo "向量存储："
echo "  Backend: $VECTOR_BACKEND"
echo "  Local Path: $QDRANT_LOCAL_PATH"
echo "  Collection: $QDRANT_COLLECTION_NAME"
```

### 步骤 5：初始化存储

**脚本：** `scripts/deploy/macos/init_storage.sh`

此脚本创建 MySQL 表和 Qdrant 存储：

```bash
./scripts/deploy/macos/init_storage.sh
```

**执行内容：**

1. ✅ 加载 `.runtime/env/.env.local`
2. ✅ 检查 MySQL 连接
3. ✅ 如果不存在则创建数据库
4. ✅ 如果不存在则创建表：
   - `workers` - Worker 元数据
   - `worker_runtime_state` - 运行时状态（online/offline）
   - `worker_profile_content` - Profile 文档
   - `worker_audit_log` - 审计日志
5. ✅ 创建 Qdrant 本地存储目录

**预期输出：**

```
========================================
STORAGE_INITIALIZATION
========================================
- mysql_host: 127.0.0.1
- mysql_port: 3306
- mysql_user: bcsfuse_user
- mysql_database: bcsfuse_oss

========================================
MYSQL_CONNECTION
========================================
✓ mysql_connection: PASS

========================================
DATABASE_INITIALIZATION
========================================
✓ database_exists: YES (bcsfuse_oss)

========================================
TABLE_INITIALIZATION
========================================
✓ table_workers: CREATED
✓ table_worker_runtime_state: CREATED
✓ table_worker_profile_content: CREATED
✓ table_worker_audit_log: CREATED
✓ tables_created: 4
✓ tables_existing_before: 0

========================================
QDRANT_STORAGE
========================================
✓ qdrant_local_path: .runtime/data/qdrant
✓ qdrant_directory: CREATED

========================================
STORAGE_INIT_COMPLETE
========================================

Next step: ./scripts/deploy/macos/start_local.sh
```

**幂等性：** 可以安全地多次运行。表使用 `CREATE TABLE IF NOT EXISTS`。

### 步骤 6：启动运行时

**脚本：** `scripts/deploy/macos/start_local.sh`

```bash
./scripts/deploy/macos/start_local.sh
```

**执行内容：**

1. ✅ 加载 `.runtime/env/.env.local`
2. ✅ 检查是否已在运行（健康检查）
3. ✅ 清理过期的 PID（如果需要）
4. ✅ 检查端口 8765 可用性
5. ✅ 启动运行时：`nohup python3 main.py > $LOG_FILE 2>&1 &`
6. ✅ 写入 PID 到 `.runtime/pids/open_core.pid`
7. ✅ 等待 5 秒
8. ✅ 验证进程存活
9. ✅ 验证端口监听
10. ✅ 健康检查（最多重试 10 次）

**预期输出：**

```
========================================
OPEN_CORE_RUNTIME_START
========================================
- bcsfuse_root: /path/to/bcsfuse
- log_file: .runtime/logs/open_core_runtime.log
- pid_file: .runtime/pids/open_core.pid
- port: 8765

========================================
ENVIRONMENT_CHECK
========================================
✓ env_file: .runtime/env/.env.local
✓ mysql_configured: YES
✓ llm_configured: YES
✓ embedding_configured: YES

========================================
PORT_CHECK
========================================
✓ port_available: YES (8765)

========================================
PROCESS_START
========================================
✓ runtime_started: PID 12345
✓ pid_written: .runtime/pids/open_core.pid

========================================
HEALTH_CHECK
========================================
✓ process_alive: YES
✓ port_listening: YES
✓ health_endpoint: {"status":"ok","startup_profile":"opensource","provider_mode":"runtime","process_health":"alive"}

========================================
RUNTIME_STARTED_SUCCESSFULLY
========================================

PID: 12345
Port: 8765
Log: .runtime/logs/open_core_runtime.log
Qdrant: .runtime/data/qdrant

Health: curl http://localhost:8765/health
Providers: curl http://localhost:8765/providers
OpenAPI: curl http://localhost:8765/openapi.json

Next step: ./scripts/deploy/macos/status_local.sh
```

### 步骤 7：验证运行时状态

**脚本：** `scripts/deploy/macos/status_local.sh`

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
- qdrant_collections: 0 (首次使用时创建)
- result: HEALTHY

Runtime is running and healthy
```

**退出码：**

- `0` - 服务运行正常且健康
- `1` - 服务未运行或不健康

### 步骤 8：测试健康端点

```bash
# 健康检查
curl http://localhost:8765/health

# 预期结果：
{
  "status": "ok",
  "startup_profile": "opensource",
  "provider_mode": "runtime",
  "process_health": "alive"
}

# 就绪检查
curl http://localhost:8765/ready

# 预期结果：
{
  "ready": true,
  "provider_mode": "runtime",
  "providers": 17,
  "vector_store_available": true,
  "vector_store_type": "QdrantLocalVectorStore"
}

# OpenAPI 规范
curl http://localhost:8765/openapi.json | jq '.info'

# 预期结果：
{
  "title": "BCSFuse Open-Core API",
  "version": "1.0.0",
  "description": "Multi-bot AI workbench"
}
```

---

## 配置指南

### 环境变量参考

#### 核心运行时

```bash
export RUNTIME_MODE="runtime"                    # runtime（生产）或 dev
export BCSFUSE_PROVIDER_MODE="runtime"           # runtime（使用 MySQL）或 dev（使用 SQLite）
export BCSFUSE_SERVER_HOST="127.0.0.1"           # 绑定地址
export BCSFUSE_SERVER_PORT="8765"                # 服务器端口
export SERVICE_HOST="0.0.0.0"                    # 服务绑定地址
export SERVICE_PORT="8765"                       # 服务端口
```

#### MySQL 配置

```bash
export MYSQL_HOST="127.0.0.1"                    # MySQL 主机
export MYSQL_PORT="3306"                         # MySQL 端口
export MYSQL_USER="bcsfuse_user"                 # MySQL 用户
export MYSQL_PASSWORD="your_password"            # MySQL 密码
export MYSQL_DATABASE="bcsfuse_oss"              # 数据库名
export MYSQL_POOL_SIZE="15"                      # 连接池大小
```

#### 向量存储（Qdrant Local）

```bash
export VECTOR_BACKEND="qdrant_local"             # 使用本地 Qdrant（嵌入式）
export QDRANT_LOCAL_PATH=".runtime/data/qdrant" # 存储路径
export QDRANT_COLLECTION_NAME="bcsfuse_profiles" # 集合名称

# ⚠️ 本地模式不要设置这些：
# export QDRANT_URL="..."                        # 仅用于服务器模式
# export QDRANT_HOST="..."                       # 仅用于服务器模式
```

#### LLM 配置

```bash
export LLM_ENABLED="true"                        # 启用 LLM 调用
export ENABLE_REAL_LLM="true"                    # 使用真实 LLM（非 mock）

export LLM_BASE_URL="https://your-llm-endpoint.com/api/anthropic"
export LLM_AUTH_TOKEN="your_llm_token_here"

export LLM_FAST_MODEL="GLM-4.7-Flash"            # 快速模型用于简单任务
export LLM_BALANCED_MODEL="GLM-4.7-Flash"        # 平衡模型
export LLM_REASONING_MODEL="GLM-5"               # 强推理模型
export LLM_LONG_CONTEXT_MODEL="GLM-4.7-Flash"    # 长上下文模型
export LLM_EXTRACTION_MODEL="GLM-4.7-Flash"      # 信息抽取模型

export LLM_DEFAULT_TIMEOUT_MS="600000"           # 10 分钟
export LLM_REASONING_TIMEOUT_MS="600000"         # 复杂任务 10 分钟
```

#### Embedding 配置

```bash
export ENABLE_REAL_EMBEDDING="true"              # 使用真实 Embedding（非 mock）
export EMBEDDING_ENABLED="true"                  # 启用 Embedding

export EMBEDDING_BASE_URL="https://your-embedding-endpoint.com/v1"
export EMBEDDING_AUTH_TOKEN="your_embedding_token_here"

export EMBEDDING_MODEL="Qwen3-Embedding-8B"      # 模型名称
export EMBEDDING_DIMENSION="4096"                # ⚠️ 关键：必须是 4096
export EMBEDDING_TIMEOUT_MS="30000"              # 30 秒
```

#### 功能开关

```bash
export ENABLE_VECTOR_AWARE_RECOMMENDATION="true"  # 语义搜索
export ENABLE_HYBRID_RETRIEVAL="true"             # 混合搜索
export ENABLE_DENSE_RETRIEVAL="true"              # 密集向量搜索
export ENABLE_SPARSE_RETRIEVAL="true"             # 稀疏检索
export ENABLE_PROFILE_EMBEDDING_INDEX="true"      # Profile 向量化
export ENABLE_G5_EXPERT_DIAGNOSIS="true"          # G5 风险评估
export ENABLE_G5_STRUCTURED_RISK="true"           # 结构化风险输出
export ENABLE_G2_STRUCTURED_STANCE="true"         # G2 立场抽取
export ENABLE_G2_CONFLICT_DIMENSIONS="true"       # G2 冲突分析
export ENABLE_G1_SEMANTIC_MATCH="true"            # G1 语义匹配
export ENABLE_G1_PROFILE_RERANK="true"            # G1 Profile 重排序
```

#### 认证

```bash
export BCSFUSE_AUTH_TOKEN="dev-opencore-token"    # 简单 token 认证
```

#### 日志

```bash
export LOG_LEVEL="INFO"                           # DEBUG, INFO, WARNING, ERROR
export LOG_ENABLE_FILE="false"                    # 启用文件日志
```

### 配置验证

编辑 `.runtime/env/.env.local` 后，验证您的配置：

```bash
# 加载环境变量
source .runtime/env/.env.local

# 验证关键设置
python3 << 'EOF'
import os
import sys

errors = []

# 检查 MySQL
if os.getenv('MYSQL_HOST') == 'change_me':
    errors.append('MYSQL_HOST 未配置')
if os.getenv('MYSQL_PASSWORD') == 'change_me':
    errors.append('MYSQL_PASSWORD 未配置')

# 检查 LLM
if os.getenv('LLM_BASE_URL') == 'change_me':
    errors.append('LLM_BASE_URL 未配置')
if os.getenv('LLM_AUTH_TOKEN') == 'change_me':
    errors.append('LLM_AUTH_TOKEN 未配置')

# 检查 Embedding
if os.getenv('EMBEDDING_BASE_URL') == 'change_me':
    errors.append('EMBEDDING_BASE_URL 未配置')
if os.getenv('EMBEDDING_AUTH_TOKEN') == 'change_me':
    errors.append('EMBEDDING_AUTH_TOKEN 未配置')

# 检查关键维度
if os.getenv('EMBEDDING_DIMENSION') != '4096':
    errors.append('EMBEDDING_DIMENSION 必须是 4096')

if errors:
    print('❌ 配置错误：')
    for error in errors:
        print(f'  - {error}')
    sys.exit(1)
else:
    print('✅ 配置有效')
    print(f'  MySQL: {os.getenv("MYSQL_HOST")}:{os.getenv("MYSQL_PORT")}/{os.getenv("MYSQL_DATABASE")}')
    print(f'  LLM: {os.getenv("LLM_BASE_URL")}')
    print(f'  Embedding: {os.getenv("EMBEDDING_BASE_URL")} (dim={os.getenv("EMBEDDING_DIMENSION")})')
    print(f'  Qdrant: {os.getenv("QDRANT_LOCAL_PATH")}')
EOF
```

---

## 运行时操作

### 启动服务

```bash
./scripts/deploy/macos/start_local.sh
```

**保证：**

- ✅ 不清除 Qdrant 数据
- ✅ 不清除 MySQL 数据
- ✅ 不重新初始化表
- ✅ 保留所有现有数据

### 停止服务

```bash
./scripts/deploy/macos/stop_local.sh
```

**执行内容：**

1. 检查 PID 文件
2. 优雅关闭（先发送 SIGTERM，10 秒后发送 SIGKILL）
3. 基于端口的回退检测
4. 清理 PID 文件
5. 验证端口已释放

**保证：**

- ✅ 不删除 Qdrant 数据
- ✅ 不删除 MySQL 数据
- ✅ 不删除日志

**预期输出：**

```
========================================
RUNTIME_STOP
========================================
- pid_file: .runtime/pids/open_core.pid
- pid: 12345

========================================
STOP_SEQUENCE
========================================
✓ graceful_shutdown: SIGTERM sent
✓ process_stopped: YES
✓ pid_file_cleaned: YES
✓ port_freed: YES

========================================
RUNTIME_STOPPED
========================================

Data preserved: Qdrant and MySQL data NOT deleted
Logs preserved: .runtime/logs/

To restart: ./scripts/deploy/macos/start_local.sh
```

### 重启服务

```bash
./scripts/deploy/macos/restart_local.sh
```

**执行内容：**

1. 调用 `stop_local.sh`
2. 等待 2 秒
3. 调用 `start_local.sh`
4. 验证健康状态

**保证：**

- ✅ 不重新初始化存储
- ✅ 不清除 Qdrant
- ✅ 不清除 MySQL
- ✅ 重用现有配置

### 检查状态

```bash
./scripts/deploy/macos/status_local.sh
```

**输出包括：**

- 服务运行状态
- PID 和端口信息
- 健康端点检查
- 运行时日志位置
- Qdrant 路径
- MySQL 主机/数据库
- 表数量
- Qdrant 集合

---

## 测试与验证

### 冒烟测试（核心验证）

运行冒烟测试验证核心功能：

```bash
python -m pytest tests/smoke/ -v
```

### 使用真实 LLM/Embedding 测试

要使用真实 LLM 和 Embedding 服务运行测试：

```bash
# 确保启用真实 LLM/Embedding
source .runtime/env/.env.local

# 验证配置
echo "ENABLE_REAL_LLM=$ENABLE_REAL_LLM"           # 应该是 "true"
echo "ENABLE_REAL_EMBEDDING=$ENABLE_REAL_EMBEDDING" # 应该是 "true"

# 运行测试验证
python -m pytest tests/smoke/ -v
```

---

## API 使用示例

### 1. 健康检查

```bash
# 健康状态
curl http://localhost:8765/health | jq

# 就绪状态
curl http://localhost:8765/ready | jq
```

### 2. 注册 Worker

```bash
curl -X POST http://localhost:8765/v1/workers/my-bot-001/sync \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Bot",
    "role": "AI Assistant",
    "capabilities": ["nlp", "search", "recommendation"],
    "visibility": "public",
    "profile": {
      "summary": "An AI assistant specialized in search and recommendation",
      "expertise": ["natural language processing", "information retrieval"],
      "scenarios": ["question answering", "expert finding"]
    }
  }' | jq
```

### 3. 设置 Worker 在线

```bash
curl -X PUT http://localhost:8765/v1/workers/my-bot-001/online \
  -H "Authorization: Bearer dev-opencore-token" | jq
```

### 4. 上传 Profile

```bash
curl -X PUT http://localhost:8765/v1/workers/my-bot-001/profiles/default \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "profile": {
      "summary": "Expert AI assistant for technical queries",
      "expertise": ["Python", "SQL", "Machine Learning"],
      "scenarios": ["code review", "debugging", "architecture design"],
      "constraints": ["No production access", "Read-only operations"]
    }
  }' | jq
```

### 5. 搜索 Workers

```bash
curl -X POST http://localhost:8765/v1/search \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "I need help with Python debugging",
    "top_k": 5
  }' | jq
```

### 6. 推荐专家

```bash
curl -X POST http://localhost:8765/api/v1/recommend \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How do I optimize a slow SQL query?",
    "context": {
      "domain": "database",
      "urgency": "high"
    },
    "top_k": 3
  }' | jq
```

### 7. 群体会诊（G2）

```bash
curl -X POST http://localhost:8765/api/v1/groups/grp-test-001/fuse \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What are the trade-offs between using MySQL vs PostgreSQL for a new project?",
    "participants": [
      {
        "worker_id": "dba-expert-001",
        "profile_key": "dba-expert-001:default"
      },
      {
        "worker_id": "backend-architect-001",
        "profile_key": "backend-architect-001:default"
      }
    ]
  }' | jq
```

---

## 监控与日志

### 日志文件

| 日志文件 | 用途 |
|----------|---------|
| `.runtime/logs/open_core_runtime.log` | 主运行时日志 |
| `.runtime/logs/deploy.log` | 部署操作日志 |
| `.runtime/logs/regression.log` | 测试执行日志 |

### 监控运行时日志

```bash
# 实时查看运行时日志
tail -f .runtime/logs/open_core_runtime.log

# 搜索错误
grep ERROR .runtime/logs/open_core_runtime.log | tail -50

# 搜索警告
grep WARNING .runtime/logs/open_core_runtime.log | tail -50

# 搜索特定 worker
grep "worker_id.*my-bot-001" .runtime/logs/open_core_runtime.log
```

### 监控指标

```bash
# 检查 provider 状态
curl http://localhost:8765/providers | jq

# 预期结果：
{
  "providers": 17,
  "provider_list": [
    {"name": "WorkerRegistryProvider", "status": "healthy"},
    {"name": "VectorStoreProvider", "status": "healthy"},
    {"name": "EmbeddingProvider", "status": "healthy"},
    {"name": "LLMProvider", "status": "healthy"},
    ...
  ]
}

# 检查特定 provider
curl http://localhost:8765/providers/WorkerRegistryProvider | jq
```

### Qdrant 向量存储

```bash
# 检查 Qdrant 集合
ls -la .runtime/data/qdrant/collections/

# 检查集合信息
curl http://localhost:8765/debug/qdrant/collections/bcsfuse_profiles | jq

# 统计向量数
curl http://localhost:8765/debug/qdrant/collections/bcsfuse_profiles/points/count | jq
```

### MySQL 数据库

```bash
# 检查表
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE \
  -e "SHOW TABLES;"

# 统计 workers
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE \
  -e "SELECT COUNT(*) FROM workers;"

# 统计在线 workers
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE \
  -e "SELECT COUNT(*) FROM worker_runtime_state WHERE runtime_state='online';"

# 查看 profiles
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE \
  -e "SELECT worker_id, profile_key, LENGTH(profile_content) FROM worker_profile_content LIMIT 10;"
```

---

## 故障排查

### 问题 1：端口已被占用

**症状：**

```
✗ port_available: NO
✗ port_listening: NO (after kill attempt)
```

**解决方案：**

```bash
# 查找占用端口 8765 的进程
lsof -iTCP:8765 -sTCP:LISTEN

# 手动杀掉进程
kill -9 <PID>

# 或者使用停止脚本
./scripts/deploy/macos/stop_local.sh

# 然后启动
./scripts/deploy/macos/start_local.sh
```

### 问题 2：MySQL 连接失败

**症状：**

```
✗ mysql_connection: FAIL
Error: Can't connect to MySQL server at '127.0.0.1'
```

**解决方案：**

```bash
# 检查 MySQL 是否运行
mysql.server status
# 或者
brew services list | grep mysql

# 如果需要，启动 MySQL
mysql.server start
# 或者
brew services start mysql

# 测试连接
mysql -h127.0.0.1 -P3306 -uroot -p -e "SELECT VERSION();"

# 如果需要，创建数据库
mysql -h127.0.0.1 -P3306 -uroot -p \
  -e "CREATE DATABASE IF NOT EXISTS bcsfuse_oss CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 如果需要，创建用户
mysql -h127.0.0.1 -P3306 -uroot -p << 'EOF'
CREATE USER 'bcsfuse_user'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON bcsfuse_oss.* TO 'bcsfuse_user'@'localhost';
FLUSH PRIVILEGES;
EOF

# 验证凭据
source .runtime/env/.env.local
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD -e "SHOW DATABASES;"
```

### 问题 3：Qdrant 锁文件检测

**症状：**

```
⚠ qdrant_lock_detected: YES
Warning: Qdrant storage locked by another process
```

**解决方案：**

```bash
# 停止运行时
./scripts/deploy/macos/stop_local.sh

# 删除锁文件（仅在运行时停止时）
rm -f .runtime/data/qdrant/.lock

# 重启
./scripts/deploy/macos/start_local.sh
```

### 问题 4：Embedding 维度不匹配

**症状：**

```
dimension error expected 4096 got 1024
```

**解决方案：**

```bash
# 编辑环境变量
vi .runtime/env/.env.local

# 确保 EMBEDDING_DIMENSION 是 4096
export EMBEDDING_DIMENSION="4096"

# 重启运行时
./scripts/deploy/macos/restart_local.sh
```

### 问题 5：LLM/Embedding 超时

**症状：**

```
ERROR: LLM request timeout after 600000ms
ERROR: Embedding request timeout after 30000ms
```

**解决方案：**

```bash
# 在 .runtime/env/.env.local 中增加超时
export LLM_DEFAULT_TIMEOUT_MS="900000"      # 15 分钟
export LLM_REASONING_TIMEOUT_MS="900000"    # 15 分钟
export EMBEDDING_TIMEOUT_MS="60000"          # 1 分钟

# 重启运行时
./scripts/deploy/macos/restart_local.sh
```

### 问题 6：运行时健康检查失败

**症状：**

```
✗ health: FAIL
[WARN] Runtime started but health check failed
```

**解决方案：**

```bash
# 检查日志
tail -100 .runtime/logs/open_core_runtime.log

# 检查常见错误：
# - MySQL 连接被拒绝
# - LLM 端点不可达
# - Embedding 端点不可达
# - Qdrant 路径权限被拒绝

# 验证环境
source .runtime/env/.env.local
echo "MySQL: $MYSQL_HOST:$MYSQL_PORT"
echo "LLM: $LLM_BASE_URL"
echo "Embedding: $EMBEDDING_BASE_URL"

# 测试 LLM 连接
curl -X POST "$LLM_BASE_URL/v1/messages" \
  -H "Authorization: Bearer $LLM_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"GLM-4.7-Flash","max_tokens":10,"messages":[{"role":"user","content":"test"}]}'

# 测试 Embedding 连接
curl -X POST "$EMBEDDING_BASE_URL/embeddings" \
  -H "Authorization: Bearer $EMBEDDING_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-Embedding-8B","input":"test"}'

# 使用调试日志重启
export LOG_LEVEL=DEBUG
./scripts/deploy/macos/restart_local.sh
```

### 问题 7：Python 模块未找到

**症状：**

```
ModuleNotFoundError: No module named 'xxx'
```

**解决方案：**

```bash
# 激活虚拟环境
source .venv/bin/activate

# 重新安装依赖
uv sync

# 重启
./scripts/deploy/macos/restart_local.sh
```

### 问题 8：权限被拒绝

**症状：**

```
PermissionError: [Errno 13] Permission denied: '.runtime/data/qdrant'
```

**解决方案：**

```bash
# 修复权限
chmod -R 755 .runtime/

# 检查所有权
ls -la .runtime/

# 如果需要，更改所有权
chown -R $(whoami) .runtime/
```

---

## 高级操作

### 备份与恢复

#### 备份 MySQL

```bash
# 设置环境变量
source .runtime/env/.env.local

# 备份数据库
mysqldump -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD \
  $MYSQL_DATABASE > backup_$(date +%Y%m%d_%H%M%S).sql

# 验证备份
ls -lh backup_*.sql
```

#### 备份 Qdrant

```bash
# 备份 Qdrant 数据
cp -r .runtime/data/qdrant qdrant_backup_$(date +%Y%m%d_%H%M%S)/

# 验证备份
du -sh qdrant_backup_*
```

#### 恢复 MySQL

```bash
# 恢复数据库
source .runtime/env/.env.local
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD \
  $MYSQL_DATABASE < backup_20260706_130000.sql
```

#### 恢复 Qdrant

```bash
# 停止运行时
./scripts/deploy/macos/stop_local.sh

# 恢复 Qdrant 数据
rm -rf .runtime/data/qdrant
cp -r qdrant_backup_20260706_130000 .runtime/data/qdrant

# 启动运行时
./scripts/deploy/macos/start_local.sh
```

### 数据重置（危险操作！）

**⚠️ 警告：这会删除所有数据**

```bash
# 重置所有数据
./scripts/deploy/macos/danger_reset_all_data.sh --confirm-reset

# 它会删除：
# - 所有 Qdrant 向量数据
# - 所有 MySQL 表数据
# - 所有运行时日志
# - 所有 PID

# 它会保留：
# - MySQL 数据库本身
# - MySQL 用户/schema 权限
# - 环境文件（.runtime/env/.env.local）
# - 代码
```

### 迁移到新环境

```bash
# 1. 备份数据
source .runtime/env/.env.local
mysqldump -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD \
  $MYSQL_DATABASE > migration_backup.sql
cp -r .runtime/data/qdrant qdrant_migration_backup/

# 2. 复制到新环境
scp migration_backup.sql user@new-host:/path/to/bcsfuse/
scp -r qdrant_migration_backup/ user@new-host:/path/to/bcsfuse/

# 3. 在新主机上恢复
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD \
  $MYSQL_DATABASE < migration_backup.sql
cp -r qdrant_migration_backup .runtime/data/qdrant

# 4. 在新主机上启动
./scripts/deploy/macos/start_local.sh
```

### 性能调优

#### MySQL 连接池

```bash
# 在 .runtime/env/.env.local 中
export MYSQL_POOL_SIZE="30"  # 增加以提高并发
```

#### LLM 超时

```bash
# 增加以处理复杂任务
export LLM_DEFAULT_TIMEOUT_MS="900000"      # 15 分钟
export LLM_REASONING_TIMEOUT_MS="1200000"   # 20 分钟
```

#### 日志级别

```bash
# 调试模式（详细）
export LOG_LEVEL="DEBUG"

# 生产模式（简洁）
export LOG_LEVEL="INFO"

# 安静模式（仅错误）
export LOG_LEVEL="ERROR"
```

---

## 最佳实践

### 1. 定期健康检查

```bash
# 启动后
./scripts/deploy/macos/status_local.sh

# 启动后
./scripts/deploy/macos/status_local.sh

# 每日 cron
0 9 * * * /path/to/bcsfuse/scripts/deploy/macos/status_local.sh > /tmp/bcsfuse_status.log 2>&1
```

### 2. 监控日志

```bash
# 实时查看运行时日志
tail -f .runtime/logs/open_core_runtime.log

# 检查错误
grep ERROR .runtime/logs/open_core_runtime.log | tail -50

# 检查 LLM 延迟
grep "LLM request took" .runtime/logs/open_core_runtime.log | tail -20
```

### 3. 破坏性操作前备份

```bash
# 重大更改前
mysqldump -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD \
  $MYSQL_DATABASE > pre_change_backup.sql
cp -r .runtime/data/qdrant qdrant_pre_change_backup/
```

### 4. 配置更改后测试

```bash
# 任何配置更改后
vi .runtime/env/.env.local
./scripts/deploy/macos/restart_local.sh
./scripts/deploy/macos/status_local.sh
python -m pytest tests/smoke/ -v
```

### 5. 部署前验证真实 LLM/Embedding

```bash
# 确保使用真实服务
source .runtime/env/.env.local
echo "ENABLE_REAL_LLM=$ENABLE_REAL_LLM"
echo "ENABLE_REAL_EMBEDDING=$ENABLE_REAL_EMBEDDING"

# 运行测试验证
python -m pytest tests/smoke/ -v
```

### 6. 保持配置同步

```bash
# 更新 .runtime/env/.env.local 后
source .runtime/env/.env.local

# 验证所有变量
env | grep -E "(MYSQL|LLM|EMBEDDING|QDRANT)" | sort
```

### 7. 文档化您的设置

```bash
# 创建设置笔记
cat > SETUP_NOTES.md << 'EOF'
# 我的 BCSFuse 设置

## 环境
- OS: macOS 13.x
- Python: 3.12.1
- MySQL: 8.0.32

## LLM 提供商
- Provider: [您的提供商]
- Model: GLM-4.7-Flash / GLM-5
- Endpoint: [您的端点]

## Embedding 提供商
- Provider: [您的提供商]
- Model: Qwen3-Embedding-8B
- Dimension: 4096

## 自定义配置
- MYSQL_POOL_SIZE=30
- LLM_REASONING_TIMEOUT_MS=1200000
EOF
```

---

## 附录 A：脚本参考

| 脚本 | 用途 | 可以多次运行 |
|--------|---------|----------------------------|
| `bootstrap_local.sh` | 一次性依赖设置 | ✅ 是 |
| `init_storage.sh` | 初始化 MySQL + Qdrant | ✅ 是 |
| `start_local.sh` | 启动运行时 | ✅ 是 |
| `stop_local.sh` | 停止运行时 | ✅ 是 |
| `restart_local.sh` | 重启运行时 | ✅ 是 |
| `status_local.sh` | 报告状态 | N/A |
| `danger_reset_all_data.sh` | **危险：重置所有数据** | ❌ 否 |

## 附录 B：环境变量检查清单

启动前，验证这些已配置：

### 必需项

- [ ] `MYSQL_HOST`
- [ ] `MYSQL_PORT`
- [ ] `MYSQL_USER`
- [ ] `MYSQL_PASSWORD`
- [ ] `MYSQL_DATABASE`
- [ ] `LLM_BASE_URL`
- [ ] `LLM_AUTH_TOKEN`
- [ ] `EMBEDDING_BASE_URL`
- [ ] `EMBEDDING_AUTH_TOKEN`

### 关键项

- [ ] `EMBEDDING_DIMENSION="4096"`（必须是 4096）
- [ ] `ENABLE_REAL_LLM="true"`
- [ ] `ENABLE_REAL_EMBEDDING="true"`

### 推荐项

- [ ] `LLM_FAST_MODEL`（例如 GLM-4.7-Flash）
- [ ] `LLM_REASONING_MODEL`（例如 GLM-5）
- [ ] `EMBEDDING_MODEL`（例如 Qwen3-Embedding-8B）
- [ ] `LOG_LEVEL`（INFO 或 DEBUG）

## 附录 C：常见问题快速参考

| 问题 | 解决方案 |
|-------|----------|
| 端口 8765 被占用 | `./scripts/deploy/macos/stop_local.sh` |
| MySQL 连接失败 | 检查 MySQL 是否运行，验证凭据 |
| Qdrant 锁文件 | `rm -f .runtime/data/qdrant/.lock` |
| Embedding 维度不匹配 | 设置 `EMBEDDING_DIMENSION="4096"` |
| LLM 超时 | 增加 `LLM_DEFAULT_TIMEOUT_MS` |
| 模块未找到 | `source .venv/bin/activate && uv sync` |
| 权限被拒绝 | `chmod -R 755 .runtime/` |
| 健康检查失败 | 检查日志：`tail .runtime/logs/open_core_runtime.log` |

---

## 总结

BCSFuse Open-Core 提供：

✅ **幂等的初始化和初始化**
✅ **固定的日志/PID/Qdrant 路径**
✅ **重启后数据持久化**
✅ **安全的启动/停止/重启脚本**
✅ **全面的测试验证**
✅ **详细的故障排查指南**
✅ **独立的数据重置危险脚本**

如有问题，请在 GitHub 提交 issue 或查阅 `docs/` 中的文档。

---

**后续步骤：**

1. ✅ 初始化：`./scripts/deploy/macos/bootstrap_local.sh`
2. ✅ 配置：编辑 `.runtime/env/.env.local`
3. ✅ 启动：`./scripts/deploy/macos/start_local.sh`
4. ✅ 验证：`python -m pytest tests/smoke/ -v`
5. ✅ 集成：使用 API 示例与您的应用集成

**支持：**

- GitHub Issues: [repository-url]/issues
- 文档：`docs/`
- 故障排查：见[故障排查](#故障排查)部分