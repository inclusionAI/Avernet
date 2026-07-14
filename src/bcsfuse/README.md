# BCSFuse - Open Source Runtime Candidate

## Project Overview

BCSFuse is a collaboration control plane for AI worker management, providing task understanding, retrieval, team composition, and OpenClaw integration. This is the open-source runtime candidate designed for public release.

**IMPORTANT:**
- This OSS candidate must not use internal DRM/Layotto/Sofa/ZDAS dependencies
- Do not commit runtime data, logs, SQLite/Faiss/Qdrant storage, or real secrets
- Original internal `bcsfuse` remains protected and will not be affected

## Quick Start

### Installation

```bash
# Using uv (recommended)
uv sync

# Or using pip
pip install -e .
```

### Start Server

```bash
# Runtime mode (requires MySQL)
export BCSFUSE_AUTH_TOKEN="your-token-here"
export BCSFUSE_PROVIDER_MODE="runtime"
python main.py

# Dev mode (SQLite + Faiss)
export BCSFUSE_AUTH_TOKEN="your-token-here"
export BCSFUSE_PROVIDER_MODE="dev"
python main.py

# Dev smoke mode (offline, no external services)
export BCSFUSE_AUTH_TOKEN="dev-smoke-token"
export BCSFUSE_PROVIDER_MODE="dev_smoke"
python main.py

# Test mode (in-memory, ephemeral)
export BCSFUSE_AUTH_TOKEN="test-token"
export BCSFUSE_PROVIDER_MODE="test"
python main.py
```

### Verify Installation

```bash
# Health check
curl http://localhost:8765/health

# OpenAPI spec
curl http://localhost:8765/openapi.json

# Providers status (requires auth)
curl -H "Authorization: Bearer your-token-here" http://localhost:8765/providers
```

## Provider Modes

| Mode | Database | Vector Store | HTTP Providers | Auth | Use Case |
|------|----------|--------------|----------------|------|----------|
| `runtime` | MySQL | QdrantLocal | Real HTTP | Required | Production deployment |
| `dev` | SQLite | FaissSQLite | Real HTTP | Required | Local development |
| `dev_smoke` | SQLite | FaissSQLite | Fake/Noop | Required | Offline testing |
| `test` | InMemory | InMemory | Fake/Noop | Required | CI/CD testing |

## Authentication

### Public Endpoints (No Auth Required)

- `GET /health` - Health check
- `GET /ready` - Ready check
- `GET /openapi.json` - OpenAPI spec
- `GET /docs` - Swagger UI
- `GET /redoc` - ReDoc UI

### Protected Endpoints (Auth Required)

All `/v1/*` endpoints require authentication:

```bash
# Example: Create worker
curl -X POST \
  -H "Authorization: Bearer your-token-here" \
  -H "Content-Type: application/json" \
  -d '{"name": "test-worker", "summary": "Test worker"}' \
  http://localhost:8765/v1/workers
```

### Token Configuration

```bash
# Environment variable
export BCSFUSE_AUTH_TOKEN="your-token-here"

# Or in application.yaml
auth:
  token: ${BCSFUSE_AUTH_TOKEN}
```

### Test Tokens

- `test-token` - For test mode
- `dev-smoke-token` - For dev_smoke mode

**Security Rules:**
- Token is NEVER printed or logged
- Token is NEVER in response body
- Only dummy tokens are used in tests
- Real production tokens are NEVER committed

## Configuration

### Application Config

**File:** `configs/application.yaml`

This is the main configuration file for OSS deployment. All credentials are loaded from environment variables.

### Required Environment Variables (Runtime Mode)

```bash
# Auth
export BCSFUSE_AUTH_TOKEN="your-token-here"

# MySQL
export MYSQL_HOST="127.0.0.1"
export MYSQL_PORT="3306"
export MYSQL_DATABASE="bcsfuse_oss"
export MYSQL_USER="bcsfuse"
export MYSQL_PASSWORD="<YOUR_PASSWORD_HERE>"

# Qdrant Local
export QDRANT_LOCAL_PATH="/var/lib/bcsfuse/qdrant"
export QDRANT_COLLECTION_NAME="worker_profiles"

# Server
export BCSFUSE_SERVER_HOST="0.0.0.0"
export BCSFUSE_SERVER_PORT="8765"

# Provider Mode
export BCSFUSE_PROVIDER_MODE="runtime"
```

### Optional Environment Variables

