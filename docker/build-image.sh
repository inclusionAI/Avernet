#!/usr/bin/env bash
# docker/build-image.sh - generic local image build script
#
# Usage:
#   docker/build-image.sh <dockerfile> [options]
#
# Examples:
#   # Build only the baas image (default ref baas:local, no push)
#   docker/build-image.sh baas.dockerfile
#
#   # Set the image name (may include a registry); still no push
#   # (you just want a registry-prefixed tag locally)
#   docker/build-image.sh baas.dockerfile --image reg.example.com/ns/baas
#
#   # Build and push (requires docker login already done)
#   docker/build-image.sh baas.dockerfile --image reg.example.com/ns/baas --push
#
#   # Multiple tags, all pushed
#   docker/build-image.sh baas.dockerfile \
#       --image reg.example.com/ns/baas --tag v1.0 --tag latest --push
#
#   # Pass through build-args (e.g. baas.dockerfile's UV_VERSION)
#   docker/build-image.sh baas.dockerfile --build-arg UV_VERSION=0.8.14
#
#   # Combined
#   docker/build-image.sh baas.dockerfile \
#       --image reg.example.com/ns/baas --tag v1.0 --push \
#       --build-arg UV_VERSION=0.8.14 --no-cache
#
# Options:
#   <dockerfile>  Filename under docker/ or its subdirectories (e.g.
#                 baas.dockerfile, services/baas.dockerfile), or a path
#                 relative to the repo root. A bare filename is searched
#                 recursively under docker/. Build context is always the
#                 repo root.
#   --image NAME  Image name without tag, e.g. baas or reg.example.com/ns/baas
#                 (registry may include a port, e.g. reg:5000/ns/baas). Defaults
#                 to <dockerfile> with .dockerfile/Dockerfile stripped. Only
#                 sets the name; does not trigger a push.
#   --tag T       Bare tag (no ':'), repeatable; defaults to local. The final
#                 reference is <image>:<tag>.
#   --push        After building, docker push every <image>:<tag> reference.
#                 Independent of --image (does not handle login; you must have
#                 docker-logged-in to that registry already).
#   --build-arg K=V   Forwarded to docker build as --build-arg, repeatable.
#   --no-cache    Disable build cache.

set -uo pipefail

# --- Locate repo root (this script lives at <repo>/docker/build-image.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DOCKERFILE=""
IMAGE=""
PUSH=0
TAGS=()
BUILD_ARGS=()
NO_CACHE=0

usage() {
    # Print the leading '#' comment block of this script (skip the shebang),
    # stopping at the first non-comment line.
    awk 'NR>1 {
        if ($0 ~ /^#/) { sub(/^# ?/,""); print; next }
        exit
    }' "${BASH_SOURCE[0]}"
    exit 0
}

# --- Argument parsing
while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h) usage ;;
        --image)
            [ $# -ge 2 ] || { echo "error: $1 requires an argument" >&2; exit 2; }
            IMAGE="$2"; shift 2 ;;
        --push) PUSH=1; shift ;;
        --tag|-t)
            [ $# -ge 2 ] || { echo "error: $1 requires an argument" >&2; exit 2; }
            TAGS+=("$2"); shift 2 ;;
        --build-arg)
            [ $# -ge 2 ] || { echo "error: $1 requires an argument" >&2; exit 2; }
            BUILD_ARGS+=("$2"); shift 2 ;;
        --no-cache) NO_CACHE=1; shift ;;
        --) shift; break ;;
        --*|-*) echo "error: unknown option $1" >&2; exit 2 ;;
        *)
            if [ -z "$DOCKERFILE" ]; then
                DOCKERFILE="$1"; shift
            else
                echo "error: only one dockerfile may be given" >&2; exit 2
            fi ;;
    esac
done

if [ -z "$DOCKERFILE" ]; then
    echo "error: missing <dockerfile> argument" >&2
    echo "usage: docker/build-image.sh <dockerfile> [--image NAME] [--tag T] [--push] [--build-arg K=V] [--no-cache]" >&2
    exit 2
fi

# --- Validate --image / --tag up front (before the docker check,
#     so argument errors surface even without docker installed)
if [ -n "$IMAGE" ]; then
    case "$IMAGE" in
        *:*)  # Likely misuse as name:tag; a real registry port is also valid, just hint.
            echo "hint: --image contains ':'; ignore if you meant a registry port (e.g. reg:5000/ns/baas);" >&2
            echo "      if you passed name:tag by mistake, use --tag for the tag instead." >&2 ;;
    esac
