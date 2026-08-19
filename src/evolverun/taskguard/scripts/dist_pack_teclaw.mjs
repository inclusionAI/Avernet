#!/usr/bin/env node
/**
 * dist_pack_teclaw.mjs — 一键生成 ClawMind 在 TeClaw 平台下的 MCP 包。
 *
 * 用法:
 *   node scripts/dist_pack_teclaw.mjs                 # 完整流程: build → generate → pack
 *   node scripts/dist_pack_teclaw.mjs --skip-build    # 跳过 build，仅打包已有的 dist/
 *
 * 输出: <project-root>/dist_pack/teclaw/clawmind-teclaw-<version>.tgz
 *
 * 包内容:
 *   - dist/esm/             编译后的 ESM 代码
 *   - teclaw.plugin.json    TeClaw 插件清单
 *   - teclaw-config.json    适配器配置（自动生成）
 *   - mcp-config.json       MCP server 配置（自动生成）
 *   - packs/                工作流包定义
 *   - configs/              运行时配置
 *   - node_modules/         打包的运行时依赖（cron-parser, yaml, zod, @modelcontextprotocol/sdk）
 */

import { execSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
  readdirSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, basename, dirname, resolve } from "node:path";
import {
  bundleRuntimeDependencies,
  verifyRuntimeImports,
} from "./runtime-dependency-bundler.mjs";
import { copyCommunityPacks } from "./community-packs.mjs";

// ── 常量 ──

const ROOT_DIR = import.meta.dirname
  ? join(import.meta.dirname, "..")
  : process.cwd();
const CUSTOM_DIST_PACK_DIR = process.env.TECLAW_DIST_PACK_DIR;
const DIST_PACK_DIR = CUSTOM_DIST_PACK_DIR
  ? resolve(CUSTOM_DIST_PACK_DIR)
  : join(ROOT_DIR, "dist_pack", "teclaw");
const DIST_DIR = join(ROOT_DIR, "dist");
const DIST_ESM_DIR = join(DIST_DIR, "esm");

// ── 参数解析 ──

const args = process.argv.slice(2);
const SKIP_BUILD = args.includes("--skip-build");
const SKIP_UPLOAD = args.includes("--skip-upload");

validateCustomDistPackDir();

// ── 读取 package.json ──

const pkg = JSON.parse(readFileSync(join(ROOT_DIR, "package.json"), "utf8"));
const VERSION = pkg.version;
const PKG_NAME = pkg.name;

console.log("╔══════════════════════════════════════════╗");
console.log("║   ClawMind TeClaw MCP Pack Builder       ║");
console.log("╚══════════════════════════════════════════╝");
console.log(`  Package:   ${PKG_NAME}`);
console.log(`  Version:   ${VERSION}`);
console.log(`  Skip build: ${SKIP_BUILD}`);
console.log(`  Skip upload: ${SKIP_UPLOAD}`);
console.log("");

// ── Step 1: Build ──

if (!SKIP_BUILD) {
  console.log("[1/3] Building project...");
  execSync("npm run build", { cwd: ROOT_DIR, stdio: "inherit" });
  console.log("  ✅ Build complete\n");
} else {
  console.log("[1/3] Skipping build (--skip-build)");
  if (!existsSync(join(DIST_ESM_DIR, "index.js"))) {
    console.error("  ❌ ERROR: dist/esm/index.js not found. Run without --skip-build first.");
    process.exit(1);
  }
  console.log("  ✅ dist/esm/ exists\n");
}

// ── Step 2: Generate facade skills ──

console.log("[2/3] Generating facade skills...");
execSync("node scripts/generate-facade-skills.mjs", { cwd: ROOT_DIR, stdio: "inherit" });
console.log("  ✅ Facade skills generated\n");

// ── Step 3: Pack ──

console.log("[3/3] Packing TeClaw MCP package...\n");

// 清理旧的输出和临时目录
prepareDistPackDir();
const STAGING = mkdtempSync(join(tmpdir(), "clawmind-teclaw-pack-"));
process.once("exit", () => rmSync(STAGING, { recursive: true, force: true }));

const pkgDir = join(STAGING, "package");
mkdirSync(pkgDir, { recursive: true });

