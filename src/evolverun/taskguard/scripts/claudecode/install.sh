#!/usr/bin/env bash
# ClawMind for Claude Code — Installer
#
# Strategy:
#   Plugin mode (default):
#     1. Set up marketplaces/clawmind/ as a local marketplace source
#     2. Register in known_marketplaces.json so Claude Code can discover it
#     3. Run `claude plugin install clawmind` — the official command handles
#        installed_plugins.json, cache/ directory, and all internal registration
#     4. Post-install: adapt .mcp.json, hooks.json, monitors.json with
#        absolute paths (replacing ${CLAUDE_PLUGIN_ROOT})
#
#   No-plugin mode (CLAWMIND_NO_PLUGIN=1):
#     Skip all Plugin system registration. MCP is registered via mcporter.json,
#     hooks/commands are injected directly into settings.json by config_claude.sh.
#     This mode only ensures the runtime files are in place at /home/admin/clawmind-mcp/.
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Step 0: No-plugin mode check ──
if [[ "${CLAWMIND_NO_PLUGIN:-}" == "1" ]]; then
  echo "[clawmind] NO_PLUGIN mode: skipping Plugin system registration"
  echo "[clawmind] MCP will be registered via mcporter.json"
  echo "[clawmind] Hooks/Commands will be injected by config_claude.sh"
  echo "[clawmind] Runtime files should already be at /home/admin/clawmind-mcp/"
  # Ensure SQLite workflow DB directory exists (for local dev)
  mkdir -p "$HOME/.openclaw/workflow" 2>/dev/null || true
  echo "[clawmind] No-plugin mode setup complete"
  exit 0
fi

# ── Step 0a: Detect Claude Code config directory ──
if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
  echo "[clawmind] CLAUDE_CONFIG_DIR explicitly set: $CLAUDE_CONFIG_DIR"
elif [[ -d "/home/admin/.claude_code/workspace" ]]; then
  CLAUDE_CONFIG_DIR="/home/admin/.claude_code/workspace/.claude"
  echo "[clawmind] Remote Bot workspace detected, using: $CLAUDE_CONFIG_DIR"
elif [[ "$HOME" == "/home/admin/.claude_code/workspace" ]]; then
  CLAUDE_CONFIG_DIR="$HOME/.claude"
  echo "[clawmind] Running inside Claude Code workspace, using: $CLAUDE_CONFIG_DIR"
else
  CLAUDE_CONFIG_DIR="$HOME/.claude"
  echo "[clawmind] Using default config dir: $CLAUDE_CONFIG_DIR"
fi

# ── Step 1: Detect CLAUDE_CODE_EXECUTABLE ──
CLAUDE_BIN=""
if [[ -x "/usr/bin/claude" ]]; then
  CLAUDE_BIN="/usr/bin/claude"
elif command -v claude &>/dev/null; then
  CLAUDE_BIN="$(command -v claude)"
elif [[ -x "/opt/homebrew/bin/claude" ]]; then
  CLAUDE_BIN="/opt/homebrew/bin/claude"
elif [[ -x "/usr/local/bin/claude" ]]; then
  CLAUDE_BIN="/usr/local/bin/claude"
fi
if [[ -n "$CLAUDE_BIN" ]]; then
  echo "[clawmind] Claude Code CLI detected: $CLAUDE_BIN"
else
  echo "[clawmind] WARNING: claude CLI not found. Plugin install may fail."
fi

# ── Step 2: Set up marketplace source directory ──
# Claude Code reads the marketplace from plugins/marketplaces/<name>/
# It needs .claude-plugin/marketplace.json (with plugins list) and
# .claude-plugin/plugin.json (plugin manifest) + the actual plugin content.
MARKETPLACE_DIR="${CLAUDE_CONFIG_DIR}/plugins/marketplaces/clawmind"
mkdir -p "$MARKETPLACE_DIR"

echo "[clawmind] Setting up marketplace source at $MARKETPLACE_DIR"

