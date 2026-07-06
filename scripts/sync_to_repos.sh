#!/bin/bash
######################################################################
# sync_to_repos.sh
# Sync monorepo modules to deployment repos, preserving full git history.
#
# Source defaults to the current branch. Target branch on the deployment
# repo defaults to the source branch name, or can be set via -t/--target.
#
# Uses `git subtree split` (stateless, no --rejoin) to extract per-module
# commit history and push it directly to deployment repos as a new branch.
#
# Usage:
#   sync_to_repos.sh                          # Sync all modules from current branch
#   sync_to_repos.sh <module>                 # Sync specific module from current branch
#   sync_to_repos.sh -s <ref>                 # Sync all modules from <ref>
#   sync_to_repos.sh -t <branch> <module>     # Sync to specific target branch
#
# Options:
#   -s, --source <ref>      Source git ref to sync from (default: current branch)
#   -t, --target <branch>   Target branch on deployment repo (default: source branch name)
#   SYNC_DRY_RUN=1          Show what would happen without making changes
#   SYNC_DEBUG=1             Enable debug logging
#
# Examples:
#   sync_to_repos.sh backend                                    # Sync backend from current branch
#   sync_to_repos.sh -t dev_dev-skill-center-migration backend       # Sync to specific target branch
#   sync_to_repos.sh -s origin/master                           # Sync all modules from origin/master
#   SYNC_DRY_RUN=1 sync_to_repos.sh
######################################################################
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/lib/sync_common.sh"

SOURCE_REF=""
TARGET_BRANCH=""

# Generate a stable staging branch name from the operator's git identity.
# Tries user.name, then user.email's local-part, then whoami — and falls
# through at each step if the candidate is empty or sanitizes to empty
# (e.g. a Chinese user.name has no ASCII alphanumerics).
make_sync_branch() {
    local operator=""
    local candidate

    for candidate in \
        "$(git config user.name 2>/dev/null || true)" \
        "$(git config user.email 2>/dev/null | cut -d@ -f1 || true)" \
        "$(whoami 2>/dev/null || true)"; do
        [[ -z "$candidate" ]] && continue
        operator=$(echo "$candidate" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '_' | sed 's/^_*//; s/_*$//')
        [[ -n "$operator" ]] && break
    done

    if [[ -z "$operator" ]]; then
        operator="user"
    fi

    echo "${operator}_sync"
}

######################################################################
# sync_module <module> <staging_branch>
#
# Extracts src/<module> history from $SOURCE_REF and pushes it as a new
# branch on the deployment repo.
######################################################################
sync_module() {
    local module="$1"
    local staging_branch="$2"
    local remote_url
    remote_url=$(get_remote_url "$module" "${PROJECT_ROOT}/src/repo_mapping.txt")

    if [[ -z "$remote_url" ]]; then
        log_warn "Skipping $module: no remote URL configured"
        return 1
    fi

    log_info "Syncing $module ($SOURCE_REF -> $remote_url:$staging_branch)"

    if [[ "${SYNC_DRY_RUN:-}" == "1" ]]; then
        log_info "[DRY RUN] Would split src/$module from $SOURCE_REF and push to $remote_url:$staging_branch"
        return 0
    fi

    # If the source looks like a remote-tracking ref (e.g. origin/dev),
    # fetch the underlying branch so we have the latest commits.
    if [[ "$SOURCE_REF" =~ ^([^/]+)/(.+)$ ]] && git -C "$PROJECT_ROOT" remote | grep -qx "${BASH_REMATCH[1]}"; then
        local remote_name="${BASH_REMATCH[1]}"
        local remote_branch="${BASH_REMATCH[2]}"
        log_debug "Fetching ${remote_name} ${remote_branch}..."
        git -C "$PROJECT_ROOT" fetch "$remote_name" "$remote_branch" --quiet
    else
        log_debug "Skipping fetch — $SOURCE_REF is not a remote-tracking ref"
    fi

    # Stateless split: walk src/<module> history from $SOURCE_REF directly.
    #
    # --ignore-joins is load-bearing. Without it, git-subtree's
    # find_existing_splits() scans git-subtree-mainline:/git-subtree-split:
    # trailers from merge commits in the walked history and prefills its
    # in-memory cache via cache_set(). If upstream contains two such
    # trailers mapping the same mainline SHA to different split SHAs
    # (common after rebases/merges of past sync runs), git-subtree dies
    # with "cache for X already exists!". The trailer scan runs on every
    # invocation regardless of --rejoin; --ignore-joins is the only way
    # to disable it. Bad trailers live in upstream commit messages, so
    # no local cleanup helps.
    log_info "Running subtree split for src/${module} ($SOURCE_REF, full history walk)..."
    local split_sha rc=0
    split_sha=$(cd "$PROJECT_ROOT" && git subtree split --prefix="src/${module}" --ignore-joins "$SOURCE_REF") || rc=$?

    if [[ $rc -ne 0 ]]; then
        log_error "git subtree split failed for src/$module (exit $rc) — aborting"
        return 1
    fi

    if [[ -z "$split_sha" ]]; then
        log_error "git subtree split produced no SHA for src/$module — aborting"
        return 1
    fi
    log_info "Split tip: ${split_sha:0:8} (full: $split_sha)"

    # Force-push: each split run produces a different commit SHA for the
    # same source (parent topology differs run-to-run), so the new split
    # is never a fast-forward of a prior push to this user-scoped staging
    # branch. --force is correct semantics here — the branch is named
    # after the operator and represents "this user's current sync attempt".
    # We can't use --force-with-lease because we push to an ad-hoc URL
    # with no remote-tracking ref to compare against.
    if retry 2 3 git -C "$PROJECT_ROOT" push --force "$remote_url" "${split_sha}:refs/heads/${staging_branch}" --quiet; then
        log_info "Pushed $module -> $staging_branch [${split_sha:0:8}]"
        return 0
    else
        log_error "Failed to push $module to $staging_branch"
        return 1
    fi
}