```bash
# SQLite (for dev mode, defaults used if not set)
export BCSFUSE_DATABASE_SQLITE_PATH="/tmp/bcsfuse_dev.db"
export BCSFUSE_FAISS_INDEX_PATH="/tmp/bcsfuse_faiss.index"
export BCSFUSE_FAISS_SQLITE_PATH="/tmp/bcsfuse_faiss.db"

# Embedding Provider (optional)
export EMBEDDING_BASE_URL="http://your-embedding-service:19998"
export EMBEDDING_AUTH_TOKEN="your-embedding-token-here"
export EMBEDDING_MODEL="text-embedding-3-small"
export EMBEDDING_DIMENSION="1024"

# Reranker Provider (optional)
export RERANKER_BASE_URL="http://your-reranker-service:19998"
export RERANKER_API_KEY="your-reranker-key-here"
export RERANKER_MODEL="rerank-1"

# LLM Provider (optional)
export LLM_BASE_URL="http://your-llm-service:19998"
export LLM_AUTH_TOKEN="your-llm-token-here"
export LLM_ENABLED="true"
export LLM_FAST_MODEL="claude-3-5-sonnet-20241022"
export LLM_REASONING_MODEL="claude-3-5-sonnet-20241022"
```

## Runtime Storage

### MySQL (Runtime Mode)

BCSFuse requires MySQL 8.0+ for runtime mode. The schema is auto-created on startup.

```sql
-- Tables auto-created:
-- - workers (worker profiles)
-- - worker_bindings (user-worker bindings)
-- - worker_skills (worker skill tags)
-- - worker_profiles_raw (raw profile storage)
```

### QdrantLocal (Runtime Mode)

Vector storage uses Qdrant in embedded mode:

```bash
# Data directory
export QDRANT_LOCAL_PATH="/var/lib/bcsfuse/qdrant"

# Collection name
export QDRANT_COLLECTION_NAME="worker_profiles"
```

### SQLite (Dev Mode)

For development, BCSFuse can use SQLite with Faiss:

```bash
# SQLite database
export BCSFUSE_DATABASE_SQLITE_PATH="/tmp/bcsfuse_dev.db"

# Faiss index
export BCSFUSE_FAISS_INDEX_PATH="/tmp/bcsfuse_faiss.index"
export BCSFUSE_FAISS_SQLITE_PATH="/tmp/bcsfuse_faiss.db"
```

### InMemory (Test Mode)

For testing, everything is in-memory:

```bash
export BCSFUSE_PROVIDER_MODE="test"
# No additional storage configuration needed
```

## Testing

### Run All Tests

```bash
cd .

# Full regression suite
python tests/smoke/provider_registry_dry_run.py
python tests/smoke/startup_smoke.py
python tests/smoke/config_contract_smoke.py
python tests/smoke/auth_regression.py
python tests/smoke/business_api_smoke.py
python tests/smoke/basic_business_regression.py
python tests/smoke/basic_lifecycle_regression.py
python tests/smoke/dev_lifecycle_regression.py
python tests/smoke/runtime_provider_contract_smoke.py
python tests/smoke/runtime_local_lifecycle_smoke.py
```

### Run Individual Test Suites

```bash
# Provider registry (8 tests)
python -m pytest tests/smoke/provider_registry_dry_run.py -v

# Startup smoke (13 tests)
python -m pytest tests/smoke/startup_smoke.py -v

# Config contract (11 tests)
python -m pytest tests/smoke/config_contract_smoke.py -v

# Auth regression (16 tests)
python -m pytest tests/smoke/auth_regression.py -v

# Business API smoke (14 tests)
python -m pytest tests/smoke/business_api_smoke.py -v

# Basic business regression (16 tests)
python -m pytest tests/smoke/basic_business_regression.py -v

# Lifecycle regressions (31 + 21 operations)
python tests/smoke/basic_lifecycle_regression.py
python tests/smoke/dev_lifecycle_regression.py

# Runtime provider contract (15 tests)
python -m pytest tests/smoke/runtime_provider_contract_smoke.py -v

# Runtime local lifecycle (21 tests) - Requires MySQL
python -m pytest tests/smoke/runtime_local_lifecycle_smoke.py -v
```

**Expected Results:**
- Total: 166 tests
- PASS: 166
- FAIL: 0
- SKIP: 0 (if MySQL credentials provided)

## Secret Safety Policy

### Forbidden Content

**NEVER commit:**
- `.env.real_token`
- `.env.live.local`
- Real production tokens
- Real passwords
- Real API keys
- Real user credentials
- Internal URLs
- Internal domain names

**ONLY allowed placeholders:**
- `test-token`
- `dev-smoke-token`
- `your-token-here`
- `your-real-token-here`
- `your-production-token`
- `dummy-local-mysql-password`
- `<YOUR_PASSWORD_HERE>`

### Secret Scanning

Before committing, run:

