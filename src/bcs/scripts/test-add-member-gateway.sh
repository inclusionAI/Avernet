#!/bin/bash
# Test Alipay Gateway addScenegroupMember API
#
# 测试 Alipay Gateway 添加成员 API

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
CONFIG_FILE="${MOLTIS_BCS_CONFIG:-$HOME/.config/bcs/bcs.toml}"

# Parse TOML
parse_toml_value() {
    local key="$1"
    local file="$2"
    grep -E "^\s*$key\s*=" "$file" | head -1 | sed -E 's/.*=\s*"?([^"]*)"?/\1/' | tr -d '"' | tr -d ' '
}

ACCESS_KEY_ID=$(parse_toml_value "access_key_id" "$CONFIG_FILE")
ACCESS_KEY_SECRET=$(parse_toml_value "access_key_secret" "$CONFIG_FILE")
EMPLOYEE_ID=$(parse_toml_value "employee_id" "$CONFIG_FILE")

GATEWAY_BASE_URL="${DINGTALK_GATEWAY_BASE_URL:-https://gateway.example.com/antdingopen}"
CONVERSATION_ID="${DINGTALK_CONVERSATION_ID:-replace-with-open-conversation-id}"
TEST_USER_ID="${DINGTALK_TEST_USER_ID:-11111111}"

echo -e "${CYAN}=== 测试 Alipay Gateway 添加成员 API ===${NC}"
echo ""
echo "配置:"
echo "  ACCESS_KEY_ID: $ACCESS_KEY_ID"
echo "  EMPLOYEE_ID: $EMPLOYEE_ID"
echo "  CONVERSATION_ID: $CONVERSATION_ID"
echo "  TEST_USER_ID: $TEST_USER_ID"
echo ""

# Generate timestamp and request ID
TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
REQUEST_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')

echo "时间: $TIME"
echo "请求ID: $REQUEST_ID"
echo ""

# Test 1: addScenegroupMember (camelCase)
echo -e "${CYAN}--- Test 1: addScenegroupMember with userIds ---${NC}"

CONTEXT="{\"accountContext\":{\"accountId\":\"$EMPLOYEE_ID\"},\"ampContext\":{\"accessKeyId\":\"$ACCESS_KEY_ID\",\"requestId\":\"$REQUEST_ID\"},\"accessKeyId\":\"$ACCESS_KEY_ID\",\"multiCorpContext\":{\"orgId\":\"\"}}"
REQUEST="{\"openConversationId\":\"$CONVERSATION_ID\",\"userIds\":[\"$TEST_USER_ID\"]}"
REQUEST_BODY="[$CONTEXT,$REQUEST]"

echo "Request body: $REQUEST_BODY"

SIGN_STR="${REQUEST_BODY}__${ACCESS_KEY_SECRET}__${TIME}"
SIGNATURE=$(echo -n "$SIGN_STR" | shasum -a 256 | awk '{print $1}')

RESPONSE=$(curl -s -X POST "$GATEWAY_BASE_URL/com.alipay.antdingopen.facade.openapi.vendor.dingtalk.v1.im.ChatService/addScenegroupMember" \
    -H "Content-Type: application/json" \
    -H "x-webgw-appId: antdingopensdk" \
    -H "x-webgw-version: 2.0" \
    -H "X-Webgw-Sofa-Baggage-SIGN: $SIGNATURE" \
    -H "X-Webgw-Sofa-Baggage-SIGNTIME: $TIME" \
    -H "X-Webgw-Sofa-Baggage-Sdkversion: 1.0.0" \
    -H "X-Webgw-Sofa-Baggage-Apitype: aliding" \
    -d "$REQUEST_BODY")

echo "Response: $RESPONSE"
echo ""

# Test 2: Try with memberUserIds field
echo -e "${CYAN}--- Test 2: addScenegroupMember with memberUserIds ---${NC}"

REQUEST_ID2=$(uuidgen | tr '[:upper:]' '[:lower:]')
TIME2=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

CONTEXT2="{\"accountContext\":{\"accountId\":\"$EMPLOYEE_ID\"},\"ampContext\":{\"accessKeyId\":\"$ACCESS_KEY_ID\",\"requestId\":\"$REQUEST_ID2\"},\"accessKeyId\":\"$ACCESS_KEY_ID\",\"multiCorpContext\":{\"orgId\":\"\"}}"
REQUEST2="{\"openConversationId\":\"$CONVERSATION_ID\",\"memberUserIds\":[\"$TEST_USER_ID\"]}"
REQUEST_BODY2="[$CONTEXT2,$REQUEST2]"

SIGN_STR2="${REQUEST_BODY2}__${ACCESS_KEY_SECRET}__${TIME2}"
SIGNATURE2=$(echo -n "$SIGN_STR2" | shasum -a 256 | awk '{print $1}')

