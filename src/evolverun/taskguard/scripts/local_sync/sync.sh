#!/usr/bin/env bash
# local_sync.sh — Sync dist_pack packages to local platform install directories.
#
# Usage:
#   ./scripts/local_sync/sync.sh                  # Sync all platforms
#   ./scripts/local_sync/sync.sh openclaw         # Sync OpenClaw only
#   ./scripts/local_sync/sync.sh claudecode       # Sync Claude Code only
#   ./scripts/local_sync/sync.sh hermes           # Sync Hermes only
#   ./scripts/local_sync/sync.sh --build          # Build first, then sync all
#   ./scripts/local_sync/sync.sh openclaw --build # Build first, then sync openclaw
#
# What each platform syncs:
#   openclaw   → ~/.openclaw/extensions/clawmind/  (rsync dist/esm + packs/configs/skills)
#   claudecode → ~/.claude/mcp.json                (register MCP server entry)
#   hermes     → ~/.hermes/extensions/clawmind/    (rsync dist/esm + packs/configs, if hermes installed)
#
# Prerequisites:
#   - npm run build (or --build flag)
#   - npm run dist:pack (or --build flag which runs both)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST_PACK_DIR="$PROJECT_ROOT/dist_pack"

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Parse Args ──

PLATFORMS=("openclaw" "claudecode" "hermes")
DO_BUILD=false
TARGET_PLATFORM=""

for arg in "$@"; do
  case "$arg" in
    --build)  DO_BUILD=true ;;
    openclaw|claudecode|hermes)
      if [ -n "$TARGET_PLATFORM" ]; then
        error "Multiple platforms specified. Use one: openclaw, claudecode, hermes"
        exit 1
      fi
      TARGET_PLATFORM="$arg"
      ;;
    *)
      error "Unknown argument: $arg"
      echo "Usage: $0 [--build] [openclaw|claudecode|hermes]"
      exit 1
      ;;
  esac
done

if [ -n "$TARGET_PLATFORM" ]; then
  PLATFORMS=("$TARGET_PLATFORM")
fi

# ── Build ──

if [ "$DO_BUILD" = true ]; then
  info "Building project and dist packs..."
  cd "$PROJECT_ROOT"
  npm run build
  npm run dist:pack
  echo ""
fi

# ── Verify dist_pack ──

if [ ! -d "$DIST_PACK_DIR" ]; then
  error "dist_pack/ directory not found. Run 'npm run dist:pack' first, or use --build flag."
  exit 1
fi

VERSION=$(node -e 'console.log(JSON.parse(require("fs").readFileSync(require("path").join(process.argv[1], "package.json"),"utf8")).version)' "$PROJECT_ROOT")

echo ""
echo -e "${BLUE}═══ ClawMind Local Sync v${VERSION} ═══${NC}"
echo -e "  Project:  $PROJECT_ROOT"
echo -e "  DistPack: $DIST_PACK_DIR"
echo -e "  Targets:  ${PLATFORMS[*]}"
echo ""

# ═══════════════════════════════════════════════════════════════════════
# OpenClaw
# ═══════════════════════════════════════════════════════════════════════

