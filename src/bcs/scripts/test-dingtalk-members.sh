#!/bin/bash
# Test DingTalk Scene Group Member Management API
#
# 测试钉钉场景群成员管理功能：
# 1. 获取 access token
# 2. 查看群信息（获取当前成员）
# 3. 尝试添加成员
# 4. 验证结果

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ============================================================================
# Configuration (从 bcs.toml 读取)
# ============================================================================

CONFIG_FILE="${MOLTIS_BCS_CONFIG:-$HOME/.config/bcs/bcs.toml}"

if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}✗ 配置文件不存在: $CONFIG_FILE${NC}"
    exit 1
fi

echo -e "${CYAN}使用配置文件: $CONFIG_FILE${NC}"
echo ""

# 简单解析 TOML 配置（提取 dingtalk 段）
parse_toml_value() {
    local key="$1"
    local file="$2"
    # 匹配 key = "value" 或 key = value 格式
    grep -E "^\s*$key\s*=" "$file" | head -1 | sed -E 's/.*=\s*"?([^"]*)"?/\1/' | tr -d '"' | tr -d ' '
}

CLIENT_ID=$(parse_toml_value "client_id" "$CONFIG_FILE")
CLIENT_SECRET=$(parse_toml_value "client_secret" "$CONFIG_FILE")
ACCESS_KEY_ID=$(parse_toml_value "access_key_id" "$CONFIG_FILE")
ACCESS_KEY_SECRET=$(parse_toml_value "access_key_secret" "$CONFIG_FILE")
EMPLOYEE_ID=$(parse_toml_value "employee_id" "$CONFIG_FILE")

# 测试用的群 ID 和用户 ID
GATEWAY_BASE_URL="${DINGTALK_GATEWAY_BASE_URL:-https://gateway.example.com/antdingopen}"
CONVERSATION_ID="${DINGTALK_CONVERSATION_ID:-replace-with-open-conversation-id}"
TEST_USER_ID="${DINGTALK_TEST_USER_ID:-11111111}"

echo ""
echo "配置信息:"
echo "  CLIENT_ID: ${CLIENT_ID:0:10}..."
echo "  ACCESS_KEY_ID: ${ACCESS_KEY_ID}"
echo "  EMPLOYEE_ID: ${EMPLOYEE_ID}"
echo "  CONVERSATION_ID: ${CONVERSATION_ID}"
echo "  TEST_USER_ID: ${TEST_USER_ID}"
echo ""

# ============================================================================
# Step 1: Get Access Token
# ============================================================================

echo -e "${CYAN}=== Step 1: 获取 Access Token ===${NC}"

TOKEN_RESPONSE=$(curl -s -X POST "https://api.dingtalk.com/v1.0/oauth2/accessToken" \
    -H "Content-Type: application/json" \
    -d "{\"appKey\":\"$CLIENT_ID\",\"appSecret\":\"$CLIENT_SECRET\"}")

echo "Token Response: $TOKEN_RESPONSE"

ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | grep -oE '"accessToken":"[^"]*"' | sed 's/"accessToken":"//;s/"//')

if [ -z "$ACCESS_TOKEN" ]; then
    echo -e "${RED}✗ 获取 Access Token 失败${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Access Token: ${ACCESS_TOKEN:0:20}...${NC}"
echo ""

# ============================================================================
# Step 2: Get Group Info (via Alipay Gateway - 复用现有方法)
# ============================================================================

echo -e "${CYAN}=== Step 2: 获取群信息 (Alipay Gateway) ===${NC}"

TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
REQUEST_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')

# Build request body
CONTEXT="{\"accountContext\":{\"accountId\":\"$EMPLOYEE_ID\"},\"ampContext\":{\"accessKeyId\":\"$ACCESS_KEY_ID\",\"requestId\":\"$REQUEST_ID\"},\"accessKeyId\":\"$ACCESS_KEY_ID\",\"multiCorpContext\":{\"orgId\":\"\"}}"
REQUEST="{\"openConversationId\":\"$CONVERSATION_ID\"}"
REQUEST_BODY="[$CONTEXT,$REQUEST]"

# Calculate signature
SIGN_STR="${REQUEST_BODY}__${ACCESS_KEY_SECRET}__${TIME}"
SIGNATURE=$(echo -n "$SIGN_STR" | shasum -a 256 | awk '{print $1}')

echo "Request ID: $REQUEST_ID"
echo "Time: $TIME"

