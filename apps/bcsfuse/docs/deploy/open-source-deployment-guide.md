# BCSFuse Open-Core: Complete Deployment and Operation Guide

**Version:** 3.4
**Last Updated:** 2026-07-06
**Target Users:** Open-source contributors and developers

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Step-by-Step Installation](#step-by-step-installation)
- [Configuration Guide](#configuration-guide)
- [Runtime Operations](#runtime-operations)
- [Testing and Validation](#testing-and-validation)
- [API Usage Examples](#api-usage-examples)
- [Monitoring and Logs](#monitoring-and-logs)
- [Troubleshooting](#troubleshooting)
- [Advanced Operations](#advanced-operations)
- [Best Practices](#best-practices)

---

## Overview

BCSFuse Open-Core is a multi-bot AI workbench that enables:

- **Bot Lifecycle Management**: Create, deploy, and monitor AI bots
- **Multi-Bot Collaboration**: Coordinate multiple bots through BCS (Bot Coordination Service)
- **Intelligent Routing**: Semantic search and recommendation for bot selection
- **Expert Consultation**: G2 (group consultation) and G5 (risk assessment) use cases

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    BCSFuse Open-Core                        │
│                   (Port 8765)                               │
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

### Components

| Component | Purpose | Storage |
|-----------|---------|---------|
| **Worker Registry** | Bot metadata and state management | MySQL 8.0+ |
| **Vector Store** | Profile semantic search | Qdrant (local mode) |
| **Embedding Service** | Text vectorization | External API (Qwen3-Embedding-8B) |
| **LLM Client** | Fusion and consultation | External API (GLM-4/5) |
| **Profile Manager** | Bot capability descriptions | MySQL + Qdrant |

---

## Prerequisites

### System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **OS** | macOS 10.15+ | macOS 12+ |
| **Python** | 3.12+ | 3.12.x |
| **MySQL** | 8.0+ | 8.0.30+ |
| **RAM** | 4 GB | 8 GB+ |
| **Disk** | 2 GB | 5 GB+ |

### Required Tools

```bash
# Check Python version
python3 --version  # Should be >= 3.12

# Check MySQL
mysql --version  # Should be >= 8.0

# Check bash
bash --version  # Should be >= 3.2

# Check curl
curl --version
```

### External Services

You need access to:

1. **LLM Service** (Anthropic-compatible API):
   - Fast model (e.g., GLM-4.7-Flash)
   - Reasoning model (e.g., GLM-5)
   - Base URL and auth token required

2. **Embedding Service** (OpenAI-compatible API):
   - Model: Qwen3-Embedding-8B (dimension: 4096)
   - Base URL and auth token required
   - ⚠️ **CRITICAL**: Must use 4096-dimensional embeddings

---

## Quick Start

### 1. Clone and Bootstrap (5 minutes)

```bash
# Clone the repository
cd /path/to/your/workspace
git clone <repository-url> bcsfuse
cd bcsfuse

# Bootstrap (one-time setup)
./scripts/deploy/macos/bootstrap_local.sh
```

### 2. Configure Environment (2 minutes)

```bash
# Edit environment configuration
vi .runtime/env/.env.local

# Replace these placeholders:
# - MYSQL_USER, MYSQL_PASSWORD (your MySQL credentials)
# - LLM_BASE_URL, LLM_AUTH_TOKEN (your LLM service)
# - EMBEDDING_BASE_URL, EMBEDDING_AUTH_TOKEN (your embedding service)
```

### 3. Start and Validate (3 minutes)

```bash
# Start runtime
./scripts/deploy/macos/start_local.sh

# Check status
./scripts/deploy/macos/status_local.sh

# Run smoke tests
python -m pytest tests/smoke/ -v
```

**Total Time: ~10 minutes**

---

## Step-by-Step Installation

### Step 1: Clone Repository

```bash
# Choose your installation directory
cd /path/to/your/workspace

# Clone
git clone <repository-url> bcsfuse
cd bcsfuse

# Verify
ls -la scripts/deploy/macos/
# Should see: bootstrap_local.sh, start_local.sh, etc.
```

### Step 2: Bootstrap Environment

**Script:** `scripts/deploy/macos/bootstrap_local.sh`

This script performs one-time setup:

```bash
./scripts/deploy/macos/bootstrap_local.sh
```

**What it does:**

1. ✅ Checks Python 3.12+
2. ✅ Checks bash, curl
3. ✅ Creates Python virtual environment (`.venv/`)
4. ✅ Installs dependencies (using `uv` or `pip`)
5. ✅ Creates `.runtime/` directory structure:
   - `.runtime/logs/` - Runtime and deployment logs
   - `.runtime/pids/` - Process ID files
   - `.runtime/data/` - Qdrant vector data
   - `.runtime/env/` - Environment configuration
6. ✅ Generates `.runtime/env/.env.local` from `.env.example`
7. ✅ Calls `init_storage.sh` automatically

**Expected Output:**

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
[... init_storage.sh output ...]

========================================
BOOTSTRAP_COMPLETE
========================================

Next steps:
  1. Edit .runtime/env/.env.local with your credentials
  2. Run: ./scripts/deploy/macos/start_local.sh
```

**Idempotency:** This script is safe to run multiple times. It will skip existing components.

### Step 3: Configure MySQL

#### 3.1 Create MySQL Database

```bash
# Connect to MySQL
mysql -u root -p

# Create database
CREATE DATABASE IF NOT EXISTS bcsfuse_oss CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

# Create user (optional)
CREATE USER 'bcsfuse_user'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON bcsfuse_oss.* TO 'bcsfuse_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;

# Verify
mysql -u bcsfuse_user -p -e "SHOW DATABASES LIKE 'bcsfuse_oss';"
```

#### 3.2 Verify MySQL Connection

```bash
# Set environment variables
export MYSQL_HOST="127.0.0.1"
export MYSQL_PORT="3306"
export MYSQL_USER="bcsfuse_user"
export MYSQL_PASSWORD="your_password"
export MYSQL_DATABASE="bcsfuse_oss"

# Test connection
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD -e "SELECT VERSION();"
```

### Step 4: Configure LLM and Embedding Services

#### 4.1 Configure LLM Service

Edit `.runtime/env/.env.local`:

```bash
# LLM Configuration
export LLM_ENABLED="true"
export ENABLE_REAL_LLM="true"

# Replace with your LLM endpoint (Anthropic-compatible)
export LLM_BASE_URL="https://your-llm-endpoint.com/api/anthropic"
export LLM_AUTH_TOKEN="your_llm_token_here"

# Model configuration
export LLM_FAST_MODEL="GLM-4.7-Flash"          # Fast responses
export LLM_BALANCED_MODEL="GLM-4.7-Flash"       # Balanced
export LLM_REASONING_MODEL="GLM-5"              # Complex reasoning
export LLM_LONG_CONTEXT_MODEL="GLM-4.7-Flash"  # Long context
export LLM_EXTRACTION_MODEL="GLM-4.7-Flash"    # Info extraction

# Timeouts (adjust based on your network)
export LLM_DEFAULT_TIMEOUT_MS="600000"          # 10 minutes
export LLM_REASONING_TIMEOUT_MS="600000"        # 10 minutes
```

**Test LLM connectivity:**

```bash
# Source environment
source .runtime/env/.env.local

# Test LLM endpoint
curl -X POST "$LLM_BASE_URL/v1/messages" \
  -H "Authorization: Bearer $LLM_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "GLM-4.7-Flash",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Hello, world!"}]
  }'
```

#### 4.2 Configure Embedding Service

Edit `.runtime/env/.env.local`:

```bash
# Embedding Configuration
export ENABLE_REAL_EMBEDDING="true"
export EMBEDDING_ENABLED="true"

# Replace with your embedding endpoint (OpenAI-compatible)
export EMBEDDING_BASE_URL="https://your-embedding-endpoint.com/v1"
export EMBEDDING_AUTH_TOKEN="your_embedding_token_here"

# Model configuration
export EMBEDDING_MODEL="Qwen3-Embedding-8B"
export EMBEDDING_DIMENSION="4096"  # ⚠️ CRITICAL: Must be 4096
export EMBEDDING_TIMEOUT_MS="30000"
```

**⚠️ CRITICAL: Embedding Dimension**

- Qwen3-Embedding-8B produces 4096-dimensional vectors
- DO NOT change `EMBEDDING_DIMENSION` to other values (1024, 768, etc.)
- Mismatched dimensions will cause runtime errors

**Test Embedding connectivity:**

```bash
# Source environment
source .runtime/env/.env.local

# Test embedding endpoint
curl -X POST "$EMBEDDING_BASE_URL/embeddings" \
  -H "Authorization: Bearer $EMBEDDING_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-Embedding-8B",
    "input": "Hello, world!"
  }'

# Verify dimension in response (should be 4096)
```

#### 4.3 Verify Complete Configuration

```bash
# Source environment
source .runtime/env/.env.local

# Verify all variables
echo "MySQL Configuration:"
echo "  Host: $MYSQL_HOST"
echo "  Port: $MYSQL_PORT"
echo "  User: $MYSQL_USER"
echo "  Database: $MYSQL_DATABASE"

echo ""
echo "LLM Configuration:"
echo "  Enabled: $ENABLE_REAL_LLM"
echo "  Base URL: $LLM_BASE_URL"
echo "  Fast Model: $LLM_FAST_MODEL"
echo "  Reasoning Model: $LLM_REASONING_MODEL"

echo ""
echo "Embedding Configuration:"
echo "  Enabled: $ENABLE_REAL_EMBEDDING"
echo "  Base URL: $EMBEDDING_BASE_URL"
echo "  Model: $EMBEDDING_MODEL"
echo "  Dimension: $EMBEDDING_DIMENSION"  # Should be 4096

echo ""
echo "Vector Store:"
echo "  Backend: $VECTOR_BACKEND"
echo "  Local Path: $QDRANT_LOCAL_PATH"
echo "  Collection: $QDRANT_COLLECTION_NAME"
```

### Step 5: Initialize Storage

**Script:** `scripts/deploy/macos/init_storage.sh`

This script creates MySQL tables and Qdrant storage:

```bash
./scripts/deploy/macos/init_storage.sh
```

**What it does:**

1. ✅ Loads `.runtime/env/.env.local`
2. ✅ Checks MySQL connection
3. ✅ Creates database if not exists
4. ✅ Creates tables if not exist:
   - `workers` - Worker metadata
   - `worker_runtime_state` - Runtime state (online/offline)
   - `worker_profile_content` - Profile documents
   - `worker_audit_log` - Audit trail
5. ✅ Creates Qdrant local storage directory

**Expected Output:**

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

**Idempotency:** Safe to run multiple times. Tables use `CREATE TABLE IF NOT EXISTS`.

### Step 6: Start Runtime

**Script:** `scripts/deploy/macos/start_local.sh`

```bash
./scripts/deploy/macos/start_local.sh
```

**What it does:**

1. ✅ Loads `.runtime/env/.env.local`
2. ✅ Checks if already running (health check)
3. ✅ Cleans stale PID if needed
4. ✅ Checks port 8765 availability
5. ✅ Starts runtime: `nohup python3 main.py > $LOG_FILE 2>&1 &`
6. ✅ Writes PID to `.runtime/pids/open_core.pid`
7. ✅ Waits 5 seconds
8. ✅ Verifies process is alive
9. ✅ Verifies port is listening
10. ✅ Health check (retry up to 10 times)

**Expected Output:**

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

### Step 7: Verify Runtime Status

**Script:** `scripts/deploy/macos/status_local.sh`

```bash
./scripts/deploy/macos/status_local.sh
```

**Expected Output (Healthy):**

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
- qdrant_collections: 0 (will be created on first use)
- result: HEALTHY

Runtime is running and healthy
```

**Exit Codes:**

- `0` - Service is running and healthy
- `1` - Service is not running or unhealthy

### Step 8: Test Health Endpoints

```bash
# Health check
curl http://localhost:8765/health

# Expected:
{
  "status": "ok",
  "startup_profile": "opensource",
  "provider_mode": "runtime",
  "process_health": "alive"
}

# Readiness check
curl http://localhost:8765/ready

# Expected:
{
  "ready": true,
  "provider_mode": "runtime",
  "providers": 17,
  "vector_store_available": true,
  "vector_store_type": "QdrantLocalVectorStore"
}

# OpenAPI spec
curl http://localhost:8765/openapi.json | jq '.info'

# Expected:
{
  "title": "BCSFuse Open-Core API",
  "version": "1.0.0",
  "description": "Multi-bot AI workbench"
}
```

---

## Configuration Guide

### Environment Variables Reference

#### Core Runtime

```bash
export RUNTIME_MODE="runtime"                    # runtime (production) or dev
export BCSFUSE_PROVIDER_MODE="runtime"           # runtime (with MySQL) or dev (SQLite)
export BCSFUSE_SERVER_HOST="127.0.0.1"           # Bind address
export BCSFUSE_SERVER_PORT="8765"                # Server port
export SERVICE_HOST="0.0.0.0"                    # Service bind address
export SERVICE_PORT="8765"                       # Service port
```

#### MySQL Configuration

```bash
export MYSQL_HOST="127.0.0.1"                    # MySQL host
export MYSQL_PORT="3306"                         # MySQL port
export MYSQL_USER="bcsfuse_user"                 # MySQL user
export MYSQL_PASSWORD="your_password"            # MySQL password
export MYSQL_DATABASE="bcsfuse_oss"              # Database name
export MYSQL_POOL_SIZE="15"                      # Connection pool size
```

#### Vector Store (Qdrant Local)

```bash
export VECTOR_BACKEND="qdrant_local"             # Use local Qdrant (embedded)
export QDRANT_LOCAL_PATH=".runtime/data/qdrant" # Storage path
export QDRANT_COLLECTION_NAME="bcsfuse_profiles" # Collection name

# ⚠️ DO NOT set these for local mode:
# export QDRANT_URL="..."                        # Only for server mode
# export QDRANT_HOST="..."                       # Only for server mode
```

#### LLM Configuration

```bash
export LLM_ENABLED="true"                        # Enable LLM calls
export ENABLE_REAL_LLM="true"                    # Use real LLM (not mock)

export LLM_BASE_URL="https://your-llm-endpoint.com/api/anthropic"
export LLM_AUTH_TOKEN="your_llm_token_here"

export LLM_FAST_MODEL="GLM-4.7-Flash"            # Fast model for simple tasks
export LLM_BALANCED_MODEL="GLM-4.7-Flash"        # Balanced model
export LLM_REASONING_MODEL="GLM-5"               # Strong reasoning model
export LLM_LONG_CONTEXT_MODEL="GLM-4.7-Flash"    # Long context model
export LLM_EXTRACTION_MODEL="GLM-4.7-Flash"      # Info extraction model

export LLM_DEFAULT_TIMEOUT_MS="600000"           # 10 minutes
export LLM_REASONING_TIMEOUT_MS="600000"         # 10 minutes for complex tasks
```

#### Embedding Configuration

```bash
export ENABLE_REAL_EMBEDDING="true"              # Use real embedding (not mock)
export EMBEDDING_ENABLED="true"                  # Enable embedding

export EMBEDDING_BASE_URL="https://your-embedding-endpoint.com/v1"
export EMBEDDING_AUTH_TOKEN="your_embedding_token_here"

export EMBEDDING_MODEL="Qwen3-Embedding-8B"      # Model name
export EMBEDDING_DIMENSION="4096"                # ⚠️ CRITICAL: Must be 4096
export EMBEDDING_TIMEOUT_MS="30000"              # 30 seconds
```

#### Feature Flags

```bash
export ENABLE_VECTOR_AWARE_RECOMMENDATION="true"  # Semantic search
export ENABLE_HYBRID_RETRIEVAL="true"             # Hybrid search
export ENABLE_DENSE_RETRIEVAL="true"              # Dense vector search
export ENABLE_SPARSE_RETRIEVAL="true"             # Sparse retrieval
export ENABLE_PROFILE_EMBEDDING_INDEX="true"      # Profile vectorization
export ENABLE_G5_EXPERT_DIAGNOSIS="true"          # G5 risk assessment
export ENABLE_G5_STRUCTURED_RISK="true"           # Structured risk output
export ENABLE_G2_STRUCTURED_STANCE="true"         # G2 stance extraction
export ENABLE_G2_CONFLICT_DIMENSIONS="true"       # G2 conflict analysis
export ENABLE_G1_SEMANTIC_MATCH="true"            # G1 semantic matching
export ENABLE_G1_PROFILE_RERANK="true"            # G1 profile reranking
```

#### Authentication

```bash
export BCSFUSE_AUTH_TOKEN="dev-opencore-token"    # Simple token auth
```

#### Logging

```bash
export LOG_LEVEL="INFO"                           # DEBUG, INFO, WARNING, ERROR
export LOG_ENABLE_FILE="false"                    # Enable file logging
```

### Configuration Validation

After editing `.runtime/env/.env.local`, validate your configuration:

```bash
# Source environment
source .runtime/env/.env.local

# Validate critical settings
python3 << 'EOF'
import os
import sys

errors = []

# Check MySQL
if os.getenv('MYSQL_HOST') == 'change_me':
    errors.append('MYSQL_HOST not configured')
if os.getenv('MYSQL_PASSWORD') == 'change_me':
    errors.append('MYSQL_PASSWORD not configured')

# Check LLM
if os.getenv('LLM_BASE_URL') == 'change_me':
    errors.append('LLM_BASE_URL not configured')
if os.getenv('LLM_AUTH_TOKEN') == 'change_me':
    errors.append('LLM_AUTH_TOKEN not configured')

# Check Embedding
if os.getenv('EMBEDDING_BASE_URL') == 'change_me':
    errors.append('EMBEDDING_BASE_URL not configured')
if os.getenv('EMBEDDING_AUTH_TOKEN') == 'change_me':
    errors.append('EMBEDDING_AUTH_TOKEN not configured')

# Check critical dimension
if os.getenv('EMBEDDING_DIMENSION') != '4096':
    errors.append('EMBEDDING_DIMENSION must be 4096')

if errors:
    print('❌ Configuration Errors:')
    for error in errors:
        print(f'  - {error}')
    sys.exit(1)
else:
    print('✅ Configuration Valid')
    print(f'  MySQL: {os.getenv("MYSQL_HOST")}:{os.getenv("MYSQL_PORT")}/{os.getenv("MYSQL_DATABASE")}')
    print(f'  LLM: {os.getenv("LLM_BASE_URL")}')
    print(f'  Embedding: {os.getenv("EMBEDDING_BASE_URL")} (dim={os.getenv("EMBEDDING_DIMENSION")})')
    print(f'  Qdrant: {os.getenv("QDRANT_LOCAL_PATH")}')
EOF
```

---

## Runtime Operations

### Start Service

```bash
./scripts/deploy/macos/start_local.sh
```

**Guarantees:**

- ✅ Does NOT clear Qdrant data
- ✅ Does NOT clear MySQL data
- ✅ Does NOT re-init tables
- ✅ Preserves all existing data

### Stop Service

```bash
./scripts/deploy/macos/stop_local.sh
```

**What it does:**

1. Checks PID file
2. Graceful shutdown (SIGTERM first, SIGKILL after 10s timeout)
3. Port-based fallback detection
4. Cleans PID file
5. Verifies port is free

**Guarantees:**

- ✅ Does NOT delete Qdrant data
- ✅ Does NOT delete MySQL data
- ✅ Does NOT delete logs

**Expected Output:**

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

### Restart Service

```bash
./scripts/deploy/macos/restart_local.sh
```

**What it does:**

1. Calls `stop_local.sh`
2. Waits 2 seconds
3. Calls `start_local.sh`
4. Verifies health

**Guarantees:**

- ✅ Does NOT re-init storage
- ✅ Does NOT clear Qdrant
- ✅ Does NOT clear MySQL
- ✅ Reuses existing configuration

### Check Status

```bash
./scripts/deploy/macos/status_local.sh
```

**Output includes:**

- Service running status
- PID and port info
- Health endpoint check
- Runtime log location
- Qdrant path
- MySQL host/database
- Tables count
- Qdrant collections

---

## Testing and Validation

### Test with Real LLM/Embedding

To run tests with real LLM and Embedding services:

```bash
# Ensure real LLM/Embedding is enabled
source .runtime/env/.env.local

# Verify configuration
echo "ENABLE_REAL_LLM=$ENABLE_REAL_LLM"           # Should be "true"
echo "ENABLE_REAL_EMBEDDING=$ENABLE_REAL_EMBEDDING" # Should be "true"

# Run full validation
python -m pytest tests/smoke/ tests/integration/ -v
```

---

## API Usage Examples

### 1. Health Check

```bash
# Health
curl http://localhost:8765/health | jq

# Ready
curl http://localhost:8765/ready | jq
```

### 2. Register Worker

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

### 3. Set Worker Online

```bash
curl -X PUT http://localhost:8765/v1/workers/my-bot-001/online \
  -H "Authorization: Bearer dev-opencore-token" | jq
```

### 4. Upload Profile

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

### 5. Search Workers

```bash
curl -X POST http://localhost:8765/v1/search \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "I need help with Python debugging",
    "top_k": 5
  }' | jq
```

### 6. Recommend Experts

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

### 7. Group Consultation (G2)

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

## Monitoring and Logs

### Log Files

| Log File | Purpose |
|----------|---------|
| `.runtime/logs/open_core_runtime.log` | Main runtime log |
| `.runtime/logs/deploy.log` | Deployment operations log |
| `.runtime/logs/regression.log` | Test execution log |

### Monitor Runtime Log

```bash
# Tail runtime log
tail -f .runtime/logs/open_core_runtime.log

# Search for errors
grep ERROR .runtime/logs/open_core_runtime.log | tail -50

# Search for warnings
grep WARNING .runtime/logs/open_core_runtime.log | tail -50

# Search for specific worker
grep "worker_id.*my-bot-001" .runtime/logs/open_core_runtime.log
```

### Monitor Metrics

```bash
# Check provider status
curl http://localhost:8765/providers | jq

# Expected:
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

# Check specific provider
curl http://localhost:8765/providers/WorkerRegistryProvider | jq
```

### Qdrant Vector Store

```bash
# Check Qdrant collections
ls -la .runtime/data/qdrant/collections/

# Check collection info
curl http://localhost:8765/debug/qdrant/collections/bcsfuse_profiles | jq

# Count vectors
curl http://localhost:8765/debug/qdrant/collections/bcsfuse_profiles/points/count | jq
```

### MySQL Database

```bash
# Check tables
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE \
  -e "SHOW TABLES;"

# Count workers
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE \
  -e "SELECT COUNT(*) FROM workers;"

# Count online workers
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE \
  -e "SELECT COUNT(*) FROM worker_runtime_state WHERE runtime_state='online';"

# View profiles
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE \
  -e "SELECT worker_id, profile_key, LENGTH(profile_content) FROM worker_profile_content LIMIT 10;"
```

---

## Troubleshooting

### Issue 1: Port Already in Use

**Symptom:**

```
✗ port_available: NO
✗ port_listening: NO (after kill attempt)
```

**Solution:**

```bash
# Find process on port 8765
lsof -iTCP:8765 -sTCP:LISTEN

# Kill process manually
kill -9 <PID>

# Or use stop script
./scripts/deploy/macos/stop_local.sh

# Then start
./scripts/deploy/macos/start_local.sh
```

### Issue 2: MySQL Connection Failed

**Symptom:**

```
✗ mysql_connection: FAIL
Error: Can't connect to MySQL server at '127.0.0.1'
```

**Solution:**

```bash
# Check MySQL is running
mysql.server status
# Or
brew services list | grep mysql

# Start MySQL if needed
mysql.server start
# Or
brew services start mysql

# Test connection
mysql -h127.0.0.1 -P3306 -uroot -p -e "SELECT VERSION();"

# Create database if needed
mysql -h127.0.0.1 -P3306 -uroot -p \
  -e "CREATE DATABASE IF NOT EXISTS bcsfuse_oss CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# Create user if needed
mysql -h127.0.0.1 -P3306 -uroot -p << 'EOF'
CREATE USER 'bcsfuse_user'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON bcsfuse_oss.* TO 'bcsfuse_user'@'localhost';
FLUSH PRIVILEGES;
EOF

# Verify credentials
source .runtime/env/.env.local
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD -e "SHOW DATABASES;"
```

### Issue 3: Qdrant Lock File Detected

**Symptom:**

```
⚠ qdrant_lock_detected: YES
Warning: Qdrant storage locked by another process
```

**Solution:**

```bash
# Stop runtime
./scripts/deploy/macos/stop_local.sh

# Remove lock file (only if runtime is stopped)
rm -f .runtime/data/qdrant/.lock

# Restart
./scripts/deploy/macos/start_local.sh
```

### Issue 4: Embedding Dimension Mismatch

**Symptom:**

```
dimension error expected 4096 got 1024
```

**Solution:**

```bash
# Edit environment
vi .runtime/env/.env.local

# Ensure EMBEDDING_DIMENSION is 4096
export EMBEDDING_DIMENSION="4096"

# Restart runtime
./scripts/deploy/macos/restart_local.sh
```

### Issue 5: LLM/Embedding Timeout

**Symptom:**

```
ERROR: LLM request timeout after 600000ms
ERROR: Embedding request timeout after 30000ms
```

**Solution:**

```bash
# Increase timeouts in .runtime/env/.env.local
export LLM_DEFAULT_TIMEOUT_MS="900000"      # 15 minutes
export LLM_REASONING_TIMEOUT_MS="900000"    # 15 minutes
export EMBEDDING_TIMEOUT_MS="60000"          # 1 minute

# Restart runtime
./scripts/deploy/macos/restart_local.sh
```

### Issue 6: Runtime Health Check Failed

**Symptom:**

```
✗ health: FAIL
[WARN] Runtime started but health check failed
```

**Solution:**

```bash
# Check logs
tail -100 .runtime/logs/open_core_runtime.log

# Check for common errors:
# - MySQL connection refused
# - LLM endpoint unreachable
# - Embedding endpoint unreachable
# - Qdrant path permission denied

# Verify environment
source .runtime/env/.env.local
echo "MySQL: $MYSQL_HOST:$MYSQL_PORT"
echo "LLM: $LLM_BASE_URL"
echo "Embedding: $EMBEDDING_BASE_URL"

# Test LLM connectivity
curl -X POST "$LLM_BASE_URL/v1/messages" \
  -H "Authorization: Bearer $LLM_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"GLM-4.7-Flash","max_tokens":10,"messages":[{"role":"user","content":"test"}]}'

# Test Embedding connectivity
curl -X POST "$EMBEDDING_BASE_URL/embeddings" \
  -H "Authorization: Bearer $EMBEDDING_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-Embedding-8B","input":"test"}'

# Restart with debug logging
export LOG_LEVEL=DEBUG
./scripts/deploy/macos/restart_local.sh
```

### Issue 7: Python Module Not Found

**Symptom:**

```
ModuleNotFoundError: No module named 'xxx'
```

**Solution:**

```bash
# Activate virtual environment
source .venv/bin/activate

# Reinstall dependencies
uv sync

# Restart
./scripts/deploy/macos/restart_local.sh
```

### Issue 8: Permission Denied

**Symptom:**

```
PermissionError: [Errno 13] Permission denied: '.runtime/data/qdrant'
```

**Solution:**

```bash
# Fix permissions
chmod -R 755 .runtime/

# Check ownership
ls -la .runtime/

# If needed, change ownership
chown -R $(whoami) .runtime/
```

---

## Advanced Operations

### Backup and Restore

#### Backup MySQL

```bash
# Set environment
source .runtime/env/.env.local

# Backup database
mysqldump -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD \
  $MYSQL_DATABASE > backup_$(date +%Y%m%d_%H%M%S).sql

# Verify backup
ls -lh backup_*.sql
```

#### Backup Qdrant

```bash
# Backup Qdrant data
cp -r .runtime/data/qdrant qdrant_backup_$(date +%Y%m%d_%H%M%S)/

# Verify backup
du -sh qdrant_backup_*
```

#### Restore MySQL

```bash
# Restore database
source .runtime/env/.env.local
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD \
  $MYSQL_DATABASE < backup_20260706_130000.sql
```

#### Restore Qdrant

```bash
# Stop runtime
./scripts/deploy/macos/stop_local.sh

# Restore Qdrant data
rm -rf .runtime/data/qdrant
cp -r qdrant_backup_20260706_130000 .runtime/data/qdrant

# Start runtime
./scripts/deploy/macos/start_local.sh
```

### Data Reset (Dangerous!)

**⚠️ WARNING: This deletes ALL data**

```bash
# Reset all data
./scripts/deploy/macos/danger_reset_all_data.sh --confirm-reset

# What it destroys:
# - All Qdrant vector data
# - All MySQL table data
# - All runtime logs
# - All PIDs

# What it preserves:
# - MySQL database itself
# - MySQL user/schema permissions
# - Environment files (.runtime/env/.env.local)
# - Code
```

### Migration to New Environment

```bash
# 1. Backup data
source .runtime/env/.env.local
mysqldump -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD \
  $MYSQL_DATABASE > migration_backup.sql
cp -r .runtime/data/qdrant qdrant_migration_backup/

# 2. Copy to new environment
scp migration_backup.sql user@new-host:/path/to/bcsfuse/
scp -r qdrant_migration_backup/ user@new-host:/path/to/bcsfuse/

# 3. On new host, restore
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD \
  $MYSQL_DATABASE < migration_backup.sql
cp -r qdrant_migration_backup .runtime/data/qdrant

# 4. Start on new host
./scripts/deploy/macos/start_local.sh
```

### Performance Tuning

#### MySQL Connection Pool

```bash
# In .runtime/env/.env.local
export MYSQL_POOL_SIZE="30"  # Increase for high concurrency
```

#### LLM Timeout

```bash
# Increase for complex tasks
export LLM_DEFAULT_TIMEOUT_MS="900000"      # 15 minutes
export LLM_REASONING_TIMEOUT_MS="1200000"   # 20 minutes
```

#### Logging Level

```bash
# Debug mode (verbose)
export LOG_LEVEL="DEBUG"

# Production mode (concise)
export LOG_LEVEL="INFO"

# Quiet mode (errors only)
export LOG_LEVEL="ERROR"
```

---

## Best Practices

### 1. Regular Health Checks

```bash
# After boot
./scripts/deploy/macos/status_local.sh

# After start
./scripts/deploy/macos/status_local.sh

# Before running tests
./scripts/deploy/macos/status_local.sh

# Daily cron
0 9 * * * /path/to/bcsfuse/scripts/deploy/macos/status_local.sh > /tmp/bcsfuse_status.log 2>&1
```

### 2. Monitor Logs

```bash
# Tail runtime log
tail -f .runtime/logs/open_core_runtime.log

# Check for errors
grep ERROR .runtime/logs/open_core_runtime.log | tail -50

# Check for LLM latency
grep "LLM request took" .runtime/logs/open_core_runtime.log | tail -20
```

### 3. Backup Before Destructive Operations

```bash
# Before major changes
mysqldump -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD \
  $MYSQL_DATABASE > pre_change_backup.sql
cp -r .runtime/data/qdrant qdrant_pre_change_backup/
```

### 4. Test After Configuration Changes

```bash
# After any config change
vi .runtime/env/.env.local
./scripts/deploy/macos/restart_local.sh
./scripts/deploy/macos/status_local.sh
python -m pytest tests/smoke/ -v
```

### 5. Validate Real LLM/Embedding Before Deployment

```bash
# Ensure real services are used
source .runtime/env/.env.local
echo "ENABLE_REAL_LLM=$ENABLE_REAL_LLM"
echo "ENABLE_REAL_EMBEDDING=$ENABLE_REAL_EMBEDDING"

# Run smoke tests
python -m pytest tests/smoke/ -v
```

### 6. Keep Configuration in Sync

```bash
# After updating .runtime/env/.env.local
source .runtime/env/.env.local

# Verify all variables
env | grep -E "(MYSQL|LLM|EMBEDDING|QDRANT)" | sort
```

### 7. Document Your Setup

```bash
# Create setup notes
cat > SETUP_NOTES.md << 'EOF'
# My BCSFuse Setup

## Environment
- OS: macOS 13.x
- Python: 3.12.1
- MySQL: 8.0.32

## LLM Provider
- Provider: [Your Provider]
- Model: GLM-4.7-Flash / GLM-5
- Endpoint: [Your Endpoint]

## Embedding Provider
- Provider: [Your Provider]
- Model: Qwen3-Embedding-8B
- Dimension: 4096

## Custom Configurations
- MYSQL_POOL_SIZE=30
- LLM_REASONING_TIMEOUT_MS=1200000
EOF
```

---

## Appendix A: Script Reference

| Script | Purpose | Safe to Run Multiple Times |
|--------|---------|----------------------------|
| `bootstrap_local.sh` | One-time dependency setup | ✅ Yes |
| `init_storage.sh` | Initialize MySQL + Qdrant | ✅ Yes |
| `start_local.sh` | Start runtime | ✅ Yes |
| `stop_local.sh` | Stop runtime | ✅ Yes |
| `restart_local.sh` | Restart runtime | ✅ Yes |
| `status_local.sh` | Report status | N/A |
| `danger_reset_all_data.sh` | **DANGER: Reset all data** | ❌ No |

## Appendix B: Environment Variables Checklist

Before starting, verify these are configured:

### Required

- [ ] `MYSQL_HOST`
- [ ] `MYSQL_PORT`
- [ ] `MYSQL_USER`
- [ ] `MYSQL_PASSWORD`
- [ ] `MYSQL_DATABASE`
- [ ] `LLM_BASE_URL`
- [ ] `LLM_AUTH_TOKEN`
- [ ] `EMBEDDING_BASE_URL`
- [ ] `EMBEDDING_AUTH_TOKEN`

### Critical

- [ ] `EMBEDDING_DIMENSION="4096"` (must be 4096)
- [ ] `ENABLE_REAL_LLM="true"`
- [ ] `ENABLE_REAL_EMBEDDING="true"`

### Recommended

- [ ] `LLM_FAST_MODEL` (e.g., GLM-4.7-Flash)
- [ ] `LLM_REASONING_MODEL` (e.g., GLM-5)
- [ ] `EMBEDDING_MODEL` (e.g., Qwen3-Embedding-8B)
- [ ] `LOG_LEVEL` (INFO or DEBUG)

## Appendix C: Common Issues Quick Reference

| Issue | Solution |
|-------|----------|
| Port 8765 in use | `./scripts/deploy/macos/stop_local.sh` |
| MySQL connection failed | Check MySQL is running, verify credentials |
| Qdrant lock file | `rm -f .runtime/data/qdrant/.lock` |
| Embedding dimension mismatch | Set `EMBEDDING_DIMENSION="4096"` |
| LLM timeout | Increase `LLM_DEFAULT_TIMEOUT_MS` |
| Module not found | `source .venv/bin/activate && uv sync` |
| Permission denied | `chmod -R 755 .runtime/` |
| Health check failed | Check logs: `tail .runtime/logs/open_core_runtime.log` |

---

## Summary

BCSFuse Open-Core provides:

✅ **Idempotent bootstrap and initialization**
✅ **Fixed log/PID/Qdrant paths**
✅ **Data preservation across restarts**
✅ **Safe start/stop/restart scripts**
✅ **Comprehensive test validation with pytest**
✅ **Detailed troubleshooting guide**
✅ **Isolated danger script for data reset**

For questions or issues, please open a GitHub issue or consult the documentation in `docs/`.

---

**Next Steps:**

1. ✅ Bootstrap: `./scripts/deploy/macos/bootstrap_local.sh`
2. ✅ Configure: Edit `.runtime/env/.env.local`
3. ✅ Start: `./scripts/deploy/macos/start_local.sh`
4. ✅ Validate: `python -m pytest tests/smoke/ -v`
5. ✅ Integrate: Use API examples to integrate with your application

**Support:**

- GitHub Issues: [repository-url]/issues
- Documentation: `docs/`
- Troubleshooting: See [Troubleshooting](#troubleshooting) section