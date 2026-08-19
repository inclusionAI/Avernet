#!/usr/bin/env node
/**
 * dist_pack.mjs — Package ClawMind for different platforms.
 *
 * Usage:
 *   node scripts/dist_pack.mjs                 # Build + pack all platforms
 *   node scripts/dist_pack.mjs --skip-build    # Skip build, pack existing dist/
 *   node scripts/dist_pack.mjs openclaw        # Only pack openclaw platform
 *   node scripts/dist_pack.mjs claudecode      # Only pack claudecode platform
 *
 * Output: <project-root>/dist_pack/<platform>/
 *   - openclaw/    → clawmind-<version>.tgz (OpenClaw plugin, npm pack compatible)
 *   - claudecode/  → clawmind-claudecode-<version>.tgz (Claude Code MCP server)
 *   - hermes/      → clawmind-hermes-<version>.tgz (Hermes adapter)
 *   - teclaw/      → clawmind-teclaw-<version>.tgz (TeClaw adapter)
 */

import { execSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
  readdirSync,
  statSync,
} from "node:fs";
import { join, basename, relative } from "node:path";
import { copyCommunityPacks } from "./community-packs.mjs";

// ── Config ──

const ROOT_DIR = import.meta.dirname
  ? join(import.meta.dirname, "..")
  : process.cwd();
const DIST_PACK_DIR = join(ROOT_DIR, "dist_pack");
const DIST_DIR = join(ROOT_DIR, "dist");
const DIST_ESM_DIR = join(DIST_DIR, "esm");

const PLATFORMS = ["openclaw", "claudecode", "hermes", "teclaw"];

// Parse args
const args = process.argv.slice(2);
const SKIP_BUILD = args.includes("--skip-build");
const TARGET_PLATFORM = args.find((a) => !a.startsWith("--")) || null;

if (TARGET_PLATFORM && !PLATFORMS.includes(TARGET_PLATFORM)) {
  console.error(`ERROR: Unknown platform "${TARGET_PLATFORM}". Valid: ${PLATFORMS.join(", ")}`);
  process.exit(1);
}

const platforms = TARGET_PLATFORM ? [TARGET_PLATFORM] : PLATFORMS;

// Read package.json
const pkg = JSON.parse(readFileSync(join(ROOT_DIR, "package.json"), "utf8"));
const VERSION = pkg.version;
const PKG_NAME = pkg.name;

console.log("=== ClawMind dist_pack ===");
console.log(`Package:  ${PKG_NAME}`);
console.log(`Version:  ${VERSION}`);
console.log(`Platforms: ${platforms.join(", ")}`);
console.log(`Skip build: ${SKIP_BUILD}`);
console.log("");

// ── Step 1: Build ──

if (!SKIP_BUILD) {
  console.log("[1/3] Building project...");
  execSync("npm run build", { cwd: ROOT_DIR, stdio: "inherit" });
  console.log("");
} else {
  console.log("[1/3] Skipping build (--skip-build)");
  if (!existsSync(join(DIST_ESM_DIR, "index.js"))) {
    console.error("ERROR: dist/esm/index.js not found. Run without --skip-build first.");
    process.exit(1);
  }
}

// ── Step 2: Generate facade skills ──

console.log("[2/3] Generating facade skills...");
execSync("node scripts/generate-facade-skills.mjs", { cwd: ROOT_DIR, stdio: "inherit" });
console.log("");

// ── Step 3: Pack each platform ──

console.log("[3/3] Packing platforms...\n");

for (const platform of platforms) {
  console.log(`── ${platform} ──`);
  const platformDir = join(DIST_PACK_DIR, platform);
  rmSync(platformDir, { recursive: true, force: true });
  mkdirSync(platformDir, { recursive: true });

  switch (platform) {
    case "openclaw":
      packOpenClaw(platformDir);
      break;
    case "claudecode":
      packClaudeCode(platformDir);
      break;
    case "hermes":
      packHermes(platformDir);
      break;
    case "teclaw":
      packTeClaw(platformDir);
      break;
  }

  console.log("");
}

// ── Summary ──