GROUP_RESPONSE=$(curl -s -X POST "$GATEWAY_BASE_URL/com.alipay.antdingopen.facade.openapi.vendor.dingtalk.v1.im.ChatService/getScenegroup" \
    -H "Content-Type: application/json" \
    -H "x-webgw-appId: antdingopensdk" \
    -H "x-webgw-version: 2.0" \
    -H "X-Webgw-Sofa-Baggage-SIGN: $SIGNATURE" \
    -H "X-Webgw-Sofa-Baggage-SIGNTIME: $TIME" \
    -H "X-Webgw-Sofa-Baggage-Sdkversion: 1.0.0" \
    -H "X-Webgw-Sofa-Baggage-Apitype: aliding" \
    -d "$REQUEST_BODY")

echo "Group Response: $GROUP_RESPONSE" | head -c 500
echo ""
echo ""

# ============================================================================
# Step 3: Try API v1.0 - Add Members (当前实现)
# ============================================================================

echo -e "${CYAN}=== Step 3: 尝试 API v1.0 添加成员 ===${NC}"
echo "Endpoint: https://api.dingtalk.com/v1.0/im/chat/scenes/groups/members/add"

ADD_RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "https://api.dingtalk.com/v1.0/im/chat/scenes/groups/members/add" \
    -H "Content-Type: application/json" \
    -H "x-acs-dingtalk-access-token: $ACCESS_TOKEN" \
    -d "{\"openConversationId\":\"$CONVERSATION_ID\",\"userIds\":[\"$TEST_USER_ID\"]}")

HTTP_STATUS=$(echo "$ADD_RESPONSE" | grep "HTTP_STATUS:" | sed 's/HTTP_STATUS://')
ADD_BODY=$(echo "$ADD_RESPONSE" | sed '/HTTP_STATUS:/d')

echo "HTTP Status: $HTTP_STATUS"
echo "Response Body: $ADD_BODY"

if [ "$HTTP_STATUS" = "200" ]; then
    echo -e "${GREEN}✓ API v1.0 添加成员成功${NC}"
else
    echo -e "${YELLOW}⚠ API v1.0 添加成员失败${NC}"
fi
echo ""

# ============================================================================
# Step 4: Try API v1.0 with chatId instead of openConversationId
# ============================================================================

echo -e "${CYAN}=== Step 4: 尝试使用 chatId 字段名 ===${NC}"
echo "Endpoint: https://api.dingtalk.com/v1.0/im/chat/scenes/groups/members/add (using chatId)"

ADD_RESPONSE2=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "https://api.dingtalk.com/v1.0/im/chat/scenes/groups/members/add" \
    -H "Content-Type: application/json" \
    -H "x-acs-dingtalk-access-token: $ACCESS_TOKEN" \
    -d "{\"chatId\":\"$CONVERSATION_ID\",\"userIds\":[\"$TEST_USER_ID\"]}")

HTTP_STATUS2=$(echo "$ADD_RESPONSE2" | grep "HTTP_STATUS:" | sed 's/HTTP_STATUS://')
ADD_BODY2=$(echo "$ADD_RESPONSE2" | sed '/HTTP_STATUS:/d')

echo "HTTP Status: $HTTP_STATUS2"
echo "Response Body: $ADD_BODY2"

if [ "$HTTP_STATUS2" = "200" ]; then
    echo -e "${GREEN}✓ 使用 chatId 添加成员成功${NC}"
else
    echo -e "${YELLOW}⚠ 使用 chatId 添加成员失败${NC}"
fi
echo ""

# ============================================================================
# Step 5: Try OAPI endpoint
# ============================================================================

echo -e "${CYAN}=== Step 5: 尝试 OAPI 端点 ===${NC}"
echo "Endpoint: https://oapi.dingtalk.com/topapi/im/chat/sceneGroup/member/add"

ADD_RESPONSE3=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "https://oapi.dingtalk.com/topapi/im/chat/sceneGroup/member/add?access_token=$ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"chat_id\":\"$CONVERSATION_ID\",\"user_ids\":[\"$TEST_USER_ID\"]}")

HTTP_STATUS3=$(echo "$ADD_RESPONSE3" | grep "HTTP_STATUS:" | sed 's/HTTP_STATUS://')
ADD_BODY3=$(echo "$ADD_RESPONSE3" | sed '/HTTP_STATUS:/d')

echo "HTTP Status: $HTTP_STATUS3"
echo "Response Body: $ADD_BODY3"

if [ "$HTTP_STATUS3" = "200" ]; then
    echo -e "${GREEN}✓ OAPI 端点成功${NC}"
    # 检查 errcode
    ERRCODE=$(echo "$ADD_BODY3" | grep -oE '"errcode":[0-9]+' | sed 's/"errcode"://')
    if [ "$ERRCODE" = "0" ]; then
        echo -e "${GREEN}✓ 操作成功 (errcode=0)${NC}"
    else
        echo -e "${YELLOW}⚠ 返回了 errcode=$ERRCODE${NC}"
    fi
else
    echo -e "${YELLOW}⚠ OAPI 端点失败${NC}"
