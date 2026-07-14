#!/bin/bash

# OPENCORE-P1 Targeted Tests Runner
# Tests S5, S6, S7, S9, S18 - Worker CRUD, Profile Lifecycle, Recommend with Reranker

set -e

# Environment setup
export BCSFUSE_AUTH_TOKEN="test-token-for-local-e2e"
export BCSFUSE_PROVIDER_MODE="runtime"
export WORKER_REGISTRY_DATABASE_MODE="mysql"
export BCSFUSE_RUN_REAL_SERVICES_E2E="1"
export ENABLE_CAPABILITY_VERIFY="true"
export CAPABILITY_VERIFY_ENABLED="true"
export MYSQL_HOST="127.0.0.1"
export MYSQL_PORT="3306"
export MYSQL_USER="root"
export MYSQL_PASSWORD="<YOUR_MYSQL_PASSWORD>"  # Set your MySQL password
export MYSQL_DATABASE="bcsfuse_oss_test"
export SERVICE_URL="http://127.0.0.1:8765"

echo "========================================="
echo "OPENCORE-P1 Targeted Tests (S5, S6, S7, S9, S18)"
echo "========================================="
echo ""
echo "Environment:"
echo "  BCSFUSE_PROVIDER_MODE: ${BCSFUSE_PROVIDER_MODE}"
echo "  WORKER_REGISTRY_DATABASE_MODE: ${WORKER_REGISTRY_DATABASE_MODE}"
echo "  MYSQL_HOST: ${MYSQL_HOST}"
echo "  MYSQL_DATABASE: ${MYSQL_DATABASE}"
echo "  SERVICE_URL: ${SERVICE_URL}"
echo ""

# Run targeted tests
cd "$(dirname "$0")/.."

python -m pytest \
  tests/integration/test_opencore_runtime_real_services_e2e_core.py \
  -v --tb=short \
  -k "worker_create_get_update_delete or profile_lifecycle or recommend_with_real_reranker or reranker_unavailable"

echo ""
echo "========================================="
echo "Targeted Tests Complete"
echo "========================================="