console.log("=== Done ===");
for (const platform of platforms) {
  const platformDir = join(DIST_PACK_DIR, platform);
  const files = readdirSync(platformDir);
  for (const f of files) {
    const fpath = join(platformDir, f);
    const size = statSync(fpath).size;
    const sizeStr = size > 1024 * 1024
      ? `${(size / (1024 * 1024)).toFixed(1)}MB`
      : `${(size / 1024).toFixed(0)}KB`;
    console.log(`  ${platform}/${f} (${sizeStr})`);
  }
}
console.log("");

// ═══════════════════════════════════════════════════════════════════════
// Platform packers
// ═══════════════════════════════════════════════════════════════════════

/**
 * OpenClaw: Full plugin tgz (same as pack-tgz.sh output).
 *
 * Contains: dist/esm/, openclaw.plugin.json, packs/, configs/, skills/,
 * bundled node_modules (cron-parser, yaml, zod).
 */
function packOpenClaw(platformDir) {
  const STAGING = join(ROOT_DIR, ".pack-staging-openclaw");
  rmSync(STAGING, { recursive: true, force: true });
  const pkgDir = join(STAGING, "package");
  mkdirSync(pkgDir, { recursive: true });

  // Copy compiled output
  mkdirSync(join(pkgDir, "dist", "esm"), { recursive: true });
  cpSync(DIST_ESM_DIR, join(pkgDir, "dist", "esm"), { recursive: true });
  if (existsSync(join(DIST_DIR, "package.json"))) {
    cpSync(join(DIST_DIR, "package.json"), join(pkgDir, "dist", "package.json"));
  }
  console.log("  Copied dist/esm/");

  // Plugin manifest
  cpSync(join(ROOT_DIR, "openclaw.plugin.json"), join(pkgDir, "openclaw.plugin.json"));
  console.log("  Copied openclaw.plugin.json");

  // Packs, configs, skills
  copyCommunityPacks(ROOT_DIR, join(pkgDir, "packs"));
  copyDirIfExists(join(ROOT_DIR, "configs"), join(pkgDir, "configs"), "configs/");
  copyDirIfExists(join(ROOT_DIR, "skills"), join(pkgDir, "skills"), "skills/");

  // install-clawmind.sh — needed by the `clawmind update` tool at runtime
  const installScriptPath = join(ROOT_DIR, "scripts", "install-clawmind.sh");
  if (existsSync(installScriptPath)) {
    mkdirSync(join(pkgDir, "scripts"), { recursive: true });
    cpSync(installScriptPath, join(pkgDir, "scripts", "install-clawmind.sh"));
    console.log("  Copied scripts/install-clawmind.sh");
  }

  // Docs
  if (existsSync(join(ROOT_DIR, "README.md"))) {
    cpSync(join(ROOT_DIR, "README.md"), join(pkgDir, "README.md"));
  }

  // Bundle dependencies
  bundleDeps(pkgDir, pkg.bundleDependencies || []);

  // Write package.json
  const tarballPkg = {
    name: PKG_NAME,
    version: VERSION,
    description: pkg.description,
    type: "module",
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
    files: ["openclaw.plugin.json", "dist/", "packs/", "configs/", "skills/", "scripts/", "node_modules/"],
    bundleDependencies: pkg.bundleDependencies,
    keywords: pkg.keywords,
    license: pkg.license,
  };
  writeFileSync(join(pkgDir, "package.json"), JSON.stringify(tarballPkg, null, 2) + "\n");

  // Create tgz
  const outputFile = join(platformDir, `clawmind-${VERSION}.tgz`);
  createTgz(STAGING, "package", outputFile);

  // Cleanup
  rmSync(STAGING, { recursive: true, force: true });
}

/**
 * Claude Code: MCP server + Plugin package.
 *
 * Contains: dist/esm/ (core engine with Agent SDK runner), .claude-plugin/,
 * .mcp.json, skills/, agents/, hooks/, monitors/, settings.json,
 * configs/, packs/, and install.sh for one-command setup.
 *
 * TWO modes of use:
 * 1. **MCP Server** — Add mcp-config.json to Claude Code's MCP servers
 * 2. **Plugin** — Install as a Claude Code Plugin (preferred)
 */
