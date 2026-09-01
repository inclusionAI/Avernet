########## Builder ##########
FROM python:3.12-slim-bookworm AS builder

# Pin uv for reproducible builds, e.g. --build-arg UV_VERSION=0.8.14
ARG UV_VERSION=

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple "uv${UV_VERSION:+==${UV_VERSION}}"

# Install dependencies first (without the project) for better layer caching.
# The default uv index is the Aliyun PyPI mirror (see src/backend/pyproject.toml).
COPY src/backend/pyproject.toml src/backend/uv.lock src/backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Then install the project itself — the entrypoint runs the app as a module
# (``python -m agentclaw.community.main``), so ``agentclaw.community`` has to be
# on the venv's import path. src/agentclaw/ carries no __init__.py: it is a PEP
# 420 namespace whose only child here is community/ (see the hatch wheel target).
COPY src/backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

########## Runtime ##########
FROM python:3.12-slim-bookworm AS runtime

# DEPLOY_PROFILE is mandatory (DeployProfile.detect raises when it is unset) and
# ``community`` is the profile this image exists to run — it selects the
# application-community.yaml overlay. Override it only to boot a different
# profile out of the same image.
ENV DEBIAN_FRONTEND=noninteractive \
    DEPLOY_PROFILE=community \
    HOME=/home/admin \
    PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# curl backs the HEALTHCHECK. git and rsync are runtime dependencies the other
# three service images do not need: the backend shells out to both — git for
# skill-center repo sync (core/skill_center/services/git_sync.py), rsync for
# skill/workspace publishing — and without the binaries those flows fail with
# FileNotFoundError at request time instead of at boot.
#
# openssh-client rides along with git deliberately. Debian's git only
# *Recommends* ssh-client, which --no-install-recommends drops, and
# GitSyncService applies no scheme validation: the operator-supplied repo URL
# goes from the secret store straight into `git clone`
# (_resolve_repo_url_from_secret → _sync_clone_bare_repo). A skills repo
# configured as git@host:org/repo.git or ssh://… is therefore a legitimate
# setting that would otherwise die on a missing SSH transport.
#
# The transport is necessary but not sufficient. `git clone` runs
# non-interactively with no SSH options set, and this image ships no
# known_hosts for admin, so first contact with an unknown host still fails
# OpenSSH's default StrictHostKeyChecking=ask ("Host key verification
# failed"). An SSH skills repo therefore also needs a known_hosts mounted for
# admin, or GIT_SSH_COMMAND supplied. Baking StrictHostKeyChecking=accept-new
# in here would trade that away for silent trust-on-first-use — the operator's
# call to make, not the image's.
#
# `sudo` is absent, and that is a real limitation rather than an oversight.
# BotBuildService._migrate_bot_instance runs `sudo chmod` and `sudo rsync`
# unconditionally, so the non-teclaw (arca/baas) service-bot publish path fails
# here with BotBuildMigrationError however storage is mounted. Installing
# passwordless sudo would hand UID 10001 unrestricted root and defeat running
# unprivileged at all; making that path work without sudo is a backend change,
# not an image change. Until that lands, this image serves the API and the
# teclaw publish path (non-mount delivery via BaaS, which never shells out to
# sudo), not the mount-based one.
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl git openssh-client rsync iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 admin \
    && useradd --uid 10001 --gid admin --create-home --shell /bin/bash admin

COPY --from=builder /app/.venv /app/.venv
COPY src/backend/src /app/src

# YamlConfigProvider searches ${PWD}/configs before the packaged
# agentclaw/community/configs, so this copy is the one an operator overrides:
# mount a ConfigMap over /app/configs and the packaged pair stays as the
# fallback. A mount must carry BOTH application.yaml and the profile overlay —
# the loader skips a directory that holds only one of the pair.
COPY src/backend/src/agentclaw/community/configs /app/configs

# data/: the community overlay's storage roots are relative paths
# (./data/workspace/*, ./data/objstore, ./data/oss, ./data/nas), so they resolve
# against WORKDIR and land here. Pre-create them owned by admin — the container
# runs unprivileged and cannot mkdir under a root-owned /app at request time.
# Mount a volume over /app/data for anything that must outlive the pod.
# tmp/ and ~/logs/ follow the same convention as the sibling service images.
#
# RUN ONE REPLICA unless you have configured BOTH of the following. Two things
# in the shipped community profile are per-process, and neither fails loudly
# when a second pod appears:
#   * /app/data is pod-local. workspace/*, oss, nas and the default
#     object_storage.backend "fs" all live here, so replicas diverge and a pod
#     replacement drops whatever it held. Set object_storage.backend: "s3"
#     (or mount shared storage) first.
#   * cache.redis_url defaults to empty, which CommunityCache serves with an
#     in-process lock — the overlay itself calls this "single-process only".
#     Lifecycle work that takes those locks (GitSyncService.startup,
#     SkillCenterSyncService, the task-discovery scheduler) then runs
#     concurrently in every pod, each believing it holds the lock. Set
#     REDIS_URL first.
# The deployment template defaults to 2 replicas, so a default backend rollout
# is affected today; scale out only once both are set.
RUN mkdir -p /app/data/workspace/openclaw \
             /app/data/workspace/claude_code \
             /app/data/workspace/aicoding \
             /app/data/objstore \
             /app/data/oss \
             /app/data/nas \
             /app/tmp \
             /home/admin/logs \
    && chown -R admin:admin /app /home/admin

# The service runs as admin (non-root). Root stays available for debugging —
# it is merely password-locked, and container exec needs no password:
#   kubectl exec -it <pod> -u 0 -- bash    (then apt-get install ...)
# Root password stays unset on purpose: no secret ships in image layers.
USER admin

EXPOSE 8888

# Unlike baas/gateway/proxy — whose listen port comes from module_config.web.port
# in the mounted config — the backend owns its listen address in code:
# main.py reads BACKEND_HOST (default 0.0.0.0) and BACKEND_PORT (default 8888),
# so setting BACKEND_PORT really does move the listener, and the healthcheck
# follows it. The probe path is /api/health, not the /health the other three
# serve.
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:${BACKEND_PORT:-8888}/api/health" >/dev/null || exit 1

# Required at run time:
#   DATABASE_URL   application-community.yaml sets database.url to ${DATABASE_URL}
#                  with no inline default, and placeholder expansion is strict —
#                  an unset DSN fails the boot loudly rather than quietly writing
#                  production traffic to a file inside the container:
#                    mysql+pymysql://user:pass@host:3306/agentclaw?charset=utf8mb4
#                  (database.backend is "mysql"; OceanBase in MySQL mode speaks
#                  that protocol. The provider rejects a url whose scheme
#                  disagrees with the declared backend.)
# Optional, per what the deployment turns on:
#   REDIS_URL      multi-worker cache + distributed lock; unset falls back to an
#                  in-process lock, which is only safe single-process.
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   when object_storage.backend = "s3".
#   AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE   the HMAC secret the
#                  gateway signs X-Avernet-Principal with. REQUIRED to boot when
#                  SERVER_ENV is pre|prod (app.py verifies strictly there);
#                  elsewhere its absence just 401s every /openapi/v1 request.
#   NO_PROXY_EXTRA extra hostnames mapped to loopback, appended to the NO_PROXY
#                  bypass main.py installs.
#
# `python -m` rather than the sibling images' script path, and no CMD: main.py
# takes no --mode, and ignores --config (DEPLOY_PROFILE picks the overlay), so
# there are no default arguments worth pairing with the entrypoint.
ENTRYPOINT ["python", "-m", "agentclaw.community.main"]
