# ============================================================
# avernet-sidecar: Outbound traffic header manipulation sidecar
# Based on Envoy + iptables, hijacks main container's outbound
# traffic to the sidecar for HTTP/HTTPS header set/remove operations.
#
# Reference: ocb/dockers/poolab-sidecar/Dockerfile
#
# CA certificates: place the following files in docker/agent/avernet-sidecar/
# before building (they are .gitignore'd, NOT committed):
#   mitm-ca.crt  — MITM CA certificate (for HTTPS interception)
#   mitm-ca.key  — MITM CA private key (sidecar signs dynamic server certs)
#   internal-gateway-ca.crt — (optional) internal gateway CA
#   The agent image must trust the same mitm-ca.crt:
#     docker/agent/avernet-sidecar/mitm-ca.crt is COPY'd into both images.
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

# Optionally trust an internal gateway CA. Place the cert at
# docker/agent/avernet-sidecar/internal-gateway-ca.crt in the build
# context. Not committed to the repository.
COPY docker/agent/avernet-sidecar/internal-gateway-ca.crt /usr/local/share/ca-certificates/ant-internal-gateway-ca.crt
RUN update-ca-certificates

# Config renderer (Python, no compilation needed).
COPY docker/agent/avernet-sidecar/render.py /usr/local/bin/config-renderer
RUN chmod +x /usr/local/bin/config-renderer && \
    sed -i '1s|^#!/usr/bin/env python3|#!/usr/bin/python3|' /usr/local/bin/config-renderer

# MITM CA certificate and key. COPY from build context — the caller
# must place ca.crt and ca.key in docker/agent/avernet-sidecar/ before
# building. These are NOT committed to the repository (.gitignore'd).
COPY docker/agent/avernet-sidecar/mitm-ca.crt /etc/sidecar/certs/mitm-ca/ca.crt
COPY docker/agent/avernet-sidecar/mitm-ca.key /etc/sidecar/certs/mitm-ca/ca.key
RUN mkdir -p /etc/sidecar/certs/mitm-ca && \
    curl -fsSL docker/agent/avernet-sidecar/mitm-ca.crt \
         -o /etc/sidecar/certs/mitm-ca/ca.crt && \
    curl -fsSL docker/agent/avernet-sidecar/mitm-ca.key \
         -o /etc/sidecar/certs/mitm-ca/ca.key

# Scripts and configs.
COPY docker/agent/avernet-sidecar/iptables-setup.sh    /usr/local/bin/iptables-setup.sh
COPY docker/agent/avernet-sidecar/entrypoint.sh        /usr/local/bin/entrypoint.sh
COPY docker/agent/avernet-sidecar/envoy-template.yaml  /etc/envoy/envoy-template.yaml
COPY docker/agent/avernet-sidecar/header-rules.yaml    /etc/sidecar/header-rules.yaml

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