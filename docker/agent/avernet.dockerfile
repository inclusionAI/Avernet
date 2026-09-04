########## Avernet Engine + OpenClaw ##########
# Multi-stage build producing an image that runs both the Python engine adapter
# and the OpenClaw gateway under supervisord. bcs-cli is compiled from the
# in-repository Rust source in its own builder stage.
#
# Reference: ocb/dockers/arca-openclaw/Dockerfile
#
# Runtime model (mirrors OCB arca-openclaw):
#   - supervisord is PID 1
#   - [program:engine]       autostart=false — started by start_service.sh
#                            (engine type from .adaptorEnv CHAT_ENGINE; the
#                            openclaw default is start_service.sh's ENGINE
#                            default, claude_code via --engine)
#   - [program:openclaw]     autostart=false — started on demand by engine
#                            via `sudo supervisorctl start openclaw`
#   - [program:claude_relay] autostart=false — started by start_service.sh when
#                            --engine claude_code (reads .relayEnv), serves
#                            ws://127.0.0.1:18900 for the engine's claude_code
#                            adapter
#
# Pod startup flow:
#   1. entrypoint.sh: pre-init (directories, config), then exec supervisord
#   2. start_service.sh (background): parse args → save credentials → check
#      --engine → exec start_openclaw.sh (engine program; openclaw gateway
#      on demand by the engine) or start_claude_code.sh (claude_relay
#      health-gated, then engine) → poll /health → write ready marker
#
# Build args:
#   OPENCLAW_VERSION   npm version of openclaw (default 2026.6.1)
#   CLAUDE_CODE_VERSION  npm version of @anthropic-ai/claude-code (default 2.1.251 —
#                       pinned, unlike scripts/toolchain.sh which installs latest;
#                       image builds must be reproducible), from npmmirror
#   UV_VERSION         uv version for pin (default: latest)
#   NPM_STRICT_SSL     npm strict-ssl toggle (default true)

# ==================== Stage 1: BCS CLI Builder ====================
FROM rust:1.91.0-bookworm AS bcs-cli-builder

WORKDIR /opt/bcs

COPY src/bcs/ /opt/bcs/
RUN cargo build --locked --release --package bcs-cli \
    && strip target/release/bcs-cli \
    && target/release/bcs-cli --help >/dev/null

# ==================== Stage 2: Builder ====================
FROM node:22-bookworm-slim AS builder

ARG OPENCLAW_VERSION=2026.5.12
ARG NPM_STRICT_SSL=true

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/root \
    PATH=/usr/local/bin:/root/.local/bin:$PATH

WORKDIR /opt

# Install build + system dependencies in one layer.
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        lsof \
        make \
        procps \
        python3 \
        python3-pip \
        python3-venv \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Install OpenClaw via npm (global).
RUN npm config set registry "https://registry.npmmirror.com" \
    && npm config set strict-ssl "${NPM_STRICT_SSL}" \
    && npm install -g "openclaw@${OPENCLAW_VERSION}"

# Install Claude Code CLI (global, pinned — same package + registry as
# scripts/toolchain.sh CLAUDE_CODE_NPM_PACKAGE/CLAUDE_CODE_NPM_REGISTRY; pinned
# here for reproducible image builds). Installed in BOTH runtime engines' scope:
# the claude_code adapter path (vendored gateway spawns the CLI when
# CLAUDE_BRIDGE=cli) and the sdk bridge's CLAUDE_CODE_PATH convention.
ARG CLAUDE_CODE_VERSION=2.1.251
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"

# Install uv via pip (Aliyun mirror — astral.sh is unreachable in CN build envs).
# --break-system-packages: required by PEP 668 on Debian 12 system Python.
ARG UV_VERSION=
RUN pip3 install --no-cache-dir --break-system-packages \
        -i https://mirrors.aliyun.com/pypi/simple \
        "uv${UV_VERSION:+==${UV_VERSION}}" \
    && which uv && uv --version