function packClaudeCode(platformDir) {
  const STAGING = join(ROOT_DIR, ".pack-staging-claudecode");
  rmSync(STAGING, { recursive: true, force: true });
  const pkgDir = join(STAGING, "package");
  mkdirSync(pkgDir, { recursive: true });

  // Copy full compiled output (adapter layer selects platform at runtime)
  mkdirSync(join(pkgDir, "dist", "esm"), { recursive: true });
  cpSync(DIST_ESM_DIR, join(pkgDir, "dist", "esm"), { recursive: true });
  if (existsSync(join(DIST_DIR, "package.json"))) {
    cpSync(join(DIST_DIR, "package.json"), join(pkgDir, "dist", "package.json"));
  }
  console.log("  Copied dist/esm/");

  // ── Claude Code Plugin structure (flattened into pkgDir root) ──
  // Claude Code expects .claude-plugin/, commands/, skills/, hooks/ at the
  // CLAUDE_PLUGIN_ROOT level. We flatten clawmind-plugin/ contents into
  // the tgz root so that installPath can point directly at the cache dir
  // (e.g. .../clawmind/0.1.0) and all plugin resources are discoverable.
  // Symlinks in clawmind-plugin/ (dist, packs, configs, node_modules) are
  // skipped because their targets are already copied separately into pkgDir.
  const pluginDir = join(ROOT_DIR, "clawmind-plugin");
  if (existsSync(pluginDir)) {
    flattenPluginDir(pluginDir, pkgDir);
    console.log("  Flattened clawmind-plugin/ into pkg root");
  }

  // ── Generate dynamic facade commands at pack time ──
  // Claude Code scans commands/ before SessionStart hook runs, so facade
  // commands must be present in the tgz at install time. We run --init
  // against the staging directory to pre-populate commands/ and skills/
  // with all dynamic facades.
  try {
    const initCmd = `node "${join(pkgDir, "dist", "esm", "platform", "mcp-entry.js")}" --init --plugin-root "${pkgDir}"`;
    console.log("  Generating dynamic facade commands...");
    execSync(initCmd, { cwd: ROOT_DIR, stdio: "pipe", timeout: 15000 });
    console.log("  Generated dynamic facade commands into staging");
  } catch (err) {
    console.log("  WARNING: Facade command generation failed (non-fatal):");
    console.log(`    ${err.message?.split("\n")[0] || err}`);
  }

  // ── Claude Code MCP manifest (legacy — for non-plugin mode) ──
  const mcpConfig = {
    mcpServers: {
      clawmind: {
        command: "node",
        args: ["./dist/esm/platform/mcp-entry.js"],
        description: "ClawMind workflow engine — YAML DAG orchestration via MCP",
        env: {
          SKILL_ROOT: "./packs",
          DATABASE_MODE: "sqlite",
          SQLITE_PATH: "~/.openclaw/workflow/engine.db",
          MCP_TRANSPORT: "stdio",
          CCT_SOP_MCP_SERVER_MODE: "local",
        },
      },
    },
  };
  writeFileSync(join(pkgDir, "mcp-config.json"), JSON.stringify(mcpConfig, null, 2) + "\n");
  console.log("  Generated mcp-config.json");

  // ── Install script ──
  writeFileSync(join(pkgDir, "install.sh"), generateInstallScript(VERSION) + "\n");
  console.log("  Generated install.sh");

  // Packs & configs (needed for workflow definitions)
  copyCommunityPacks(ROOT_DIR, join(pkgDir, "packs"));
  copyDirIfExists(join(ROOT_DIR, "configs"), join(pkgDir, "configs"), "configs/");

  // install-clawmind.sh — needed by the `clawmind update` tool at runtime
  const claudecodeInstallScript = join(ROOT_DIR, "scripts", "install-clawmind.sh");
  if (existsSync(claudecodeInstallScript)) {
    mkdirSync(join(pkgDir, "scripts"), { recursive: true });
    cpSync(claudecodeInstallScript, join(pkgDir, "scripts", "install-clawmind.sh"));
    console.log("  Copied scripts/install-clawmind.sh");
  }

  if (existsSync(join(ROOT_DIR, "README.md"))) {
    cpSync(join(ROOT_DIR, "README.md"), join(pkgDir, "README.md"));
  }

  // Bundle dependencies
  bundleDeps(pkgDir, pkg.bundleDependencies || []);

  // Write package.json
  const tarballPkg = {
    name: `${PKG_NAME}-claudecode`,
    version: VERSION,
    description: `${pkg.description} — Claude Code Plugin + MCP Server package`,
    type: "module",
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
    files: ["dist/", "packs/", "configs/", "scripts/", "mcp-config.json", "commands/", "skills/", "hooks/", "agents/", ".claude-plugin/", ".mcp.json", "install.sh", "node_modules/"],
    bundleDependencies: pkg.bundleDependencies,
    keywords: [...(pkg.keywords || []), "claude-code", "mcp", "plugin"],
    license: pkg.license,
  };
  writeFileSync(join(pkgDir, "package.json"), JSON.stringify(tarballPkg, null, 2) + "\n");

  // Create tgz
  const outputFile = join(platformDir, `clawmind-claudecode-${VERSION}.tgz`);
  createTgz(STAGING, "package", outputFile);

  // Cleanup
  rmSync(STAGING, { recursive: true, force: true });
}