######################################################################
# Main
######################################################################
usage() {
    echo "Usage:"
    echo "  sync_to_repos.sh                          # Sync all modules from current branch"
    echo "  sync_to_repos.sh <module>                 # Sync specific module from current branch"
    echo "  sync_to_repos.sh -s <ref>                 # Sync all modules from <ref>"
    echo "  sync_to_repos.sh -s <ref> <module>        # Sync specific module from <ref>"
    echo "  sync_to_repos.sh -t <branch> <module>     # Sync to specific target branch"
    echo ""
    echo "Options:"
    echo "  -s, --source <ref>      Source git ref to sync from (default: current branch)"
    echo "  -t, --target <branch>   Target branch on deployment repo (default: source branch name)"
    echo "  -h, --help              Show this help message"
    echo "  SYNC_DRY_RUN=1          Show what would be synced without making changes"
    echo "  SYNC_DEBUG=1             Enable debug logging"
}

main() {
    local module=""
    local positional=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -s|--source)
                if [[ -z "${2:-}" ]]; then
                    log_error "--source requires a git ref argument"
                    exit 1
                fi
                SOURCE_REF="$2"
                shift 2
                ;;
            -t|--target)
                if [[ -z "${2:-}" ]]; then
                    log_error "--target requires a branch name argument"
                    exit 1
                fi
                TARGET_BRANCH="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            --)
                shift
                while [[ $# -gt 0 ]]; do
                    positional+=("$1")
                    shift
                done
                ;;
            -*)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
            *)
                positional+=("$1")
                shift
                ;;
        esac
    done

    if [[ ${#positional[@]} -eq 0 ]]; then
        module=""
    elif [[ ${#positional[@]} -eq 1 ]]; then
        module="${positional[0]}"
    else
        usage
        exit 1
    fi

    check_dependencies

    # Resolve SOURCE_REF: default to current branch
    if [[ -z "$SOURCE_REF" ]]; then
        SOURCE_REF=$(git -C "$PROJECT_ROOT" symbolic-ref --short HEAD 2>/dev/null || echo "HEAD")
    fi

    if ! git -C "$PROJECT_ROOT" rev-parse --verify --quiet "$SOURCE_REF" >/dev/null; then
        # Only warn — sync_module will fetch remote-tracking refs before resolving
        log_debug "Source ref $SOURCE_REF not yet resolvable; will attempt fetch"
    fi

    # Resolve TARGET_BRANCH: default to source branch name (strip remote prefix)
    if [[ -z "$TARGET_BRANCH" ]]; then
        if [[ "$SOURCE_REF" =~ ^[^/]+/(.+)$ ]] && git -C "$PROJECT_ROOT" remote | grep -qx "${SOURCE_REF%%/*}"; then
            TARGET_BRANCH="${BASH_REMATCH[1]}"
        else
            TARGET_BRANCH="$SOURCE_REF"
        fi
    fi

    local staging_branch="$TARGET_BRANCH"

    log_info "Source: $SOURCE_REF"
    log_info "Target branch: $staging_branch"

    local success=0 failed=0
    local synced_modules=()

    if [[ -n "$module" ]]; then
        if sync_module "$module" "$staging_branch"; then
            synced_modules+=("$module")
            ((success++))
        else
            ((failed++))
        fi
    else
        for mod in $(get_all_modules "${PROJECT_ROOT}/src/repo_mapping.txt"); do
            if sync_module "$mod" "$staging_branch"; then
                synced_modules+=("$mod")
                ((success++))
            else
                ((failed++))
            fi
        done
    fi

    echo ""
    log_info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_info "Sync complete — $success succeeded, $failed failed"
    log_info ""
    if [[ ${#synced_modules[@]} -gt 0 ]]; then
        log_info "Synced to branch: ${staging_branch}"
        for mod in "${synced_modules[@]}"; do
            local url
            url=$(get_remote_url "$mod" "${PROJECT_ROOT}/src/repo_mapping.txt")
            log_info "  $mod  →  ${url}  (branch: ${staging_branch})"
        done
    fi
    log_info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

main "$@"