# Build engine virtualenv from pyproject.toml.
COPY src/engine/ /opt/engine/
RUN uv venv --python 3 /opt/.venv \
    && UV_INDEX_URL="https://mirrors.aliyun.com/pypi/simple" \
       uv pip install --python /opt/.venv/bin/python -r /opt/engine/pyproject.toml \
    && UV_INDEX_URL="https://mirrors.aliyun.com/pypi/simple" \
       uv pip install --python /opt/.venv/bin/python -e /opt/engine \
    && find /opt/.venv -name "*.pyc" -delete \
    && find /opt/.venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true \
    && rm -rf /root/.cache/uv /root/.local/share/uv

# Build the vendored claude_code relay gateway (the Node WS server the engine's
# claude_code mode connects to at ws://127.0.0.1:18900). Build command mirrors
# scripts/modules/claude_relays.sh::claude_relays_setup (npm install --include=dev
# --ignore-scripts, tshy build via prepublishOnly), then prune devDeps like the
# bcn plugin build below keeps the runtime layer lean. The repo ships no dist/;
# it must be built here or the relay cannot run in the image.
RUN cd /opt/engine/src/engine/community/claude_code_gateway \
    && npm install --include=dev --ignore-scripts --no-audit --no-fund \
    && npm run prepublishOnly \
    && npm prune --omit=dev \
    && test -f dist/esm/server.js

# Build the openclaw-channel-bcn plugin (BCS WebSocket channel).
# Mirrors Dockerfile.ocb: npm install → build → prune devDeps.
COPY src/bcs/crates/plugins/openclaw-channel-bcn/ /tmp/openclaw-channel-bcn/
RUN cd /tmp/openclaw-channel-bcn \
    && npm install \
    && npm run build \
    && npm prune --omit=dev \
    && mkdir -p /opt/openclawExt/openclaw-channel-bcn \
    && cp -R dist node_modules package.json openclaw.plugin.json \
           /opt/openclawExt/openclaw-channel-bcn/ \
    && rm -rf /tmp/openclaw-channel-bcn

# Build the taskguard plugin (TaskFlow DAG workflow engine for openclaw).
#
# Mirrors the bcn pattern above: install → build → prune devDeps → copy.
#
# The previous flow ran `node scripts/dist_pack.mjs openclaw`, which uses an
# in-repo bundleDeps() helper that *shallow*-copies only the entries listed
# in package.json#bundleDependencies (the 7 direct runtime deps). It does NOT
# copy their transitive closure. After npm hoists transitive runtime deps
# (express → cookie/qs/debug/body-parser/send/router/parseurl/..., ajv,
# ajv-formats, etc.) to the top-level node_modules, those are dropped. The
# resulting tarball passes the basic manifest checks but fails the moment the
# runtime reaches `require('cookie')` or `import 'ajv-formats'`, with
# MODULE_NOT_FOUND.
#
# `npm prune --omit=dev` is the correct way to drop devDependencies while
# preserving every transitive dep reachable from runtime deps. We pair it
# with a manual `cp -R` of the runtime assets the openclaw plugin loader
# expects (dist/, configs/, skills/, packs/, scripts/, openclaw.plugin.json,
# package.json, node_modules/).
#
# Layer caching: manifests + scripts/configs → `npm ci` (heavy) → rest of
# source → build → prune. Source edits do not invalidate the npm ci layer.
# node_modules / dist / *.tgz are excluded by the repo .dockerignore.
COPY src/evolverun/taskguard/package.json \
     src/evolverun/taskguard/package-lock.json \
     src/evolverun/taskguard/tsconfig.json \
     /tmp/taskguard/
# scripts/ ships with the manifest stage so `npm run build` (scripts/build/*.mjs)
# can be invoked without an extra source layer. configs/ arrives with the
# full source COPY below.
COPY src/evolverun/taskguard/scripts /tmp/taskguard/scripts
RUN cd /tmp/taskguard \
    && npm ci --no-audit --no-fund --ignore-scripts \
    && npm cache clean --force

