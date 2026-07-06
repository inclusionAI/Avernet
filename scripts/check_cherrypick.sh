#!/bin/bash
# check_cherrypick.sh - 检查 cherry-pick 是否丢失了关键代码
# 用法: ./scripts/check_cherrypick.sh [source_branch] [target_branch]
# 示例: ./scripts/check_cherrypick.sh origin/hotfix_protected HEAD

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 默认分支
SOURCE_BRANCH="${1:-origin/hotfix_protected}"
TARGET_BRANCH="${2:-HEAD}"
SERVER_FILE="src/bcs/crates/bcs/src/server.rs"

echo "=== Cherry-pick 完整性检查 ==="
echo "Source: $SOURCE_BRANCH"
echo "Target: $TARGET_BRANCH"
echo "File: $SERVER_FILE"
echo ""

# 临时文件
TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

git show "$SOURCE_BRANCH:$SERVER_FILE" 2>/dev/null > "$TMP_DIR/source.rs" || {
    echo -e "${RED}错误: 无法获取源分支文件${NC}"
    exit 1
}

git show "$TARGET_BRANCH:$SERVER_FILE" 2>/dev/null > "$TMP_DIR/target.rs" || {
    echo -e "${RED}错误: 无法获取目标分支文件${NC}"
    exit 1
}

# 检查函数
CHECK_PASS=0
CHECK_FAIL=0

check_pattern() {
    local name="$1"
    local pattern="$2"
    local source_count=$(grep -c "$pattern" "$TMP_DIR/source.rs" 2>/dev/null | tr -d '\n' || echo 0)
    local target_count=$(grep -c "$pattern" "$TMP_DIR/target.rs" 2>/dev/null | tr -d '\n' || echo 0)

    source_count=$(echo "$source_count" | tr -d '\n')
    target_count=$(echo "$target_count" | tr -d '\n')

    if [ "$source_count" = "$target_count" ]; then
        printf "${GREEN}✓${NC} %s: 源=%s, 目标=%s\n" "$name" "$source_count" "$target_count"
        CHECK_PASS=$((CHECK_PASS + 1))
    else
        printf "${RED}✗${NC} %s: 源=%s, 目标=%s (不匹配!)\n" "$name" "$source_count" "$target_count"
        CHECK_FAIL=$((CHECK_FAIL + 1))
    fi
}

check_function_signature() {
    local func="$1"
    local source_sig=$(grep -A1 "$func" "$TMP_DIR/source.rs" 2>/dev/null | head -2 | tr '\n' ' ')
    local target_sig=$(grep -A1 "$func" "$TMP_DIR/target.rs" 2>/dev/null | head -2 | tr '\n' ' ')

    # 简化比较（只比较参数列表）
    local source_params=$(echo "$source_sig" | grep -oE '\([^)]*\)' | head -1)
    local target_params=$(echo "$target_sig" | grep -oE '\([^)]*\)' | head -1)

    if [ "$source_params" = "$target_params" ]; then
        printf "${GREEN}✓${NC} %s: 签名一致\n" "$func"
        CHECK_PASS=$((CHECK_PASS + 1))
    else
        printf "${RED}✗${NC} %s: 签名不一致!\n" "$func"
        printf "  源: %s\n" "$source_params"
        printf "  目标: %s\n" "$target_params"
        CHECK_FAIL=$((CHECK_FAIL + 1))
    fi
}

echo "=== 函数签名检查 ==="
check_function_signature "async fn delete_session"
check_function_signature "async fn onboard_bot"
check_function_signature "async fn admin_onboard_bot"
check_function_signature "async fn add_session_member"

echo ""
echo "=== 关键代码模式检查 (对比 dev 分支) ==="
# 获取 dev 分支作为参考
git show origin/dev:$SERVER_FILE 2>/dev/null > "$TMP_DIR/dev.rs" || true

if [ -f "$TMP_DIR/dev.rs" ]; then
    # 检查 dev 有但 target 可能没有的功能
    dev_count=$(grep -c "check_bot_ownership" "$TMP_DIR/dev.rs" 2>/dev/null | tr -d '\n' || echo 0)
    target_count=$(grep -c "check_bot_ownership" "$TMP_DIR/target.rs" 2>/dev/null | tr -d '\n' || echo 0)
    if [ "$dev_count" -gt 0 ] && [ "$target_count" -lt "$dev_count" ]; then
        printf "${YELLOW}!${NC} check_bot_ownership: dev=%s, target=%s (可能丢失 dev 功能)\n" "$dev_count" "$target_count"
        CHECK_FAIL=$((CHECK_FAIL + 1))
    else
        printf "${GREEN}✓${NC} check_bot_ownership: %s 处\n" "$target_count"
        CHECK_PASS=$((CHECK_PASS + 1))
    fi
fi

echo ""
echo "=== 错误传播模式检查 ==="
# 检查 save_to_storage 的 ? 使用
if grep -q "save_to_storage.*await" "$TMP_DIR/target.rs"; then
    target_ok=$(grep -c "save_to_storage.*map_err" "$TMP_DIR/target.rs" | tr -d '\n' || echo 0)
    target_ok=$(echo "$target_ok" | tr -d '\n')
    if [ "$target_ok" -ge 2 ]; then
        printf "${GREEN}✓${NC} save_to_storage 错误传播: %s 处正确\n" "$target_ok"
        CHECK_PASS=$((CHECK_PASS + 1))
    else
        printf "${YELLOW}!${NC} save_to_storage 可能缺少 ? 错误传播 (仅 %s 处)\n" "$target_ok"
        CHECK_PASS=$((CHECK_PASS + 1))
    fi
fi

echo ""
echo "=== 总结 ==="
if [ "$CHECK_FAIL" -eq 0 ]; then
    printf "${GREEN}所有检查通过! (%s 项)${NC}\n" "$CHECK_PASS"
    exit 0
else
    printf "${RED}发现 %s 个问题，%s 项通过${NC}\n" "$CHECK_FAIL" "$CHECK_PASS"
    echo ""
    echo "常见修复方法:"
    echo "1. 手动对比差异: git diff $SOURCE_BRANCH..$TARGET_BRANCH -- $SERVER_FILE"
    echo "2. VSCode: 打开 server.rs, 使用 GitLens 查看行级 blame"
    echo "3. 冲突解决时，确保合并双方改动而非选择一方"
    exit 1
fi
