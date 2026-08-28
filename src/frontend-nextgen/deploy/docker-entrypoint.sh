#!/bin/sh
set -eu

required_vars="BCS_AUTH_UPSTREAM TEAMCLAW_GATEWAY_UPSTREAM TEAMCLAW_ADMIN_UPSTREAM PRIVATE_CHAT_MANAGEMENT_UPSTREAM PRIVATE_CHAT_SESSION_UPSTREAM CLAWWEB_UPSTREAM AIXCORE_UPSTREAM"
for name in $required_vars; do
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "Missing required deployment variable: $name" >&2
    exit 1
  fi
done
