#!/bin/bash
#
# install-clawmind.sh — Download and overwrite-install taskguard to the extension dir
# Preserves packs across the upgrade.
# Restarts openclaw gateway after installation.
#

set -euo pipefail

INSTALL_DIR="${OPENCLAW_EXTENSION_DIR:-${HOME}/openclawExt}"
# Persistent timestamped archive under the workspace dir (~/.openclaw/workspace),
# so every upgrade keeps a restorable snapshot of the previous packs.
ARCHIVE_ROOT="${HOME}/.openclaw/workspace/taskguard-packs-archive"
PACKS_BACKUP_DIR="${ARCHIVE_ROOT}/packs-$(date +%Y%m%d%H%M%S)"
# Transient copy used to restore packs into the freshly extracted install (deleted after restore).
PACKS_RESTORE_DIR="/tmp/taskguard-packs-restore-$(date +%Y%m%d%H%M%S)"
DOWNLOAD_URL="${TASKGUARD_DOWNLOAD_URL:-}"
TGZ_FILE="/tmp/taskguard-0.1.0.tgz"

# How many timestamped snapshots to keep in ARCHIVE_ROOT; older ones are pruned.
KEEP_ARCHIVES=5

echo "=== ClawMind Overwrite Install Script ==="
echo ""

# ── Step 1: Download ──
echo "[1/6] Downloading clawmind-0.1.0.tgz ..."
if ! curl -fL --progress-bar -o "$TGZ_FILE" "$DOWNLOAD_URL"; then
    echo "ERROR: Download failed. Please check the URL and network connectivity."
    exit 1
fi
echo "  → Downloaded to $TGZ_FILE"
echo ""

# ── Step 2: Archive packs directory (timestamped, persistent) + staging copy ──
PACKS_DIR="$INSTALL_DIR/clawmind/packs"
if [ -d "$PACKS_DIR" ]; then
    mkdir -p "$ARCHIVE_ROOT"
    echo "[2/6] Archiving packs directory to $PACKS_BACKUP_DIR ..."
    cp -a "$PACKS_DIR" "$PACKS_BACKUP_DIR"
    echo "  → Archived $(find "$PACKS_BACKUP_DIR" -type f | wc -l | tr -d ' ') files"
    # Staging copy used for restore after re-extraction (kept independent from the archive).
    cp -a "$PACKS_DIR" "$PACKS_RESTORE_DIR"
    # Prune old snapshots, keeping only the newest KEEP_ARCHIVES.
    prune_count=$(find "$ARCHIVE_ROOT" -maxdepth 1 -mindepth 1 -type d -name 'packs-*' \
        | sort | head -n "-${KEEP_ARCHIVES}" | wc -l | tr -d ' ')
    if [ "${prune_count:-0}" -gt 0 ]; then
        find "$ARCHIVE_ROOT" -maxdepth 1 -mindepth 1 -type d -name 'packs-*' \
            | sort | head -n "-${KEEP_ARCHIVES}" \
            | while read -r old; do rm -rf "$old"; done
        echo "  → Pruned ${prune_count} old snapshot(s), keeping newest ${KEEP_ARCHIVES}"
    fi
else
    echo "[2/6] No existing packs directory found, skipping backup."
fi
echo ""

# ── Step 3: Remove old installation ──
CLAWMIND_DIR="$INSTALL_DIR/clawmind"
if [ -d "$CLAWMIND_DIR" ]; then
    echo "[3/6] Removing old installation at $CLAWMIND_DIR ..."
    rm -rf "$CLAWMIND_DIR"
    echo "  → Removed"
else
    echo "[3/6] No previous installation found at $CLAWMIND_DIR, clean install."
fi
echo ""

# ── Step 4: Extract new version ──
echo "[4/6] Extracting new version to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
tar xzf "$TGZ_FILE" -C "$INSTALL_DIR"
echo "  → Extracted"
echo ""

# ── Step 5: Restore packs directory from staging copy ──
if [ -d "$PACKS_RESTORE_DIR" ]; then
    echo "[5/6] Restoring packs directory ..."
    # Ensure the clawmind directory exists (it should from extraction)
    mkdir -p "$CLAWMIND_DIR"
    cp -a "$PACKS_RESTORE_DIR" "$PACKS_DIR"
    echo "  → Restored $(find "$PACKS_DIR" -type f | wc -l | tr -d ' ') files"
    # Clean up the transient staging copy only — the timestamped archive under
    # ~/.openclaw/workspace is intentionally preserved for rollback.
    rm -rf "$PACKS_RESTORE_DIR"
    echo "  → Staging copy cleaned up (archive preserved at $ARCHIVE_ROOT)"
else
    echo "[5/6] No packs backup to restore."
fi
echo ""

# ── Step 6: Restart openclaw gateway ──
echo "[6/6] Restarting openclaw gateway ..."
if command -v openclaw &>/dev/null; then
    openclaw gateway restart
    echo "  → openclaw gateway restarted"
elif [ -f /etc/init.d/openclaw-gateway ]; then
    /etc/init.d/openclaw-gateway restart
    echo "  → openclaw gateway restarted via init.d"
elif systemctl list-units --type=service | grep -q openclaw-gateway; then
    systemctl restart openclaw-gateway
    echo "  → openclaw gateway restarted via systemctl"
else
    echo "  → WARNING: Could not determine how to restart openclaw gateway."
    echo "    Please restart it manually."
fi
echo ""

# ── Cleanup ──
rm -f "$TGZ_FILE"

echo "=== Installation Complete ==="
echo "  Install path: $CLAWMIND_DIR"
echo "  Packs preserved: $PACKS_DIR"
if [ -d "$PACKS_DIR" ]; then
    echo "  Pack count: $(find "$PACKS_DIR" -type f | wc -l | tr -d ' ')"
fi
echo "  Packs archive: $PACKS_BACKUP_DIR"
echo "  (keeping newest ${KEEP_ARCHIVES} snapshots under $ARCHIVE_ROOT)"