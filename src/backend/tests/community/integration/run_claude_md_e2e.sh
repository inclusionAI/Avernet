#!/usr/bin/env bash
# =============================================================================
# CLAUDE.md CRUD 端到端测试脚本
#
# 拉起 backend (local mode) → 执行 HTTP 接口验证 → 停止 backend
#
# 用法:
#   cd ocb_worktrees/feat-claude-md-crud
#   bash src/backend/tests/integration/run_claude_md_e2e.sh
#
# 退出码:
#   0 = 全部通过
#   1 = 有失败用例
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OCB_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
BACKEND_DIR="$OCB_ROOT/src/backend"

BASE_URL="http://127.0.0.1:8888"
ENTITY_TYPE="staff"
ENTITY_ID="330429"
BOT_ID="default"
HEADERS='-H "x-user-id: 330429" -H "Cookie: ctoken=test"'

PASSED=0
FAILED=0
TOTAL=0

# ---- 颜色 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ---- 辅助函数 ----
log_info()  { echo -e "${GREEN}[PASS]${NC} $1"; }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; }
log_section() { echo -e "\n${YELLOW}── $1 ──${NC}"; }

assert_status() {
    local desc="$1" expected="$2" actual="$3"
    TOTAL=$((TOTAL + 1))
    if [ "$actual" -eq "$expected" ]; then
        PASSED=$((PASSED + 1))
        log_info "$desc (HTTP $actual)"
    else
        FAILED=$((FAILED + 1))
        log_fail "$desc (expected $expected, got $actual)"
    fi
}

assert_contains() {
    local desc="$1" body="$2" pattern="$3"
    TOTAL=$((TOTAL + 1))
    if echo "$body" | grep -q "$pattern"; then
        PASSED=$((PASSED + 1))
        log_info "$desc"
    else
        FAILED=$((FAILED + 1))
        log_fail "$desc — pattern '$pattern' not found in response"
    fi
}

assert_not_contains() {
    local desc="$1" body="$2" pattern="$3"
    TOTAL=$((TOTAL + 1))
    if echo "$body" | grep -q "$pattern"; then
        FAILED=$((FAILED + 1))
        log_fail "$desc — pattern '$pattern' unexpectedly found"
    else
        PASSED=$((PASSED + 1))
        log_info "$desc"
    fi
}

assert_equals() {
    local desc="$1" expected="$2" actual="$3"
    TOTAL=$((TOTAL + 1))
    if [ "$expected" = "$actual" ]; then
        PASSED=$((PASSED + 1))
        log_info "$desc"
    else
        FAILED=$((FAILED + 1))
        log_fail "$desc — expected '$expected', got '$actual'"
    fi
}

identity_url() {
    echo "$BASE_URL/api/identity/$ENTITY_TYPE/$ENTITY_ID/bot/$BOT_ID/$1"
}

do_get() {
    curl -s -w "\n%{http_code}" \
        -H "x-user-id: $ENTITY_ID" -H "Cookie: ctoken=test" \
        "$(identity_url "$1")$2"
}

do_put() {
    curl -s -w "\n%{http_code}" \
        -X PUT \
        -H "x-user-id: $ENTITY_ID" -H "Cookie: ctoken=test" \
        -H "Content-Type: application/json" \
        "$(identity_url "$1")" \
        -d "$2"
}

# ---- Step 1: 拉起 backend ----
log_section "Step 1: 启动 backend (local mode)"

cd "$OCB_ROOT"

# 先停止可能存在的旧进程
./scripts/local_setup.sh stop backend 2>/dev/null || true

./scripts/local_setup.sh --local start backend 2>&1 | grep -E "INFO|WARN" | tail -5

# 等待 backend 就绪
echo "等待 backend 就绪..."
for i in $(seq 1 10); do
    if curl -s "$BASE_URL/api/health" | grep -q "ok"; then
        echo "backend 已就绪 (${i}s)"
        break
    fi
    if [ "$i" -eq 10 ]; then
        echo -e "${RED}ERROR: backend 启动超时${NC}"
        exit 1
    fi
    sleep 1
done

# ---- Step 2: 执行测试 ----
log_section "Step 2: 执行 CLAUDE.md CRUD 测试"

# --- TC-01: GET CLAUDE.md 返回 200 ---
resp=$(do_get "CLAUDE.md" "")
status=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')

assert_status "TC-01: GET CLAUDE.md 返回 200" 200 "$status"
assert_contains "TC-01: response.success == true" "$body" '"success":true'
assert_contains "TC-01: response.file_type == CLAUDE.md" "$body" '"file_type":"CLAUDE.md"'

