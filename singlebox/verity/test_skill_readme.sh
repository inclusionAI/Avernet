#!/bin/bash
# 模拟前端调用 get_skill_readme 全链路测试脚本
#
# 用法:
#   # 测试本地技能（"我的"tab）
#   ./scripts/test_skill_readme.sh local <skill_name>
#
#   # 测试市场技能（"全部"tab，走 SkillCenter）
#   ./scripts/test_skill_readme.sh market <skill_name>
#
#   # 直接调 SkillCenter file-content API（绕过后端，用于对比验证）
#   ./scripts/test_skill_readme.sh direct <skill_code>
#
# 示例:
#   ./scripts/test_skill_readme.sh local odps-sql-generator
#   ./scripts/test_skill_readme.sh market odps-sql-generator
#   ./scripts/test_skill_readme.sh direct odps-sql-generator
#
# 环境变量:
#   BACKEND_URL    后端地址 (默认 http://localhost:8888)
#   USER_ID        用户ID (默认 12345678)
#   CTOKEN         认证token (可选)

set -euo pipefail

# ── 配置 ──
BACKEND_URL="${BACKEND_URL:-http://localhost:8888}"
USER_ID="${USER_ID:-12345678}"
CTOKEN="${CTOKEN:-}"

# 认证方式：
#   本地模式（DATABASE_MODE=sqlite）: 通过 x-user-id header 或 staff_id cookie
#   dev/prod 模式: 需要从浏览器复制完整 cookie 字符串设置 AUTH_COOKIE 环境变量
#
# 用法示例：
#   # 本地模式（默认，无需额外配置）
#   ./scripts/test_skill_readme.sh local odps-sql-generator
#
#   # dev 模式（需要从浏览器 DevTools -> Application -> Cookies 复制）
#   export AUTH_COOKIE="ALIPAYJSESSIONID=xxx; ctoken=xxx; ..."
#   ./scripts/test_skill_readme.sh local odps-sql-generator
AUTH_COOKIE="${AUTH_COOKIE:-}"

# SkillCenter 直连配置（示例地址，真实地址请通过环境变量覆盖）
SC_BASE_URL="${SC_BASE_URL:-https://skillcenter.example.com}"
SC_SOURCE="${SC_SOURCE:-teamclaw}"
SC_APP_KEY="${SC_APP_KEY:-example-skillcenter-token}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

usage() {
    echo "用法: $0 <mode> <skill_name>"
    echo ""
    echo "Modes:"
    echo "  local   - 通过后端 /api/skills/{name}/readme（模拟前端调用）"
    echo "  market  - 同上，但预期走 SkillCenter 回退路径"
    echo "  direct  - 直接调 SkillCenter /api/v1/skills/{code}/file-content"
    echo ""
    echo "示例:"
    echo "  $0 local odps-sql-generator"
    echo "  $0 market some-market-only-skill"
    echo "  $0 direct odps-sql-generator"
    exit 1
}

if [[ $# -lt 2 ]]; then
    usage
fi

MODE="$1"
SKILL_NAME="$2"

echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Skill README 全链路测试${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo ""

case "$MODE" in
    local|market)
        # 通过后端 API（模拟前端 getSkillReadme 调用）
        URL="${BACKEND_URL}/api/skills/${SKILL_NAME}/readme?user_id=${USER_ID}"
        if [[ -n "$CTOKEN" ]]; then
            URL="${URL}&ctoken=${CTOKEN}"
        fi

        echo -e "${YELLOW}Mode:${NC}        $MODE"
        echo -e "${YELLOW}Skill Name:${NC}  $SKILL_NAME"
        echo -e "${YELLOW}URL:${NC}         $URL"
        echo ""
        echo -e "${CYAN}── 请求 ──${NC}"

        # 构建 curl 认证参数
        AUTH_ARGS=()
        if [[ -n "$AUTH_COOKIE" ]]; then
            # dev/prod 模式：使用浏览器 cookie
            AUTH_ARGS+=(-H "Cookie: ${AUTH_COOKIE}")
            echo -e "${YELLOW}Auth:${NC}        Browser cookie"
        else
            # 本地模式：使用 x-user-id header + staff_id cookie
            AUTH_ARGS+=(-H "x-user-id: ${USER_ID}" -b "staff_id=${USER_ID}")
            echo -e "${YELLOW}Auth:${NC}        Local mode (x-user-id: ${USER_ID})"
        fi

        HTTP_CODE=$(curl -s -o /tmp/skill_readme_response.json -w '%{http_code}' \
            -H "Content-Type: application/json" \
            "${AUTH_ARGS[@]}" \
            "$URL")

        echo -e "${YELLOW}HTTP Status:${NC} $HTTP_CODE"
        echo ""

        if [[ "$HTTP_CODE" == "200" ]]; then
            echo -e "${GREEN}✓ 成功${NC}"
            echo -e "${CYAN}── 响应 ──${NC}"
            # 显示 success 和 content 前200字符
            python3 -c "
import json, sys
with open('/tmp/skill_readme_response.json') as f:
    data = json.load(f)
print(f\"success: {data.get('success')}\")
content = data.get('data', {}).get('content', '')
print(f\"content length: {len(content)} chars\")
print(f\"content preview:\")
print(content[:500] if content else '(empty)')
"
        else
            echo -e "${RED}✗ 失败 (HTTP $HTTP_CODE)${NC}"
            echo -e "${CYAN}── 响应 ──${NC}"
            cat /tmp/skill_readme_response.json 2>/dev/null || echo "(no response body)"
        fi
        ;;

    direct)
        # 直连 SkillCenter（绕过后端）
        URL="${SC_BASE_URL}/api/v1/skills/${SKILL_NAME}/file-content?filePath=SKILL.md&source=${SC_SOURCE}&code=${SC_APP_KEY}"

        echo -e "${YELLOW}Mode:${NC}        direct (SkillCenter)"
        echo -e "${YELLOW}Skill Code:${NC} $SKILL_NAME"
        echo -e "${YELLOW}URL:${NC}         ${SC_BASE_URL}/api/v1/skills/${SKILL_NAME}/file-content"
        echo -e "${YELLOW}Params:${NC}      filePath=SKILL.md, source=${SC_SOURCE}, code=${SC_APP_KEY}"
        echo ""
        echo -e "${CYAN}── 请求 ──${NC}"

        HTTP_CODE=$(curl -s -o /tmp/skill_readme_direct.json -w '%{http_code}' "$URL")

        echo -e "${YELLOW}HTTP Status:${NC} $HTTP_CODE"
        echo ""

        if [[ "$HTTP_CODE" == "200" ]]; then
            echo -e "${GREEN}✓ 成功${NC}"
            echo -e "${CYAN}── 响应 ──${NC}"
            python3 -c "
import json
with open('/tmp/skill_readme_direct.json') as f:
    data = json.load(f)
print(f\"success: {data.get('success')}\")
d = data.get('data', {})
print(f\"path: {d.get('path')}\")
print(f\"name: {d.get('name')}\")
print(f\"type: {d.get('type')}\")
content = d.get('content', '')
print(f\"content length: {len(content)} chars\")
print(f\"content preview:\")
print(content[:500] if content else '(empty)')
"
        else
            echo -e "${RED}✗ 失败 (HTTP $HTTP_CODE)${NC}"
            echo -e "${CYAN}── 响应 ──${NC}"
            cat /tmp/skill_readme_direct.json 2>/dev/null || echo "(no response body)"
        fi
        ;;

    *)
        usage
        ;;
esac

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
