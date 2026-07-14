# BCSFuse Open-Core macOS Local Deployment Guide

**Phase 3.1 - OSS Deployability macOS First Gate**

This guide provides step-by-step instructions for deploying BCSFuse Open-Core on macOS.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Detailed Steps](#detailed-steps)
- [Script Reference](#script-reference)
- [Data Persistence](#data-persistence)
- [Troubleshooting](#troubleshooting)
- [Danger Operations](#danger-operations)

## Prerequisites

### Required

- **macOS** 10.15+
- **Python** 3.12+
- **MySQL** 8.0+ (running locally)
- **uv** or **pip** (for dependency management)

### Optional (but recommended)

- **mysql** client (for manual verification)

## Quick Start

```bash
# 1. Bootstrap (one-time setup)
./scripts/deploy/macos/bootstrap_local.sh

# 2. Edit environment file with your credentials
vi .runtime/env/.env.local

# 3. Start runtime
./scripts/deploy/macos/start_local.sh

# 4. Check status
./scripts/deploy/macos/status_local.sh

# 5. Run smoke tests
python -m pytest tests/smoke/ -v

# 6. Stop runtime
./scripts/deploy/macos/stop_local.sh
```

## Detailed Steps

### 1. Bootstrap (First Time)

**Script:** `scripts/deploy/macos/bootstrap_local.sh`

**Purpose:** One-click dependency preparation

**What it does:**
- Checks Python 3.12+
- Checks bash, curl
- Creates or reuses `.venv`
- Installs dependencies (uv sync)
- Creates `.runtime/` directory structure:
  - `.runtime/logs/`
  - `.runtime/pids/`
  - `.runtime/data/`
  - `.runtime/env/`
- Generates `.runtime/env/.env.local` from `.env.example` (if not exists)
- Calls `init_storage.sh` automatically

**Idempotency:**
- ✅ venv exists: skip creation
- ✅ env exists: skip generation, warn if placeholders
- ✅ MySQL tables exist: skip creation
- ✅ Qdrant path exists: skip creation
- ❌ No data destruction

**Example:**

```bash
cd /path/to/bcsfuse
./scripts/deploy/macos/bootstrap_local.sh
```

**Expected output:**

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

Next steps:
  1. Edit .runtime/env/.env.local with your credentials
  2. Run: ./scripts/deploy/macos/start_local.sh
```

### 2. Configure Environment

**File:** `.runtime/env/.env.local`

**Critical fields:**

```bash
# MySQL Configuration
export MYSQL_HOST="127.0.0.1"
export MYSQL_PORT="3306"
export MYSQL_USER="your_user"
export MYSQL_PASSWORD="your_password"
export MYSQL_DATABASE="bcsfuse_oss"

# LLM Configuration
export LLM_BASE_URL="your_llm_endpoint"
export LLM_AUTH_TOKEN="your_llm_token"

# Embedding Configuration
export EMBEDDING_BASE_URL="your_embedding_endpoint"
export EMBEDDING_AUTH_TOKEN="your_embedding_token"
export EMBEDDING_DIMENSION="4096"  # CRITICAL: must be 4096

# Qdrant (auto-configured)
export QDRANT_LOCAL_PATH=".runtime/data/qdrant"
```

**Important:**
- Replace `change_me` placeholders with real credentials
- Ensure `EMBEDDING_DIMENSION="4096"` (for Qwen3-Embedding-8B)

### 3. Initialize Storage

**Script:** `scripts/deploy/macos/init_storage.sh`

**Purpose:** Initialize MySQL schema and Qdrant local storage

**What it does:**
- Loads `.runtime/env/.env.local`
- Checks MySQL connection
- Creates database if not exists (`CREATE DATABASE IF NOT EXISTS`)
- Creates tables if not exist (`CREATE TABLE IF NOT EXISTS`)
  - `workers`
  - `worker_runtime_state`
  - `worker_profile_content`
  - `worker_audit_log`
- Creates `.runtime/data/qdrant/` directory
- Logs to `.runtime/logs/deploy.log`

**No Data Loss:**
- ✅ Uses `CREATE DATABASE IF NOT EXISTS` (not `DROP DATABASE`)
- ✅ Uses `CREATE TABLE IF NOT EXISTS` (not `DROP TABLE`)
- ✅ Uses `mkdir -p` for Qdrant (not `rm -rf`)
- ❌ No destructive operations

**Example:**

```bash
./scripts/deploy/macos/init_storage.sh
```

**Idempotency test:**

```bash
# Run twice - should be safe
./scripts/deploy/macos/init_storage.sh
./scripts/deploy/macos/init_storage.sh  # Should say "tables_existing_before: 4"
```

### 4. Start Runtime

**Script:** `scripts/deploy/macos/start_local.sh`

**Purpose:** Start open-core runtime with fixed paths

**Fixed paths:**
- Log: `.runtime/logs/open_core_runtime.log`
- PID: `.runtime/pids/open_core.pid`
- Qdrant: `.runtime/data/qdrant` (unless externally set)

**What it does:**
- Loads `.runtime/env/.env.local`
- Checks if already running (health check and exit 0 if healthy)
- Cleans stale PID if needed
- Checks port 8765 availability
- Starts runtime: `nohup python3 main.py > $LOG_FILE 2>&1 &`
- Writes PID to `.runtime/pids/open_core.pid`
- Waits 5 seconds
- Verifies process is alive
- Verifies port is listening
- Health check (retry up to 10 times)

**Restart guarantee:**
- ✅ Does NOT clear Qdrant
- ✅ Does NOT clear MySQL
- ✅ Does NOT re-init tables

**Example:**

```bash
./scripts/deploy/macos/start_local.sh
```

**Expected output:**

```
========================================
RUNTIME_STARTED_SUCCESSFULLY
========================================

PID: 12345
Port: 8765
Log: .runtime/logs/open_core_runtime.log
Qdrant: .runtime/data/qdrant

Health: curl http://localhost:8765/health
Providers: curl http://localhost:8765/providers

Next step: ./scripts/deploy/macos/status_local.sh
```

### 5. Check Status

**Script:** `scripts/deploy/macos/status_local.sh`

**Purpose:** Report service status with data location info

**Output includes:**
- Service running status
- PID and port info
- Health endpoint check
- Runtime log location
- Qdrant path
- MySQL host/database
- Tables count
- Qdrant collections

**Example:**

```bash
./scripts/deploy/macos/status_local.sh
```

**Expected output (healthy):**

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

Runtime is running and healthy
```

**Exit codes:**
- 0: Service is running and healthy
- 1: Service is not running or unhealthy

### 7. Stop Runtime

**Script:** `scripts/deploy/macos/stop_local.sh`

**Purpose:** Safely stop runtime without data loss

**What it does:**
- Checks PID file
- Graceful shutdown (SIGTERM first, SIGKILL after 10s timeout)
- Port-based fallback detection
- Cleans PID file
- Verifies port is free

**No Data Loss:**
- ✅ Does NOT delete Qdrant data
- ✅ Does NOT delete MySQL data
- ✅ Does NOT delete logs
- ✅ Does NOT delete PID file (just empties it)

**Example:**

```bash
./scripts/deploy/macos/stop_local.sh
```

**Expected output:**

```
========================================
RUNTIME_STOPPED
========================================

Data preserved: Qdrant and MySQL data NOT deleted
Logs preserved: .runtime/logs/

To restart: ./scripts/deploy/macos/start_local.sh
```

### 8. Restart Runtime

**Script:** `scripts/deploy/macos/restart_local.sh`

**Purpose:** Safe restart (stop + start) with data preservation guarantee

**What it does:**
- Calls `stop_local.sh`
- Waits 2 seconds
- Calls `start_local.sh`
- Verifies health

**Data preservation guarantee:**
- ✅ Does NOT re-init storage
- ✅ Does NOT clear Qdrant
- ✅ Does NOT clear MySQL
- ✅ Reuses existing `.runtime/env/.env.local`
- ✅ Reuses existing `QDRANT_LOCAL_PATH`

**Example:**

```bash
./scripts/deploy/macos/restart_local.sh
```

## Script Reference

| Script | Purpose | Idempotent | Data Loss Risk |
|--------|---------|------------|----------------|
| `bootstrap_local.sh` | Dependency setup | ✅ Yes | ❌ None |
| `init_storage.sh` | Initialize MySQL + Qdrant | ✅ Yes | ❌ None |
| `start_local.sh` | Start runtime | ✅ Yes | ❌ None |
| `stop_local.sh` | Stop runtime | ✅ Yes | ❌ None |
| `restart_local.sh` | Restart runtime | ✅ Yes | ❌ None |
| `status_local.sh` | Report status | N/A | ❌ None |
| `danger_reset_all_data.sh` | **DANGER: Reset all data** | ❌ No | ⚠️ **HIGH** |

## Data Persistence

### What is preserved across restarts?

| Data | Path | Persistence |
|------|------|-------------|
| MySQL database | Configured in `MYSQL_DATABASE` | ✅ Preserved |
| MySQL tables | `workers`, `worker_profile_content`, `worker_runtime_state`, `worker_audit_log` | ✅ Preserved |
| Qdrant vectors | `.runtime/data/qdrant/` | ✅ Preserved |
| Runtime logs | `.runtime/logs/` | ✅ Preserved |
| Environment config | `.runtime/env/.env.local` | ✅ Preserved |
| PIDs | `.runtime/pids/open_core.pid` | ❌ Cleaned (not data) |

### Backups

**MySQL backup:**

```bash
mysqldump -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE > backup.sql
```

**Qdrant backup:**

```bash
cp -r .runtime/data/qdrant qdrant_backup_$(date +%Y%m%d)
```

## Troubleshooting

### Common Issues

#### 1. Port 8765 already in use

**Symptom:**

```
✗ port_available: NO
✗ port_listening: NO (after kill attempt)
```

**Solution:**

```bash
# Find process on port
lsof -iTCP:8765 -sTCP:LISTEN

# Kill process
kill -9 <PID>

# Or use stop script
./scripts/deploy/macos/stop_local.sh
```

#### 2. MySQL connection failed

**Symptom:**

```
✗ mysql_connection: FAIL
```

**Solution:**

```bash
# Check MySQL is running
mysql.server status

# Start MySQL if needed
mysql.server start

# Verify database exists
mysql -h127.0.0.1 -P3306 -uroot -p -e "SHOW DATABASES LIKE 'bcsfuse_oss';"

# Create database if needed
mysql -h127.0.0.1 -P3306 -uroot -p -e "CREATE DATABASE IF NOT EXISTS bcsfuse_oss CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

#### 3. Qdrant lock file detected

**Symptom:**

```
⚠ qdrant_lock_detected: YES
```

**Solution:**

```bash
# Stop runtime
./scripts/deploy/macos/stop_local.sh

# Remove lock file (only if runtime is stopped)
rm -f .runtime/data/qdrant/.lock

# Start runtime
./scripts/deploy/macos/start_local.sh
```

#### 4. Embedding dimension mismatch

**Symptom:**

```
dimension error expected 4096 got 1024
```

**Solution:**

```bash
# Edit env file
vi .runtime/env/.env.local

# Ensure EMBEDDING_DIMENSION="4096"
export EMBEDDING_DIMENSION="4096"
```

#### 5. Runtime started but health check failed

**Symptom:**

```
✗ health: FAIL
[WARN] Runtime started but health check failed
```

**Solution:**

```bash
# Check logs
tail -100 .runtime/logs/open_core_runtime.log

# Common issues:
# - MySQL connection refused
# - Embedding endpoint unreachable
# - Qdrant path issues

# Verify environment
cat .runtime/env/.env.local | grep -E "(MYSQL|EMBEDDING|QDRANT)"

# Restart with debug
export LOG_LEVEL=DEBUG
./scripts/deploy/macos/restart_local.sh
```

## Danger Operations

### Reset All Data

**⚠️ WARNING: This deletes ALL data (Qdrant + MySQL tables + logs)**

**Script:** `scripts/deploy/macos/danger_reset_all_data.sh`

**Usage:**

```bash
./scripts/deploy/macos/danger_reset_all_data.sh --confirm-reset
```

**What it destroys:**
- All Qdrant vector data (`.runtime/data/qdrant/`)
- All MySQL table data (`workers`, `worker_profile_content`, `worker_runtime_state`, `worker_audit_log`)
- All runtime logs (`.runtime/logs/`)
- All PIDs (`.runtime/pids/`)

**What it preserves:**
- MySQL database itself
- MySQL user/schema permissions
- Environment files (`.runtime/env/.env.local`)
- Code

**When to use:**
- Development testing reset
- Complete data cleanup
- Before re-running initial test suite

**When NOT to use:**
- Production environments
- When you have valuable test data
- When other users are depending on the data

## Best Practices

### 1. Check Status Regularly

```bash
# After boot
./scripts/deploy/macos/status_local.sh

# After start
./scripts/deploy/macos/status_local.sh

# Before P0 regression
./scripts/deploy/macos/status_local.sh
```

### 2. Monitor Logs

```bash
# Tail runtime log
tail -f .runtime/logs/open_core_runtime.log

# Check deploy log
tail -f .runtime/logs/deploy.log

# Check regression log
tail -f .runtime/logs/regression.log
```

### 3. Backup Before Destructive Operations

```bash
# Backup MySQL
mysqldump -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE > backup_$(date +%Y%m%d).sql

# Backup Qdrant
cp -r .runtime/data/qdrant qdrant_backup_$(date +%Y%m%d)
```

### 4. Idempotency Testing

```bash
# Test init_storage is idempotent
./scripts/deploy/macos/init_storage.sh
./scripts/deploy/macos/init_storage.sh  # Should be safe

# Test restart preserves data
./scripts/deploy/macos/start_local.sh
python -m pytest tests/smoke/ -v  # Assume all PASS
./scripts/deploy/macos/restart_local.sh
python -m pytest tests/smoke/ -v  # Should still be all PASS
```

### 5. Always Verify P0 After Changes

After any configuration change or restart:

```bash
python -m pytest tests/smoke/ -v
```

Expected: **All PASS**

## Summary

BCSFuse Open-Core macOS local deployment provides:

✅ **Idempotent bootstrap and initialization**
✅ **Fixed log/PID/Qdrant paths**
✅ **Data preservation across restarts**
✅ **Safe start/stop/restart scripts**
✅ **P0 regression validation**
✅ **Comprehensive troubleshooting guide**
✅ **Isolated danger script for data reset**

For Docker deployment, see: [docker.md](docker.md)

For troubleshooting, see: [troubleshooting.md](troubleshooting.md)