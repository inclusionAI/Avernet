#!/usr/bin/env bash
# pack-tgz.sh — Package ClawMind as a standalone tgz installable on remote openclaw instances.
#
# Usage:
#   ./scripts/pack-tgz.sh              # Build + pack
#   ./scripts/pack-tgz.sh --skip-build # Skip build, only pack existing dist/
#
# Output: <project-root>/clawmind-<version>.tgz
#
# What the tgz contains (package/ prefix, npm pack compatible):
#   - package/openclaw.plugin.json   → plugin manifest
#   - package/dist/esm/              → compiled + bundled runtime
#   - package/configs/               → application config (application.yaml)
#   - package/node_modules/          → bundled dependencies (cron-parser, yaml, zod)
#
# NOTE: packs/ is NOT included — workflow packs are managed separately

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STAGING_DIR="$ROOT_DIR/.pack-staging"

# Parse args
SKIP_BUILD=false
if [[ "${1:-}" == "--skip-build" ]]; then
  SKIP_BUILD=true
fi

cd "$ROOT_DIR"

# Read version from package.json
VERSION=$(node -e 'console.log(JSON.parse(require("fs").readFileSync("package.json","utf8")).version)')
PKG_NAME="@avernet/taskguard"
OUTPUT_FILE="$ROOT_DIR/taskguard-${VERSION}.tgz"

echo "=== taskguard Pack TGZ ==="
echo "Package: $PKG_NAME"
echo "Version: $VERSION"
echo "Output:  $OUTPUT_FILE"
echo ""

# ─── Step 1: Build ───────────────────────────────────────────────
if [[ "$SKIP_BUILD" == false ]]; then
  echo "[1/5] Building project..."
  npm run build
  echo ""
else
  echo "[1/5] Skipping build (--skip-build)"
  if [[ ! -f dist/esm/index.js ]]; then
    echo "ERROR: dist/esm/index.js not found. Run without --skip-build first."
    exit 1
  fi
fi

# ─── Step 2: Facade skills (skipped) ─────────────────────────────
echo "[2/5] Skipping facade skills generation (skills not included in tgz)"
echo ""

# ─── Step 3: Prepare staging directory ──────────────────────────
echo "[3/5] Preparing staging directory..."
rm -rf "$STAGING_DIR"
PKG_DIR="$STAGING_DIR/clawmind"
mkdir -p "$PKG_DIR"

# Copy compiled output (only esm/, not node_modules which has symlinks)
mkdir -p "$PKG_DIR/dist/esm"
cp -r dist/esm/ "$PKG_DIR/dist/esm/"
cp dist/package.json "$PKG_DIR/dist/" 2>/dev/null || true
echo "  Copied dist/esm/"

# Copy plugin manifest
cp openclaw.plugin.json "$PKG_DIR/"
echo "  Copied openclaw.plugin.json"

# Copy docs
cp README.md "$PKG_DIR/" 2>/dev/null || true

# NOTE: packs/ is NOT copied — workflow packs are managed separately

# Copy configs (application.yaml, etc.)
if [[ -d configs ]]; then
  cp -r configs/ "$PKG_DIR/configs/"
  echo "  Copied configs/ ($(ls configs/ | wc -l | tr -d ' ') files)"
fi

# Copy install-clawmind.sh — needed by the `clawmind update` tool at runtime
if [[ -f scripts/install-clawmind.sh ]]; then
  mkdir -p "$PKG_DIR/scripts"
  cp scripts/install-clawmind.sh "$PKG_DIR/scripts/"
  echo "  Copied scripts/install-clawmind.sh"
fi

# Copy examples if exists
if [[ -d examples ]]; then
  cp -r examples/ "$PKG_DIR/examples/"
fi
echo ""

# ─── Step 4: Bundle dependencies ────────────────────────────────
echo "[4/5] Bundling dependencies (cron-parser, yaml, zod)..."

BUNDLE_DEPS=$(node -e 'const pkg=JSON.parse(require("fs").readFileSync("package.json","utf8"));console.log((pkg.bundleDependencies||[]).join(" "))')

mkdir -p "$PKG_DIR/node_modules"

for dep in $BUNDLE_DEPS; do
  if [[ -d "node_modules/$dep" ]]; then
    cp -r "node_modules/$dep" "$PKG_DIR/node_modules/"
    echo "  Bundled: $dep"
  else
    echo "  WARNING: $dep not found in node_modules, skipping"
  fi
done
echo ""

# ─── Step 5: Write package.json and create tgz ──────────────────
echo "[5/5] Creating tgz..."

# Write a clean package.json for the tarball
node -e '
  const fs = require("fs");
  const stagingDir = process.argv[1];
  const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));

  const tarballPkg = {
    name: pkg.name,
    version: pkg.version,
    description: pkg.description,
    type: pkg.type,
    main: "./dist/esm/index.js",
    module: "./dist/esm/index.js",
    types: "./dist/esm/index.d.ts",
    exports: {
      ".": {
        import: {
          types: "./dist/esm/index.d.ts",
          default: "./dist/esm/index.js",
        },
      },
    },
    openclaw: pkg.openclaw,
    files: [
      "openclaw.plugin.json",
      "dist/",
      "configs/",
      "scripts/",
      "node_modules/",
    ],
    bundleDependencies: pkg.bundleDependencies,
    keywords: pkg.keywords,
    license: pkg.license,
  };

  fs.writeFileSync(stagingDir + "/clawmind/package.json", JSON.stringify(tarballPkg, null, 2) + "\n");
  console.log("  Written clawmind/package.json");
' "$STAGING_DIR"

# Create tarball
rm -f "$OUTPUT_FILE"
cd "$STAGING_DIR"
tar -czf "$OUTPUT_FILE" clawmind/
cd "$ROOT_DIR"

# Cleanup staging
rm -rf "$STAGING_DIR"

# Verify
if [[ -f "$OUTPUT_FILE" ]]; then
  SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)
  echo ""
  echo "=== Done ==="
  echo "Output: $OUTPUT_FILE ($SIZE)"
  echo ""
  echo "Install on remote openclaw:"
  echo "  openclaw plugin install $OUTPUT_FILE"
  echo "  # or"
  echo "  npm install $OUTPUT_FILE"
  echo ""
  echo "Verify contents:"
  echo "  tar -tzf $OUTPUT_FILE | head -30"
else
  echo "ERROR: Failed to create tgz"
  exit 1
fi