RESPONSE2=$(curl -s -X POST "$GATEWAY_BASE_URL/com.alipay.antdingopen.facade.openapi.vendor.dingtalk.v1.im.ChatService/addScenegroupMember" \
    -H "Content-Type: application/json" \
    -H "x-webgw-appId: antdingopensdk" \
    -H "x-webgw-version: 2.0" \
    -H "X-Webgw-Sofa-Baggage-SIGN: $SIGNATURE2" \
    -H "X-Webgw-Sofa-Baggage-SIGNTIME: $TIME2" \
    -H "X-Webgw-Sofa-Baggage-Sdkversion: 1.0.0" \
    -H "X-Webgw-Sofa-Baggage-Apitype: aliding" \
    -d "$REQUEST_BODY2")

echo "Response: $RESPONSE2"
echo ""

# Test 5: Try with just memberId (singular)
echo -e "${CYAN}--- Test 5: addScenegroupMember with memberId ---${NC}"

REQUEST_ID5=$(uuidgen | tr '[:upper:]' '[:lower:]')
TIME5=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

CONTEXT5="{\"accountContext\":{\"accountId\":\"$EMPLOYEE_ID\"},\"ampContext\":{\"accessKeyId\":\"$ACCESS_KEY_ID\",\"requestId\":\"$REQUEST_ID5\"},\"accessKeyId\":\"$ACCESS_KEY_ID\",\"multiCorpContext\":{\"orgId\":\"\"}}"
REQUEST5="{\"openConversationId\":\"$CONVERSATION_ID\",\"memberId\":\"$TEST_USER_ID\"}"
REQUEST_BODY5="[$CONTEXT5,$REQUEST5]"

SIGN_STR5="${REQUEST_BODY5}__${ACCESS_KEY_SECRET}__${TIME5}"
SIGNATURE5=$(echo -n "$SIGN_STR5" | shasum -a 256 | awk '{print $1}')

RESPONSE5=$(curl -s -X POST "$GATEWAY_BASE_URL/com.alipay.antdingopen.facade.openapi.vendor.dingtalk.v1.im.ChatService/addScenegroupMember" \
    -H "Content-Type: application/json" \
    -H "x-webgw-appId: antdingopensdk" \
    -H "x-webgw-version: 2.0" \
    -H "X-Webgw-Sofa-Baggage-SIGN: $SIGNATURE5" \
    -H "X-Webgw-Sofa-Baggage-SIGNTIME: $TIME5" \
    -H "X-Webgw-Sofa-Baggage-Sdkversion: 1.0.0" \
    -H "X-Webgw-Sofa-Baggage-Apitype: aliding" \
    -d "$REQUEST_BODY5")

echo "Response: $RESPONSE5"
echo ""

# Test 6: Try with memberUserIds and chatId instead of openConversationId
echo -e "${CYAN}--- Test 6: with chatId instead of openConversationId ---${NC}"

REQUEST_ID6=$(uuidgen | tr '[:upper:]' '[:lower:]')
TIME6=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

CONTEXT6="{\"accountContext\":{\"accountId\":\"$EMPLOYEE_ID\"},\"ampContext\":{\"accessKeyId\":\"$ACCESS_KEY_ID\",\"requestId\":\"$REQUEST_ID6\"},\"accessKeyId\":\"$ACCESS_KEY_ID\",\"multiCorpContext\":{\"orgId\":\"\"}}"
REQUEST6="{\"chatId\":\"$CONVERSATION_ID\",\"memberUserIds\":[\"$TEST_USER_ID\"]}"
REQUEST_BODY6="[$CONTEXT6,$REQUEST6]"

SIGN_STR6="${REQUEST_BODY6}__${ACCESS_KEY_SECRET}__${TIME6}"
SIGNATURE6=$(echo -n "$SIGN_STR6" | shasum -a 256 | awk '{print $1}')

RESPONSE6=$(curl -s -X POST "$GATEWAY_BASE_URL/com.alipay.antdingopen.facade.openapi.vendor.dingtalk.v1.im.ChatService/addScenegroupMember" \
    -H "Content-Type: application/json" \
    -H "x-webgw-appId: antdingopensdk" \
    -H "x-webgw-version: 2.0" \
    -H "X-Webgw-Sofa-Baggage-SIGN: $SIGNATURE6" \
    -H "X-Webgw-Sofa-Baggage-SIGNTIME: $TIME6" \
    -H "X-Webgw-Sofa-Baggage-Sdkversion: 1.0.0" \
    -H "X-Webgw-Sofa-Baggage-Apitype: aliding" \
    -d "$REQUEST_BODY6")

echo "Response: $RESPONSE6"
echo ""

echo -e "${CYAN}=== 测试完成 ===${NC}"
