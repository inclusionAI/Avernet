#!/bin/bash
set -euo pipefail

# Unified image build entrypoint for Avernet services.
#
# Usage (legacy/env style):
#   DOCKER_REGISTRY=... DOCKER_TAG=... ./docker/build_image.sh <service>
#
# Usage (explicit flag style, aligned with xhunter deploy flow):
#   ./docker/build_image.sh <dockerfile> --image <image> --tag <tag>
#
# Supported services for env style:
#  - bcs        (alias for bcn)
#  - bcn
#  - backend
#  - baas
  - api-gateway
  - node-fe
  - bcsfuse

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Default values when using env style
REGISTRY="${DOCKER_REGISTRY:-}"
TAG="${DOCKER_TAG:-local}"

# Parse arguments that may be either a service name or a dockerfile path.
if [ $# -lt 1 ]; then
    echo "Error: missing dockerfile or service name" >&2
    echo "Usage: ./docker/build_image.sh <dockerfile> [--image IMAGE] [--tag TAG]" >&2
    echo "   or: ./docker/build_image.sh <service>  (env: DOCKER_REGISTRY, DOCKER_TAG)" >&2
    exit 1
fi

FIRST_ARG="${1}"
shift || true

# Detect flag style vs service style
if [[ "$FIRST_ARG" == *.Dockerfile ]] || [ -f "${PROJECT_ROOT}/${FIRST_ARG}" ]; then
    # Explicit Dockerfile mode
    DOCKERFILE="${FIRST_ARG}"
    IMAGE_NAME_FROM_DOCKERFILE=$(basename "${DOCKERFILE}" .Dockerfile)

    while [ $# -gt 0 ]; do
        case "$1" in
            --image)
                shift
                IMAGE_REF="${1:-}"
                ;;
            --tag)
                shift
                TAG="${1:-}"
                ;;
            *)
                echo "Error: unknown option $1" >&2
                exit 1
                ;;
        esac
        shift || true
    done

    if [ -z "${IMAGE_REF:-}" ]; then
        echo "Error: --image is required when specifying a Dockerfile" >&2
        exit 1
    fi
else
    # Service/env style, for backward compatibility
    SERVICE="${FIRST_ARG}"

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
            echo "Usage: ./docker/build_image.sh <bcs|bcn|backend|baas|api-gateway|node-fe|bcsfuse>" >&2
            exit 1
            ;;
    esac

    IMAGE_REF="${REGISTRY:+${REGISTRY%/}/}${IMAGE_NAME}:${TAG}"
fi

cd "${PROJECT_ROOT}"

if [ ! -f "${DOCKERFILE}" ]; then
    echo "Error: Dockerfile not found: ${DOCKERFILE}" >&2
    exit 1
fi

echo "================================================================================"
echo "Building Avernet service image"
echo "  Dockerfile:   ${DOCKERFILE}"
echo "  Image:        ${IMAGE_REF}"
echo "  Context:      ${PROJECT_ROOT}"
echo "================================================================================"

BUILD_EXTRA_ARGS=("-f" "${DOCKERFILE}")
[ -n "${NO_CACHE:-}" ] && BUILD_EXTRA_ARGS+=("--no-cache")

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
