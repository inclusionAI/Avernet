#!/bin/sh
set -eu

# FRONTEND_API_PROFILE 选择 API 前置形态与运行时可选 Backend 开关（docs/deployment.md
# 「Deployment profiles」）；本脚本以 10-* 前缀安装，必须先于官方
# 20-envsubst-on-templates.sh 运行，bcs-only 模板才能走标准 ${BCS_UPSTREAM} 变量替换。
# - full（缺省）：全栈多服务部署；保留全部必填上游变量与镜像内 full Nginx 模板。
# - bcs-only：好友发现 BCS-only 部署；仅要求非空 BCS_UPSTREAM，把独立只读配置目录里的
#   bcs-only 模板拷到 /etc/nginx/templates 交由官方 Nginx entrypoint 完成变量替换。
case "${FRONTEND_API_PROFILE:-full}" in
  full)
    required_vars="BCS_AUTH_UPSTREAM TASK_ENGINE_UPSTREAM TEAMCLAW_GATEWAY_UPSTREAM TEAMCLAW_ADMIN_UPSTREAM PRIVATE_CHAT_MANAGEMENT_UPSTREAM PRIVATE_CHAT_SESSION_UPSTREAM CLAWWEB_UPSTREAM AIXCORE_UPSTREAM BCS_ENDPOINT_PRE BCS_ENDPOINT_PROD"
    public_bot_backend_enabled="true"
    ;;
  bcs-only)
    required_vars="BCS_UPSTREAM"
    public_bot_backend_enabled="false"
    cp /etc/nginx/template-profiles/default.bcs-only.conf.template /etc/nginx/templates/default.conf.template
    # BCS endpoint 运行时键保留为可选输入：未设置时输出空串，前端回退编译期缺省。
    BCS_ENDPOINT_PRE="${BCS_ENDPOINT_PRE:-}"
    BCS_ENDPOINT_PROD="${BCS_ENDPOINT_PROD:-}"
    ;;
  *)
    echo "Unknown FRONTEND_API_PROFILE: ${FRONTEND_API_PROFILE}" >&2
    exit 1
    ;;
esac

for name in $required_vars; do
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "Missing required deployment variable: $name" >&2
    exit 1
  fi
done

escape_js() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

BCS_ENDPOINT_PRE_JS=$(escape_js "$BCS_ENDPOINT_PRE")
BCS_ENDPOINT_PROD_JS=$(escape_js "$BCS_ENDPOINT_PROD")

QWENWORK_PROVIDER_SLUG_JS=$(escape_js "${QWENWORK_PROVIDER_SLUG:-}")
DSH_REGISTRATION_GUIDE_URL_JS=$(escape_js "${DSH_REGISTRATION_GUIDE_URL:-}")

# PUBLIC_BOT_BACKEND_ENABLED 必须是 JS 布尔量（true/false），不能写字符串 'false'；
# 前端能力读取口见 src/capabilities/defaultCapabilities.ts。
cat > /usr/share/nginx/html/runtime-config.js <<EOF
window.AVERNET_RUNTIME_CONFIG = {
  DSH_REGISTRATION_GUIDE_URL: "$DSH_REGISTRATION_GUIDE_URL_JS",
  QWENWORK_PROVIDER_SLUG: "$QWENWORK_PROVIDER_SLUG_JS",
  BCS_ENDPOINT_PRE: "$BCS_ENDPOINT_PRE_JS",
  BCS_ENDPOINT_PROD: "$BCS_ENDPOINT_PROD_JS",
  PUBLIC_BOT_BACKEND_ENABLED: $public_bot_backend_enabled,
};
EOF
