#!/bin/bash

##############################################
# normalize-openclaw-cron.sh - OpenClaw cron store ownership helper
#
# OpenClaw's cron store lives under the configured OpenClaw state dir on a
# persistent volume. Some deployments provision that volume before the admin
# user exists (or as a different uid), leaving /home/admin/.openclaw/cron
# unreadable by OpenClaw (admin); `cron.list` then reports EACCES.
#
# Run once per engine startup, before OpenClaw starts. This helper is
# intentionally narrow:
#   - is root-owned and runs under root via the sudo allow rule
#   - touches ONLY the cron directory subtree, not the whole OpenClaw state
#   - changes ownership, not file contents or existing modes
#
# The image-owned admin uid/gid is fixed by the Dockerfile, so the helper
# deliberately takes no arguments.
##############################################

set -euo pipefail

CRON_DIR="/home/admin/.openclaw/cron"
OWNER_GROUP="admin:admin"

if [ ! -e "$CRON_DIR" ]; then
    echo "OpenClaw cron store not present; no ownership repair needed ($CRON_DIR)"
    exit 0
fi

if [ ! -d "$CRON_DIR" ]; then
    echo "OpenClaw cron store is not a directory: $CRON_DIR" >&2
    exit 1
fi

# -P keeps symlink traversal out of the repair. The second find deliberately
# includes the top-level directory so a failed chown is also not hidden.
find -P "$CRON_DIR" \
    ! \( -user admin -a -group admin \) \
    -exec chown -h "$OWNER_GROUP" {} +

residual="$(find -P "$CRON_DIR" \
    ! \( -user admin -a -group admin \) \
    -print -quit)"
if [ -n "$residual" ]; then
    echo "ERROR: still not ${OWNER_GROUP} after chown: $residual" >&2
    exit 1
fi

echo "OpenClaw cron store ownership is ${OWNER_GROUP} ($CRON_DIR)"
