#!/bin/bash
# ============================================================
# entrypoint.sh
# Sidecar 启动入口 (仅出站流量):
#   以 root 启动, 执行 iptables 设置,
#   然后切换到 sidecar 用户 (uid 1337) 启动 Envoy
#
# CA 证书在构建时从 OSS 下载并烘入镜像:
#   /etc/sidecar/certs/mitm-ca/ca.crt  (CA 证书, 主镜像需信任此证书)
#   /etc/sidecar/certs/mitm-ca/ca.key  (CA 私钥, sidecar 用于签发动态证书)
#
# header-rules.yaml 中的 enc: 值在渲染时自动解密 (XOR + base64)
# ============================================================

set -euo pipefail

ENVOY_TEMPLATE="${ENVOY_TEMPLATE:-/etc/envoy/envoy-template.yaml}"
ENVOY_CONFIG="${ENVOY_CONFIG:-/etc/envoy/envoy.yaml}"
HEADER_RULES="${HEADER_RULES:-/etc/sidecar/header-rules.yaml}"
MITM_CA_DIR="${MITM_CA_DIR:-/etc/sidecar/certs/mitm-ca}"
SIDECAR_ADMIN_PORT="${SIDECAR_ADMIN_PORT:-38081}"
SIDECAR_PROXY_PORT="${SIDECAR_PROXY_PORT:-38080}"
SIDECAR_UID="${SIDECAR_UID:-1337}"
LOG_LEVEL="${LOG_LEVEL:-info}"

log() {
    echo "[sidecar-entrypoint] $(date '+%Y-%m-%d %H:%M:%S') $*"
}

# ---- Step 1: 校验 MITM CA 证书 ----
check_mitm_ca() {
    if [ -f "${MITM_CA_DIR}/ca.crt" ] && [ -f "${MITM_CA_DIR}/ca.key" ]; then
        log "MITM CA certificate found:"
        log "  CA cert: ${MITM_CA_DIR}/ca.crt"
        log "  CA key:  ${MITM_CA_DIR}/ca.key"
    else
        log "ERROR: MITM CA certificate not found!"
        log "  Expected: ${MITM_CA_DIR}/ca.crt and ${MITM_CA_DIR}/ca.key"
        log "  These are downloaded from OSS at build time."
        log "  If missing, re-build the image."
        exit 1
    fi
}

