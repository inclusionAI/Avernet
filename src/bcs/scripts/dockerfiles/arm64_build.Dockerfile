FROM registry.access.redhat.com/ubi9/ubi-minimal:9.5

ARG RUST_VERSION=stable
ARG PROTOC_VERSION=25.3
ARG PROTOC_URL=

ENV RUSTUP_HOME=/usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo
ENV PATH=/usr/local/cargo/bin:${PATH}

RUN microdnf install -y \
      gcc \
      make \
      git \
      pkgconf-pkg-config \
      openssl-devel \
      perl \
      findutils \
      ca-certificates \
      tar \
      gzip \
      unzip \
    && microdnf clean all

RUN set -eux; \
    url="${PROTOC_URL:-https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOC_VERSION}/protoc-${PROTOC_VERSION}-linux-aarch_64.zip}"; \
    curl -fL "${url}" -o /tmp/protoc.zip; \
    unzip /tmp/protoc.zip -d /usr/local bin/protoc 'include/*'; \
    rm -f /tmp/protoc.zip; \
    protoc --version

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y --no-modify-path --profile minimal --default-toolchain ${RUST_VERSION} \
    && rustup component add rustfmt clippy \
    && printf '[source.crates-io]\nreplace-with = "aliyun"\n\n[source.aliyun]\nregistry = "sparse+https://mirrors.aliyun.com/crates.io-index/"\n' > ${CARGO_HOME}/config.toml \
    && rustc --version \
    && cargo --version

WORKDIR /workspace

CMD ["/bin/sh"]