# Copy plugin scaffold (includes .claude-plugin/, commands/, skills/, agents/, hooks/, monitors/)
if [[ -d "$SCRIPT_DIR/clawmind-plugin" ]]; then
  cp -r "$SCRIPT_DIR/clawmind-plugin/"* "$MARKETPLACE_DIR/"
  cp -r "$SCRIPT_DIR/clawmind-plugin/.claude-plugin" "$MARKETPLACE_DIR/" 2>/dev/null || true
  cp "$SCRIPT_DIR/clawmind-plugin/.mcp.json" "$MARKETPLACE_DIR/" 2>/dev/null || true
fi

# Copy runtime assets
for item in dist packs configs; do
  if [[ -e "$SCRIPT_DIR/$item" ]]; then
    cp -r "$SCRIPT_DIR/$item" "$MARKETPLACE_DIR/"
  fi
done

# Copy node_modules/ — required because zod and @modelcontextprotocol/sdk are
# external in the esbuild bundle (zod v4's $constructor/__esm pattern breaks
# when bundled into a single file).  The plugin's node_modules/ provides these
# at runtime, INCLUDING their full transitive dependency trees (MCP SDK depends
# on raw-body → http-errors → depd etc.).
if [[ -d "$SCRIPT_DIR/node_modules" ]]; then
  # Walk full transitive dependency tree from bundleDependencies
  DEP_LIST=$(node -e "
    const fs = require('fs');
    const path = require('path');
    const scriptDir = process.argv[1];
    const pkg = JSON.parse(fs.readFileSync(path.join(scriptDir, 'package.json'), 'utf8'));
    const bundleDeps = pkg.bundleDependencies || [];
    const visited = new Set();
    function walk(pkgName) {
      if (visited.has(pkgName)) return;
      visited.add(pkgName);
      const pkgPath = path.join(scriptDir, 'node_modules', pkgName, 'package.json');
      if (!fs.existsSync(pkgPath)) return;
      const p = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
      for (const dep of Object.keys(p.dependencies || {})) {
        if (fs.existsSync(path.join(scriptDir, 'node_modules', dep, 'package.json'))) {
          walk(dep);
        }
      }
    }
    for (const dep of bundleDeps) walk(dep);
    console.log([...visited].join('\n'));
  " "$SCRIPT_DIR" 2>/dev/null || true)
  if [[ -n "$DEP_LIST" ]]; then
    mkdir -p "$MARKETPLACE_DIR/node_modules"
    while IFS= read -r dep; do
      [[ -z "$dep" ]] && continue
      if [[ -d "$SCRIPT_DIR/node_modules/$dep" ]]; then
        dep_parent="$(dirname "$MARKETPLACE_DIR/node_modules/$dep")"
        mkdir -p "$dep_parent"
        cp -r "$SCRIPT_DIR/node_modules/$dep" "$MARKETPLACE_DIR/node_modules/$dep"
      else
        echo "[clawmind] WARNING: Missing node_modules/$dep"
      fi
    done <<< "$DEP_LIST"
    echo "[clawmind] Copied node_modules/ (bundleDependencies + transitive deps)"
  fi
fi

# Copy root-level files
for item in SKILL.md claudecode.plugin.json mcp-config.json settings.json; do
  if [[ -f "$SCRIPT_DIR/$item" ]]; then
    cp "$SCRIPT_DIR/$item" "$MARKETPLACE_DIR/"
  fi
done

# Ensure .openclaw/workflow exists (only needed for sqlite mode in local dev;
# Bot uses api mode and doesn't need this directory)
if [[ "$CLAUDE_BIN" != *"/usr/bin/"* ]]; then
  mkdir -p "$HOME/.openclaw/workflow" 2>/dev/null || true
fi
echo "[clawmind] Marketplace source set up"

# ── Step 3: Register in known_marketplaces.json ──
# This tells Claude Code where to find the "clawmind" marketplace.
# Without it, `claude plugin install clawmind` won't find anything.
KNOWN_MARKETPLACES="${CLAUDE_CONFIG_DIR}/plugins/known_marketplaces.json"
mkdir -p "${CLAUDE_CONFIG_DIR}/plugins"
node -e '
  const fs = require("fs");
  const filePath = process.argv[1];
  const marketplaceDir = process.argv[2];
  let data = {};
  try { data = JSON.parse(fs.readFileSync(filePath, "utf8")); } catch(e) {}
  data["clawmind"] = {
    source: {
      source: "directory",
      path: marketplaceDir
    },
    installLocation: marketplaceDir,
    lastUpdated: new Date().toISOString()
  };
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2) + "\n");
' "$KNOWN_MARKETPLACES" "$MARKETPLACE_DIR" 2>/dev/null || true
echo "[clawmind] Registered in known_marketplaces.json"

