#!/bin/bash
set -euo pipefail

# Unified image build entrypoint for Avernet services.
#
# Usage:
#   ./docker/build_image.sh <service>
#
# Supported services:
#  - bcs        (alias for bcn, kept for backward compatibility)
#  - bcn
#  - backend
#  - baas
#  - api-gateway
#  - node-fe
#  - bcsfuse
#
# Optional environment variables:
#   DOCKER_REGISTRY    prefix for image tag (default: empty, local)
#   DOCKER_TAG         image tag suffix   (default: local)
#   DOCKER_BUILD_ARGS  extra --build-arg flags
#   NO_CACHE           pass --no-cache if set

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

USAGE="Usage: ./docker/build_image.sh <bcs|bcn|backend|baas|api-gateway|node-fe|bcsfuse>"

if [ $# -lt 1 ]; then
    echo "Error: missing service name" >&2
    echo "${USAGE}" >&2
    exit 1
fi

SERVICE="${1}"

# Resolve aliases and dockerfile names.
case "${SERVICE}" in
    bcs|bcn)
        DOCKERFILE="docker/bcn.Dockerfile"
        IMAGE_NAME="bcn"
        ;;
    backend)
        DOCKERFILE="docker/backend.Dockerfile"
        IMAGE_NAME="backend"
        ;;
    baas)
        DOCKERFILE="docker/baas.Dockerfile"
        IMAGE_NAME="baas"
        ;;
    api-gateway)
        DOCKERFILE="docker/api-gateway.Dockerfile"
        IMAGE_NAME="api-gateway"
        ;;
    node-fe)
        DOCKERFILE="docker/node-fe.Dockerfile"
        IMAGE_NAME="node-fe"
        ;;
    bcsfuse)
        DOCKERFILE="docker/bcsfuse.Dockerfile"
        IMAGE_NAME="bcsfuse"
        ;;
    *)
        echo "Error: unsupported service '${SERVICE}'" >&2
        echo "${USAGE}" >&2
        exit 1
        ;;
esac

REGISTRY="${DOCKER_REGISTRY:-}"
TAG="${DOCKER_TAG:-local}"
IMAGE_REF="${REGISTRY:+${REGISTRY%/}/}${IMAGE_NAME}:${TAG}"

cd "${PROJECT_ROOT}"

if [ ! -f "${DOCKERFILE}" ]; then
    echo "Error: Dockerfile not found: ${DOCKERFILE}" >&2
    echo "Hint: create it first. For bcsfuse, docker/bcsfuse.Dockerfile exists." >&2
    exit 1
fi

echo "================================================================================"
echo "Building Avernet service image"
echo "  Service:      ${SERVICE}"
echo "  Dockerfile:   ${DOCKERFILE}"
echo "  Image:        ${IMAGE_REF}"
echo "  Context:      ${PROJECT_ROOT}"
echo "================================================================================"

BUILD_EXTRA_ARGS=("-f" "${DOCKERFILE}")
[ -n "${NO_CACHE:-}" ] && BUILD_EXTRA_ARGS+=("--no-cache")

# Accept arbitrary extra build args.
if [ -n "${DOCKER_BUILD_ARGS:-}" ]; then
    # shellcheck disable=SC2086
    BUILD_EXTRA_ARGS+=(${DOCKER_BUILD_ARGS})
fi

docker build \
    "${BUILD_EXTRA_ARGS[@]}" \
    -t "${IMAGE_REF}" \
    "${PROJECT_ROOT}"

echo ""
echo "================================================================================"
echo "Build complete: ${IMAGE_REF}"
echo "================================================================================"