```bash
# Check for potential secrets
grep -R -n -I -E 'Bearer [A-Za-z0-9._-]{12,}|AUTH_TOKEN=.*[^_A-Z]|PASSWORD=.*[^_A-Z]|SECRET=.*[^_A-Z]' \
  . \
  && echo "BLOCKER_POSSIBLE_SECRET_LITERAL" \
  || echo "NO_SECRET_LITERAL_PASS"
```

## Runtime Artifact Policy

### Forbidden Runtime Files

**NEVER commit:**
- `logs/`
- `data/`
- `qdrant_storage/`
- `*.log`
- `*.sqlite`
- `*.sqlite3`
- `*.db`
- `*.faiss`
- `*.index`

### Correct Runtime Paths

**Use system temp directories:**
- Qdrant: `/tmp/bcsfuse_qdrant/` or `/var/lib/bcsfuse/qdrant_storage/`
- SQLite: `/tmp/bcsfuse_dev.db` or `/var/lib/bcsfuse/bcsfuse.db`
- Faiss: `/tmp/bcsfuse_faiss.index` or `/var/lib/bcsfuse/faiss.index`

**WRONG (do NOT use):**
- Qdrant: `./qdrant_storage/` (in source directory)
- SQLite: `./data/bcsfuse.db` (in source directory)
- Faiss: `./faiss.index` (in source directory)

### Cleanup Runtime Artifacts

```bash
# Clean up after testing
rm -rf /tmp/bcsfuse_*
rm -rf /var/folders/*/T/bcsfuse_*
```

## Dependencies

### Core Dependencies

- `fastapi>=0.100.0` - Web framework
- `uvicorn[standard]>=0.23.0` - ASGI server
- `pydantic>=2.0.0` - Data validation
- `pydantic-settings>=2.0.0` - Settings management
- `httpx>=0.24.0` - HTTP client
- `httpx-sse>=0.4.0` - SSE support
- `requests` - HTTP library
- `numpy==1.26.4` - Numerical computing
- `faiss-cpu==1.8.0` - Vector similarity search
- `qdrant-client>=1.12.0` - Vector database client
- `mysql-connector-python>=8.0.0` - MySQL driver
- `apscheduler>=3.10.0` - Task scheduling
- `prometheus-client>=0.17.0` - Metrics
- `python-dotenv>=1.0.0` - Environment management
- `pyyaml>=6.0` - YAML parser
- `jsonschema>=4.0.0` - JSON schema validation

### Development Dependencies

- `pytest>=7.0.0` - Testing framework
- `pytest-asyncio>=0.21.0` - Async testing
- `pytest-cov>=4.0.0` - Coverage reporting

## Known Limitations

1. **MySQL Required for `runtime` Mode**
   - Full lifecycle tests require MySQL
   - Use `dev_smoke` or `test` mode for offline testing

2. **External HTTP Providers Optional**
   - Embedding/Reranker/LLM providers can be faked
   - Use `dev_smoke` mode for full offline testing

3. **Qdrant Path on macOS**
   - Python's `tempfile.mkdtemp()` returns `/var/folders/...` not `/tmp`
   - This is expected and safe

4. **No Rust Components in OSS**
   - BCS Rust service not included in OSS candidate
   - OSS candidate only includes Python components

5. **Provider Mode `dev_smoke` vs `test`**
   - `dev_smoke`: Uses SQLite + Faiss + Fake providers (persistent)
   - `test`: Uses InMemory + Fake providers (ephemeral)

## Public Candidate Status

**Current Status:** Release Candidate Preflight Ready

This OSS candidate has passed:
- ✅ Original bcsfuse protection verified (never modified)
- ✅ Full regression suite (166/166 tests passed)
- ✅ Auth policy enforced on all protected endpoints
- ✅ Config contract verified (no hardcoded secrets)
- ✅ MySQL stores implemented and tested
- ✅ QdrantLocal vector store implemented and tested
- ✅ Fake providers work for offline testing
- ✅ Runtime local lifecycle fully verified
- ✅ No forbidden imports in OSS code
- ✅ No forbidden directories created
- ✅ No secrets or runtime artifacts in git

**NOT READY Conditions:**

The public candidate is NOT READY for release if:
1. Original `src/bcsfuse/` has been modified
2. Full regression tests are failing
3. Secrets or runtime artifacts in git
4. Forbidden imports in OSS candidate
5. Forbidden directories created
6. Config contract violations
7. Dependencies missing or incomplete

## License

Apache License 2.0

## Support

For issues and feature requests, please use the GitHub issue tracker.

---

**Document Version:** 1.0
**Last Updated:** 2026-06-23
**Status:** OSS Public Candidate