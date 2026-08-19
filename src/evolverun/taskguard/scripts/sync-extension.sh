#!/usr/bin/env bash
# Sync built ClawMind to openclaw extension directory.
# Preserves openclaw.plugin.json and skills/ that are not in dist/esm.
set -euo pipefail

EXT_DIR="$HOME/.openclaw/extensions/clawmind"
SOURCE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$SOURCE_ROOT/dist/esm"

if [ ! -f "$DIST_DIR/index.js" ]; then
  echo "❌ Build output not found at $DIST_DIR/index.js. Run 'npm run build' first."
  exit 1
fi

mkdir -p "$EXT_DIR"

# Back up files that rsync --delete would remove
BACKUP_DIR=$(mktemp -d)
PRESERVED_FILES="openclaw.plugin.json package.json"
PRESERVED_DIRS="skills configs"

for f in $PRESERVED_FILES; do
  if [ -f "$EXT_DIR/$f" ]; then
    cp "$EXT_DIR/$f" "$BACKUP_DIR/$f"
  fi
done
for d in $PRESERVED_DIRS; do
  if [ -d "$EXT_DIR/$d" ]; then
    mkdir -p "$BACKUP_DIR/$d"
    cp -r "$EXT_DIR/$d/" "$BACKUP_DIR/$d/" 2>/dev/null || true
  fi
done

# Sync dist to extension (with --delete to keep clean)
rsync -av --delete "$DIST_DIR/" "$EXT_DIR/"

# Restore preserved files
for f in $PRESERVED_FILES; do
  if [ -f "$BACKUP_DIR/$f" ]; then
    cp "$BACKUP_DIR/$f" "$EXT_DIR/$f"
  fi
done

# Copy plugin manifest from source if not present
if [ ! -f "$EXT_DIR/openclaw.plugin.json" ] && [ -f "$SOURCE_ROOT/openclaw.plugin.json" ]; then
  cp "$SOURCE_ROOT/openclaw.plugin.json" "$EXT_DIR/openclaw.plugin.json"
fi

# Copy skills from source (generated during build) or restore backed-up skills
SOURCE_SKILLS="$SOURCE_ROOT/skills"
if [ -d "$SOURCE_SKILLS" ]; then
  cp -r "$SOURCE_SKILLS/" "$EXT_DIR/skills/"
elif [ -d "$BACKUP_DIR/skills" ]; then
  mkdir -p "$EXT_DIR/skills"
  cp -r "$BACKUP_DIR/skills/"* "$EXT_DIR/skills/" 2>/dev/null || true
fi

# Copy configs from source (contains application.yaml with database mode etc.)
SOURCE_CONFIGS="$SOURCE_ROOT/configs"
if [ -d "$SOURCE_CONFIGS" ]; then
  mkdir -p "$EXT_DIR/configs"
  cp -r "$SOURCE_CONFIGS/" "$EXT_DIR/configs/"
elif [ -d "$BACKUP_DIR/configs" ]; then
  mkdir -p "$EXT_DIR/configs"
  cp -r "$BACKUP_DIR/configs/"* "$EXT_DIR/configs/" 2>/dev/null || true
fi

# Fix package.json entry points: rsync flattens dist/esm/* into EXT_DIR/,
# so all references to ./dist/esm/ must be rewritten to ./
if [ -f "$EXT_DIR/package.json" ]; then
  CHANGED=0
  # Fix "main" field
  if grep -q '"main": "./dist/esm/index.js"' "$EXT_DIR/package.json" 2>/dev/null; then
    sed -i.bak 's|"main": "./dist/esm/index.js"|"main": "./index.js"|' "$EXT_DIR/package.json"
    CHANGED=1
  fi
  # Fix "module" field
  if grep -q '"module": "./dist/esm/index.js"' "$EXT_DIR/package.json" 2>/dev/null; then
    sed -i.bak 's|"module": "./dist/esm/index.js"|"module": "./index.js"|' "$EXT_DIR/package.json"
    CHANGED=1
  fi
  # Fix "types" field
  if grep -q '"types": "./dist/esm/index.d.ts"' "$EXT_DIR/package.json" 2>/dev/null; then
    sed -i.bak 's|"types": "./dist/esm/index.d.ts"|"types": "./index.d.ts"|' "$EXT_DIR/package.json"
    CHANGED=1
  fi
  # Fix "openclaw.extensions" array entries
  if grep -q '"./dist/esm/index.js"' "$EXT_DIR/package.json" 2>/dev/null; then
    sed -i.bak 's|"./dist/esm/index.js"|"./index.js"|g' "$EXT_DIR/package.json"
    CHANGED=1
  fi
  # Fix "exports" nested paths (default, types, source)
  if grep -q '"./dist/esm/' "$EXT_DIR/package.json" 2>/dev/null; then
    sed -i.bak 's|"./dist/esm/|"./|g' "$EXT_DIR/package.json"
    CHANGED=1
  fi
  rm -f "$EXT_DIR/package.json.bak"
  if [ "$CHANGED" -eq 1 ]; then
    echo "   Fixed package.json paths (dist/esm → flat)"
  fi
fi

# Clean up
rm -rf "$BACKUP_DIR"

echo "✅ Synced $DIST_DIR → $EXT_DIR"
echo "   Preserved: $PRESERVED_FILES $PRESERVED_DIRS"