#!/bin/bash

# Start BCSFuse Runtime Service
#
# IMPORTANT: This is a template file. DO NOT commit real credentials.
# Copy this file to start_runtime_service.local.sh and set your real credentials there.
# Add start_runtime_service.local.sh to .gitignore

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export BCSFUSE_PROVIDER_MODE="runtime"
export WORKER_REGISTRY_DATABASE_MODE="mysql"
export BCSFUSE_RUN_REAL_SERVICES_E2E="1"
export ENABLE_CAPABILITY_VERIFY="true"
export CAPABILITY_VERIFY_ENABLED="true"

# MySQL Configuration
export MYSQL_HOST="127.0.0.1"
export MYSQL_PORT="3306"
export MYSQL_USER="root"
export MYSQL_PASSWORD="<YOUR_MYSQL_PASSWORD>"  # Set your MySQL password
export MYSQL_DATABASE="bcsfuse_oss_test"

# Auth Configuration
export BCSFUSE_AUTH_TOKEN="test-token-for-local-e2e"

# LLM Configuration (Anthropic-compatible endpoint)
# Configure your LLM endpoint URL and auth token
export LLM_BASE_URL=""  # configure your endpoint
export LLM_AUTH_TOKEN="<YOUR_LLM_AUTH_TOKEN>"

# Embedding Configuration (OpenAI-compatible endpoint)
# IMPORTANT: EMBEDDING_BASE_URL must use /v1 path (OpenAI-compatible)
export EMBEDDING_BASE_URL=""  # configure your endpoint
export EMBEDDING_MODEL="Qwen3-Embedding-8B"
export EMBEDDING_DIMENSION="4096"
export EMBEDDING_AUTH_TOKEN="<YOUR_EMBEDDING_AUTH_TOKEN>"

# Reranker Configuration (OpenAI-compatible endpoint)
export RERANKER_BASE_URL=""  # configure your endpoint
export RERANKER_AUTH_TOKEN="<YOUR_RERANKER_AUTH_TOKEN>"

nohup python3 main.py > /tmp/bcsfuse_runtime_restart.log 2>&1 &

echo "Service started, PID: $!"
sleep 5

# Check health
curl -s http://127.0.0.1:8765/health | python -m json.tool