/**
 * Generate the install.sh script that configures Claude Code to load ClawMind.
 *
 * Two modes:
 * 1. Plugin mode (preferred): Copies plugin files and registers via Claude Code settings
 * 2. MCP mode (legacy): Adds MCP server entry to Claude Code settings
 */
function generateInstallScript(version) {
  return `#!/usr/bin/env bash
# ClawMind for Claude Code — Installer (v${version})
#
# Usage:
#   ./install.sh              # Install as Claude Code Plugin (recommended)
#   ./install.sh --mcp        # Install as MCP Server only (legacy)
#   ./install.sh --uninstall  # Remove ClawMind configuration
#
set -euo pipefail

VERSION="${version}"
SCRIPT_DIR="$(cd "$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_CONFIG_DIR="\${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CLAUDE_SETTINGS="\${CLAUDE_CONFIG_DIR}/settings.json"

# ── Colors ──
RED='\\033[0;31m'
GREEN='\\033[0;32m'
YELLOW='\\033[1;33m'
NC='\\033[0m' # No Color

info()  { echo -e "\${GREEN}[clawmind]\${NC} $*"; }
warn()  { echo -e "\${YELLOW}[clawmind]\${NC} $*"; }
error() { echo -e "\${RED}[clawmind]\${NC} $*" >&2; }

# ── Parse args ──
MODE="plugin"
UNINSTALL=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --mcp)       MODE="mcp";    shift ;;
    --uninstall) UNINSTALL=true; shift ;;
    --help|-h)   echo "Usage: $0 [--mcp] [--uninstall]"; exit 0 ;;
    *)           error "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Uninstall ──
if [[ "$UNINSTALL" == true ]]; then
  info "Removing ClawMind configuration from Claude Code..."

  if [[ -f "$CLAUDE_SETTINGS" ]]; then
    # Use node to safely modify JSON
    node -e "
      const fs = require('fs');
      const path = '$CLAUDE_SETTINGS';
      let settings = {};
      try { settings = JSON.parse(fs.readFileSync(path, 'utf8')); } catch {}

      // Remove MCP server entry
      if (settings.mcpServers?.clawmind) {
        delete settings.mcpServers.clawmind;
        console.log('Removed MCP server: clawmind');
      }

      fs.writeFileSync(path, JSON.stringify(settings, null, 2) + '\\\\n');
    "
    info "Claude Code settings updated."
  fi

  # Remove plugin directory
  PLUGIN_DIR="\${CLAUDE_CONFIG_DIR}/plugins/clawmind"
  if [[ -d "$PLUGIN_DIR" ]]; then
    rm -rf "$PLUGIN_DIR"
    info "Removed plugin directory: $PLUGIN_DIR"
  fi

  info "Uninstall complete."
  exit 0
fi

# ── Ensure settings.json exists ──
if [[ ! -f "$CLAUDE_SETTINGS" ]]; then
  mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
  echo '{}' > "$CLAUDE_SETTINGS"
  info "Created $CLAUDE_SETTINGS"
fi

# ── Plugin mode (recommended) ──
if [[ "$MODE" == "plugin" ]]; then
  info "Installing ClawMind as Claude Code Plugin (v$VERSION)..."

  PLUGIN_DIR="\${CLAUDE_CONFIG_DIR}/plugins/clawmind"
  mkdir -p "$PLUGIN_DIR"

  # Copy plugin files from clawmind-plugin/
  if [[ -d "\${SCRIPT_DIR}/clawmind-plugin" ]]; then
    cp -r "\${SCRIPT_DIR}/clawmind-plugin/"* "$PLUGIN_DIR/"
    info "Copied plugin files to $PLUGIN_DIR"
  else
    warn "clawmind-plugin/ directory not found, generating inline..."

    # Generate .claude-plugin/plugin.json
    mkdir -p "\${PLUGIN_DIR}/.claude-plugin"
    cat > "\${PLUGIN_DIR}/.claude-plugin/plugin.json" << 'PLUGIN_JSON'
{
  "name": "clawmind",
  "version": "${version}",
  "description": "taskguard — YAML-based DAG workflow orchestration engine for Claude Code",
  "author": "taskguard contributors",
  "homepage": "https://github.com/avernet/taskguard",
  "license": "Apache-2.0"
}
PLUGIN_JSON

    # Generate .mcp.json
    cat > "\${PLUGIN_DIR}/.mcp.json" << 'MCP_JSON'
{
  "mcpServers": {
    "clawmind": {
      "command": "node",
      "args": ["\${CLAUDE_PLUGIN_ROOT}/dist/esm/platform/mcp-entry.js"],
      "env": {
        "SKILL_ROOT": "\${CLAUDE_PLUGIN_ROOT}/packs",
        "DATABASE_MODE": "sqlite",
        "SQLITE_PATH": "~/.openclaw/workflow/engine.db",
        "MCP_TRANSPORT": "stdio",
        "CCT_SOP_MCP_SERVER_MODE": "local",
        "ANTHROPIC_API_KEY": "\${ANTHROPIC_API_KEY}"
      }
    }
  }
}
MCP_JSON

    # Generate settings.json
    echo '{ "subagentStatusLine": "clawmind: 工作流引擎就绪" }' > "\${PLUGIN_DIR}/settings.json"

    info "Generated plugin files."
  fi

  # Copy dist/ and packs/ to plugin directory
  if [[ -d "\${SCRIPT_DIR}/dist" ]]; then
    cp -r "\${SCRIPT_DIR}/dist" "$PLUGIN_DIR/"
    info "Copied dist/ to plugin directory"
  fi
  if [[ -d "\${SCRIPT_DIR}/packs" ]]; then
    cp -r "\${SCRIPT_DIR}/packs" "$PLUGIN_DIR/"
    info "Copied packs/ to plugin directory"
  fi
  if [[ -d "\${SCRIPT_DIR}/configs" ]]; then
    cp -r "\${SCRIPT_DIR}/configs" "$PLUGIN_DIR/"
    info "Copied configs/ to plugin directory"
  fi
  if [[ -d "\${SCRIPT_DIR}/node_modules" ]]; then
    cp -r "\${SCRIPT_DIR}/node_modules" "$PLUGIN_DIR/"
    info "Copied node_modules/ to plugin directory"
  fi

  # Ensure SQLite directory exists
  mkdir -p "$HOME/.openclaw/workflow"
  info "Ensured ~/.openclaw/workflow/ exists for SQLite database"

  info ""
  info "╔══════════════════════════════════════════════════════╗"
  info "║  ClawMind Plugin installed successfully! (v$VERSION)  ║"
  info "╠══════════════════════════════════════════════════════╣"
  info "║                                                      ║"
  info "║  Location: $PLUGIN_DIR"
  info "║                                                      ║"
  info "║  Next steps:                                         ║"
  info "║  1. Restart Claude Code to load the plugin           ║"
  info "║  2. Set ANTHROPIC_API_KEY for Agent SDK (optional)   ║"
  info "║     export ANTHROPIC_API_KEY=sk-...                  ║"
  info "║  3. Try: /workflow-help                              ║"
  info "║                                                      ║"
  info "╚══════════════════════════════════════════════════════╝"

# ── MCP mode (legacy) ──
else
  info "Installing ClawMind as MCP Server (legacy mode, v$VERSION)..."

  # Add MCP server to Claude Code settings
  node -e "
    const fs = require('fs');
    const path = '$CLAUDE_SETTINGS';
    let settings = {};
    try { settings = JSON.parse(fs.readFileSync(path, 'utf8')); } catch {}

    if (!settings.mcpServers) settings.mcpServers = {};

    settings.mcpServers.clawmind = {
      command: 'node',
      args: ['$PLUGIN_DIR/dist/esm/platform/mcp-entry.js'],
      description: 'ClawMind workflow engine — YAML DAG orchestration via MCP',
      env: {
        SKILL_ROOT: '$PLUGIN_DIR/packs',
        DATABASE_MODE: 'sqlite',
        SQLITE_PATH: '~/.openclaw/workflow/engine.db',
        MCP_TRANSPORT: 'stdio',
        CCT_SOP_MCP_SERVER_MODE: 'local',
      },
    };

    fs.writeFileSync(path, JSON.stringify(settings, null, 2) + '\\\\n');
    console.log('Added MCP server: clawmind');
  "

  # Copy dist/ and packs/ to plugin directory
  mkdir -p "$PLUGIN_DIR"
  if [[ -d "\${SCRIPT_DIR}/dist" ]]; then cp -r "\${SCRIPT_DIR}/dist" "$PLUGIN_DIR/"; fi
  if [[ -d "\${SCRIPT_DIR}/packs" ]]; then cp -r "\${SCRIPT_DIR}/packs" "$PLUGIN_DIR/"; fi
  if [[ -d "\${SCRIPT_DIR}/configs" ]]; then cp -r "\${SCRIPT_DIR}/configs" "$PLUGIN_DIR/"; fi
  if [[ -d "\${SCRIPT_DIR}/node_modules" ]]; then cp -r "\${SCRIPT_DIR}/node_modules" "$PLUGIN_DIR/"; fi

  mkdir -p "$HOME/.openclaw/workflow"

  info ""
  info "ClawMind MCP Server configured (legacy mode)."
  info "Restart Claude Code to use the clawmind MCP server."
fi
`;
}

