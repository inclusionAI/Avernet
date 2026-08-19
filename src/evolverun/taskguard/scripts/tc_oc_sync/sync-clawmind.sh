#!/usr/bin/env bash
# sync-clawmind.sh — 在天翼云机器上从 git 拉取 ClawMind master 代码，
# 构建后同步到 openclaw 插件目录。
#
# 旧流程（本地打包上传）：
#   1. 本地 npm run build → 打包 clawmind-0.1.0.gz
#   2. scp clawmind-0.1.0.gz 远程机器
#   3. cd /home/admin/openclawExt && rm -rf clawmind
#   4. tar -xzf /home/admin/.openclaw/workspace/clawmind-0.1.0.gz -C /home/admin/openclawExt/
#
# 新流程（本脚本，远程直接拉取构建）：
#   1. git pull master 到 /home/admin/.openclaw/workspace/clawmind
#   2. npm install && npm run build
#   3. rm -rf /home/admin/openclawExt/clawmind
#   4. 同步 dist/esm + 元文件 到 /home/admin/openclawExt/clawmind
#
# 用法：
#   bash sync-clawmind.sh              # 完整流程（pull + build + sync）
#   bash sync-clawmind.sh --skip-build # 跳过 build，只 pull + sync
#
# 环境要求：
#   - git, node, npm 已安装
#   - /home/admin/.openclaw/workspace 目录存在
#   - 有 ClawMind 仓库的读取权限

set -euo pipefail

# ── 配置 ──────────────────────────────────────────────────────────
GIT_REPO="${TASKGUARD_GIT_REPO:-https://github.com/avernet/taskguard.git}"
BRANCH="master"
WORKSPACE_DIR="${HOME}/.openclaw/workspace"
CLAWMIND_SRC="${WORKSPACE_DIR}/taskguard"
EXT_DIR="${OPENCLAW_EXTENSION_DIR:-${HOME}/openclawExt/taskguard}"

# ── 参数解析 ──────────────────────────────────────────────────────
SKIP_BUILD=false
if [[ "${1:-}" == "--skip-build" ]]; then
  SKIP_BUILD=true
fi

echo "=== sync-clawmind ==="
echo "Source:  ${GIT_REPO} (${BRANCH})"
echo "Target: ${EXT_DIR}"
echo ""

# ── Step 1: Git 拉取 ─────────────────────────────────────────────
echo "[1/4] Git pull ${BRANCH}..."

if [[ -d "${CLAWMIND_SRC}/.git" ]]; then
  # 仓库已存在，pull 最新代码
  cd "${CLAWMIND_SRC}"
  git fetch origin "${BRANCH}"
  git reset --hard "origin/${BRANCH}"
  echo "  ✅ Updated to $(git rev-parse --short HEAD)"
else
  # 首次克隆
  mkdir -p "${WORKSPACE_DIR}"
  git clone -b "${BRANCH}" --depth 1 "${GIT_REPO}" "${CLAWMIND_SRC}"
  cd "${CLAWMIND_SRC}"
  echo "  ✅ Cloned at $(git rev-parse --short HEAD)"
fi
echo ""

# ── Step 2: 安装依赖 & 构建 ──────────────────────────────────────
if [[ "$SKIP_BUILD" == false ]]; then
  echo "[2/4] npm install & build..."
  cd "${CLAWMIND_SRC}"

  npm install --production=false 2>&1 | tail -3
  npm run build 2>&1 | tail -5

  if [[ ! -f "dist/esm/index.js" ]]; then
    echo "❌ Build failed: dist/esm/index.js not found"
    exit 1
  fi
  echo "  ✅ Build succeeded"
else
  echo "[2/4] Skipping build (--skip-build)"
  if [[ ! -f "${CLAWMIND_SRC}/dist/esm/index.js" ]]; then
    echo "❌ dist/esm/index.js not found, cannot skip build"
    exit 1
  fi
fi
echo ""

# ── Step 3: 清理旧插件 ──────────────────────────────────────────
echo "[3/4] Cleaning old extension..."
rm -rf "${EXT_DIR}"
mkdir -p "${EXT_DIR}"
echo "  ✅ Removed ${EXT_DIR}"
echo ""

# ── Step 4: 同步到插件目录（目录结构与 tgz 解压一致）──────────────
# 最终布局（与 clawmind-0.1.0.gz 解压结果一致）：
#   /home/admin/openclawExt/clawmind/
#     package.json              ← "main": "./dist/esm/index.js"
#     openclaw.plugin.json
#     dist/esm/index.js         ← 编译产物
#     packs/                    ← workflow packs
#     configs/                  ← application.yaml
#     node_modules/             ← bundleDependencies

echo "[4/4] Syncing to extension dir..."

cd "${CLAWMIND_SRC}"

# 4a. 同步编译产物 → dist/esm/
mkdir -p "${EXT_DIR}/dist/esm"
rsync -av --delete "${CLAWMIND_SRC}/dist/esm/" "${EXT_DIR}/dist/esm/"
echo "  Synced dist/esm/"

# 4b. 复制 package.json（保持 "main": "./dist/esm/index.js" 路径不变）
if [[ -f "${CLAWMIND_SRC}/dist/package.json" ]]; then
  cp "${CLAWMIND_SRC}/dist/package.json" "${EXT_DIR}/package.json"
  echo "  Copied package.json (from dist/)"
elif [[ -f "${CLAWMIND_SRC}/package.json" ]]; then
  cp "${CLAWMIND_SRC}/package.json" "${EXT_DIR}/package.json"
  echo "  Copied package.json (from root)"
fi

# 4c. 复制插件 manifest
if [[ -f "openclaw.plugin.json" ]]; then
  cp openclaw.plugin.json "${EXT_DIR}/"
  echo "  Copied openclaw.plugin.json"
fi

# 4d. 复制 workflow packs
if [[ -d "packs" ]]; then
  cp -r packs/ "${EXT_DIR}/packs/"
  echo "  Copied packs/ ($(ls packs/ | wc -l | tr -d ' ') packs)"
fi

# 4e. 复制 configs（application.yaml 等）
if [[ -d "configs" ]]; then
  cp -r configs/ "${EXT_DIR}/configs/"
  echo "  Copied configs/"
fi

# 4f. 复制打包依赖（cron-parser, yaml, zod）
BUNDLE_DEPS=$(node -e '
  const pkg = JSON.parse(require("fs").readFileSync("package.json", "utf8"));
  console.log((pkg.bundleDependencies || []).join(" "));
' 2>/dev/null || echo "")

if [[ -n "$BUNDLE_DEPS" ]]; then
  mkdir -p "${EXT_DIR}/node_modules"
  for dep in $BUNDLE_DEPS; do
    if [[ -d "node_modules/${dep}" ]]; then
      cp -r "node_modules/${dep}" "${EXT_DIR}/node_modules/"
      echo "  Bundled: ${dep}"
    fi
  done
fi

echo ""
echo "=== Done ==="
echo "Source:  ${CLAWMIND_SRC} ($(git rev-parse --short HEAD))"
echo "Target: ${EXT_DIR}"
echo ""
echo "Verify:"
echo "  ls ${EXT_DIR}/dist/esm/index.js"
echo "  ls ${EXT_DIR}/openclaw.plugin.json"