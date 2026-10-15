#!/usr/bin/env bash
set -euo pipefail

# Bootstrap only the pinned release tool, never the user's bridge or configuration.
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
dist_version="$(python3 -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1],"rb"))["dist"]["cargo-dist-version"])' "$repo_root/dist-workspace.toml")"
case "$(uname -s):$(uname -m)" in
  Darwin:arm64) target=aarch64-apple-darwin ;;
  Darwin:x86_64) target=x86_64-apple-darwin ;;
  Linux:x86_64) target=x86_64-unknown-linux-gnu ;;
  *) echo "Unsupported dist bootstrap platform" >&2; exit 1 ;;
esac
tool_dir="${DIST_TOOL_DIR:?Set DIST_TOOL_DIR to a temporary tool directory}"
mkdir -p "$tool_dir"
archive="cargo-dist-$target.tar.xz"
url="https://github.com/axodotdev/cargo-dist/releases/download/v$dist_version"
curl --proto '=https' --tlsv1.2 -fLsS --connect-timeout 15 --max-time 120 --retry 3 "$url/$archive" -o "$tool_dir/$archive"
curl --proto '=https' --tlsv1.2 -fLsS --connect-timeout 15 --max-time 120 --retry 3 "$url/$archive.sha256" -o "$tool_dir/$archive.sha256"
python3 - "$tool_dir/$archive" <<'PY'
import hashlib, pathlib, re, sys
p = pathlib.Path(sys.argv[1])
line = p.with_name(p.name + ".sha256").read_text().splitlines()[0]
expected = line.split()[0]
if not re.fullmatch(r"[0-9a-f]{64}", expected) or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
    raise SystemExit("cargo-dist archive checksum mismatch")
PY
tar -xf "$tool_dir/$archive" -C "$tool_dir"
"$tool_dir/cargo-dist-$target/dist" --version
if [[ -n "${GITHUB_PATH:-}" ]]; then
  echo "$tool_dir/cargo-dist-$target" >> "$GITHUB_PATH"
fi