/**
 * Hermes: Adapter package for Hermes platform.
 *
 * Contains: dist/esm/ (core engine), hermes manifest, configs/, packs/.
 */
function packHermes(platformDir) {
  const STAGING = join(ROOT_DIR, ".pack-staging-hermes");
  rmSync(STAGING, { recursive: true, force: true });
  const pkgDir = join(STAGING, "package");
  mkdirSync(pkgDir, { recursive: true });

  // Copy compiled output
  mkdirSync(join(pkgDir, "dist", "esm"), { recursive: true });
  cpSync(DIST_ESM_DIR, join(pkgDir, "dist", "esm"), { recursive: true });
  if (existsSync(join(DIST_DIR, "package.json"))) {
    cpSync(join(DIST_DIR, "package.json"), join(pkgDir, "dist", "package.json"));
  }
  console.log("  Copied dist/esm/");

  // Hermes plugin manifest
  if (existsSync(join(ROOT_DIR, "hermes.plugin.json"))) {
    cpSync(join(ROOT_DIR, "hermes.plugin.json"), join(pkgDir, "hermes.plugin.json"));
    console.log("  Copied hermes.plugin.json");
  }

  // Hermes adapter manifest
  const hermesConfig = {
    adapter: "hermes",
    entry: "./dist/esm/index.js",
    version: VERSION,
    description: "ClawMind workflow engine — Hermes adapter",
  };
  writeFileSync(join(pkgDir, "hermes-config.json"), JSON.stringify(hermesConfig, null, 2) + "\n");
  console.log("  Generated hermes-config.json");

  // Packs & configs
  copyCommunityPacks(ROOT_DIR, join(pkgDir, "packs"));
  copyDirIfExists(join(ROOT_DIR, "configs"), join(pkgDir, "configs"), "configs/");

  // install-clawmind.sh — needed by the `clawmind update` tool at runtime
  const hermesInstallScript = join(ROOT_DIR, "scripts", "install-clawmind.sh");
  if (existsSync(hermesInstallScript)) {
    mkdirSync(join(pkgDir, "scripts"), { recursive: true });
    cpSync(hermesInstallScript, join(pkgDir, "scripts", "install-clawmind.sh"));
    console.log("  Copied scripts/install-clawmind.sh");
  }

  if (existsSync(join(ROOT_DIR, "README.md"))) {
    cpSync(join(ROOT_DIR, "README.md"), join(pkgDir, "README.md"));
  }

  // Bundle dependencies
  bundleDeps(pkgDir, pkg.bundleDependencies || []);

  // Write package.json
  const tarballPkg = {
    name: `${PKG_NAME}-hermes`,
    version: VERSION,
    description: `${pkg.description} — Hermes adapter package`,
    type: "module",
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
    files: ["dist/", "packs/", "configs/", "scripts/", "hermes-config.json", "hermes.plugin.json", "node_modules/"],
    bundleDependencies: pkg.bundleDependencies,
    keywords: [...(pkg.keywords || []), "hermes"],
    license: pkg.license,
  };
  writeFileSync(join(pkgDir, "package.json"), JSON.stringify(tarballPkg, null, 2) + "\n");

  // Create tgz
  const outputFile = join(platformDir, `clawmind-hermes-${VERSION}.tgz`);
  createTgz(STAGING, "package", outputFile);

  // Cleanup
  rmSync(STAGING, { recursive: true, force: true });
}