// 3.1 复制编译产物
mkdirSync(join(pkgDir, "dist", "esm"), { recursive: true });
cpSync(DIST_ESM_DIR, join(pkgDir, "dist", "esm"), { recursive: true });

if (existsSync(join(DIST_DIR, "package.json"))) {
  cpSync(join(DIST_DIR, "package.json"), join(pkgDir, "dist", "package.json"));
}
console.log("  📦 Copied dist/esm/");

// 3.2 复制 TeClaw 插件清单
if (existsSync(join(ROOT_DIR, "teclaw.plugin.json"))) {
  cpSync(join(ROOT_DIR, "teclaw.plugin.json"), join(pkgDir, "teclaw.plugin.json"));
  console.log("  📦 Copied teclaw.plugin.json");
}

// 3.3 生成 TeClaw 适配器配置
const teclawConfig = {
  adapter: "teclaw",
  entry: "./dist/esm/platform/mcp-entry.js",
  version: VERSION,
  description: "ClawMind workflow engine — TeClaw adapter",
};
writeFileSync(join(pkgDir, "teclaw-config.json"), JSON.stringify(teclawConfig, null, 2) + "\n");
console.log("  📦 Generated teclaw-config.json");

// 3.4 生成 MCP server 配置
const mcpConfig = {
  mcpServers: {
    clawmind: {
      command: "node",
      args: ["./dist/esm/platform/mcp-entry.js"],
      description: "ClawMind workflow engine — YAML DAG orchestration via MCP (TeClaw)",
    },
  },
};
writeFileSync(join(pkgDir, "mcp-config.json"), JSON.stringify(mcpConfig, null, 2) + "\n");
console.log("  📦 Generated mcp-config.json");

// 3.5 复制 packs 与 configs
copyCommunityPacks(ROOT_DIR, join(pkgDir, "packs"));
copyDirIfExists(join(ROOT_DIR, "configs"), join(pkgDir, "configs"), "configs/");

// 3.5.1 复制 install-clawmind.sh — `clawmind update` 工具运行时需要
const teclawInstallScript = join(ROOT_DIR, "scripts", "install-clawmind.sh");
if (existsSync(teclawInstallScript)) {
  mkdirSync(join(pkgDir, "scripts"), { recursive: true });
  cpSync(teclawInstallScript, join(pkgDir, "scripts", "install-clawmind.sh"));
  console.log("  📦 Copied scripts/install-clawmind.sh");
}

// 3.6 复制 README
if (existsSync(join(ROOT_DIR, "README.md"))) {
  cpSync(join(ROOT_DIR, "README.md"), join(pkgDir, "README.md"));
}

// 3.7 打包运行时依赖
bundleDeps(pkgDir, pkg.bundleDependencies || []);

// 3.8 写入 package.json
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
  files: [
    "dist/",
    "packs/",
    "configs/",
    "scripts/",
    "teclaw-config.json",
    "teclaw.plugin.json",
    "mcp-config.json",
    "node_modules/",
  ],
  bundleDependencies: pkg.bundleDependencies,
  keywords: [...(pkg.keywords || []), "teclaw", "mcp"],
  license: pkg.license,
};
writeFileSync(join(pkgDir, "package.json"), JSON.stringify(tarballPkg, null, 2) + "\n");
console.log("  📦 Generated package.json");

// 3.9 创建 tgz
const outputFile = join(DIST_PACK_DIR, `clawmind-teclaw-${VERSION}.tgz`);
createTgz(STAGING, "package", outputFile);

// 3.10 清理临时目录
rmSync(STAGING, { recursive: true, force: true });

// 3.11 上传到 OSS
if (SKIP_UPLOAD) {
  console.log("  Skipped OSS upload (--skip-upload)");
} else {
  uploadToOss(outputFile);
}

// ── 输出摘要 ──

console.log("\n╔══════════════════════════════════════════╗");
console.log("║   ✅ Pack Complete                        ║");
console.log("╚══════════════════════════════════════════╝");