# ---- Step 1.5: 用 CA 签发服务端证书 ----
# CA 证书的 EKU 是 keyCertSign, 不能直接当 TLS 服务端证书用
# 需要用 CA 签发一个 serverAuth 用途的证书给 Envoy 下行 TLS 使用
# SAN 包含 header-rules.yaml 中需要 MITM 的域名
generate_server_cert() {
    local server_cert_dir="${MITM_CA_DIR}/server"
    mkdir -p "${server_cert_dir}"

    local server_crt="${server_cert_dir}/server.crt"
    local server_key="${server_cert_dir}/server.key"

    if [ -f "${server_crt}" ] && [ -f "${server_key}" ]; then
        log "Server certificate already exists, skipping generation."
        chown -R sidecar:sidecar "${server_cert_dir}"
        return 0
    fi

    # 从 header-rules.yaml 提取需要 MITM 的域名 (排除通配符 '*')
    local san_domains
    san_domains=$(python3 -c "
import yaml, sys
try:
    data = yaml.safe_load(open('${HEADER_RULES}')) or {}
    domains = set()
    for r in data.get('rules', []):
        for d in r.get('domains', []):
            if d != '*':
                domains.add(d.strip())
    print(' '.join(sorted(domains)) if domains else '')
except Exception:
    print('')
" 2>/dev/null || true)

    # 构建 SAN 扩展: 每个域名单独列出, 加上通配符形式
    # 例如 api.example.com → DNS:api.example.com, DNS:*.example.com
    local san_entries="DNS:localhost"
    for domain in ${san_domains}; do
        san_entries="${san_entries},DNS:${domain}"
        # 自动加一级通配符: api.example.com → *.example.com
        local parent="${domain#*.}"
        if [ "${parent}" != "${domain}" ]; then
            san_entries="${san_entries},DNS:*.${parent}"
        fi
    done
    # 追加兜底通配符
    san_entries="${san_entries},DNS:*.*"

    log "Generating server certificate with SAN: ${san_entries}"
    log "  MITM domains from rules: ${san_domains:-<none>}"

    openssl req -new -newkey rsa:2048 -sha256 -nodes \
        -keyout "${server_key}" \
        -out "${server_cert_dir}/server.csr" \
        -subj "/CN=poolab-sidecar-mitm" \
        -addext "subjectAltName=${san_entries}" \
        -addext "extendedKeyUsage=serverAuth" \
        -addext "basicConstraints=CA:FALSE" 2>/dev/null

    openssl x509 -req -sha256 -days 3650 \
        -in "${server_cert_dir}/server.csr" \
        -CA "${MITM_CA_DIR}/ca.crt" \
        -CAkey "${MITM_CA_DIR}/ca.key" \
        -CAcreateserial \
        -out "${server_crt}" \
        -extfile <(printf "extendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\nsubjectAltName=%s" "${san_entries}") 2>/dev/null

    rm -f "${server_cert_dir}/server.csr"
    log "Server certificate generated:"
    log "  Server cert: ${server_crt}"
    log "  Server key:  ${server_key}"

    # sidecar 用户需要有读权限
    chown -R sidecar:sidecar "${server_cert_dir}"
}

# ---- Step 2: 设置 iptables (需要 root 权限) ----
setup_iptables() {
    log "Setting up iptables rules for outbound traffic hijacking..."
    if [ -x /usr/local/bin/iptables-setup.sh ]; then
        /usr/local/bin/iptables-setup.sh
    else
        log "WARNING: iptables-setup.sh not found, skipping iptables setup."
    fi
}

# ---- Step 3: 渲染 Envoy 配置 ----
render_config() {
    log "Rendering Envoy configuration from ${HEADER_RULES}..."
    if command -v config-renderer &>/dev/null; then
        config-renderer \
            --template="${ENVOY_TEMPLATE}" \
            --rules="${HEADER_RULES}" \
            --output="${ENVOY_CONFIG}" \
            --proxy-port="${SIDECAR_PROXY_PORT}" \
            --admin-port="${SIDECAR_ADMIN_PORT}"
    else
        log "WARNING: config-renderer not found, using template as-is."
        cp "${ENVOY_TEMPLATE}" "${ENVOY_CONFIG}"
    fi
    log "Envoy config rendered to ${ENVOY_CONFIG}"
    log "--- Rendered Envoy config ---"
    cat "${ENVOY_CONFIG}"
    log "--- End of rendered config ---"
}

# ---- Step 4: 以 sidecar 用户启动 Envoy ----
start_envoy() {
    log "Starting Envoy proxy (outbound only) as uid ${SIDECAR_UID}..."
    log "  Admin port        : ${SIDECAR_ADMIN_PORT}"
    log "  Proxy port        : ${SIDECAR_PROXY_PORT}"
    log "  Log level         : ${LOG_LEVEL}"

    # 使用 gosu 切换到 sidecar 用户启动 Envoy
    # iptables 规则通过 --uid-owner 排除该用户的流量, 避免重定向死循环
    exec gosu sidecar /usr/local/bin/envoy \
        -c "${ENVOY_CONFIG}" \
        --log-level "${LOG_LEVEL}" \
        --component-log-level misc:error \
        --drain-time-s 30 \
        --parent-shutdown-time-s 35
}

main() {
    log "=== Poolab Sidecar Starting (outbound only) ==="

    # 以下步骤需要 root 权限
    check_mitm_ca
    generate_server_cert
    setup_iptables
    render_config

    # 切换到 sidecar 用户启动 Envoy (exec 替换当前进程)
    start_envoy
}

main "$@"