/**
 * TeClaw: Adapter package for TeClaw platform.
 *
 * Contains: dist/esm/ (core engine), teclaw manifest, configs/, packs/.
 */
function packTeClaw(platformDir) {
  const STAGING = join(ROOT_DIR, ".pack-staging-teclaw");
  rmSync(STAGING, { recursive: true, force: true });
  const pkgDir = join(STAGING, "package");
  mkdirSync(pkgDir, { recursive: true });

  // Copy compiled output
  mkdirSync(join(pkgDir, "dist", "esm"), { recursive: true });
  cpSync(DIST_ESM_DIR, join(pkgDir, "dist", "esm"), { recursive: true });
  if (existsSync(join(DIST_DIR, "package.json"))) {
    cpSync(join(DIST_DIR, "package.json"), join(pkgDir, "dist", "package.json"));
  }
  console.log("  Copied dist/esm/");

  // TeClaw plugin manifest
  if (existsSync(join(ROOT_DIR, "teclaw.plugin.json"))) {
    cpSync(join(ROOT_DIR, "teclaw.plugin.json"), join(pkgDir, "teclaw.plugin.json"));
    console.log("  Copied teclaw.plugin.json");
  }

  // TeClaw adapter manifest
  const teclawConfig = {
    adapter: "teclaw",
    entry: "./dist/esm/index.js",
    version: VERSION,
    description: "ClawMind workflow engine — TeClaw adapter",
  };
  writeFileSync(join(pkgDir, "teclaw-config.json"), JSON.stringify(teclawConfig, null, 2) + "\n");
  console.log("  Generated teclaw-config.json");

  // Packs & configs
  copyCommunityPacks(ROOT_DIR, join(pkgDir, "packs"));
  copyDirIfExists(join(ROOT_DIR, "configs"), join(pkgDir, "configs"), "configs/");

  // install-clawmind.sh — needed by the `clawmind update` tool at runtime
  const teclawInstallScript = join(ROOT_DIR, "scripts", "install-clawmind.sh");
  if (existsSync(teclawInstallScript)) {
    mkdirSync(join(pkgDir, "scripts"), { recursive: true });
    cpSync(teclawInstallScript, join(pkgDir, "scripts", "install-clawmind.sh"));
    console.log("  Copied scripts/install-clawmind.sh");
  }

  if (existsSync(join(ROOT_DIR, "README.md"))) {
    cpSync(join(ROOT_DIR, "README.md"), join(pkgDir, "README.md"));
  }

  // Bundle dependencies
  bundleDeps(pkgDir, pkg.bundleDependencies || []);

  // Write package.json
  const tarballPkg = {
    name: `${PKG_NAME}-teclaw`,
    version: VERSION,
    description: `${pkg.description} — TeClaw adapter package`,
    type: "module",
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
    files: ["dist/", "packs/", "configs/", "scripts/", "teclaw-config.json", "teclaw.plugin.json", "node_modules/"],
    bundleDependencies: pkg.bundleDependencies,
    keywords: [...(pkg.keywords || []), "teclaw"],
    license: pkg.license,
  };
  writeFileSync(join(pkgDir, "package.json"), JSON.stringify(tarballPkg, null, 2) + "\n");

  // Create tgz
  const outputFile = join(platformDir, `clawmind-teclaw-${VERSION}.tgz`);
  createTgz(STAGING, "package", outputFile);

  // Cleanup
  rmSync(STAGING, { recursive: true, force: true });
}

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