const outputFiles = readdirSync(DIST_PACK_DIR);
for (const f of outputFiles) {
  const fpath = join(DIST_PACK_DIR, f);
  const size = statSync(fpath).size;
  const sizeStr = size > 1024 * 1024
    ? `${(size / (1024 * 1024)).toFixed(1)} MB`
    : `${(size / 1024).toFixed(0)} KB`;
  console.log(`  ${sizeStr.padEnd(10)} ${f}`);
}

console.log(`\n  Output: ${DIST_PACK_DIR}`);
console.log("");

// ═══════════════════════════════════════════════════════════════════════
// 辅助函数
// ═══════════════════════════════════════════════════════════════════════

function validateCustomDistPackDir() {
  if (!CUSTOM_DIST_PACK_DIR) return;

  const invalidRoot = dirname(DIST_PACK_DIR) === DIST_PACK_DIR;
  const invalidProjectRoot = DIST_PACK_DIR === resolve(ROOT_DIR);
  const invalidExistingDir = existsSync(DIST_PACK_DIR)
    && (!statSync(DIST_PACK_DIR).isDirectory() || readdirSync(DIST_PACK_DIR).length > 0);

  if (invalidRoot || invalidProjectRoot || invalidExistingDir) {
    throw new Error(
      `Invalid TECLAW_DIST_PACK_DIR: expected a missing or empty custom directory, received ${DIST_PACK_DIR}`,
    );
  }
}

function prepareDistPackDir() {
  if (CUSTOM_DIST_PACK_DIR) {
    validateCustomDistPackDir();
  } else {
    rmSync(DIST_PACK_DIR, { recursive: true, force: true });
  }
  mkdirSync(DIST_PACK_DIR, { recursive: true });
}

function copyDirIfExists(src, dest, label) {
  if (existsSync(src)) {
    cpSync(src, dest, { recursive: true });
    const count = readdirSync(dest).length;
    console.log(`  📦 Copied ${label} (${count} items)`);
  }
}

function bundleDeps(pkgDir, rootDependencies) {
  if (!rootDependencies || rootDependencies.length === 0) return;

  console.log("  Bundling runtime dependency closure...");
  const bundledPackages = bundleRuntimeDependencies({
    rootDir: ROOT_DIR,
    packageDir: pkgDir,
    rootDependencies,
    log: (packageName) => console.log(`     Bundled: ${packageName}`),
  });

  verifyRuntimeImports({
    packageDir: pkgDir,
    moduleSpecifiers: ["@modelcontextprotocol/sdk/server/sse.js"],
  });
  console.log(`  Runtime import verification passed (${bundledPackages.length} packages)`);
}

function uploadToOss(localFile) {
  const OSS_BUCKET = "alps-risk-com";
  const OSS_PATH = `clawmind/${basename(localFile)}`;
  const OSS_URL = `oss://${OSS_BUCKET}/${OSS_PATH}`;
  const HTTP_URL = process.env.OSS_HTTP_URL || `https://${OSS_BUCKET}.${process.env.OSS_ENDPOINT || "oss.example.com"}/${OSS_PATH}`;

  const ossutil = process.env.OSSUTIL_PATH || "ossutil";

  console.log(`  📤 Uploading to OSS: ${OSS_URL}`);
  try {
    execSync(`${ossutil} cp "${localFile}" ${OSS_URL} -f`, { encoding: "utf8" });
    console.log(`  ✅ Uploaded: ${HTTP_URL}`);
  } catch (err) {
    console.error(`  ⚠️  OSS upload failed (non-fatal): ${err.message?.split("\n")[0]}`);
    console.error(`     Upload manually: ${ossutil} cp "${localFile}" ${OSS_URL} -f`);
  }
}

function createTgz(stagingDir, sourceDir, outputFile) {
  execSync(`tar -czf "${outputFile}" ${sourceDir}/`, {
    cwd: stagingDir,
    encoding: "utf8",
  });
  if (existsSync(outputFile)) {
    const size = statSync(outputFile).size;
    const sizeStr = size > 1024 * 1024
      ? `${(size / (1024 * 1024)).toFixed(1)} MB`
      : `${(size / 1024).toFixed(0)} KB`;
    console.log(`  📦 Created ${basename(outputFile)} (${sizeStr})`);
  } else {
    console.error(`  ❌ Failed to create ${outputFile}`);
    process.exit(1);
  }
}