# ── Step 4: Install plugin via official `claude plugin install` ──
# This is the key step. The official command handles:
#   - Copying to plugins/cache/clawmind/clawmind/<version>/
#   - Writing installed_plugins.json with correct format
#   - All internal registration that manual approaches keep missing
PLUGIN_INSTALL_OK=false
if [[ -n "$CLAUDE_BIN" ]]; then
  # Need to set HOME correctly for the claude CLI on Bot
  ORIGINAL_HOME="$HOME"
  if [[ -d "/home/admin/.claude_code/workspace" ]]; then
    export HOME="/home/admin/.claude_code/workspace"
  fi

  echo "[clawmind] Running: $CLAUDE_BIN plugin install clawmind -s user"
  # Try piped yes for non-interactive confirmation
  INSTALL_OUTPUT=$(yes 2>/dev/null | "$CLAUDE_BIN" plugin install clawmind -s user 2>&1) && PLUGIN_INSTALL_OK=true || true
  echo "[clawmind] plugin install output: $INSTALL_OUTPUT"

  if [[ "$PLUGIN_INSTALL_OK" != "true" ]]; then
    echo "[clawmind] Retrying with --scope user..."
    INSTALL_OUTPUT=$("$CLAUDE_BIN" plugin install clawmind --scope user 2>&1) && PLUGIN_INSTALL_OK=true || true
    echo "[clawmind] plugin install output: $INSTALL_OUTPUT"
  fi

  export HOME="$ORIGINAL_HOME"
fi

if [[ "$PLUGIN_INSTALL_OK" != "true" ]]; then
  echo "[clawmind] WARNING: claude plugin install failed, falling back to manual registration"
  MANUAL_FALLBACK=true
fi

# ── Step 4.5: Manual fallback registration ──
if [[ "${MANUAL_FALLBACK:-}" == "true" ]]; then
  echo "[clawmind] Manual registration fallback..."
  PLUGIN_DIR="${CLAUDE_CONFIG_DIR}/plugins/cache/clawmind/clawmind/0.1.0"
  mkdir -p "$PLUGIN_DIR"

  # Copy everything from marketplace dir to cache dir
  cp -r "$MARKETPLACE_DIR/"* "$PLUGIN_DIR/" 2>/dev/null || true
  cp -r "$MARKETPLACE_DIR/.claude-plugin" "$PLUGIN_DIR/" 2>/dev/null || true
  cp "$MARKETPLACE_DIR/.mcp.json" "$PLUGIN_DIR/" 2>/dev/null || true

  # Register in installed_plugins.json
  INSTALLED_PLUGINS="${CLAUDE_CONFIG_DIR}/plugins/installed_plugins.json"
  node -e '
    const fs = require("fs");
    const filePath = process.argv[1];
    const pluginDir = process.argv[2];
    let data = { version: 2, plugins: {} };
    try { data = JSON.parse(fs.readFileSync(filePath, "utf8")); } catch(e) {}
    if (!data.plugins) data.plugins = {};
    if (!data.plugins["clawmind@clawmind"]) data.plugins["clawmind@clawmind"] = [];
    const existing = data.plugins["clawmind@clawmind"];
    const entry = {
      scope: "user",
      installPath: pluginDir,
      version: "0.1.0",
      installedAt: new Date().toISOString(),
      lastUpdated: new Date().toISOString()
    };
    const idx = existing.findIndex(e => e.scope === "user");
    if (idx >= 0) { existing[idx] = entry; } else { existing.push(entry); }
    fs.writeFileSync(filePath, JSON.stringify(data, null, 2) + "\n");
  ' "$INSTALLED_PLUGINS" "$PLUGIN_DIR" 2>/dev/null || true
  echo "[clawmind] Manual registration done: $PLUGIN_DIR"