sync_openclaw() {
  info "Syncing OpenClaw extension..."

  local EXT_DIR="$HOME/.openclaw/extensions/clawmind"
  local DIST_ESM="$PROJECT_ROOT/dist/esm"

  if [ ! -f "$DIST_ESM/index.js" ]; then
    error "dist/esm/index.js not found. Run 'npm run build' first."
    return 1
  fi

  mkdir -p "$EXT_DIR"

  # ── Back up files that rsync --delete would remove ──
  local BACKUP_DIR
  BACKUP_DIR=$(mktemp -d)
  local PRESERVED_FILES="openclaw.plugin.json package.json .env"
  local PRESERVED_DIRS="skills configs node_modules packs"

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

  # ── rsync dist/esm → extension dir (with --delete to keep clean) ──
  rsync -av --delete "$DIST_ESM/" "$EXT_DIR/" 2>&1 | tail -1

  # ── Restore preserved files ──
  for f in $PRESERVED_FILES; do
    if [ -f "$BACKUP_DIR/$f" ]; then
      cp "$BACKUP_DIR/$f" "$EXT_DIR/$f"
    fi
  done

  # ── Copy plugin manifest if not present ──
  if [ ! -f "$EXT_DIR/openclaw.plugin.json" ] && [ -f "$PROJECT_ROOT/openclaw.plugin.json" ]; then
    cp "$PROJECT_ROOT/openclaw.plugin.json" "$EXT_DIR/openclaw.plugin.json"
  fi

  # ── Copy skills from source (generated during build) ──
  local SOURCE_SKILLS="$PROJECT_ROOT/skills"
  if [ -d "$SOURCE_SKILLS" ]; then
    mkdir -p "$EXT_DIR/skills"
    cp -r "$SOURCE_SKILLS/" "$EXT_DIR/skills/"
  elif [ -d "$BACKUP_DIR/skills" ]; then
    mkdir -p "$EXT_DIR/skills"
    cp -r "$BACKUP_DIR/skills/"* "$EXT_DIR/skills/" 2>/dev/null || true
  fi

  # ── Copy configs from source ──
  local SOURCE_CONFIGS="$PROJECT_ROOT/configs"
  if [ -d "$SOURCE_CONFIGS" ]; then
    mkdir -p "$EXT_DIR/configs"
    cp -r "$SOURCE_CONFIGS/" "$EXT_DIR/configs/"
  elif [ -d "$BACKUP_DIR/configs" ]; then
    mkdir -p "$EXT_DIR/configs"
    cp -r "$BACKUP_DIR/configs/"* "$EXT_DIR/configs/" 2>/dev/null || true
  fi

  # ── Copy packs from source ──
  local SOURCE_PACKS="$PROJECT_ROOT/packs"
  if [ -d "$SOURCE_PACKS" ]; then
    mkdir -p "$EXT_DIR/packs"
    cp -r "$SOURCE_PACKS/" "$EXT_DIR/packs/"
  fi

  # ── Bundle dependencies (cron-parser, yaml, zod) ──
  local BUNDLE_DEPS
  BUNDLE_DEPS=$(node -e 'const pkg=JSON.parse(require("fs").readFileSync(require("path").join(process.argv[1],"package.json"),"utf8"));console.log((pkg.bundleDependencies||[]).join(" "))' "$PROJECT_ROOT")
  mkdir -p "$EXT_DIR/node_modules"
  for dep in $BUNDLE_DEPS; do
    if [ -d "$PROJECT_ROOT/node_modules/$dep" ]; then
      rm -rf "$EXT_DIR/node_modules/$dep"
      cp -r "$PROJECT_ROOT/node_modules/$dep" "$EXT_DIR/node_modules/$dep"
    fi
  done

  # ── Fix package.json paths (dist/esm → flat) ──
  if [ -f "$EXT_DIR/package.json" ]; then
    local CHANGED=0
    if grep -q '"./dist/esm/' "$EXT_DIR/package.json" 2>/dev/null; then
      sed -i.bak 's|"./dist/esm/|"./|g' "$EXT_DIR/package.json"
      CHANGED=1
    fi
    rm -f "$EXT_DIR/package.json.bak"
    [ "$CHANGED" -eq 1 ] && info "  Fixed package.json paths (dist/esm → flat)"
  fi

  # ── Cleanup ──
  rm -rf "$BACKUP_DIR"

  ok "OpenClaw → $EXT_DIR"
  echo ""
  echo -e "  ${YELLOW}验证方式:${NC}"
  echo -e "  1. 重启 OpenClaw: openclaw restart"
  echo -e "  2. 检查插件加载: openclaw plugin list"
  echo -e "  3. 查看扩展目录: ls -la $EXT_DIR"
}

# ═══════════════════════════════════════════════════════════════════════
# Claude Code
# ═══════════════════════════════════════════════════════════════════════

