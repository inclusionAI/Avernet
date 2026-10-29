#!/usr/bin/env bash
set -euo pipefail

# Install the BCS test tools from pinned, SHA-256 verified release archives.
# Usage: bash scripts/install_cargo_test_tools.sh [nextest|llvm-cov ...]
# Network/unsupported-platform failures fall back to the same source version;
# checksum mismatches fail closed before extraction or execution.
NEXTEST_VERSION="${BCS_NEXTEST_VERSION:-0.9.150}"
LLVM_COV_VERSION="${BCS_LLVM_COV_VERSION:-0.9.1}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cargo_bin_dir="${CARGO_HOME:-$HOME/.cargo}/bin"
mkdir -p "$cargo_bin_dir"
export PATH="$cargo_bin_dir:$PATH"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

host_targets=()
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) host_targets=(x86_64-unknown-linux-musl x86_64-unknown-linux-gnu) ;;
  Linux-aarch64 | Linux-arm64) host_targets=(aarch64-unknown-linux-musl aarch64-unknown-linux-gnu) ;;
  Darwin-x86_64) host_targets=(x86_64-apple-darwin) ;;
  Darwin-arm64) host_targets=(aarch64-apple-darwin) ;;
esac

fetch_verified() {
  local url="$1" version="$2" binary="$3"
  local asset="${url##*/}" expected actual extracted
  expected="$(awk -v key="$version/$asset" '$2 == key { print $1 }' "$script_dir/cargo_test_tools.sha256")"
  if [[ -z "$expected" ]]; then
    echo "  ! no pinned checksum for $version/$asset; using source fallback" >&2
    return 1
  fi
  curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --retry-delay 2 \
    --connect-timeout 10 --max-time 120 -o "$tmp/tool.tar.gz" "$url" || return 1
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$tmp/tool.tar.gz")"
  else
    actual="$(shasum -a 256 "$tmp/tool.tar.gz")"
  fi
  if [[ "${actual%% *}" != "$expected" ]]; then
    echo "checksum mismatch for $asset; refusing installation" >&2
    return 2
  fi
  rm -rf "$tmp/extracted"
  mkdir -p "$tmp/extracted"
  tar -xzf "$tmp/tool.tar.gz" -C "$tmp/extracted" || return 1
  extracted="$(find "$tmp/extracted" -type f -name "$binary" -print -quit)"
  [[ -n "$extracted" ]] || return 1
  chmod +x "$extracted"
  mv -f "$extracted" "$cargo_bin_dir/$binary"
}

install_tool() {
  local binary="$1" version="$2" target asset url status
  local targets=("${host_targets[@]+"${host_targets[@]}"}")
  if command -v "$binary" >/dev/null 2>&1 &&
     [[ "$("$binary" --version 2>/dev/null | awk 'NR == 1 { print $2 }')" == "$version" ]]; then
    echo "  ✓ $binary $version already installed"
    return 0
  fi
  # nextest publishes one universal Darwin archive, not per-architecture assets.
  if [[ "$binary" == cargo-nextest && "$(uname -s)" == Darwin ]]; then
    targets=(universal-apple-darwin)
  fi
  for target in ${targets[@]+"${targets[@]}"}; do
    if [[ "$binary" == cargo-nextest ]]; then
      asset="cargo-nextest-${version}-${target}.tar.gz"
      url="https://github.com/nextest-rs/nextest/releases/download/cargo-nextest-${version}/$asset"
    else
      asset="cargo-llvm-cov-${target}.tar.gz"
      url="https://github.com/taiki-e/cargo-llvm-cov/releases/download/v${version}/$asset"
    fi
    if fetch_verified "$url" "$version" "$binary"; then
      echo "  ✓ $binary $version installed from verified archive ($target)"
      return 0
    else
      status=$?
      [[ "$status" -ne 2 ]] || return "$status"
    fi
  done
  echo "  ! prebuilt $binary unavailable; falling back to cargo install" >&2
  cargo install "$binary" --locked --version "$version"
}

tools=("$@")
[[ "${#tools[@]}" -gt 0 ]] || tools=(nextest llvm-cov)
echo "--- installing cargo test tools ---"
for tool in "${tools[@]}"; do
  case "$tool" in
    nextest) install_tool cargo-nextest "$NEXTEST_VERSION" ;;
    llvm-cov) install_tool cargo-llvm-cov "$LLVM_COV_VERSION" ;;
    *) echo "unknown tool: $tool (expected nextest or llvm-cov)" >&2; exit 2 ;;
  esac
done