/**
 * Flatten clawmind-plugin/ contents into the target directory.
 * Copies real files/dirs, skips symlinks (their targets are copied separately),
 * and merges into the target without creating a subdirectory wrapper.
 */
function flattenPluginDir(srcDir, destDir) {
  // Directories that are already copied separately into pkgDir — skip them
  const SKIP_ENTRIES = new Set(["dist", "packs", "configs", "node_modules"]);

  for (const entry of readdirSync(srcDir, { withFileTypes: true })) {
    if (SKIP_ENTRIES.has(entry.name)) {
      console.log(`    Skipped ${entry.name}/ (already copied separately)`);
      continue;
    }

    // Check for symlinks — skip them (their targets are already in pkgDir)
    const fullPath = join(srcDir, entry.name);
    if (lstatSync(fullPath).isSymbolicLink()) {
      console.log(`    Skipped symlink: ${entry.name}`);
      continue;
    }

    const destPath = join(destDir, entry.name);

    if (entry.isDirectory()) {
      // For directories that already exist in dest (e.g. commands/, skills/),
      // merge contents rather than overwrite the entire directory
      if (existsSync(destPath)) {
        console.log(`    Merged ${entry.name}/ (dest exists)`);
        cpSync(fullPath, destPath, { recursive: true, force: true });
      } else {
        cpSync(fullPath, destPath, { recursive: true });
        console.log(`    Copied ${entry.name}/`);
      }
    } else if (entry.isFile()) {
      cpSync(fullPath, destPath);
      console.log(`    Copied ${entry.name}`);
    }
  }
}

