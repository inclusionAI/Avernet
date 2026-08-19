#!/usr/bin/env bash
# ClawMind for Claude Code — Pack Script
#
# Builds a Claude Code Plugin package from the compiled ClawMind dist.
# The resulting package can be installed via the accompanying install.sh.
#
# Usage:
#   ./pack.sh                    # Build and pack into dist_pack/claudecode/
#   ./pack.sh --skip-build       # Skip npm run build (use existing dist/)
#
# Output:
#   dist_pack/claudecode/clawmind-claudecode-<version>.gz
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST_PACK_DIR="$ROOT_DIR/dist_pack/claudecode"

# ── Read version from package.json ──
VERSION=$(node -e "console.log(require('$ROOT_DIR/package.json').version)")

# ── Colors ──
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[pack:claudecode]${NC} $*"; }
warn() { echo -e "${YELLOW}[pack:claudecode]${NC} $*"; }

# ── Parse args ──
SKIP_BUILD=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --skip-build) SKIP_BUILD=true; shift ;;
    --help|-h)    echo "Usage: $0 [--skip-build]"; exit 0 ;;
    *)            warn "Unknown option: $1"; shift ;;
  esac
done

# ── Step 1: Build ──
if [[ "$SKIP_BUILD" == false ]]; then
  info "Building ClawMind (npm run build)..."
  cd "$ROOT_DIR"
  npm run build
  info "Build complete."
else
  info "Skipping build (--skip-build)."
fi

# Verify dist/ exists
if [[ ! -d "$ROOT_DIR/dist/esm" ]]; then
  echo "ERROR: dist/esm/ not found. Run without --skip-build or run 'npm run build' first." >&2
  exit 1
fi

# ── Step 2: Prepare staging directory ──
STAGING="$ROOT_DIR/.pack-staging-claudecode"
rm -rf "$STAGING"
PKG_DIR="$STAGING/package"
mkdir -p "$PKG_DIR"

info "Staging directory: $STAGING"

# ── Step 3: Copy compiled output ──
info "Copying dist/..."
mkdir -p "$PKG_DIR/dist"
cp -r "$ROOT_DIR/dist/esm" "$PKG_DIR/dist/esm"
if [[ -f "$ROOT_DIR/dist/package.json" ]]; then
  cp "$ROOT_DIR/dist/package.json" "$PKG_DIR/dist/package.json"
fi

