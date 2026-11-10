#!/bin/sh
set -eu

# FRONTEND_API_PROFILE 选择 API 前置形态与运行时可选 Backend 开关（docs/deployment.md
# 「Deployment profiles」）；本脚本以 10-* 前缀安装，必须先于官方
# 20-envsubst-on-templates.sh 运行，bcs-only 才能在官方替换前换入 profile 模板并
# 写入归一化后的 BCS_UPSTREAM。
# - full（缺省）：全栈多服务部署；保留全部必填上游变量与镜像内 full Nginx 模板。
# - bcs-only：好友发现 BCS-only 部署；仅要求非空 BCS_UPSTREAM，把独立只读配置目录里的
#   bcs-only 模板拷到 /etc/nginx/templates，写入归一后的 BCS_UPSTREAM，其余 ${...}
#   仍交由官方 Nginx entrypoint 完成替换。
case "${FRONTEND_API_PROFILE:-full}" in
  full)
    required_vars="BCS_AUTH_UPSTREAM TASK_ENGINE_UPSTREAM TEAMCLAW_GATEWAY_UPSTREAM TEAMCLAW_ADMIN_UPSTREAM PRIVATE_CHAT_MANAGEMENT_UPSTREAM PRIVATE_CHAT_SESSION_UPSTREAM CLAWWEB_UPSTREAM AIXCORE_UPSTREAM BCS_ENDPOINT_PRE BCS_ENDPOINT_PROD"
    # 缺省 true（逐字保持既有行为）；外部只允许显式 true/false，避免把编译期开关
    # 在容器内静默写坏成垃圾值。
    case "${PUBLIC_BOT_BACKEND_ENABLED:-true}" in
      true | false) public_bot_backend_enabled="${PUBLIC_BOT_BACKEND_ENABLED:-true}" ;;
      *)
        echo "PUBLIC_BOT_BACKEND_ENABLED must be true or false: ${PUBLIC_BOT_BACKEND_ENABLED}" >&2
        exit 1
        ;;
    esac
    ;;
  bcs-only)
    required_vars="BCS_UPSTREAM"
    # bcs-only 不部署 Backend/Gateway：开关固定 false，不接受外部覆盖。
    public_bot_backend_enabled="false"
    bcs_only_template=/etc/nginx/template-profiles/default.bcs-only.conf.template
    active_nginx_template=/etc/nginx/templates/default.conf.template
    cp "$bcs_only_template" "$active_nginx_template"
    # BCS_UPSTREAM 归一化与校验：前缀 location 的 proxy_pass 带 URI 会替换匹配路径、
    # 正则 location 带 URI 会让 nginx 拒绝启动，故只接受无路径的 http(s) origin。
    # 空值不在此报错，留给下面的必填变量校验输出统一提示。
    if [ -n "${BCS_UPSTREAM:-}" ]; then
      bcs_upstream="$BCS_UPSTREAM"
      while [ "${bcs_upstream%/}" != "$bcs_upstream" ]; do
        bcs_upstream="${bcs_upstream%/}"
      done
      case "$bcs_upstream" in
        http://* | https://*) ;;
        *)
          echo "BCS_UPSTREAM must start with http:// or https://: $BCS_UPSTREAM" >&2
          exit 1
          ;;
      esac
      case "${bcs_upstream#*://}" in
        *[/?#]*)
          echo "BCS_UPSTREAM must be an origin without a path: $BCS_UPSTREAM" >&2
          exit 1
          ;;
      esac
      # 归一值必须写进模板本体：官方 20-envsubst-on-templates.sh 在自己的进程里
      # 读取原始环境变量，本脚本内的赋值不会传播给后续脚本。
      escaped_bcs_upstream=$(printf '%s' "$bcs_upstream" | sed 's/[\\&|]/\\&/g')
      sed "s|\${BCS_UPSTREAM};|${escaped_bcs_upstream};|g" "$active_nginx_template" \
        > "${active_nginx_template}.normalized"
      mv "${active_nginx_template}.normalized" "$active_nginx_template"
    fi
    # BCS endpoint 运行时键保留为可选输入：未设置时输出空串，前端回退浏览器同源
    # （src/services/workspace/botRegistrationService.ts 的 resolveBcsEndpoint）。
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