# Layer the rest of the source on top. The COPY above does not overwrite
# node_modules (it is .dockerignore'd from the build context). configs/,
# skills/, packs/, src/ all land here, ready for `npm run build`.
COPY src/evolverun/taskguard/ /tmp/taskguard/

# Build (tshy compile + facade skills + runtime asset bundling), then prune
# devDeps so node_modules keeps ONLY the transitive closure reachable from
# runtime deps. After this step the source tree shrinks to exactly the
# assets the openclaw runtime needs.
RUN cd /tmp/taskguard \
    && npm run build \
    && npm prune --omit=dev \
    && rm -rf src tests docs .tshy dist_pack *.md *.tgz \
              node_modules/.cache node_modules/.bin/tshy \
              /root/.npm /root/.cache \
    && mkdir -p /opt/openclawExt/taskguard \
    && cp -R dist openclaw.plugin.json package.json skills packs configs scripts \
                node_modules /opt/openclawExt/taskguard/ \
    && test -f /opt/openclawExt/taskguard/openclaw.plugin.json \
    && test -d /opt/openclawExt/taskguard/dist \
    && test -d /opt/openclawExt/taskguard/node_modules \
    && rm -rf /tmp/taskguard

# Overlay the source-of-truth config so the docker image always ships the
# latest baseUrl / apiKey / etc. from src/evolverun/taskguard/configs/.
# The cp -R configs/ above mirrors the source tree, but pinning a single
# file guarantees application.yaml always reflects repo HEAD even if a
# future build step mutates configs/ in /tmp.
COPY src/evolverun/taskguard/configs/application.yaml \
     /opt/openclawExt/taskguard/configs/application.yaml

# Install supervisor in an isolated venv (avoids conflicts with engine site-packages).
RUN python3 -m venv /opt/supervisor-venv \
    && /opt/supervisor-venv/bin/pip install --no-cache-dir supervisor \
    && ln -sf /opt/supervisor-venv/bin/supervisord /usr/local/bin/supervisord \
    && ln -sf /opt/supervisor-venv/bin/supervisorctl /usr/local/bin/supervisorctl \
    && mkdir -p /var/log/supervisor /var/run

# Create admin user (uid/gid 10001) for running engine + openclaw.
RUN groupadd --gid 10001 admin 2>/dev/null || true \
    && useradd --uid 10001 --gid admin --create-home --shell /bin/bash admin 2>/dev/null || true \
    && echo 'admin:*' | chpasswd -e \
    && echo 'admin ALL=(ALL) NOPASSWD: /usr/local/bin/supervisorctl *' > /etc/sudoers.d/admin-supervisorctl \
    && chmod 440 /etc/sudoers.d/admin-supervisorctl \
    && su admin -s /bin/bash -c 'mkdir -p /home/admin/.openclaw/workspace /home/admin/logs'

# Create symlink for CA bundle path expected by run.sh / Node (RHEL-style path on Debian).
RUN ln -sf /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-bundle.crt

# ==================== Stage 3: Runtime ====================
FROM node:22-bookworm-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/home/admin \
    OPENCLAW_PORT=18789 \
    OPENCLAW_GATEWAY_TOKEN=ack-openclaw \
    OPENCLAW_STATE_DIR=/home/admin/.openclaw \
    PATH=/usr/local/bin:/home/admin/.local/bin:$PATH \
    TZ=Asia/Shanghai

# Runtime deps (python3 needed by supervisor venv symlink).
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        jq \
        lsof \
        procps \
        python3 \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Bring over installed openclaw + claude-code from builder.
COPY --from=builder /usr/local/lib/node_modules /usr/local/lib/node_modules