# ── Step 4: Copy plugin structure ──
PLUGIN_SRC="$ROOT_DIR/clawmind-plugin"
if [[ -d "$PLUGIN_SRC" ]]; then
  info "Copying clawmind-plugin/..."
  # Skip dev-only symlinks that point back to the project root and cause cycles:
  #   dist, packs, configs, node_modules — their content is copied in other steps.
  SKIP_SYMLINKS="dist packs configs node_modules"
  mkdir -p "$PKG_DIR/clawmind-plugin"
  for item in "$PLUGIN_SRC"/*; do
    basename_item="$(basename "$item")"
    if [[ -L "$item" ]] && echo " $SKIP_SYMLINKS " | grep -q " $basename_item "; then
      info "  Skipping dev symlink: $basename_item → $(readlink "$item")"
      continue
    fi
    if [[ -L "$item" ]]; then
      # Resolve other symlinks: copy the actual target content
      info "  Resolving symlink: $basename_item → $(readlink "$item")"
      cp -rL "$item" "$PKG_DIR/clawmind-plugin/$basename_item"
    else
      cp -r "$item" "$PKG_DIR/clawmind-plugin/$basename_item"
    fi
  done
  # Also copy hidden files like .claude-plugin
  for item in "$PLUGIN_SRC"/.*; do
    basename_item="$(basename "$item")"
    [[ "$basename_item" == "." || "$basename_item" == ".." ]] && continue
    if [[ -L "$item" ]]; then
      cp -rL "$item" "$PKG_DIR/clawmind-plugin/$basename_item"
    else
      cp -r "$item" "$PKG_DIR/clawmind-plugin/$basename_item"
    fi
  done
else
  warn "clawmind-plugin/ not found — plugin structure will be missing!"
fi

# ── Step 4.3: Merge generated facade skills and commands ──
# npm run build generates dynamic facade skills/ and commands/ in the project root
# (from packs' facade definitions). These are NOT in clawmind-plugin/ — they must
# be copied into the plugin's skills/ and commands/ directories for Claude Code
# to discover them as /clawmind:<facade-name> slash commands.
FACADE_SKILLS_SRC="$ROOT_DIR/skills"
FACADE_COMMANDS_SRC="$ROOT_DIR/commands"
PLUGIN_SKILLS_DIR="$PKG_DIR/clawmind-plugin/skills"
PLUGIN_COMMANDS_DIR="$PKG_DIR/clawmind-plugin/commands"

if [[ -d "$FACADE_SKILLS_SRC" ]]; then
  FACADE_COUNT=0
  for skill_dir in "$FACADE_SKILLS_SRC"/*/; do
    [[ -d "$skill_dir" ]] || continue
    skill_name="$(basename "$skill_dir")"
    # Skip skills already in clawmind-plugin (workflow-dispatch, workflow-help)
    if [[ -d "$PLUGIN_SKILLS_DIR/$skill_name" ]]; then
      continue
    fi
    mkdir -p "$PLUGIN_SKILLS_DIR/$skill_name"
    cp -r "$skill_dir"* "$PLUGIN_SKILLS_DIR/$skill_name/"
    FACADE_COUNT=$((FACADE_COUNT + 1))
  done
  if [[ $FACADE_COUNT -gt 0 ]]; then
    info "Merged $FACADE_COUNT facade skills into clawmind-plugin/skills/"
  else
    # Fallback: copy all if loop didn't work (e.g. glob issue)
    for skill_dir in "$FACADE_SKILLS_SRC"/*/; do
      [[ -d "$skill_dir" ]] || continue
      skill_name="$(basename "$skill_dir")"
      if [[ ! -d "$PLUGIN_SKILLS_DIR/$skill_name" ]]; then
        mkdir -p "$PLUGIN_SKILLS_DIR/$skill_name"
        cp -r "$skill_dir"* "$PLUGIN_SKILLS_DIR/$skill_name/"
        FACADE_COUNT=$((FACADE_COUNT + 1))
      fi
    done
    info "Merged $FACADE_COUNT facade skills into clawmind-plugin/skills/ (fallback)"
  fi
fi

if [[ -d "$FACADE_COMMANDS_SRC" ]]; then
  CMD_COUNT=0
  for cmd_file in "$FACADE_COMMANDS_SRC"/*.md; do
    [[ -f "$cmd_file" ]] || continue
    cmd_name="$(basename "$cmd_file")"
    if [[ ! -f "$PLUGIN_COMMANDS_DIR/$cmd_name" ]]; then
      cp "$cmd_file" "$PLUGIN_COMMANDS_DIR/$cmd_name"
      CMD_COUNT=$((CMD_COUNT + 1))
    fi
  done
  if [[ $CMD_COUNT -gt 0 ]]; then
    info "Merged $CMD_COUNT facade commands into clawmind-plugin/commands/"
  fi
fi

# ── Step 4.5: Copy Claude Code plugin manifest ──
# This is the contract that tells Claude Code which MCP tools are available.
# Without it, tools like workflow_recent_events won't be registered.
if [[ -f "$ROOT_DIR/claudecode.plugin.json" ]]; then
  info "Copying claudecode.plugin.json..."
  cp "$ROOT_DIR/claudecode.plugin.json" "$PKG_DIR/claudecode.plugin.json"
  # Also place inside clawmind-plugin/ for skills-dir installs
  cp "$ROOT_DIR/claudecode.plugin.json" "$PKG_DIR/clawmind-plugin/claudecode.plugin.json"
else
  warn "claudecode.plugin.json not found — tool contracts will be missing!"
fi

# ── Step 5: Copy configs (packs/ intentionally excluded) ──
# NOTE: We deliberately skip packing $ROOT_DIR/packs/ into the Claude Code
# tarball. Reasons:
#   1. packs/ is large and primarily consumed by the OpenClaw/TeClaw runtime;
#      the Claude Code MCP server loads skills via SKILL_ROOT (see mcp-config.json)
#      and does not need them pre-bundled.
#   2. The clawmind-plugin/packs symlink (→ ../../packs) is already excluded
#      by SKIP_SYMLINKS in Step 4 to avoid cycles.
if [[ -d "$ROOT_DIR/configs" ]]; then
  info "Copying configs/..."
  cp -r "$ROOT_DIR/configs" "$PKG_DIR/configs"

  # ── Step 5.1: Patch teclaw.enabled for Claude Code ──
  # Step 5 copies from root configs/ (teclaw.enabled=true for OpenClaw/TeClaw),
  # which overwrites the clawmind-plugin/configs/ copy from Step 4.
  # Re-patch to ensure teclaw.enabled=false for Claude Code.
  # NOTE: clawmind-plugin/configs/ is a standalone copy (not a symlink) with
  # teclaw.enabled=false for local dev; this patch ensures the .gz package is correct too.
  APP_YAML="$PKG_DIR/configs/application.yaml"
  if [[ -f "$APP_YAML" ]]; then
    node -e '
      const fs = require("fs");
      const f = process.argv[1];
      let yaml = fs.readFileSync(f, "utf8");
      yaml = yaml.replace(/^(teclaw:\s*\n(?:\s*#[^\n]*\n)*\s*)enabled: true/m, "$1enabled: false");
      yaml = yaml.replace(/^(\s*asyncRun:)\s*true/m, "$1 false");
      fs.writeFileSync(f, yaml);
    ' "$APP_YAML"
    info "Patched configs/application.yaml: teclaw.enabled → false, asyncRun → false (Claude Code package)"
  fi

  # ── Step 5.2: Patch dist/esm/configs/application.yaml ──
  # The build process copies configs/ into dist/esm/configs/ (via copy-runtime-assets.mjs).
  # findConfigFile() walk-up from import.meta.dirname hits dist/esm/package.json +
  # dist/esm/configs/application.yaml BEFORE reaching the package root — so this file
  # is the one actually loaded at runtime. If teclaw.enabled=true here, the MCP server
  # will try to connect to TeClaw WebSocket (ws://127.0.0.1:8080) and fail with
  # ECONNREFUSED in Claude Code environments where TeClaw doesn't exist.
  DIST_ESM_YAML="$PKG_DIR/dist/esm/configs/application.yaml"
  if [[ -f "$DIST_ESM_YAML" ]]; then
    node -e '
      const fs = require("fs");
      const f = process.argv[1];
      let yaml = fs.readFileSync(f, "utf8");
      yaml = yaml.replace(/^(teclaw:\s*\n(?:\s*#[^\n]*\n)*\s*)enabled: true/m, "$1enabled: false");
      yaml = yaml.replace(/^(\s*asyncRun:)\s*true/m, "$1 false");
      fs.writeFileSync(f, yaml);
    ' "$DIST_ESM_YAML"
    info "Patched dist/esm/configs/application.yaml: teclaw.enabled → false, asyncRun → false (Claude Code package)"
  fi
fi

# ── Step 5.5: Generate root-level SKILL.md ──
# Claude Code skills-dir discovery requires SKILL.md at the root of the skill directory.
# Without it, Claude Code cannot find or register the skill.
info "Generating root-level SKILL.md..."
cat > "$PKG_DIR/SKILL.md" << 'SKILL_MD'
---
name: clawmind
description: YAML-based DAG workflow orchestration engine for Claude Code
---

# ClawMind

YAML-based DAG workflow orchestration engine for Claude Code.

## Usage

Use `/clawmind:help` to see available workflow commands, or call MCP tools directly:
- `workflow_engine_dispatch` — Run, manage, and query workflows
- `workflow_state` — Query running flow state
- `workflow_flows` — List flows
- `workflow_recent_events` — Poll workflow progress

Typical: `/kf-direct <question>` for knowledge-base direct support.
SKILL_MD

# ── Step 6: Copy README ──
if [[ -f "$ROOT_DIR/README.md" ]]; then
  cp "$ROOT_DIR/README.md" "$PKG_DIR/README.md"
fi

# ── Step 7: Bundle node_modules ──
# Copy bundled dependencies from the root node_modules, including their
# FULL transitive dependency trees. This is essential because zod and
# @modelcontextprotocol/sdk are external in the esbuild bundle (zod v4's
# $constructor/__esm pattern breaks when bundled into a single file), so
# they must be resolvable from node_modules/ at runtime — including all
# their own dependencies (raw-body, http-errors, express, etc.).
if [[ -d "$ROOT_DIR/node_modules" ]]; then
  info "Bundling node_modules/ (with transitive deps)..."

  # Use Node.js to walk the full transitive dependency tree starting from
  # bundleDependencies. This ensures we copy @modelcontextprotocol/sdk's
  # 90+ transitive deps (raw-body → bytes/http-errors → depd/statuses etc.)
  # not just the top-level entries.
  DEP_LIST=$(node -e "
    const fs = require('fs');
    const path = require('path');
    const rootDir = process.argv[1];
    const pkg = JSON.parse(fs.readFileSync(path.join(rootDir, 'package.json'), 'utf8'));
    const bundleDeps = pkg.bundleDependencies || [];

    const visited = new Set();
    function walk(pkgName) {
      if (visited.has(pkgName)) return;
      visited.add(pkgName);
      const pkgPath = path.join(rootDir, 'node_modules', pkgName, 'package.json');
      if (!fs.existsSync(pkgPath)) return;
      const p = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
      for (const dep of Object.keys(p.dependencies || {})) {
        if (fs.existsSync(path.join(rootDir, 'node_modules', dep, 'package.json'))) {
          walk(dep);
        }
      }
    }
    for (const dep of bundleDeps) walk(dep);
    console.log([...visited].join('\n'));
  " "$ROOT_DIR" 2>/dev/null || true)

  if [[ -n "$DEP_LIST" ]]; then
    mkdir -p "$PKG_DIR/node_modules"
    COPIED=0
    while IFS= read -r dep; do
      [[ -z "$dep" ]] && continue
      if [[ -d "$ROOT_DIR/node_modules/$dep" ]]; then
        dep_parent="$(dirname "$PKG_DIR/node_modules/$dep")"
        mkdir -p "$dep_parent"
        cp -r "$ROOT_DIR/node_modules/$dep" "$PKG_DIR/node_modules/$dep"
        COPIED=$((COPIED + 1))
      else
        warn "  Missing: $dep (not found in node_modules)"
      fi
    done <<< "$DEP_LIST"
    info "  Bundled $COPIED packages (bundleDependencies + transitive deps)"
  else
    # No bundleDependencies defined — copy all production deps
    warn "No bundleDependencies defined. Copying all node_modules/..."
    cp -r "$ROOT_DIR/node_modules" "$PKG_DIR/node_modules"
  fi
fi

# ── Step 8: Generate mcp-config.json (legacy mode fallback) ──
info "Generating mcp-config.json..."
cat > "$PKG_DIR/mcp-config.json" << 'MCP_CONFIG'
{
  "mcpServers": {
    "clawmind": {
      "command": "node",
      "args": ["./dist/esm/platform/mcp-entry.js"],
      "description": "ClawMind workflow engine — YAML DAG orchestration via MCP",
      "env": {
        "SKILL_ROOT": "./packs",
        "DATABASE_MODE": "sqlite",
        "SQLITE_PATH": "~/.openclaw/workflow/engine.db",
        "MCP_TRANSPORT": "stdio",
        "CCT_SOP_MCP_SERVER_MODE": "local",
        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
        "CLAUDE_CODE_EXECUTABLE": "${CLAUDE_CODE_EXECUTABLE}"
      }
    }
  }
}
MCP_CONFIG

# ── Step 9: Copy install.sh ──
INSTALL_SCRIPT_SRC="$SCRIPT_DIR/install.sh"
if [[ -f "$INSTALL_SCRIPT_SRC" ]]; then
  info "Copying install.sh from scripts/claudecode/..."
  cp "$INSTALL_SCRIPT_SRC" "$PKG_DIR/install.sh"
  chmod +x "$PKG_DIR/install.sh"
else
  warn "install.sh not found at $SCRIPT_DIR/install.sh — package will have NO installer!"
fi

# ── Step 10: Generate package.json for the tarball ──
info "Generating package.json..."
cat > "$PKG_DIR/package.json" << PKG_JSON
{
  "name": "@alipay/clawmind-claudecode",
  "version": "${VERSION}",
  "description": "ClawMind — YAML-based DAG workflow orchestration engine for Claude Code",
  "type": "module",
  "main": "./dist/esm/index.js",
  "module": "./dist/esm/index.js",
  "types": "./dist/esm/index.d.ts",
  "exports": {
    ".": {
      "import": {
        "types": "./dist/esm/index.d.ts",
        "default": "./dist/esm/index.js"
      }
    }
  },
  "files": ["dist/", "configs/", "SKILL.md", "clawmind-plugin/", "claudecode.plugin.json", "mcp-config.json", "install.sh"],
  "keywords": ["clawmind", "workflow", "dag", "claude-code", "mcp", "plugin"],
  "license": "UNLICENSED"
}
PKG_JSON

# ── Step 11: Create tarball ──
mkdir -p "$DIST_PACK_DIR"
OUTPUT_FILE="$DIST_PACK_DIR/clawmind-claudecode-${VERSION}.gz"

info "Creating tarball: $(basename "$OUTPUT_FILE")"
cd "$STAGING"
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$OUTPUT_FILE" -C "$STAGING" "package"
# Note: file extension is .gz (gzip-compressed tar), not .tgz — same format, different extension

# ── Step 12: Cleanup staging ──
rm -rf "$STAGING"

# ── Done ──
OUTPUT_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)
info ""
info "╔══════════════════════════════════════════════════╗"
info "║  Pack complete!                                  ║"
info "╠══════════════════════════════════════════════════╣"
info "║  Output: $OUTPUT_FILE"
info "║  Size:   $OUTPUT_SIZE"
info "║                                                  ║"
info "║  Install with:                                   ║"
info "║  tar -xzf $(basename "$OUTPUT_FILE")                                  ║"
info "║  cd package && ./install.sh                      ║"
info "╚══════════════════════════════════════════════════╝"