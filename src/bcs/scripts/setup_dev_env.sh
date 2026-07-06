#!/usr/bin/env bash
set -euo pipefail

# One-click BCS development environment setup.
#
# Owner: 章梧
# Installs/configures prerequisites for building and testing the BCS Rust workspace:
#   1. Rust/Cargo (via rustup, stable channel; BCS does not pin a version)
#   2. cargo-nextest (test runner)
#   3. protoc (protobuf compiler; several build.rs depend on it)
#   4. cargo-llvm-cov + llvm-tools (coverage, used by ci_test.sh --coverage)
#   5. Aliyun crates.io mirror (avoids crates.io timeouts on the corp network)
#
# Usage:
#   bash src/bcs/scripts/setup_dev_env.sh           # detect + install missing + configure mirror (default)
#   bash src/bcs/scripts/setup_dev_env.sh --check   # check only, no install/no config change (read-only)
#
# Idempotent: already-installed tools and configured mirror are skipped; safe to re-run.
# Standalone (does not depend on scripts/utils.sh) so new contributors can run it right after clone.

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bcs_dir="$(cd "$script_dir/.." && pwd)"

check_only=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --check)
      check_only=1
      shift
      ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

os="$(uname -s)"
arch="$(uname -m)"

echo "========== BCS dev env setup =========="
echo "bcs_dir: $bcs_dir"
echo "os: $os ($arch)"
[[ "$check_only" -eq 1 ]] && echo "mode: CHECK ONLY (no install, no config change)"
date

have() { command -v "$1" >/dev/null 2>&1; }
missing=0

# ---- 1. Rust/Cargo ----
echo ""
echo "--- Rust/Cargo ---"
if have cargo; then
  echo "  ✓ cargo -> $(command -v cargo) ($(rustc --version 2>/dev/null || echo 'rustc ?'))"
else
  echo "  ✗ cargo not installed"
  if [[ "$check_only" -eq 1 ]]; then
    missing=1
  else
    echo "  → installing Rust stable via rustup..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
    source "${HOME}/.cargo/env"
    have cargo && echo "  ✓ cargo installed ($(cargo --version))" || { echo "  ✗ rustup install failed" >&2; exit 1; }
  fi
fi

# ---- 2. cargo-nextest ----
echo ""
echo "--- cargo-nextest ---"
if have cargo-nextest; then
  echo "  ✓ cargo-nextest -> $(command -v cargo-nextest)"
else
  echo "  ✗ cargo-nextest not installed"
  if [[ "$check_only" -eq 1 ]]; then
    missing=1
  else
    echo "  → cargo install cargo-nextest --locked ..."
    cargo install cargo-nextest --locked --quiet
    have cargo-nextest && echo "  ✓ cargo-nextest installed" || { echo "  ✗ cargo-nextest install failed" >&2; exit 1; }
  fi
fi

# ---- 3. protoc ----
echo ""
echo "--- protoc (protobuf) ---"
if have protoc; then
  echo "  ✓ protoc -> $(command -v protoc) ($(protoc --version 2>/dev/null || echo '?'))"
else
  echo "  ✗ protoc not installed"
  if [[ "$check_only" -eq 1 ]]; then
    missing=1
  else
    case "$os" in
      Darwin)
        if have brew; then
          echo "  → brew install protobuf ..."
          HOMEBREW_NO_AUTO_UPDATE=1 brew install protobuf
        else
          echo "  ✗ brew not found; install Homebrew first (https://brew.sh) then protobuf" >&2
          exit 1
        fi
        ;;
      Linux)
        if have apt-get; then
          echo "  → apt-get install -y protobuf-compiler ..."
          sudo apt-get update && sudo apt-get install -y protobuf-compiler
        elif have yum; then
          echo "  → yum install -y protobuf-compiler ..."
          sudo yum install -y protobuf-compiler
        else
          echo "  ✗ unrecognized Linux package manager (not apt/yum); install protobuf-compiler manually" >&2
          exit 1
        fi
        ;;
      *)
        echo "  ✗ unsupported OS: $os" >&2
        exit 1
        ;;
    esac
    have protoc && echo "  ✓ protoc installed" || { echo "  ✗ protoc install failed" >&2; exit 1; }
  fi
fi