# --- TC-02: PUT CLAUDE.md 写入内容 ---
CONTENT_V1='# E2E Test CLAUDE.md\n\nFirst version.'
resp=$(do_put "CLAUDE.md" "{\"content\": \"$CONTENT_V1\"}")
status=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')

assert_status "TC-02: PUT CLAUDE.md 返回 200" 200 "$status"
assert_contains "TC-02: write success" "$body" '"success":true'

# --- TC-03: GET 读回刚写入的内容 ---
resp=$(do_get "CLAUDE.md" "")
status=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')

assert_status "TC-03: GET 读回内容返回 200" 200 "$status"
assert_contains "TC-03: content 包含写入文本" "$body" "E2E Test CLAUDE.md"

# --- TC-04: PUT 覆盖更新 ---
CONTENT_V2='# Updated CLAUDE.md\n\nSecond version with changes.'
resp=$(do_put "CLAUDE.md" "{\"content\": \"$CONTENT_V2\"}")
status=$(echo "$resp" | tail -1)
assert_status "TC-04: PUT 覆盖更新返回 200" 200 "$status"

resp=$(do_get "CLAUDE.md" "")
body=$(echo "$resp" | sed '$d')
assert_contains "TC-04: 读回更新后内容" "$body" "Second version"
assert_not_contains "TC-04: 旧内容已被覆盖" "$body" "First version"

# --- TC-05: 非法文件类型返回 400 ---
resp=$(do_get "INVALID.md" "")
status=$(echo "$resp" | tail -1)
assert_status "TC-05: GET INVALID.md 返回 400" 400 "$status"

# --- TC-06: AGENTS.md 回归 (仍可访问) ---
resp=$(do_get "AGENTS.md" "")
status=$(echo "$resp" | tail -1)
assert_status "TC-06: GET AGENTS.md 回归正常 200" 200 "$status"

# --- TC-07: RULES.md 写入回归 ---
resp=$(do_put "RULES.md" '{"content": "# E2E Rules"}')
status=$(echo "$resp" | tail -1)
assert_status "TC-07: PUT RULES.md 回归正常 200" 200 "$status"

# --- TC-08: List 接口包含 CLAUDE.md ---
resp=$(curl -s -w "\n%{http_code}" \
    -H "x-user-id: $ENTITY_ID" -H "Cookie: ctoken=test" \
    "$BASE_URL/api/identity/$ENTITY_TYPE/$ENTITY_ID/bot/$BOT_ID")
status=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')

assert_status "TC-08: List 接口返回 200" 200 "$status"
assert_contains "TC-08: List 包含 CLAUDE.md" "$body" "CLAUDE.md"

# --- TC-09: engine_type query override ---
resp=$(do_get "CLAUDE.md" "?engine_type=openclaw")
status=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')

assert_status "TC-09: engine_type=openclaw override 返回 200" 200 "$status"
assert_contains "TC-09: 路径包含 openclaw" "$body" "openclaw"
assert_contains "TC-09: 路径包含 workspace" "$body" "workspace"

# --- TC-10: 路径验证 (默认 engine) ---
resp=$(do_get "CLAUDE.md" "")
body=$(echo "$resp" | sed '$d')
file_path=$(echo "$body" | python3 -c "import json,sys; print(json.load(sys.stdin).get('file_path',''))" 2>/dev/null || echo "")

if [ -n "$file_path" ]; then
    # 默认 bot 的 engine 决定路径中是否有 workspace
    if echo "$file_path" | grep -q "claude_code"; then
        assert_not_contains "TC-10: claude_code 路径无 workspace/" "$file_path" "/workspace/"
    else
        assert_contains "TC-10: openclaw 路径有 workspace/" "$file_path" "/workspace/"
    fi
else
    TOTAL=$((TOTAL + 1))
    FAILED=$((FAILED + 1))
    log_fail "TC-10: 无法解析 file_path"
fi

# ---- Step 3: 停止 backend ----
log_section "Step 3: 停止 backend"
cd "$OCB_ROOT"
./scripts/local_setup.sh stop backend 2>&1 | grep -E "INFO|WARN" | tail -3

# ---- 结果报告 ----
log_section "测试结果"
echo "总计: $TOTAL"
echo -e "通过: ${GREEN}$PASSED${NC}"
if [ "$FAILED" -gt 0 ]; then
    echo -e "失败: ${RED}$FAILED${NC}"
    echo ""
    echo -e "${RED}E2E 测试未通过！${NC}"
    exit 1
else
    echo -e "失败: 0"
    echo ""
    echo -e "${GREEN}E2E 测试全部通过！${NC}"
    exit 0
fi