# Install the source-built BCS CLI and its matching OpenClaw coordination skill.
COPY --from=bcs-cli-builder /opt/bcs/target/release/bcs-cli /usr/local/bin/bcs-cli
COPY src/bcs/crates/tools/bcs-cli/bcs-coordination/ /usr/local/lib/node_modules/openclaw/skills/bcs-coordination/
RUN bcs-cli --help >/dev/null \
    && test -f /usr/local/lib/node_modules/openclaw/skills/bcs-coordination/SKILL.md

# Recreate npm bin symlink: COPY --from resolves symlinks to files,
# which breaks the script's __dirname-based dist/ path resolution.
RUN BIN_REL=$(node -e "\
  const p = require('/usr/local/lib/node_modules/openclaw/package.json'); \
  const b = p.bin || {}; \
  const t = typeof b === 'string' ? b : (b.openclaw || Object.values(b)[0]); \
  console.log(t)") \
    && ln -sf "/usr/local/lib/node_modules/openclaw/${BIN_REL}" \
              /usr/local/bin/openclaw \
    && chmod +x /usr/local/bin/openclaw

# Recreate the claude bin symlink (same COPY --from symlink issue, scoped pkg).
# The generic bin-field parse handles any bin shape the package may use.
RUN CC_BIN=$(node -e "\
  const p = require('/usr/local/lib/node_modules/@anthropic-ai/claude-code/package.json'); \
  const b = p.bin || {}; \
  const t = typeof b === 'string' ? b : (b.claude || Object.values(b)[0]); \
  console.log(t)") \
    && ln -sf "/usr/local/lib/node_modules/@anthropic-ai/claude-code/${CC_BIN}" \
              /usr/local/bin/claude \
    && chmod +x /usr/local/bin/claude \
    && /usr/local/bin/claude --version

# Bring over the engine venv + source code.
COPY --from=builder /opt/.venv /opt/.venv
COPY --from=builder /opt/engine /opt/engine

# Bring over supervisor binaries.
COPY --from=builder /opt/supervisor-venv /opt/supervisor-venv
COPY --from=builder /usr/local/bin/supervisord /usr/local/bin/supervisord
COPY --from=builder /usr/local/bin/supervisorctl /usr/local/bin/supervisorctl

# Bring over uv for developer convenience.
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

# Bring over the openclaw-channel-bcn plugin and link it into openclaw extensions.
COPY --from=builder /opt/openclawExt/openclaw-channel-bcn /opt/openclawExt/openclaw-channel-bcn

# Bring over the taskguard plugin (TaskFlow DAG workflow engine) so the runtime
# stage actually sees it. The builder stage already unpacked it under
# /opt/openclawExt/taskguard (see the corresponding step in the builder stage
# above); without this COPY --from=builder line, the directory would never
# reach the final image because the builder stage is otherwise discarded.
COPY --from=builder /opt/openclawExt/taskguard /opt/openclawExt/taskguard

# CA bundle symlink for run.sh's hardcoded NODE_EXTRA_CA_CERTS path.
# Optionally trust the avernet-sidecar MITM CA (generated by gen-mitm-ca.sh):
# COPY with wildcard — if mitm-ca.crt is absent, the build still succeeds.
COPY docker/agent/avernet-sidecar/mitm-ca.crt* /tmp/
RUN if [ -f /tmp/mitm-ca.crt ]; then \
        cp /tmp/mitm-ca.crt /usr/local/share/ca-certificates/avernet-sidecar-mitm-ca.crt; \
    fi \
    && update-ca-certificates \
    && ln -sf /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-bundle.crt

