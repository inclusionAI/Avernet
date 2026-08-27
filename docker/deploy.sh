#!/usr/bin/env bash
set -euo pipefail

# Deploy bcsfuse to Kubernetes.
#
# Usage:
#   cd /home/xhunter/avernet-deploy
#   /path/to/Avernet/docker/deploy.sh bcsfuse <image:tag>
#
# Example:
#   /path/to/Avernet/docker/deploy.sh bcsfuse \
#     avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse:20260826

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SERVICE="${1:-}"
IMAGE="${2:-}"

if [ -z "$SERVICE" ] || [ -z "$IMAGE" ]; then
    echo "usage: $(basename "$0") bcsfuse <image:tag>" >&2
    exit 2
fi

if [ "$SERVICE" != "bcsfuse" ]; then
    echo "error: unsupported service '${SERVICE}' (only 'bcsfuse' is supported)" >&2
    exit 2
fi

ENV_FILE="./bcsfuse.env"
DEPLOYMENT_FILE="./bcsfuse-deployment.yaml"
EXAMPLE_ENV_FILE="${SCRIPT_DIR}/bcsfuse.env.example"

if [ ! -f "$ENV_FILE" ]; then
    if [ ! -f "$EXAMPLE_ENV_FILE" ]; then
        echo "error: neither ${ENV_FILE} nor ${EXAMPLE_ENV_FILE} found" >&2
        exit 1
    fi
    cp "$EXAMPLE_ENV_FILE" "$ENV_FILE"
    echo "==> created ${ENV_FILE} from example"
    echo "==> fill in REPLACE_WITH_* values, then re-run this script" >&2
    exit 2
fi

if grep -q 'REPLACE_WITH_' "$ENV_FILE"; then
    echo "error: ${ENV_FILE} still contains REPLACE_WITH_* placeholders; fill them in first" >&2
    exit 2
fi

# Rewrite public registry endpoint to VPC endpoint for ACK pull.
DEPLOY_IMAGE="${IMAGE/avernet-registry.cn-beijing.cr.aliyuncs.com/avernet-registry-vpc.cn-beijing.cr.aliyuncs.com}"

if [ "$DEPLOY_IMAGE" != "$IMAGE" ]; then
    echo "==> using VPC registry for deployment: ${DEPLOY_IMAGE}"
else
    echo "==> generating ${DEPLOYMENT_FILE} for ${IMAGE}"
fi

python3 "${SCRIPT_DIR}/generate_deploy_config.py" \
    --env "$ENV_FILE" \
    --no-mask \
    "$PWD" \
    "$DEPLOY_IMAGE"

echo "==> applying ${DEPLOYMENT_FILE}"
kubectl apply -f "$DEPLOYMENT_FILE"

echo ""
echo "==> deployed ${SERVICE}. check status:"
echo "    kubectl get pods -l app=${SERVICE}"
