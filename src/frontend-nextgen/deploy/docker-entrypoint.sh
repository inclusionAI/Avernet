#!/bin/sh
set -eu

required_vars="BCS_AUTH_UPSTREAM TASK_ENGINE_UPSTREAM TEAMCLAW_GATEWAY_UPSTREAM TEAMCLAW_ADMIN_UPSTREAM PRIVATE_CHAT_MANAGEMENT_UPSTREAM PRIVATE_CHAT_SESSION_UPSTREAM CLAWWEB_UPSTREAM AIXCORE_UPSTREAM BCS_ENDPOINT_PRE BCS_ENDPOINT_PROD"
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

cat > /usr/share/nginx/html/runtime-config.js <<EOF
window.AVERNET_RUNTIME_CONFIG = {
  BCS_ENDPOINT_PRE: "$BCS_ENDPOINT_PRE_JS",
  BCS_ENDPOINT_PROD: "$BCS_ENDPOINT_PROD_JS",
};
EOF