# Recreate admin user at runtime stage (uid/gid 10001).
RUN groupadd --gid 10001 admin 2>/dev/null || true \
    && useradd --uid 10001 --gid admin --create-home --shell /bin/bash admin 2>/dev/null || true \
    && echo 'admin ALL=(ALL) NOPASSWD: /usr/local/bin/supervisorctl *' > /etc/sudoers.d/admin-supervisorctl \
    && chmod 440 /etc/sudoers.d/admin-supervisorctl \
    && mkdir -p /var/log/supervisor /var/run/agentclaw \
    && chown admin:admin /var/run/agentclaw \
    && su admin -s /bin/bash -c 'mkdir -p /home/admin/.openclaw/workspace /home/admin/.openclaw/extensions /home/admin/logs' \
    && ln -sfn /opt/openclawExt/openclaw-channel-bcn \
               /home/admin/.openclaw/extensions/openclaw-channel-bcn \
    && ln -sfn /opt/openclawExt/taskguard \
               /home/admin/.openclaw/extensions/taskguard

# Supervisor configuration: engine(autostart=false) + openclaw(autostart=false).
COPY docker/agent/avernet-supervisord.conf /etc/supervisor/supervisord.conf

# OpenClaw default config template (env-var placeholders substituted at runtime).
COPY docker/agent/openclaw.json /opt/openclaw.json.template

# Claude Code provider settings template — staged like openclaw.json above:
# /home/admin is NAS-mounted at pod start, which SHADOWS anything baked
# there, so the file must live in /opt and be copied into ~/.claude AFTER
# the mount (start_claude_code.sh does the copy, substituting the
# MODEL_PROVIDER_HOST placeholder from the pod env — same variable and
# bare-host contract as the entrypoint's openclaw.json rendering, defaulting
# to dashscope.aliyuncs.com; mount-wins: a file already on the NAS is kept).
# Everything else is static, mirroring openclaw.json's hardcoded config:
# model (glm-5.2), and the auth token as the literal
# placeholder "Bearer ${API-KEY}" — the gateway on the upstream side
# replaces the placeholder with the real key (same pattern as openclaw.json's
# "apiKey": "Bearer ${API-KEY}"). No credentials are injected into the pod
# at runtime for this engine. The claude CLI reads the file natively via
# CLAUDE_CONFIG_DIR; the relay gateway's model-provider loader via
# RELAY_MODEL_SETTINGS_SOURCE (set in start_claude_code.sh). A deployment
# with a different provider scenario mounts its own file at the final path.
COPY docker/agent/claude-settings.json /opt/claude-settings.json.template

# HEARTBEAT.md template — staged in /opt like the templates above, but with
# opposite runtime semantics. openclaw appends its own tasks to
# workspace/HEARTBEAT.md while running, and /home/admin is NAS-mounted, so
# the runtime copy persists across pod restarts. start_openclaw.sh copies
# this template over it on EVERY startup (template-wins, unlike the
# mount-wins claude-settings.json above), so each run starts from the
# pristine empty heartbeat shipped in the image.
COPY docker/agent/HEARTBEAT.md /opt/config/HEARTBEAT.md

# Shared utility functions (logging, helpers).
COPY docker/agent/util.sh /usr/local/bin/util.sh

# Pod startup: thin dispatcher (parses args, saves credentials, checks
# --engine, execs the right script) + one per-engine script.
COPY docker/agent/start_service.sh /usr/local/bin/start_service.sh
COPY docker/agent/start_openclaw.sh /usr/local/bin/start_openclaw.sh
COPY docker/agent/start_claude_code.sh /usr/local/bin/start_claude_code.sh

# Entrypoint: pre-init, config generation from template, then execs supervisord.
COPY docker/agent/avernet-entrypoint.sh /usr/local/bin/avernet-entrypoint
RUN chmod +x /usr/local/bin/avernet-entrypoint \
             /usr/local/bin/start_service.sh \
             /usr/local/bin/start_openclaw.sh \
             /usr/local/bin/start_claude_code.sh \
             /usr/local/bin/util.sh

EXPOSE 20003 18789 18900

HEALTHCHECK --interval=10s --timeout=5s --start-period=120s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:20003/health" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/avernet-entrypoint"]