fi
echo ""

# ============================================================================
# Step 6: Try more possible endpoints
# ============================================================================

echo -e "${CYAN}=== Step 6: 尝试更多可能的端点 ===${NC}"

# 6a: POST /topapi/im/chat/member/add
echo "--- 尝试 /topapi/im/chat/member/add ---"
ADD_RESPONSE4=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "https://oapi.dingtalk.com/topapi/im/chat/member/add?access_token=$ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"chatid\":\"$CONVERSATION_ID\",\"useridlist\":[\"$TEST_USER_ID\"]}")
HTTP_STATUS4=$(echo "$ADD_RESPONSE4" | grep "HTTP_STATUS:" | sed 's/HTTP_STATUS://')
ADD_BODY4=$(echo "$ADD_RESPONSE4" | sed '/HTTP_STATUS:/d')
echo "HTTP Status: $HTTP_STATUS4"
echo "Response: $ADD_BODY4"
echo ""

# 6b: POST /topapi/im/chat/create (check if we can create with members)
echo "--- 尝试 /v1.0/im/chat/groups/members/add ---"
ADD_RESPONSE5=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "https://api.dingtalk.com/v1.0/im/chat/groups/members/add" \
    -H "Content-Type: application/json" \
    -H "x-acs-dingtalk-access-token: $ACCESS_TOKEN" \
    -d "{\"openConversationId\":\"$CONVERSATION_ID\",\"userIds\":[\"$TEST_USER_ID\"]}")
HTTP_STATUS5=$(echo "$ADD_RESPONSE5" | grep "HTTP_STATUS:" | sed 's/HTTP_STATUS://')
ADD_BODY5=$(echo "$ADD_RESPONSE5" | sed '/HTTP_STATUS:/d')
echo "HTTP Status: $HTTP_STATUS5"
echo "Response: $ADD_BODY5"
echo ""

# 6c: PUT /v1.0/im/chat/groups/{chatId}/members
echo "--- 尝试 PUT /v1.0/im/chat/groups/{chatId}/members ---"
ADD_RESPONSE6=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X PUT "https://api.dingtalk.com/v1.0/im/chat/groups/$CONVERSATION_ID/members" \
    -H "Content-Type: application/json" \
    -H "x-acs-dingtalk-access-token: $ACCESS_TOKEN" \
    -d "{\"userIds\":[\"$TEST_USER_ID\"]}")
HTTP_STATUS6=$(echo "$ADD_RESPONSE6" | grep "HTTP_STATUS:" | sed 's/HTTP_STATUS://')
ADD_BODY6=$(echo "$ADD_RESPONSE6" | sed '/HTTP_STATUS:/d')
echo "HTTP Status: $HTTP_STATUS6"
echo "Response: $ADD_BODY6"
echo ""

# 6d: POST /v1.0/im/chat/groups/members/add (with extension)
echo "--- 尝试 /topapi/v2/im/chat/member/add ---"
ADD_RESPONSE7=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "https://oapi.dingtalk.com/topapi/v2/im/chat/member/add?access_token=$ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"chat_id\":\"$CONVERSATION_ID\",\"user_ids\":[\"$TEST_USER_ID\"]}")
HTTP_STATUS7=$(echo "$ADD_RESPONSE7" | grep "HTTP_STATUS:" | sed 's/HTTP_STATUS://')
ADD_BODY7=$(echo "$ADD_RESPONSE7" | sed '/HTTP_STATUS:/d')
echo "HTTP Status: $HTTP_STATUS7"
echo "Response: $ADD_BODY7"
echo ""

# ============================================================================
# Summary
# ============================================================================

echo -e "${CYAN}=== 测试总结 ===${NC}"
echo ""
echo "| 端点 | HTTP状态 | 说明 |"
echo "|------|----------|------|"
echo "| API v1.0 scenes/groups/members/add | $HTTP_STATUS | 404 = 不存在 |"
echo "| OAPI sceneGroup/member/add | $HTTP_STATUS3 | 方法名错误 |"
echo "| OAPI /topapi/im/chat/member/add | $HTTP_STATUS4 | - |"
echo "| API v1.0 groups/members/add | $HTTP_STATUS5 | - |"
echo "| API v1.0 PUT groups/members | $HTTP_STATUS6 | - |"
echo "| OAPI v2 /im/chat/member/add | $HTTP_STATUS7 | - |"
echo ""
echo -e "${YELLOW}权限检查:${NC}"
echo "1. 需要申请权限: qyapi_chat_read (读取群信息)"
echo "2. 需要确认场景群成员管理的正确 API"
echo ""
echo "查看钉钉开放平台文档:"
echo "https://open.dingtalk.com/document/orgapp/add-scene-group-members"