function copyDirIfExists(src, dest, label) {
  if (existsSync(src)) {
    cpSync(src, dest, { recursive: true });
    const count = readdirSync(dest).length;
    console.log(`  Copied ${label} (${count} items)`);
  }
}

function bundleDeps(pkgDir, bundleDeps) {
  if (!bundleDeps || bundleDeps.length === 0) return;

  console.log("  Bundling dependencies...");
  const nodeModulesDir = join(pkgDir, "node_modules");
  mkdirSync(nodeModulesDir, { recursive: true });

  for (const dep of bundleDeps) {
    const src = join(ROOT_DIR, "node_modules", dep);
    if (existsSync(src)) {
      cpSync(src, join(nodeModulesDir, dep), { recursive: true });
      console.log(`    Bundled: ${dep}`);
    } else {
      console.log(`    WARNING: ${dep} not found in node_modules, skipping`);
    }
  }
}

function createTgz(stagingDir, sourceDir, outputFile) {
  // Use npm pack to create a proper tgz
  const result = execSync(`tar -czf "${outputFile}" ${sourceDir}/`, {
    cwd: stagingDir,
    encoding: "utf8",
  });
  if (existsSync(outputFile)) {
    const size = statSync(outputFile).size;
    const sizeStr = size > 1024 * 1024
      ? `${(size / (1024 * 1024)).toFixed(1)}MB`
      : `${(size / 1024).toFixed(0)}KB`;
    console.log(`  Created ${basename(outputFile)} (${sizeStr})`);
  } else {
    console.error(`  ERROR: Failed to create ${outputFile}`);
    process.exit(1);
  }
}