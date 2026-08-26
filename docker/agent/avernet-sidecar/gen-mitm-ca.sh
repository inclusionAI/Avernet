#!/bin/bash
# ============================================================
# gen-mitm-ca.sh — Generate a self-signed MITM CA for avernet-sidecar
#
# Produces mitm-ca.crt and mitm-ca.key in the script's directory.
# The agent image trusts mitm-ca.crt; the sidecar image uses both
# to sign dynamic server certificates for HTTPS interception.
#
# Usage:
#   docker/agent/avernet-sidecar/gen-mitm-ca.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT="${SCRIPT_DIR}/mitm-ca.crt"
KEY="${SCRIPT_DIR}/mitm-ca.key"

if [ -f "$CERT" ] && [ -f "$KEY" ]; then
    echo "MITM CA already exists:"
    echo "  cert: $CERT"
    echo "  key:  $KEY"
    echo "Delete them first to regenerate."
    exit 0
fi

echo "Generating MITM CA certificate..."
openssl req -x509 -newkey rsa:2048 \
    -keyout "$KEY" \
    -out "$CERT" \
    -days 3650 -nodes \
    -subj "/CN=avernet-sidecar-mitm-ca/O=Avernet" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"

chmod 644 "$CERT"
chmod 600 "$KEY"

echo ""
echo "MITM CA generated:"
echo "  cert: $CERT"
echo "  key:  $KEY"
echo ""
echo "Build images with:"
echo "  docker/build-image.sh avernet.dockerfile --no-cache"
echo "  docker/build-image.sh avernet-sidecar.dockerfile --no-cache"