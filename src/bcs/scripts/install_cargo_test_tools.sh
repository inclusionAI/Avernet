#!/usr/bin/env bash
set -euo pipefail

# Install the cargo subcommands the BCS test gates need, from prebuilt release
# binaries instead of `cargo install`.
#
# Owner: 章梧
#
# Why this exists: `cargo install cargo-nextest --locked` plus
# `cargo install cargo-llvm-cov --locked` builds ~380 crates from source. On a
# 4-core `ubuntu-latest` runner that is ~215s per job — a third of the whole
# `BCS unit tests` wall clock — spent producing two binaries that upstream
# already publishes for this exact target. Downloading them takes a few seconds.
#
# Both projects publish per-target release tarballs on GitHub:
#   cargo-nextest   https://github.com/nextest-rs/nextest/releases
#   cargo-llvm-cov  https://github.com/taiki-e/cargo-llvm-cov/releases
#
# Usage:
#   bash scripts/install_cargo_test_tools.sh                # nextest + llvm-cov
#   bash scripts/install_cargo_test_tools.sh nextest        # just nextest
#   bash scripts/install_cargo_test_tools.sh llvm-cov       # just llvm-cov
#
# Versions are pinned so CI is reproducible; bump them here deliberately. If a
# download fails for any reason — release-asset outage, a renamed asset, an
# unrecognised platform — the script falls back to `cargo install --locked` at
# the same pinned version. A failed download therefore costs the time it used
# to cost, and never breaks the gate.

NEXTEST_VERSION="${BCS_NEXTEST_VERSION:-0.9.143}"
LLVM_COV_VERSION="${BCS_LLVM_COV_VERSION:-0.8.7}"

cargo_bin_dir="${CARGO_HOME:-$HOME/.cargo}/bin"
mkdir -p "$cargo_bin_dir"

# Both projects name their assets by target triple, but not always with the same
# libc flavour available, so each host maps to the triples to try in order.
# An unrecognised host leaves the lists empty and takes the cargo-install path.
host_targets=()
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)
    host_targets=(x86_64-unknown-linux-musl x86_64-unknown-linux-gnu)
    ;;
  Linux-aarch64 | Linux-arm64)
    host_targets=(aarch64-unknown-linux-musl aarch64-unknown-linux-gnu)
    ;;
  Darwin-x86_64)
    host_targets=(x86_64-apple-darwin)
    ;;
  Darwin-arm64)
    host_targets=(aarch64-apple-darwin)
    ;;
esac

# Download $1, extract it, and move the binary named $2 into the cargo bin dir.
# Returns non-zero on any failure so callers can fall back to cargo install.
fetch_tarball() {
  local url="$1"
  local binary="$2"
  local tmp
  tmp="$(mktemp -d)"
  # shellcheck disable=SC2064  # expand $tmp now, not at trap time
  trap "rm -rf '$tmp'" RETURN

  curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --retry-delay 2 \
    -o "$tmp/tool.tar.gz" "$url" || return 1
  tar -xzf "$tmp/tool.tar.gz" -C "$tmp" || return 1

  # Tarball layouts differ (flat vs nested), so locate the binary rather than
  # assuming a path.
  local extracted
  extracted="$(find "$tmp" -type f -name "$binary" -print -quit)"
  [[ -n "$extracted" ]] || return 1
  chmod +x "$extracted"
  mv -f "$extracted" "$cargo_bin_dir/$binary"
}

# install_prebuilt <binary> <version> <url-template-with-{target}-placeholder>
# Tries each host target in turn; returns non-zero if none produced a binary.
install_prebuilt() {
  local binary="$1"
  local version="$2"
  local url_template="$3"
  local target
  for target in ${host_targets[@]+"${host_targets[@]}"}; do
    if fetch_tarball "${url_template//\{target\}/$target}" "$binary"; then
      echo "  ✓ ${binary} ${version} installed from prebuilt binary (${target})"
      return 0
    fi
  done
  return 1
}

install_nextest() {
  if command -v cargo-nextest >/dev/null 2>&1; then
    echo "  ✓ cargo-nextest already present -> $(command -v cargo-nextest)"
    return 0
  fi
  if install_prebuilt cargo-nextest "$NEXTEST_VERSION" \
       "https://github.com/nextest-rs/nextest/releases/download/cargo-nextest-${NEXTEST_VERSION}/cargo-nextest-${NEXTEST_VERSION}-{target}.tar.gz"; then
    return 0
  fi
  echo "  ! prebuilt cargo-nextest unavailable; falling back to cargo install" >&2
  cargo install cargo-nextest --locked --version "$NEXTEST_VERSION"
}

install_llvm_cov() {
  if command -v cargo-llvm-cov >/dev/null 2>&1; then
    echo "  ✓ cargo-llvm-cov already present -> $(command -v cargo-llvm-cov)"
    return 0
  fi
  if install_prebuilt cargo-llvm-cov "$LLVM_COV_VERSION" \
       "https://github.com/taiki-e/cargo-llvm-cov/releases/download/v${LLVM_COV_VERSION}/cargo-llvm-cov-{target}.tar.gz"; then
    return 0
  fi
  echo "  ! prebuilt cargo-llvm-cov unavailable; falling back to cargo install" >&2
  cargo install cargo-llvm-cov --locked --version "$LLVM_COV_VERSION"
}

targets=("$@")
if [[ "${#targets[@]}" -eq 0 ]]; then
  targets=(nextest llvm-cov)
fi

echo "--- installing cargo test tools ---"
for target in "${targets[@]}"; do
  case "$target" in
    nextest) install_nextest ;;
    llvm-cov) install_llvm_cov ;;
    *)
      echo "unknown tool: $target (expected 'nextest' or 'llvm-cov')" >&2
      exit 2
      ;;
  esac
done