# ---- 4. cargo-llvm-cov + llvm-tools (coverage) ----
# Required by ci_test.sh --coverage (cargo llvm-cov nextest --cobertura).
echo ""
echo "--- cargo-llvm-cov + llvm-tools (coverage) ---"
llvm_cov_ok=0
if have cargo-llvm-cov; then
  echo "  ✓ cargo-llvm-cov -> $(command -v cargo-llvm-cov)"
else
  echo "  ✗ cargo-llvm-cov not installed"
  if [[ "$check_only" -eq 1 ]]; then
    missing=1
  else
    echo "  → cargo install cargo-llvm-cov --locked ..."
    cargo install cargo-llvm-cov --locked --quiet
    have cargo-llvm-cov && echo "  ✓ cargo-llvm-cov installed" || { echo "  ✗ cargo-llvm-cov install failed" >&2; exit 1; }
  fi
fi
# llvm-cov requires the llvm-tools rustup component. rustup component list --installed
# prints entries with a target suffix (e.g. llvm-tools-aarch64-apple-darwin), so match
# with ^llvm-tools.
if rustup component list --installed 2>/dev/null | grep -q '^llvm-tools'; then
  echo "  ✓ llvm-tools (rustup component)"
else
  echo "  ✗ llvm-tools component not installed"
  if [[ "$check_only" -eq 1 ]]; then
    missing=1
  else
    echo "  → rustup component add llvm-tools ..."
    rustup component add llvm-tools
    rustup component list --installed 2>/dev/null | grep -q '^llvm-tools' \
      && echo "  ✓ llvm-tools installed" || { echo "  ✗ llvm-tools install failed" >&2; exit 1; }
  fi
fi

# ---- 5. Aliyun crates.io mirror ----
# Direct crates.io access times out on the corp network; the aliyun sparse mirror speeds it up.
# Idempotent: skip if already configured (contains replace-with = "aliyun"); otherwise append
# to ~/.cargo/config.toml without clobbering existing content (e.g. [net] git-fetch-with-cli).
echo ""
echo "--- cargo Aliyun mirror ---"
cargo_home="${CARGO_HOME:-${HOME}/.cargo}"
cargo_config="$cargo_home/config.toml"
marker='replace-with = "aliyun"'

if [[ -f "$cargo_config" ]] && grep -qF "$marker" "$cargo_config"; then
  echo "  ✓ aliyun mirror configured -> $cargo_config"
else
  echo "  ✗ aliyun mirror not configured"
  if [[ "$check_only" -eq 1 ]]; then
    missing=1
  else
    mkdir -p "$cargo_home"
    echo "  → writing $cargo_config ..."
    cat >> "$cargo_config" <<'EOF'

# Aliyun crates.io sparse mirror (auto-appended by BCS dev env setup)
[source.crates-io]
replace-with = "aliyun"

[source.aliyun]
registry = "sparse+https://mirrors.aliyun.com/crates.io-index/"
EOF
    if grep -qF "$marker" "$cargo_config"; then
      echo "  ✓ aliyun mirror configured -> $cargo_config"
    else
      echo "  ✗ aliyun mirror config failed" >&2
      exit 1
    fi
    # Note: individual crates may occasionally fail checksum verification on the aliyun
    # mirror (cache not synced). Temporarily comment out replace-with = "aliyun" under
    # [source.crates-io] to fall back to direct crates.io.
    echo "  note: if a crate fails checksum verification (mirror cache not synced), temporarily"
    echo "     comment out replace-with = \"aliyun\" under [source.crates-io] to use direct crates.io."
  fi
fi

# ---- Wrap up ----
echo ""
if [[ "$check_only" -eq 1 ]]; then
  if [[ "$missing" -ne 0 ]]; then
    echo "✗ environment check failed (missing items above). Re-run without --check to install/configure."
    exit 1
  fi
  echo "✓ environment check passed; all prerequisites and mirror ready."
else
  if [[ "$missing" -ne 0 ]]; then
    echo "✗ some tools/config still missing; see log above." >&2
    exit 1
  fi
  echo "✓ BCS dev environment ready."
  echo ""
  echo "Next steps:"
  echo "  build:    cd src/bcs && cargo build --workspace"
  echo "  test:     cd src/bcs && cargo nextest run --profile default --retries 0"
  echo "  coverage: bash src/bcs/scripts/ci_test.sh --coverage"
  echo "  push (triggers pre-push gate): git push origin <branch>"
fi
