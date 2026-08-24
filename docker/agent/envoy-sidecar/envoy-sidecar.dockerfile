# ============================================================
# envoy-sidecar: Outbound traffic header manipulation sidecar
# Based on Envoy + iptables, hijacks main container's outbound
# traffic to the sidecar for HTTP/HTTPS header set/remove operations.
#
# Reference: ocb/dockers/poolab-sidecar/Dockerfile
#
# CA certs are downloaded from OSS at build time:
#   MITM CA (for HTTPS man-in-the-middle decryption):
#     https://antsys-agentclaw-prod.cn-shanghai-ant-office.oss-alipay.aliyuncs.com/agentclaw-sys/certs/ca.crt
#     https://antsys-agentclaw-prod.cn-shanghai-ant-office.oss-alipay.aliyuncs.com/agentclaw-sys/certs/ca.key
#   Internal gateway CA (Nautilus SWG CA):
#     https://antsys-agentclaw-prod.cn-shanghai-ant-office.oss-alipay.aliyuncs.com/agentclaw-sys/certs/ant-internal-gateway-ca.crt
#   Main container image must trust the same MITM ca.crt:
#     ADD ca.crt /usr/local/share/ca-certificates/envoy-sidecar-ca.crt
#     RUN update-ca-certificates
#
# Encrypted values: header-rules.yaml values prefixed with "enc:" are
# auto-decrypted at render time (XOR + base64).
# ============================================================

FROM envoyproxy/envoy:v1.30-latest

USER root

# Use Aliyun mirrors for faster apt installs in CN.
RUN sed -i 's|archive.ubuntu.com|mirrors.aliyun.com|g; s|security.ubuntu.com|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    true

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        iptables \
        iproute2 \
        conntrack \
        ca-certificates \
        curl \
        python3 \
        python3-yaml \
        tzdata \
        tini \
        gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1337 sidecar \
    && useradd -u 1337 -g sidecar -s /sbin/nologin -M sidecar

# Trust internal gateway CA (Nautilus SWG CA), downloaded from OSS.
RUN curl -fsSL https://antsys-agentclaw-prod.cn-shanghai-ant-office.oss-alipay.aliyuncs.com/agentclaw-sys/certs/ant-internal-gateway-ca.crt \
         -o /usr/local/share/ca-certificates/ant-internal-gateway-ca.crt \
    && update-ca-certificates

# Config renderer (Python, no compilation needed).
COPY render.py /usr/local/bin/config-renderer
RUN chmod +x /usr/local/bin/config-renderer && \
    sed -i '1s|^#!/usr/bin/env python3|#!/usr/bin/python3|' /usr/local/bin/config-renderer

# MITM CA certificates (downloaded from OSS at build time).
RUN mkdir -p /etc/sidecar/certs/mitm-ca && \
    curl -fsSL https://antsys-agentclaw-prod.cn-shanghai-ant-office.oss-alipay.aliyuncs.com/agentclaw-sys/certs/ca.crt \
         -o /etc/sidecar/certs/mitm-ca/ca.crt && \
    curl -fsSL https://antsys-agentclaw-prod.cn-shanghai-ant-office.oss-alipay.aliyuncs.com/agentclaw-sys/certs/ca.key \
         -o /etc/sidecar/certs/mitm-ca/ca.key

# Scripts and configs.
COPY iptables-setup.sh    /usr/local/bin/iptables-setup.sh
COPY entrypoint.sh        /usr/local/bin/entrypoint.sh
COPY envoy-template.yaml  /etc/envoy/envoy-template.yaml
COPY header-rules.yaml    /etc/sidecar/header-rules.yaml

RUN chmod +x /usr/local/bin/iptables-setup.sh \
             /usr/local/bin/entrypoint.sh \
    && mkdir -p /var/log/envoy \
    && chown sidecar:sidecar /var/log/envoy

ENV SIDECAR_PROXY_PORT=38080       \
    SIDECAR_ADMIN_PORT=38081       \
    SIDECAR_UID=1337               \
    MITM_CA_DIR=/etc/sidecar/certs/mitm-ca \
    LOG_LEVEL=info

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD curl -sf http://127.0.0.1:${SIDECAR_ADMIN_PORT}/ready || exit 1

EXPOSE 38080 38081

ENTRYPOINT ["tini", "--", "/usr/local/bin/entrypoint.sh"]