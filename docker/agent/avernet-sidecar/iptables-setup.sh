#!/bin/bash
# ============================================================
# iptables-setup.sh
# 出站流量劫持: 将主容器的出站 TCP 流量重定向到 sidecar 代理
#
# 工作原理:
#   主容器出站流量 → OUTPUT 链 → SIDECAR_OUTPUT 链
#   → 排除 sidecar 自身流量(UID) & localhost
#   → REDIRECT 到 sidecar 出站代理端口
#   → sidecar 操作 header → 转发到原始目的地
# ============================================================

set -euo pipefail

# ---- 默认参数 ----
SIDECAR_PROXY_PORT="${SIDECAR_PROXY_PORT:-38080}"       # 出站代理端口 (HTTP + HTTPS MITM)
SIDECAR_UID="${SIDECAR_UID:-1337}"                      # sidecar 进程 UID (排除自身流量)

# Chain 名称
ISTIO_REDIRECT="SIDECAR_REDIRECT"
ISTIO_OUTPUT="SIDECAR_OUTPUT"

log() {
    echo "[iptables-setup] $(date '+%Y-%m-%d %H:%M:%S') $*"
}

# ---- 清理已有规则 ----
cleanup() {
    log "Cleaning up existing iptables rules..."
    iptables -t nat -D OUTPUT -j ${ISTIO_OUTPUT} 2>/dev/null || true
    iptables -t nat -F ${ISTIO_REDIRECT} 2>/dev/null || true
    iptables -t nat -F ${ISTIO_OUTPUT} 2>/dev/null || true
    iptables -t nat -X ${ISTIO_REDIRECT} 2>/dev/null || true
    iptables -t nat -X ${ISTIO_OUTPUT} 2>/dev/null || true
}

# ---- 创建自定义 Chain ----
setup_chains() {
    log "Setting up iptables chains..."

    # 出站重定向链: 80/443 放行到 Envoy, 其余端口 REJECT
    iptables -t nat -N ${ISTIO_REDIRECT} 2>/dev/null || true
    iptables -t nat -F ${ISTIO_REDIRECT}
    iptables -t nat -A ${ISTIO_REDIRECT} -p tcp --dport 80 -j REDIRECT --to-port ${SIDECAR_PROXY_PORT}
    iptables -t nat -A ${ISTIO_REDIRECT} -p tcp --dport 443 -j REDIRECT --to-port ${SIDECAR_PROXY_PORT}
    # 其他端口: REJECT (发送 RST, 阻止主容器直连非 HTTP/HTTPS 服务)
    iptables -t nat -A ${ISTIO_REDIRECT} -p tcp -j REJECT --reject-with tcp-reset

    # OUTPUT 过滤链
    iptables -t nat -N ${ISTIO_OUTPUT} 2>/dev/null || true
    iptables -t nat -F ${ISTIO_OUTPUT}

    # 排除 sidecar 自身流量, 避免死循环
    iptables -t nat -A ${ISTIO_OUTPUT} -m owner --uid-owner ${SIDECAR_UID} -j RETURN

    # 排除 loopback
    iptables -t nat -A ${ISTIO_OUTPUT} -o lo -j RETURN

    # 排除 localhost
    iptables -t nat -A ${ISTIO_OUTPUT} -d 127.0.0.1/32 -j RETURN

    # 所有其他出站 TCP → 进入重定向链 (80/443 放行, 其余 REJECT)
    iptables -t nat -A ${ISTIO_OUTPUT} -p tcp -j ${ISTIO_REDIRECT}
}

# ---- 挂载规则 ----
apply_rules() {
    log "Applying iptables rules for outbound traffic hijacking..."
    iptables -t nat -A OUTPUT -j ${ISTIO_OUTPUT}
}

# ---- 验证 ----
verify() {
    log "Verifying iptables rules..."
    echo "=== NAT OUTPUT ==="
    iptables -t nat -L OUTPUT -n -v 2>/dev/null || true
    echo ""
    echo "=== ${ISTIO_OUTPUT} ==="
    iptables -t nat -L ${ISTIO_OUTPUT} -n -v 2>/dev/null || true
    echo ""
    echo "=== ${ISTIO_REDIRECT} ==="
    iptables -t nat -L ${ISTIO_REDIRECT} -n -v 2>/dev/null || true
    log "Iptables rules applied successfully."
}

main() {
    log "Starting iptables setup (outbound only)..."
    log "  SIDECAR_PROXY_PORT     = ${SIDECAR_PROXY_PORT}"
    log "  SIDECAR_UID            = ${SIDECAR_UID}"

    cleanup
    setup_chains
    apply_rules
    verify

    log "Done."
}

main "$@"