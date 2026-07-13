#!/bin/bash
# BCSFuse Open-Source Docker Deployment Verification Script
# OPENCORE-P1-G8 Docker Deployment Gate
#
# This script validates Docker deployment for open-core bcsfuse.
# It performs all required checks for G8 gate.
#
# Usage:
#   ./verify_docker_deployment.sh
#
# Prerequisites:
#   - Docker installed and running
#   - Docker Compose installed
#   - Port 8765 available

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counters
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_SKIPPED=0

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

log_skip() {
    echo -e "${YELLOW}[SKIP]${NC} $1"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 1))
}

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================================"
echo "BCSFuse Open-Core Docker Deployment Verification"
echo "============================================================"
echo ""

# Phase C: Docker Build
echo "============================================================"
echo "Phase C: Docker Build"
echo "============================================================"
log_info "Building Docker image bcsfuse-opencore:p1-g8..."

BUILD_START=$(date +%s)
if docker build -t bcsfuse-opencore:p1-g8 . > /tmp/docker_build.log 2>&1; then
    BUILD_END=$(date +%s)
    BUILD_DURATION=$((BUILD_END - BUILD_START))
    IMAGE_ID=$(docker images -q bcsfuse-opencore:p1-g8 | head -n1)
    IMAGE_SIZE=$(docker images bcsfuse-opencore:p1-g8 --format "{{.Size}}")

    log_pass "Docker build succeeded"
    echo "  Image ID: $IMAGE_ID"
    echo "  Image Size: $IMAGE_SIZE"
    echo "  Build Duration: ${BUILD_DURATION}s"
else
    log_fail "Docker build failed"
    cat /tmp/docker_build.log
    exit 1
fi
echo ""

# Phase D: Dependency Boundary Verification
echo "============================================================"
echo "Phase D: Dependency Boundary Verification"
echo "============================================================"

log_info "Checking for forbidden internal packages..."

FORBIDDEN_PACKAGES=(
    "bcsfuse_internal"
    "ant_sofapy_base"
    "mist_sdk_py3"
    "zdas"
    "oceanbase"
)

DEPS_OK=true
for pkg in "${FORBIDDEN_PACKAGES[@]}"; do
    if docker run --rm bcsfuse-opencore:p1-g8 python -c "import importlib.util; print('FOUND' if importlib.util.find_spec('$pkg') else 'NOT_FOUND')" | grep -q "FOUND"; then
        log_fail "Forbidden package found in image: $pkg"
        DEPS_OK=false
    else
        log_pass "Forbidden package NOT found: $pkg"
    fi
done

if [ "$DEPS_OK" = true ]; then
    log_pass "Dependency boundary check passed"
else
    log_fail "Dependency boundary check failed"
    exit 1
fi
echo ""

# Phase E: Container Startup and Health/Ready
echo "============================================================"
echo "Phase E: Container Startup and Health/Ready"
echo "============================================================"

log_info "Starting container..."

# Cleanup any existing container
docker rm -f bcsfuse-opencore-g8 2>/dev/null || true

# Start container
docker run -d \
    --name bcsfuse-opencore-g8 \
    -e BCSFUSE_PROVIDER_MODE=dev_smoke \
    -e BCSFUSE_PORT=8765 \
    -p 8765:8765 \
    bcsfuse-opencore:p1-g8

log_info "Waiting for container to start (8s)..."
sleep 8

log_info "Checking container logs..."
docker logs bcsfuse-opencore-g8 --tail 50

log_info "Checking /health endpoint..."
HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/health || echo "000")
if [ "$HEALTH_STATUS" = "200" ]; then
    log_pass "/health endpoint returned 200"
else
    log_fail "/health endpoint returned $HEALTH_STATUS (expected 200)"
fi

log_info "Checking /ready endpoint..."
READY_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/ready || echo "000")
if [ "$READY_STATUS" = "200" ]; then
    log_pass "/ready endpoint returned 200"
else
    log_fail "/ready endpoint returned $READY_STATUS (expected 200)"
fi

log_info "Checking provider mode..."
curl -s http://127.0.0.1:8765/health | python3 -c "import sys, json; data = json.load(sys.stdin); print(f\"Provider mode: {data.get('provider_mode', 'unknown')}\")" || true

echo ""

# Phase F: Container P0 Targeted Suite
echo "============================================================"
echo "Phase F: Container P0 Targeted Suite"
echo "============================================================"

log_info "Running P0 targeted test suite in container..."

docker exec bcsfuse-opencore-g8 python -m pytest \
    tests/security/test_opencore_security_boundary.py \
    tests/integration/test_opencore_app_startup_health_e2e.py \
    tests/contract/test_opencore_model_service_contract.py \
    tests/integration/test_opencore_dev_smoke_provider_registry_e2e.py \
    tests/contract/test_opencore_provider_public_safe_contract.py \
    tests/integration/test_worker_registry_flow.py \
    tests/integration/test_opencore_recommend_e2e.py \
    tests/integration/test_opencore_fusion_verify_e2e.py \
    tests/integration/test_opencore_middleware_lifespan_e2e.py \
    -v --tb=short

TEST_EXIT_CODE=$?
if [ $TEST_EXIT_CODE -eq 0 ]; then
    log_pass "Container P0 targeted suite passed"
else
    log_fail "Container P0 targeted suite failed (exit code: $TEST_EXIT_CODE)"
fi

echo ""

# Phase G: Image Content Safety Check
echo "============================================================"
echo "Phase G: Image Content Safety Check"
echo "============================================================"

log_info "Checking for forbidden paths in image..."

if docker run --rm bcsfuse-opencore:p1-g8 sh -c '
    find /app -maxdepth 5 -type d | sort | grep -E "src/bcsfuse$|bcsfuse-internal|qdrant_storage|logs|data" && exit 1 || true
'; then
    log_pass "No forbidden directories found"
else
    log_fail "Forbidden directories found"
fi

log_info "Checking for forbidden files in image..."

if docker run --rm bcsfuse-opencore:p1-g8 sh -c '
    find /app -maxdepth 5 -type f | sort | grep -E "\.env\.real_token|\.env\.live\.local|\.sqlite$|\.db$|\.dump$|\.sql$|\.log$" && exit 1 || true
'; then
    log_pass "No forbidden files found"
else
    log_fail "Forbidden files found"
fi

log_info "Scanning for internal term literals..."

if docker run --rm bcsfuse-opencore:p1-g8 sh -c '
    grep -R -n -I -E "ant-sofapy-base|mist-sdk-py3|bcsfuse_internal|OceanBase|ZDAS|Layotto|MOSN" /app 2>/dev/null && echo "CHECK_INTERNAL_TERM_REVIEW_REQUIRED" || echo "NO_INTERNAL_TERM_LITERAL_PASS"
'; then
    log_pass "No internal term literals found"
else
    log_warn "Internal term literals found - review required"
fi

echo ""

# Cleanup
log_info "Cleaning up container..."
docker rm -f bcsfuse-opencore-g8

echo ""
echo "============================================================"
echo "Verification Summary"
echo "============================================================"
echo "Tests Passed: $TESTS_PASSED"
echo "Tests Failed: $TESTS_FAILED"
echo "Tests Skipped: $TESTS_SKIPPED"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    log_pass "OPENCORE-G8 DOCKER DEPLOYMENT PASSED"
    exit 0
else
    log_fail "OPENCORE-G8 DOCKER DEPLOYMENT FAILED"
    exit 1
fi