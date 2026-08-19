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
#   # Bake environment variables into the image (visible at runtime)
#   docker/build-image.sh baas.dockerfile --env PASSW=111 --env DEBUG=1
#
#   # Pass through build-args (e.g. baas.dockerfile's USE_CN_MIRROR)
#   docker/build-image.sh baas.dockerfile --build-arg USE_CN_MIRROR=1
#
#   # Combined
#   docker/build-image.sh baas.dockerfile \
#       --image reg.example.com/ns/baas --tag v1.0 --push \
#       --env PASSW=111 --build-arg USE_CN_MIRROR=1 --no-cache
#
# Options:
#   <dockerfile>  Filename under docker/ (e.g. baas.dockerfile), or a path
#                 relative to the repo root. Build context is always the
#                 repo root (same as Dockerfile.ocb).
#   --image NAME  Image name without tag, e.g. baas or reg.example.com/ns/baas
#                 (registry may include a port, e.g. reg:5000/ns/baas). Defaults
#                 to <dockerfile> with .dockerfile/Dockerfile stripped. Only
#                 sets the name; does not trigger a push.
#   --tag T       Bare tag (no ':'), repeatable; defaults to local. The final
#                 reference is <image>:<tag>.
#   --push        After building, docker push every <image>:<tag> reference.
#                 Independent of --image (does not handle login; you must have
#                 docker-logged-in to that registry already).
#   --env K=V     Append K=V as an ENV instruction at the end of the Dockerfile
#                 (runtime env var), repeatable. Values are double-quote escaped
#                 into a temp Dockerfile; spaces/special chars are supported.
#   --build-arg K=V   Forwarded to docker build as --build-arg, repeatable.
#   --no-cache    Disable build cache.
#
# Security note:
#   Values baked in via --env appear in `docker inspect` output and are visible
#   to anyone who can pull the image. For sensitive data (keys/passwords),
#   prefer runtime injection via `docker run -e` or a secret mount instead of
#   baking them into image layers.

set -uo pipefail

# --- Locate repo root (this script lives at <repo>/docker/build-image.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DOCKERFILE=""
IMAGE=""
PUSH=0
TAGS=()
ENVS=()
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
        --env)
            [ $# -ge 2 ] || { echo "error: $1 requires an argument" >&2; exit 2; }
            ENVS+=("$2"); shift 2 ;;
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
    echo "usage: docker/build-image.sh <dockerfile> [--image NAME] [--tag T] [--push] [--env K=V] [--build-arg K=V] [--no-cache]" >&2
    exit 2
fi

# --- Validate --image / --env / --tag up front (before the docker check,
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
ENV_KEY_RE='^[A-Za-z_][A-Za-z0-9_]*$'
if [ "${#ENVS[@]}" -gt 0 ]; then
    for spec in "${ENVS[@]}"; do
        if [ "$spec" = "${spec#*=}" ]; then
            echo "error: --env requires KEY=VALUE form: ${spec}" >&2
            exit 2
        fi
        k="${spec%%=*}"
        if ! [[ "$k" =~ $ENV_KEY_RE ]]; then
            echo "error: invalid --env KEY (letters/digits/underscore only, not starting with a digit): ${spec}" >&2
            exit 2
        fi
    done
fi

command -v docker >/dev/null 2>&1 || { echo "error: docker command not found" >&2; exit 1; }

# --- Resolve dockerfile path (prefer docker/, else relative to repo root)
if [ -f "${SCRIPT_DIR}/${DOCKERFILE}" ]; then
    DOCKERFILE_PATH="${SCRIPT_DIR}/${DOCKERFILE}"
elif [ -f "${REPO_ROOT}/${DOCKERFILE}" ]; then
    DOCKERFILE_PATH="${REPO_ROOT}/${DOCKERFILE}"
else
    echo "error: dockerfile not found: ${DOCKERFILE} (not under docker/ nor repo root)" >&2
    exit 1
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

# --- Escape ENV value (inside double quotes: escape backslash and double quote)
escape_env_value() {
    local v="$1"
    v="${v//\\/\\\\}"   # \ -> \\
    v="${v//\"/\\\"}"   # " -> \"
    printf '%s' "$v"
}

# --- If any --env, generate a temp Dockerfile: copy the original and append
#     ENV instructions at the end. In a multi-stage Dockerfile, the appended
#     ENV belongs to the last (runtime) stage.
GEN_DOCKERFILE=""
if [ "${#ENVS[@]}" -gt 0 ]; then
    GEN_DOCKERFILE="$(mktemp 2>/dev/null || mktemp -t bi)" || { echo "error: failed to create temp file" >&2; exit 1; }
    trap 'rm -f "${GEN_DOCKERFILE}"' EXIT

    cp "${DOCKERFILE_PATH}" "${GEN_DOCKERFILE}"
    {
        echo ""
        echo "# ===== ENV injected by docker/build-image.sh ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ====="
        for spec in "${ENVS[@]}"; do
            k="${spec%%=*}"
            v="${spec#*=}"
            printf 'ENV %s="%s"\n' "$k" "$(escape_env_value "$v")"
        done
    } >> "${GEN_DOCKERFILE}"

    BUILD_DOCKERFILE="${GEN_DOCKERFILE}"
else
    BUILD_DOCKERFILE="${DOCKERFILE_PATH}"
fi

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
BUILD_CMD+=("-f" "${BUILD_DOCKERFILE}" "${REPO_ROOT}")

# --- Run the build
echo "==> repo root:    ${REPO_ROOT}"
echo "==> Dockerfile:   ${BUILD_DOCKERFILE}"
[ -n "$GEN_DOCKERFILE" ] && echo "==> injected env: ${ENVS[*]}"
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