fi
if [ "${#TAGS[@]}" -gt 0 ]; then
    for t in "${TAGS[@]}"; do
        case "$t" in
            "")  echo "error: --tag must not be empty" >&2; exit 2 ;;
            *:*) echo "error: --tag must not contain ':' (give the tag only; name/registry go via --image): ${t}" >&2; exit 2 ;;
        esac
    done
fi

command -v docker >/dev/null 2>&1 || { echo "error: docker command not found" >&2; exit 1; }

# --- Resolve dockerfile path (prefer docker/ and its subdirs, else repo root)
if [ -f "${SCRIPT_DIR}/${DOCKERFILE}" ]; then
    DOCKERFILE_PATH="${SCRIPT_DIR}/${DOCKERFILE}"
elif [ -f "${REPO_ROOT}/${DOCKERFILE}" ]; then
    DOCKERFILE_PATH="${REPO_ROOT}/${DOCKERFILE}"
else
    # Search recursively under docker/ for a bare filename
    FOUND=$(find "${SCRIPT_DIR}" -name "${DOCKERFILE}" -type f 2>/dev/null | head -1)
    if [ -n "$FOUND" ]; then
        DOCKERFILE_PATH="$FOUND"
    else
        echo "error: dockerfile not found: ${DOCKERFILE} (not under docker/ nor repo root)" >&2
        exit 1
    fi
fi

# --- Derive default image name: strip .dockerfile / Dockerfile suffix
DF_BASENAME="$(basename "${DOCKERFILE_PATH}")"
NAME="${DF_BASENAME%.dockerfile}"
[ "$NAME" = "$DF_BASENAME" ] && NAME="${DF_BASENAME%Dockerfile}"
[ "$NAME" = "$DF_BASENAME" ] && NAME="image"
[ -z "$IMAGE" ] && IMAGE="$NAME"
if [ "${#TAGS[@]}" -eq 0 ]; then
    TAGS=("local")
fi

# --- Compose final image references <image>:<tag>
REFS=()
for t in "${TAGS[@]}"; do
    REFS+=("${IMAGE}:${t}")
done

# --- Assemble the docker build command
BUILD_CMD=(docker build)
for ref in "${REFS[@]}"; do
    BUILD_CMD+=("-t" "$ref")
done
[ "$NO_CACHE" -eq 1 ] && BUILD_CMD+=(--no-cache)
if [ "${#BUILD_ARGS[@]}" -gt 0 ]; then
    for ba in "${BUILD_ARGS[@]}"; do
        BUILD_CMD+=("--build-arg" "$ba")
    done
fi
BUILD_CMD+=("-f" "${DOCKERFILE_PATH}" "${REPO_ROOT}")

# --- Run the build
echo "==> repo root:    ${REPO_ROOT}"
echo "==> Dockerfile:   ${DOCKERFILE_PATH}"
echo "==> image refs:   ${REFS[*]}"
if [ "$PUSH" -eq 1 ]; then
    echo "==> push after build: yes (triggered by --push; docker login required)"
    case "$IMAGE" in
        */*) ;;  # Has a registry/namespace path, fine
        *)  echo "==> hint: --image has no '/', will push to the Docker Hub default library (usually pair with --image <registry>/<ns>/<name>)" ;;
    esac
fi
echo "==> command:      ${BUILD_CMD[*]}"
echo
"${BUILD_CMD[@]}"
BUILD_RC=$?
if [ $BUILD_RC -ne 0 ]; then
    echo "build failed (exit ${BUILD_RC})" >&2
    exit $BUILD_RC
fi

# --- When --push is given, push every reference (login is not handled here)
if [ "$PUSH" -eq 1 ]; then
    echo
    echo "==> pushing images:"
    PUSH_FAIL=0
    for ref in "${REFS[@]}"; do
        echo "docker push ${ref}"
        if docker push "$ref"; then
            echo "  pushed: ${ref}"
        else
            echo "  push failed: ${ref} (make sure you docker-logged-in to that registry)" >&2
            PUSH_FAIL=1
        fi
    done
    if [ "$PUSH_FAIL" -ne 0 ]; then
        echo "one or more images failed to push; see above" >&2
        exit 1
    fi
fi

echo
echo "build complete:"
for ref in "${REFS[@]}"; do
    echo "  ${ref}"
done
