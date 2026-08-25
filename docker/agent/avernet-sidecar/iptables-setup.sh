#!/bin/bash
# ============================================================
# iptables-setup.sh
# 出站流量劫持: 将主容器的出站 TCP 流量重定向到 sidecar 代理
#
# 工作原理:
#   nat OUTPUT → SIDECAR_OUTPUT
#     → 排除 sidecar UID / loopback / localhost
#     → 所有 TCP → SIDECAR_REDIRECT
#       → dport 80  → REDIRECT 38080 (Envoy HTTP)
#       → dport 443 → REDIRECT 38080 (Envoy HTTPS MITM/passthrough)
#       → 其他端口 → RETURN (不重定向, 由 filter 表决定)
#
#   filter OUTPUT → SIDECAR_FILTER
#     → 排除 sidecar UID / loopback / localhost
#     → dport 80/443 → ACCEPT (已重定向, 放行)
#     → 其他 TCP → REJECT tcp-reset (阻止直连非 HTTP/HTTPS)
# ============================================================

set -euo pipefail

# ---- 默认参数 ----
SIDECAR_PROXY_PORT="${SIDECAR_PROXY_PORT:-38080}"       # 出站代理端口 (HTTP + HTTPS MITM)
SIDECAR_UID="${SIDECAR_UID:-1337}"                      # sidecar 进程 UID (排除自身流量)

# Chain 名称
ISTIO_REDIRECT="SIDECAR_REDIRECT"
ISTIO_OUTPUT="SIDECAR_OUTPUT"
SIDECAR_FILTER="SIDECAR_FILTER"

log() {
    echo "[iptables-setup] $(date '+%Y-%m-%d %H:%M:%S') $*"
}

# ---- 清理已有规则 ----
cleanup() {
    log "Cleaning up existing iptables rules..."

    # nat 表
    iptables -t nat -D OUTPUT -j ${ISTIO_OUTPUT} 2>/dev/null || true
    iptables -t nat -F ${ISTIO_REDIRECT} 2>/dev/null || true
    iptables -t nat -F ${ISTIO_OUTPUT} 2>/dev/null || true
    iptables -t nat -X ${ISTIO_REDIRECT} 2>/dev/null || true
    iptables -t nat -X ${ISTIO_OUTPUT} 2>/dev/null || true

    # filter 表
    iptables -D OUTPUT -j ${SIDECAR_FILTER} 2>/dev/null || true
    iptables -F ${SIDECAR_FILTER} 2>/dev/null || true
    iptables -X ${SIDECAR_FILTER} 2>/dev/null || true
}

# ---- 创建自定义 Chain ----
setup_chains() {
    log "Setting up iptables chains..."

    # ---- nat 表: 重定向 ----

    # 出站重定向链: 只重定向 80/443 到 Envoy
    iptables -t nat -N ${ISTIO_REDIRECT} 2>/dev/null || true
    iptables -t nat -F ${ISTIO_REDIRECT}
    iptables -t nat -A ${ISTIO_REDIRECT} -p tcp --dport 80 -j REDIRECT --to-port ${SIDECAR_PROXY_PORT}
    iptables -t nat -A ${ISTIO_REDIRECT} -p tcp --dport 443 -j REDIRECT --to-port ${SIDECAR_PROXY_PORT}
    # 其他端口: RETURN (不重定向, 由 filter 表决定是否拒绝)

    # OUTPUT 过滤链 (nat)
    iptables -t nat -N ${ISTIO_OUTPUT} 2>/dev/null || true
    iptables -t nat -F ${ISTIO_OUTPUT}

    # 排除 sidecar 自身流量, 避免死循环
    iptables -t nat -A ${ISTIO_OUTPUT} -m owner --uid-owner ${SIDECAR_UID} -j RETURN

    # 排除 loopback
    iptables -t nat -A ${ISTIO_OUTPUT} -o lo -j RETURN

    # 排除 localhost
    iptables -t nat -A ${ISTIO_OUTPUT} -d 127.0.0.1/32 -j RETURN

    # 所有其他出站 TCP → 进入重定向链
    iptables -t nat -A ${ISTIO_OUTPUT} -p tcp -j ${ISTIO_REDIRECT}

    # ---- filter 表: 拒绝非 80/443 的出站流量 ----

    iptables -N ${SIDECAR_FILTER} 2>/dev/null || true
    iptables -F ${SIDECAR_FILTER}

    # 排除 sidecar 自身流量
    iptables -A ${SIDECAR_FILTER} -m owner --uid-owner ${SIDECAR_UID} -j RETURN

    # 排除 loopback / localhost
    iptables -A ${SIDECAR_FILTER} -o lo -j RETURN
    iptables -A ${SIDECAR_FILTER} -d 127.0.0.1/32 -j RETURN

    # 放行 80/443 (已被 nat 重定向到 Envoy)
    iptables -A ${SIDECAR_FILTER} -p tcp -m multiport --dports 80,443 -j ACCEPT

    # 拒绝其他所有出站 TCP (REJECT 发送 RST)
    iptables -A ${SIDECAR_FILTER} -p tcp -j REJECT --reject-with tcp-reset
}

# ---- 挂载规则 ----
apply_rules() {
    log "Applying iptables rules for outbound traffic hijacking..."

    # nat 表: OUTPUT 链 → SIDECAR_OUTPUT
    iptables -t nat -A OUTPUT -j ${ISTIO_OUTPUT}

    # filter 表: OUTPUT 链 → SIDECAR_FILTER
    iptables -A OUTPUT -j ${SIDECAR_FILTER}
}

# ---- 验证 ----
verify() {
    log "Verifying iptables rules..."
    echo "=== NAT ${ISTIO_REDIRECT} ==="
    iptables -t nat -L ${ISTIO_REDIRECT} -n -v 2>/dev/null || true
    echo ""
    echo "=== FILTER ${SIDECAR_FILTER} ==="
    iptables -L ${SIDECAR_FILTER} -n -v 2>/dev/null || true
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