sync_claudecode() {
  info "Syncing Claude Code MCP configuration..."

  local MCP_CONFIG="$HOME/.claude/mcp.json"
  local DIST_ESM="$PROJECT_ROOT/dist/esm"
  local ENTRY_POINT="$DIST_ESM/platform/mcp-entry.js"

  if [ ! -f "$ENTRY_POINT" ]; then
    error "dist/esm/platform/mcp-entry.js not found. Run 'npm run build' first."
    return 1
  fi

  # ── Ensure mcp.json exists ──
  if [ ! -f "$MCP_CONFIG" ]; then
    echo '{}' > "$MCP_CONFIG"
    info "Created $MCP_CONFIG"
  fi

  # ── Update MCP config using Node (safe JSON manipulation) ──
  # Auto-detect claude CLI for Agent SDK embedded-agent support.
  # Sets CLAUDE_CODE_EXECUTABLE so the SDK's query() can find the native
  # binary instead of throwing "Native CLI binary for <platform> not found".
  local CLAUDE_BIN=""
  if command -v claude &>/dev/null; then
    CLAUDE_BIN="$(command -v claude)"
  elif [ -x "/opt/homebrew/bin/claude" ]; then
    CLAUDE_BIN="/opt/homebrew/bin/claude"
  elif [ -x "/usr/local/bin/claude" ]; then
    CLAUDE_BIN="/usr/local/bin/claude"
  fi

  # Build CLAUDE_CODE_EXECUTABLE env entry conditionally
  local CLAUDE_BIN_FOR_NODE=""
  if [ -n "$CLAUDE_BIN" ]; then
    CLAUDE_BIN_FOR_NODE="$CLAUDE_BIN"
    info "Auto-detected claude CLI: $CLAUDE_BIN"
  else
    warn "claude CLI not found — Agent SDK embedded-agent may fail with 'Native CLI binary not found'"
    warn "Set CLAUDE_CODE_EXECUTABLE in mcp.json env or install claude CLI"
  fi

  # NOTE: We do NOT register clawmind in ~/.claude/mcp.json anymore.
  # The marketplace plugin's .mcp.json handles MCP server registration via
  # ${CLAUDE_PLUGIN_ROOT}. Adding a duplicate entry in mcp.json causes:
  #   1. Two clawmind MCP server processes (port/DB lock conflicts)
  #   2. Tool registration conflicts
  #   3. Connection instability when mcp.json changes mid-session
  # CLAUDE_CODE_EXECUTABLE is injected into the marketplace .mcp.json below.

  # ── Sync dist/esm/ to marketplace plugin directory ──
  # Claude Code loads ClawMind from the marketplace path, NOT from mcp.json entry.
  # The mcp.json registration is a fallback for manual/config-based setups.
  local MARKETPLACE_DIR="$HOME/.claude/plugins/marketplaces/clawmind"
  if [ -d "$MARKETPLACE_DIR" ]; then
    info "Syncing dist/esm/ → $MARKETPLACE_DIR/dist/esm/ (marketplace plugin)..."
    mkdir -p "$MARKETPLACE_DIR/dist/esm"
    rsync -av --delete "$DIST_ESM/" "$MARKETPLACE_DIR/dist/esm/" 2>&1 | tail -1

    # Copy packs & configs into marketplace dir
    mkdir -p "$MARKETPLACE_DIR/packs" "$MARKETPLACE_DIR/configs"
    if [ -d "$PROJECT_ROOT/packs" ]; then
      rsync -av --delete "$PROJECT_ROOT/packs/" "$MARKETPLACE_DIR/packs/" 2>&1 | tail -1
    fi
    if [ -d "$PROJECT_ROOT/configs" ]; then
      rsync -av --delete "$PROJECT_ROOT/configs/" "$MARKETPLACE_DIR/configs/" 2>&1 | tail -1
    fi

    # Copy plugin manifest (contracts.tools for MCP tool registration)
    if [ -f "$PROJECT_ROOT/claudecode.plugin.json" ]; then
      cp "$PROJECT_ROOT/claudecode.plugin.json" "$MARKETPLACE_DIR/claudecode.plugin.json"
      info "  Copied claudecode.plugin.json → marketplace dir"
    fi

    # Sync .claude-plugin/ directory (contains plugin.json with id, name, version)
    # This is critical — the plugin id defines the skill namespace prefix
    # (e.g., id="clawmind" → /clawmind:kf-direct; id="clawmind-claudecode" → /clawmind-claudecode:kf-direct)
    local SOURCE_PLUGIN_DIR="$PROJECT_ROOT/clawmind-plugin/.claude-plugin"
    if [ -d "$SOURCE_PLUGIN_DIR" ]; then
      mkdir -p "$MARKETPLACE_DIR/.claude-plugin"
      cp -r "$SOURCE_PLUGIN_DIR/"* "$MARKETPLACE_DIR/.claude-plugin/"
      info "  Synced .claude-plugin/ → marketplace dir"
    fi

    # ── Inject CLAUDE_CODE_EXECUTABLE into marketplace .mcp.json ──
    # The marketplace .mcp.json is the single source of truth for MCP server config.
    # We inject the auto-detected claude CLI path so the Agent SDK can find it.
    local MCP_JSON="$MARKETPLACE_DIR/.mcp.json"
    if [ -f "$MCP_JSON" ] && [ -n "$CLAUDE_BIN_FOR_NODE" ]; then
      node -e '
        const fs = require("fs");
        const configPath = process.argv[1];
        const claudeBinPath = process.argv[2];
        const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
        if (config.mcpServers && config.mcpServers.clawmind && config.mcpServers.clawmind.env) {
          config.mcpServers.clawmind.env.CLAUDE_CODE_EXECUTABLE = claudeBinPath;
          fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n");
        }
      ' "$MCP_JSON" "$CLAUDE_BIN_FOR_NODE"
      info "  Injected CLAUDE_CODE_EXECUTABLE=$CLAUDE_BIN_FOR_NODE → .mcp.json"
    fi
  else
    warn "Marketplace plugin dir not found: $MARKETPLACE_DIR"
    warn "Skipping marketplace sync. Plugin may be installed via skills-dir instead."
  fi

  # ── Copy plugin manifest for reference (extensions dir) ──
  local CC_PLUGIN_DIR="$HOME/.claude/extensions/clawmind"
  mkdir -p "$CC_PLUGIN_DIR"
  if [ -f "$PROJECT_ROOT/claudecode.plugin.json" ]; then
    cp "$PROJECT_ROOT/claudecode.plugin.json" "$CC_PLUGIN_DIR/claudecode.plugin.json"
  fi
  if [ -f "$PROJECT_ROOT/mcp-config.json" ] || true; then
    # Generate mcp-config.json snippet for local dev reference
    cat > "$CC_PLUGIN_DIR/mcp-config.json" << 'MCPEOF'
{
  "mcpServers": {
    "clawmind": {
      "command": "node",
      "args": ["dist/esm/platform/mcp-entry.js"],
      "env": {
        "DATABASE_MODE": "sqlite",
        "SQLITE_PATH": "~/.openclaw/workflow/engine.db"
      },
      "description": "ClawMind — YAML DAG workflow engine (MCP server)"
    }
  }
}
MCPEOF
  fi

  # ── Copy packs & configs for extensions dir ──
  mkdir -p "$CC_PLUGIN_DIR/packs" "$CC_PLUGIN_DIR/configs"
  if [ -d "$PROJECT_ROOT/packs" ]; then
    cp -r "$PROJECT_ROOT/packs/" "$CC_PLUGIN_DIR/packs/"
  fi
  if [ -d "$PROJECT_ROOT/configs" ]; then
    cp -r "$PROJECT_ROOT/configs/" "$CC_PLUGIN_DIR/configs/"
  fi

  # ── Also sync to skills-dir if it exists ──
  local SKILLS_DIR="$HOME/.claude/skills/clawmind"
  if [ -d "$SKILLS_DIR" ] && [ -d "$SKILLS_DIR/dist/esm" ]; then
    info "Syncing dist/esm/ → $SKILLS_DIR/dist/esm/ (skills-dir plugin)..."
    rsync -av --delete "$DIST_ESM/" "$SKILLS_DIR/dist/esm/" 2>&1 | tail -1
    if [ -f "$PROJECT_ROOT/claudecode.plugin.json" ]; then
      cp "$PROJECT_ROOT/claudecode.plugin.json" "$SKILLS_DIR/claudecode.plugin.json"
    fi
  fi

  ok "Claude Code → $MCP_CONFIG (clawmind MCP server registered)"
  echo ""
  echo -e "  ${YELLOW}配置说明:${NC}"
  echo -e "  MCP 配置文件位置:"
  echo -e "    $MCP_CONFIG"
  echo ""
  echo -e "  ${YELLOW}如果需要手动配置 ~/.claude/mcp.json，添加:${NC}"
  echo -e '  {'
  echo -e '    "mcpServers": {'
  echo -e '      "clawmind": {'
  echo -e '        "command": "node",'
  echo -e "        \"args\": [\"$ENTRY_POINT\"],"
  echo -e '        "env": {'
  echo -e '          "DATABASE_MODE": "sqlite",'
  echo -e '          "SQLITE_PATH": "~/.openclaw/workflow/engine.db"'
  echo -e '        }'
  echo -e '      }'
  echo -e '    }'
  echo -e '  }'
  echo ""
  echo -e "  ${YELLOW}验证方式:${NC}"
  echo -e "  1. 重启 Claude Code"
  echo -e "  2. 在对话中输入: /mcp 查看已注册的 MCP 服务器"
  echo -e "  3. 调用 workflow_engine_dispatch 工具测试"
}

