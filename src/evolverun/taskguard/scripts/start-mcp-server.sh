#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# start-mcp-server.sh — 本机启动 ClawMind MCP Server
#
# 用法:
#   ./scripts/start-mcp-server.sh                # http-sse 模式, 端口 3002
#   ./scripts/start-mcp-server.sh --port 3100     # http-sse 模式, 自定义端口
#   ./scripts/start-mcp-server.sh --https         # https 模式 (自动生成自签名证书)
#   ./scripts/start-mcp-server.sh --stdio         # stdio 模式
#   ./scripts/start-mcp-server.sh --skip-build    # 跳过 build, 直接启动
#   ./scripts/start-mcp-server.sh --api           # API 数据库模式 (连接 clawweb)
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PACK_DIR="$PROJECT_ROOT/dist_pack/teclaw"
RUN_DIR="/tmp/clawmind-mcp"

# ── 默认参数 ──
MCP_TRANSPORT="http-sse"
MCP_PORT=3002
DATABASE_MODE="api"
SKIP_BUILD=false
API_MODE=false
MCP_TLS=false

# ── 参数解析 ──
while [[ $# -gt 0 ]]; do
  case $1 in
    --port)
      MCP_PORT="$2"
      shift 2
      ;;
    --stdio)
      MCP_TRANSPORT="stdio"
      shift
      ;;
    --https)
      MCP_TLS=true
      shift
      ;;
    --skip-build)
      SKIP_BUILD=true
      shift
      ;;
    --api)
      API_MODE=true
      DATABASE_MODE="api"
      shift
      ;;
    --sqlite)
      DATABASE_MODE="sqlite"
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --port PORT      MCP server port (default: 3002, only for http-sse)"
      echo "  --stdio          Use stdio transport instead of http-sse"
      echo "  --https          Enable HTTPS with auto self-signed cert"
      echo "  --skip-build     Skip build step, use existing dist_pack"
      echo "  --api            Use API database mode (connect to clawweb) [default]"
      echo "  --sqlite         Use SQLite database mode (local dev)"
      echo "  -h, --help       Show this help"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

echo "╔══════════════════════════════════════════╗"
echo "║   ClawMind MCP Server Launcher           ║"
echo "╚══════════════════════════════════════════╝"
echo "  Transport:   $MCP_TRANSPORT"
echo "  Port:        $MCP_PORT (http-sse only)"
echo "  TLS:         $MCP_TLS"
echo "  Database:    $DATABASE_MODE"
echo "  Skip build:  $SKIP_BUILD"
echo ""

# ── Step 1: Build & Pack (if needed) ──

TGZ_FILE="$PACK_DIR/clawmind-teclaw-$(node -p "require('$PROJECT_ROOT/package.json').version").tgz"

if [[ "$SKIP_BUILD" == false || ! -f "$TGZ_FILE" ]]; then
  if [[ "$SKIP_BUILD" == true && ! -f "$TGZ_FILE" ]]; then
    echo "⚠️  --skip-build but no existing pack found, building anyway..."
  fi
  echo "[1/2] Building & packing..."
  cd "$PROJECT_ROOT"
  node scripts/dist_pack_teclaw.mjs
  echo "  ✅ Pack complete"
else
  echo "[1/2] Skipping build (--skip-build, existing pack found)"
fi

# ── Step 2: Extract & Launch ──

echo ""
echo "[2/2] Extracting & launching MCP server..."

# 清理旧的运行目录
rm -rf "$RUN_DIR"
mkdir -p "$RUN_DIR"

# 解压 tgz
cd "$RUN_DIR"
tar -xzf "$TGZ_FILE"

# 构造环境变量
export DATABASE_MODE
export MCP_TRANSPORT
export MCP_PORT

# 明确指定 ClawMind 配置文件路径（优先级最高，避免被 ~/.openclaw/extensions/ 下的旧配置干扰）
export CLAWMIND_CONFIG_PATH="$RUN_DIR/package/configs/application.yaml"

# HTTPS 模式
if [[ "$MCP_TLS" == true ]]; then
  export MCP_TLS=true
  # 检查 openssl 可用性
  if ! command -v openssl &>/dev/null; then
    echo "⚠️  openssl not found! HTTPS requires openssl for self-signed cert generation."
    echo "   Install: apt-get install openssl (Debian/Ubuntu) or apk add openssl (Alpine)"
    exit 1
  fi
fi

# API 模式额外变量
if [[ "$API_MODE" == true ]]; then
  : "${CLAWWEB_API_URL:?CLAWWEB_API_URL is required for --api mode}"
  : "${CLAWWEB_API_PRIVATE_KEY:?CLAWWEB_API_PRIVATE_KEY is required for --api mode}"
  export CLAWWEB_API_URL
  export CLAWWEB_API_PRIVATE_KEY
fi

echo ""
echo "──────────────────────────────────────────"
if [[ "$MCP_TRANSPORT" == "http-sse" ]]; then
  LOCAL_IP=$(ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -1)
  if [[ "$MCP_TLS" == true ]]; then
    SCHEME="https"
  else
    SCHEME="http"
  fi
  echo "  SSE:             ${SCHEME}://127.0.0.1:${MCP_PORT}/sse"
  echo "  Streamable HTTP: ${SCHEME}://127.0.0.1:${MCP_PORT}/mcp"
  echo "  SSE (IP):        ${SCHEME}://${LOCAL_IP}:${MCP_PORT}/sse"
  echo "  Health:          ${SCHEME}://127.0.0.1:${MCP_PORT}/health"
  echo "  Messages:        ${SCHEME}://127.0.0.1:${MCP_PORT}/messages?sessionId=xxx"
  if [[ "$MCP_TLS" == true ]]; then
    echo "  TLS:             self-signed cert (auto-generated)"
  fi
else
  echo "  Transport: stdio (reading from stdin)"
fi
echo "  Database:  $DATABASE_MODE"
echo "  Run dir:   $RUN_DIR/package"
echo "──────────────────────────────────────────"
echo ""
echo "Press Ctrl+C to stop."
echo ""

# 启动
cd "$RUN_DIR/package"
exec node dist/esm/platform/mcp-entry.js