#!/bin/bash
# Reset environment for clean skill system testing
# WARNING: This will delete all data!

set -e

# Get CHAT_ENGINE from environment or default to openclaw
CHAT_ENGINE="${CHAT_ENGINE:-openclaw}"

echo "=============================================="
echo "OpenClaw Environment Reset Script"
echo "=============================================="
echo ""
echo "CHAT_ENGINE: $CHAT_ENGINE"
echo ""
echo "This will delete:"
echo "  - Database (~/.moltis/$CHAT_ENGINE/workspace/*.db)"
echo "  - All skill directories (~/.moltis/skills/*)"
echo "  - Skills repository (~/.moltis/skills-repo/)"
echo "  - Local skills (~/.moltis/skills-local/)"
echo "  - Old database location (~/.openclaw/*.db if exists)"
echo "  - Frontend build cache"
echo ""

read -p "Are you sure you want to continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 1
fi

echo ""
echo "Starting cleanup..."
echo ""

# 1. Stop running servers
echo "[1/7] Stopping servers..."
pkill -f "python.*start.py" 2>/dev/null || true
pkill -f "python /tmp/server.py" 2>/dev/null || true
pkill -f "npm run dev" 2>/dev/null || true
sleep 2
echo "  ✓ Servers stopped"

# 2. Clean old database location (migration cleanup)
echo "[2/7] Cleaning old database location..."
OLD_DB_PATH="/Users/zhaosenlin/.openclaw"
if [ -d "$OLD_DB_PATH" ]; then
    rm -rf "$OLD_DB_PATH"/*.db 2>/dev/null || true
    rm -rf "$OLD_DB_PATH"/*.sqlite 2>/dev/null || true
    rm -rf "$OLD_DB_PATH"/*.sqlite3 2>/dev/null || true
    echo "  ✓ Old database files removed from $OLD_DB_PATH"
else
    echo "  - Old database directory not found, skipping"
fi

# 3. Clean database locations
# Current location (flat structure, no CHAT_ENGINE subdir)
echo "[3/7] Cleaning database (~/.moltis/workspace)..."
DB_PATH="/Users/zhaosenlin/.moltis/workspace"
if [ -d "$DB_PATH" ]; then
    rm -rf "$DB_PATH"/*.db
    rm -rf "$DB_PATH"/*.sqlite
    rm -rf "$DB_PATH"/*.sqlite3
    echo "  ✓ Database files removed from $DB_PATH"
else
    echo "  - Database directory not found, will be created on first run"
fi

# Legacy location (with CHAT_ENGINE subdir, for backward compatibility)
LEGACY_DB_PATH="/Users/zhaosenlin/.moltis/$CHAT_ENGINE/workspace"
if [ -d "$LEGACY_DB_PATH" ]; then
    rm -rf "$LEGACY_DB_PATH"/*.db
    rm -rf "$LEGACY_DB_PATH"/*.sqlite
    rm -rf "$LEGACY_DB_PATH"/*.sqlite3
    echo "  ✓ Legacy database files removed from $LEGACY_DB_PATH"
fi

# 4. Clean skill directories
echo "[4/7] Cleaning skill directories..."
MOLTIS_DIR="/Users/zhaosenlin/.moltis"

# Remove skills directory (symlinks)
if [ -d "$MOLTIS_DIR/skills" ]; then
    rm -rf "$MOLTIS_DIR/skills"
    echo "  ✓ Removed $MOLTIS_DIR/skills"
fi

# Remove skills-repo directory (git clone)
if [ -d "$MOLTIS_DIR/skills-repo" ]; then
    rm -rf "$MOLTIS_DIR/skills-repo"
    echo "  ✓ Removed $MOLTIS_DIR/skills-repo"
fi

# Remove skills-local directory (uploaded skills)
if [ -d "$MOLTIS_DIR/skills-local" ]; then
    rm -rf "$MOLTIS_DIR/skills-local"
    echo "  ✓ Removed $MOLTIS_DIR/skills-local"
fi

# Recreate empty directories
mkdir -p "$MOLTIS_DIR/skills"
mkdir -p "$MOLTIS_DIR/skills-local"
echo "  ✓ Recreated empty skill directories"

# 5. Clean other CHAT_ENGINE directories (optional cleanup)
echo "[5/7] Checking for other CHAT_ENGINE data..."
for engine_dir in "$MOLTIS_DIR"/openclaw "$MOLTIS_DIR"/moltis; do
    if [ -d "$engine_dir" ] && [ "$engine_dir" != "$MOLTIS_DIR/$CHAT_ENGINE" ]; then
        echo "  - Found other engine data: $engine_dir (keeping it)"
    fi
done
echo "  ✓ Other engine data check complete"

# 6. Clean frontend cache
echo "[6/7] Cleaning frontend build cache..."
cd /Users/zhaosenlin/workspace/open-claw
if [ -d "src/.umi" ]; then
    rm -rf src/.umi
    echo "  ✓ Removed src/.umi"
fi
if [ -d "src/.umi-production" ]; then
    rm -rf src/.umi-production
    echo "  ✓ Removed src/.umi-production"
fi
if [ -d "dist" ]; then
    rm -rf dist
    echo "  ✓ Removed dist"
fi
echo "  ✓ Frontend cache cleaned"

# 7. Clean temp files
echo "[7/7] Cleaning temporary files..."
rm -f /tmp/backend*.log
rm -f /tmp/dev*.log
rm -f /tmp/tw.log
rm -f /tmp/*.png
echo "  ✓ Temporary files cleaned"

# Verify cleanup
echo ""
echo "=============================================="
echo "Environment reset complete!"
echo "=============================================="
echo ""
echo "Cleanup summary:"
echo "  CHAT_ENGINE: $CHAT_ENGINE"
echo "  Database: ~/.moltis/$CHAT_ENGINE/workspace/ (clean)"
echo "  Skills: ~/.moltis/skills/ (empty)"
echo "  Skills repo: ~/.moltis/skills-repo/ (deleted, will clone on first use)"
echo "  Local skills: ~/.moltis/skills-local/ (empty)"
echo ""
echo "Next steps:"
echo "  1. Set CHAT_ENGINE if needed:"
echo "     export CHAT_ENGINE=$CHAT_ENGINE"
echo ""
echo "  2. Start backend:"
echo "     cd /Users/zhaosenlin/workspace/OpenClawEnterprise"
echo "     python start.py --port 20003"
echo ""
echo "  3. Build frontend:"
echo "     cd /Users/zhaosenlin/workspace/open-claw"
echo "     npm run build"
echo ""
echo "  4. Start frontend proxy:"
echo "     python /tmp/server.py"
echo ""
echo "The default skill set '默认技能集' will be created automatically on first start."
echo ""
