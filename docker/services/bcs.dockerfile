########## Builder ##########
FROM rust:1.91-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_TERM_COLOR=never \
    CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse

WORKDIR /build

# Aliyun apt mirror (matches the gateway/baas builder convention).
# native-tls (tokio-tungstenite/mysql_async) needs OpenSSL; rusqlite is *not*
# bundled, so both need their dev headers at build time.
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        pkg-config libssl-dev libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Aliyun crates.io mirror (sparse index) so `cargo fetch` / `cargo build` are
# not blocked pulling the crates.io index from the public internet. Written to
# $CARGO_HOME/config.toml in its own layer before the source COPY so it survives
# across source changes. The sparse+ URL keeps the fast sparse protocol.
RUN mkdir -p /usr/local/cargo \
    && printf '[source.crates-io]\nreplace-with="aliyun"\n[source.aliyun]\nregistry="sparse+https://mirrors.aliyun.com/crates.io-index/"\n' \
       > /usr/local/cargo/config.toml

# "Pull code": the BCS Rust workspace is brought in from the build context
# (repo root). docker/build-image.sh sends the repo root as the context, so
# this COPY is the code-fetch step — there is no in-image git clone. The
# workspace is self-contained under src/bcs (no path deps outside it).
COPY src/bcs /build/src/bcs

# Prefetch dependencies into the registry cache mount. BuildKit retains the
# cache across builds, so repeated builds reuse already-downloaded crates even
# though the `COPY src/bcs` layer above busts on any source change.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    cargo fetch --locked --manifest-path /build/src/bcs/Cargo.toml

# Build the `bcs` server binary (release, pinned to the checked-in Cargo.lock).
# The target dir is a cache mount, which is NOT retained in the layer after the
# RUN — so copy the produced binary to a stable path and strip it within the
# same RUN while the cache mount is still mounted.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/src/bcs/target \
    cargo build --release --locked --manifest-path /build/src/bcs/Cargo.toml --bin bcs \
    && cp /build/src/bcs/target/release/bcs /build/bcs \
    && strip /build/bcs

########## Runtime ##########
FROM debian:bookworm-slim AS runtime

# BCS_CONFIG_DIR points the binary at its config directory (clap reads this env
# directly). The public deployment example is installed as the base config;
# deployments can mount bcs-config-{env}.toml to override it.
ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/home/admin \
    BCS_CONFIG_DIR=/app/configs

WORKDIR /app

RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl libssl3 libsqlite3-0 iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 admin \
    && useradd --uid 10001 --gid admin --create-home --shell /bin/bash admin

COPY --from=builder /build/bcs /usr/local/bin/bcs
COPY src/bcs/configs/bcs-config-example.toml /app/configs/bcs-config.toml

# Persistence: bots_base_dir and the session-file data_dir must live on a
# writable volume. The shipped example config uses /var/lib/bcs; mount a
# PVC/emptyDir here and point the config's data paths at it.
RUN mkdir -p /app/tmp /var/lib/bcs /home/admin/logs \
    && chown -R admin:admin /app /var/lib/bcs /home/admin

# The service runs as admin (non-root). Root stays available for debugging —
# it is merely password-locked, and container exec needs no password:
#   kubectl exec -it <pod> -u 0 -- bash    (then apt-get install ...)
# Root password stays unset on purpose: no secret ships in image layers.
USER admin

EXPOSE 21000

# BCS reads its listen address from the mounted config (`bind`/`port`), not
# from an env var. The healthcheck targets 21000 — keep it in sync with the
# config's port.
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:21000/health" >/dev/null || exit 1

# clap picks up BCS_CONFIG_DIR from the environment; set SERVER_ENV and mount an
# environment-specific config when the deployment needs to override the base.
ENTRYPOINT ["bcs"]