fi

# ── Step 5: Find the installed plugin directory ──
# After `claude plugin install`, the plugin lives in cache/.
# We need to find it to adapt .mcp.json and other files.
PLUGIN_DIR=""
# Try the expected path first
CANDIDATE="${CLAUDE_CONFIG_DIR}/plugins/cache/clawmind/clawmind/0.1.0"
if [[ -d "$CANDIDATE" && -f "$CANDIDATE/.claude-plugin/plugin.json" ]]; then
  PLUGIN_DIR="$CANDIDATE"
else
  # Search for it
  for d in "${CLAUDE_CONFIG_DIR}"/plugins/cache/clawmind/clawmind/*/; do
    if [[ -f "$d/.claude-plugin/plugin.json" ]]; then
      PLUGIN_DIR="$d"
      break
    fi
  done
fi

if [[ -z "$PLUGIN_DIR" ]]; then
  echo "[clawmind] ERROR: Could not find installed plugin directory in cache/"
  echo "[clawmind] Plugin install may have failed. Check Claude Code logs."
  exit 1
fi

echo "[clawmind] Plugin installed at: $PLUGIN_DIR"

# ── Step 5.5: Ensure critical node_modules exist in plugin dir ──
# zod and @modelcontextprotocol/sdk are external in the esbuild bundle
# (zod v4's $constructor/__esm pattern breaks when bundled into a single file).
# They must be resolvable from node_modules/ at runtime.
# `claude plugin install` may not copy node_modules/ from the marketplace source,
# or may create an empty/incomplete node_modules/ directory. So we check for
# critical packages (not just the directory existence) and copy if missing.
NODE_MODULES_NEEDED=false
for critical in zod @modelcontextprotocol/sdk; do
  if [[ ! -d "$PLUGIN_DIR/node_modules/$critical" ]]; then
    NODE_MODULES_NEEDED=true
    break
  fi
done

if [[ "$NODE_MODULES_NEEDED" == "true" ]]; then
  # Source priority: MARKETPLACE_DIR → SCRIPT_DIR (package/ dir from .gz)
  NM_SOURCE=""
  if [[ -d "$MARKETPLACE_DIR/node_modules/zod" ]]; then
    NM_SOURCE="$MARKETPLACE_DIR"
    echo "[clawmind] Copying node_modules/ from marketplace dir (required for external zod/MCP SDK)..."
  elif [[ -d "$SCRIPT_DIR/node_modules/zod" ]]; then
    NM_SOURCE="$SCRIPT_DIR"
    echo "[clawmind] Copying node_modules/ from package dir (with transitive deps)..."
  fi

  if [[ -n "$NM_SOURCE" ]]; then
    rm -rf "$PLUGIN_DIR/node_modules"
    cp -r "$NM_SOURCE/node_modules" "$PLUGIN_DIR/node_modules"
  else
    # Neither marketplace nor package dir has node_modules — walk from bundleDependencies
    echo "[clawmind] Walking transitive deps from bundleDependencies..."
    DEP_LIST_FALLBACK=$(node -e "
      const fs = require('fs');
      const path = require('path');
      const scriptDir = process.argv[1];
      const pkg = JSON.parse(fs.readFileSync(path.join(scriptDir, 'package.json'), 'utf8'));
      const bundleDeps = pkg.bundleDependencies || [];
      const visited = new Set();
      function walk(pkgName) {
        if (visited.has(pkgName)) return;
        visited.add(pkgName);
        const pkgPath = path.join(scriptDir, 'node_modules', pkgName, 'package.json');
        if (!fs.existsSync(pkgPath)) return;
        const p = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
        for (const dep of Object.keys(p.dependencies || {})) {
          if (fs.existsSync(path.join(scriptDir, 'node_modules', dep, 'package.json'))) {
            walk(dep);
          }
        }
      }
      for (const dep of bundleDeps) walk(dep);
      console.log([...visited].join('\n'));
    " "$SCRIPT_DIR" 2>/dev/null || true)
    if [[ -n "$DEP_LIST_FALLBACK" ]]; then
      rm -rf "$PLUGIN_DIR/node_modules"
      mkdir -p "$PLUGIN_DIR/node_modules"
      while IFS= read -r dep; do
        [[ -z "$dep" ]] && continue
        if [[ -d "$SCRIPT_DIR/node_modules/$dep" ]]; then
          dep_parent="$(dirname "$PLUGIN_DIR/node_modules/$dep")"
          mkdir -p "$dep_parent"
          cp -r "$SCRIPT_DIR/node_modules/$dep" "$PLUGIN_DIR/node_modules/$dep"
        fi
      done <<< "$DEP_LIST_FALLBACK"
    fi
  fi
fi

# Verify critical modules
for critical in zod @modelcontextprotocol/sdk; do
  if [[ ! -d "$PLUGIN_DIR/node_modules/$critical" ]]; then
    echo "[clawmind] WARNING: node_modules/$critical not found — MCP server will fail with 'Cannot find package'"
  else
    echo "[clawmind] ✅ node_modules/$critical available"
  fi
done

# ── Step 6: Adapt .mcp.json — replace ${CLAUDE_PLUGIN_ROOT} with absolute path ──
MCP_FILE="$PLUGIN_DIR/.mcp.json"

# Determine env settings based on environment
IS_BOT=false
if [[ "$CLAUDE_BIN" == *"/usr/bin/"* ]] || [[ "${REMOTE_BOT:-}" == "1" ]]; then
  IS_BOT=true
fi

# Build the correct .mcp.json content via node — this is more reliable than
# trying to patch variable references, because `claude plugin install` may
# overwrite the file with the original template containing ${CLAUDE_PLUGIN_ROOT}.
MCP_ENTRY_FILE="$PLUGIN_DIR/dist/esm/platform/mcp-entry.js"
if [[ -f "$MCP_ENTRY_FILE" ]]; then
  node -e '
    const fs = require("fs");
    const mcpPath = process.argv[1];
    const pluginDir = process.argv[2];
    const mcpEntry = process.argv[3];
    const isBot = process.argv[4] === "true";
    const claudeBin = process.argv[5] || "";

    const env = {
      SKILL_ROOT: pluginDir + "/packs",
      DATABASE_MODE: isBot ? "api" : "sqlite",
      MCP_TRANSPORT: "stdio",
      CCT_SOP_MCP_SERVER_MODE: isBot ? "prod" : "local",
      TECLAW_ENABLED: "false",
      CLAUDE_CODE_EXECUTABLE: claudeBin,
      CLAUDE_PLUGIN_ROOT: pluginDir,
    };
    if (!isBot) {
      env.SQLITE_PATH = "~/.openclaw/workflow/engine.db";
    }

    const config = {
      mcpServers: {
        clawmind: {
          command: "node",
          args: [mcpEntry],
          env: env,
        },
      },
    };
    fs.writeFileSync(mcpPath, JSON.stringify(config, null, 2) + "\n");
  ' "$MCP_FILE" "$PLUGIN_DIR" "$MCP_ENTRY_FILE" "$IS_BOT" "$CLAUDE_BIN" 2>/dev/null

  # Verify: ensure no ${CLAUDE_PLUGIN_ROOT} references remain
  if grep -q 'CLAUDE_PLUGIN_ROOT' "$MCP_FILE" 2>/dev/null; then
    # Fallback: sed replacement for any remaining references
    sed -i.bak "s|\${CLAUDE_PLUGIN_ROOT}|$PLUGIN_DIR|g" "$MCP_FILE" 2>/dev/null || true
    rm -f "$MCP_FILE.bak"
  fi

  echo "[clawmind] .mcp.json written (command=$MCP_ENTRY_FILE, isBot=$IS_BOT)"
else
  echo "[clawmind] WARNING: MCP entry file not found: $MCP_ENTRY_FILE"
fi

# ── Step 7: Adapt hooks.json — replace ${CLAUDE_PLUGIN_ROOT} ──
HOOKS_FILE="$PLUGIN_DIR/hooks/hooks.json"
if [[ -f "$HOOKS_FILE" ]]; then
  node -e '
    const fs = require("fs");
    const filePath = process.argv[1];
    const pluginDir = process.argv[2];
    let content = fs.readFileSync(filePath, "utf8");
    content = content.replace(/\$\{CLAUDE_PLUGIN_ROOT\}/g, pluginDir);
    fs.writeFileSync(filePath, content);
  ' "$HOOKS_FILE" "$PLUGIN_DIR" 2>/dev/null || true
  echo "[clawmind] hooks.json adapted (absolute paths)"
fi

# ── Step 8: Adapt monitors — replace ${CLAUDE_PLUGIN_ROOT} ──
MONITORS_FILE="$PLUGIN_DIR/monitors/monitors.json"
if [[ -f "$MONITORS_FILE" ]]; then
  node -e '
    const fs = require("fs");
    const filePath = process.argv[1];
    const pluginDir = process.argv[2];
    let content = fs.readFileSync(filePath, "utf8");
    content = content.replace(/\$\{CLAUDE_PLUGIN_ROOT\}/g, pluginDir);
    fs.writeFileSync(filePath, content);
  ' "$MONITORS_FILE" "$PLUGIN_DIR" 2>/dev/null || true
  echo "[clawmind] monitors.json adapted (absolute paths)"
fi

# ── Step 9: Fix disable-model-invocation in commands/ and skills/ ──
for md_file in "$PLUGIN_DIR"/commands/*.md "$PLUGIN_DIR"/skills/*/SKILL.md; do
  [[ -f "$md_file" ]] || continue
  if grep -q "disable-model-invocation: true" "$md_file" 2>/dev/null; then
    sed -i.bak 's/disable-model-invocation: true/disable-model-invocation: false/' "$md_file" 2>/dev/null || true
    rm -f "$md_file.bak"
    echo "[clawmind] Fixed disable-model-invocation in $(basename "$md_file")"
  fi
done

# ── Step 10: Export CLAUDE_CODE_EXECUTABLE ──
if [[ -n "$CLAUDE_BIN" ]]; then
  export CLAUDE_CODE_EXECUTABLE="$CLAUDE_BIN"
  ADAPTOR_ENV="/home/admin/.adaptorEnv"
  if [[ -w "$(dirname "$ADAPTOR_ENV" 2>/dev/null)" ]]; then
    echo "export CLAUDE_CODE_EXECUTABLE=\"$CLAUDE_BIN\"" >> "$ADAPTOR_ENV" 2>/dev/null || true
  fi
fi

echo ""
echo "[clawmind] ══════════════════════════════════════════"
echo "[clawmind] Installation complete!"
echo "[clawmind]"
echo "[clawmind] Plugin dir:   $PLUGIN_DIR"
echo "[clawmind] Marketplace:  $MARKETPLACE_DIR"
if [[ -n "$CLAUDE_BIN" ]]; then
echo "[clawmind] claude CLI:   $CLAUDE_BIN"
fi
echo "[clawmind]"
echo "[clawmind] Slash commands: /clawmind:help, /clawmind:workflow-dispatch"
echo "[clawmind] Restart Claude Code to activate."
echo "[clawmind] ══════════════════════════════════════════"