# ═══════════════════════════════════════════════════════════════════════
# Hermes
# ═══════════════════════════════════════════════════════════════════════

sync_hermes() {
  info "Syncing Hermes extension..."

  local HERMES_EXT_DIR="$HOME/.hermes/extensions/clawmind"
  local DIST_ESM="$PROJECT_ROOT/dist/esm"

  if [ ! -f "$DIST_ESM/platform/mcp-entry.js" ]; then
    error "dist/esm/platform/mcp-entry.js not found. Run 'npm run build' first."
    return 1
  fi

  # ── Check if Hermes is installed locally ──
  if [ ! -d "$HOME/.hermes" ]; then
    warn "Hermes not installed locally ($HOME/.hermes not found)"
    warn "Creating directory structure for future use..."
    mkdir -p "$HERMES_EXT_DIR"
  else
    mkdir -p "$HERMES_EXT_DIR"
  fi

  # ── Same rsync pattern as OpenClaw ──
  local BACKUP_DIR
  BACKUP_DIR=$(mktemp -d)
  local PRESERVED_FILES="hermes.plugin.json package.json .env"
  local PRESERVED_DIRS="configs packs node_modules"

  for f in $PRESERVED_FILES; do
    if [ -f "$HERMES_EXT_DIR/$f" ]; then
      cp "$HERMES_EXT_DIR/$f" "$BACKUP_DIR/$f"
    fi
  done
  for d in $PRESERVED_DIRS; do
    if [ -d "$HERMES_EXT_DIR/$d" ]; then
      mkdir -p "$BACKUP_DIR/$d"
      cp -r "$HERMES_EXT_DIR/$d/" "$BACKUP_DIR/$d/" 2>/dev/null || true
    fi
  done

  rsync -av --delete "$DIST_ESM/" "$HERMES_EXT_DIR/" 2>&1 | tail -1

  # ── Restore preserved ──
  for f in $PRESERVED_FILES; do
    if [ -f "$BACKUP_DIR/$f" ]; then
      cp "$BACKUP_DIR/$f" "$HERMES_EXT_DIR/$f"
    fi
  done

  # ── Copy hermes manifest ──
  if [ -f "$PROJECT_ROOT/hermes.plugin.json" ]; then
    cp "$PROJECT_ROOT/hermes.plugin.json" "$HERMES_EXT_DIR/hermes.plugin.json"
  fi

  # ── Copy configs & packs ──
  if [ -d "$PROJECT_ROOT/configs" ]; then
    mkdir -p "$HERMES_EXT_DIR/configs"
    cp -r "$PROJECT_ROOT/configs/" "$HERMES_EXT_DIR/configs/"
  fi
  if [ -d "$PROJECT_ROOT/packs" ]; then
    mkdir -p "$HERMES_EXT_DIR/packs"
    cp -r "$PROJECT_ROOT/packs/" "$HERMES_EXT_DIR/packs/"
  fi

  # ── Bundle dependencies ──
  local BUNDLE_DEPS
  BUNDLE_DEPS=$(node -e 'const pkg=JSON.parse(require("fs").readFileSync(require("path").join(process.argv[1],"package.json"),"utf8"));console.log((pkg.bundleDependencies||[]).join(" "))' "$PROJECT_ROOT")
  mkdir -p "$HERMES_EXT_DIR/node_modules"
  for dep in $BUNDLE_DEPS; do
    if [ -d "$PROJECT_ROOT/node_modules/$dep" ]; then
      rm -rf "$HERMES_EXT_DIR/node_modules/$dep"
      cp -r "$PROJECT_ROOT/node_modules/$dep" "$HERMES_EXT_DIR/node_modules/$dep"
    fi
  done

  # ── Fix package.json paths ──
  if [ -f "$HERMES_EXT_DIR/package.json" ]; then
    if grep -q '"./dist/esm/' "$HERMES_EXT_DIR/package.json" 2>/dev/null; then
      sed -i.bak 's|"./dist/esm/|"./|g' "$HERMES_EXT_DIR/package.json"
      rm -f "$HERMES_EXT_DIR/package.json.bak"
      info "  Fixed package.json paths (dist/esm → flat)"
    fi
  fi

  rm -rf "$BACKUP_DIR"

  ok "Hermes → $HERMES_EXT_DIR"
  echo ""
  echo -e "  ${YELLOW}配置说明:${NC}"
  echo -e "  Hermes 扩展目录: $HERMES_EXT_DIR"
  echo ""
  echo -e "  ${YELLOW}验证方式:${NC}"
  echo -e "  1. 重启 Hermes"
  echo -e "  2. 检查扩展目录: ls -la $HERMES_EXT_DIR"
}

# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

FAILED=0
for platform in "${PLATFORMS[@]}"; do
  case "$platform" in
    openclaw)
      sync_openclaw || FAILED=$((FAILED + 1))
      ;;
    claudecode)
      sync_claudecode || FAILED=$((FAILED + 1))
      ;;
    hermes)
      sync_hermes || FAILED=$((FAILED + 1))
      ;;
    *)
      error "Unknown platform: $platform"
      FAILED=$((FAILED + 1))
      ;;
  esac
done

echo ""
if [ "$FAILED" -eq 0 ]; then
  ok "All ${#PLATFORMS[@]} platform(s) synced successfully!"
else
  error "$FAILED platform(s) failed to sync."
  exit 1
fi