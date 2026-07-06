#!/bin/bash
######################################################################
# sync_common.sh - Shared functions for monorepo to multi-repo sync
######################################################################

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Logging
log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_debug() { [[ "${SYNC_DEBUG:-}" == "1" ]] && echo -e "${CYAN}[DEBUG]${NC} $1"; true; }

# Get remote URL for a module from repo_mapping.txt
get_remote_url() {
    local module="$1"
    local mapping_file="${2:-src/repo_mapping.txt}"
    local line mod url

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" ]] && continue
        mod="${line%%:*}"
        url="${line#*:}"
        mod=$(echo "$mod" | xargs)
        url=$(echo "$url" | xargs)
        [[ "$mod" == "$module" ]] && echo "$url" && return 0
    done < "$mapping_file"

    log_error "No mapping found for module: $module"
    return 1
}

# Get all module names from repo_mapping.txt
get_all_modules() {
    local mapping_file="${1:-src/repo_mapping.txt}"
    local line

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" ]] && continue
        local mod="${line%%:*}"
        [[ -n "$mod" ]] && echo "$mod" | xargs
    done < "$mapping_file"
}

# Check if branch matches allowed patterns (supports dev_* wildcard)
should_sync_branch() {
    local branch="$1"
    local allowed="${2:-master,dev,dev_*}"
    local pattern prefix remaining="$allowed"

    while [[ -n "$remaining" ]]; do
        pattern="${remaining%%,*}"
        [[ "$remaining" == *,* ]] && remaining="${remaining#*,}" || remaining=""
        pattern=$(echo "$pattern" | xargs)
        [[ -z "$pattern" ]] && continue

        if [[ "$pattern" == *"*" ]]; then
            prefix="${pattern%\*}"
            [[ "$branch" == "$prefix"* ]] && return 0
        elif [[ "$branch" == "$pattern" ]]; then
            return 0
        fi
    done
    return 1
}

# Check if local branch is synced with remote (no unpushed commits)
# Returns 0 if synced, 1 if has unpushed commits or fetch failed
check_branch_synced_with_remote() {
    local branch="$1"
    local remote="${2:-origin}"

    # Fetch remote to get latest state
    if ! git fetch "$remote" --quiet 2>/dev/null; then
        log_warn "Failed to fetch from $remote, assuming branch is not synced"
        return 1
    fi

    # Check if remote branch exists
    if ! git rev-parse --verify "$remote/$branch" &>/dev/null; then
        log_warn "Remote branch $remote/$branch does not exist"
        return 1
    fi

    # Check if local HEAD is an ancestor of remote branch (local <= remote)
    # This means local branch is either at remote or behind remote (merged)
    if git merge-base --is-ancestor HEAD "$remote/$branch" 2>/dev/null; then
        return 0
    fi

    # Local has commits not in remote
    return 1
}

# Git helpers
get_commit_author()  { git log -1 --format='%an' "${1:-HEAD}"; }
get_commit_email()   { git log -1 --format='%ae' "${1:-HEAD}"; }
get_commit_message() { git log -1 --format='%B' "${1:-HEAD}"; }
get_short_hash()     { git rev-parse --short "${1:-HEAD}"; }

# Check required tools
check_dependencies() {
    command -v git &> /dev/null || { log_error "git not found"; return 1; }
    git subtree 2>&1 | grep -qiE 'usage:|用法：' || { log_error "git-subtree not found (install via: brew install git)"; return 1; }
}

# Retry with backoff
retry() {
    local max="$1" delay="$2"
    shift 2
    local n=1
    while [[ $n -le $max ]]; do
        "$@" && return 0
        [[ $n -lt $max ]] && { log_warn "Attempt $n failed, retrying..."; sleep "$delay"; }
        ((n++))
    done
    return 1
}

######################################################################
# Subtree-split incremental story
#
# We rely on `git subtree split --rejoin` against a per-(module,source)
# cache branch (`split/<module>-cache/<source-slug>`):
#   - First run on a cache branch walks full history and writes a rejoin
#     merge commit with `git-subtree-mainline:`/`git-subtree-split:` trailers.
#   - Subsequent runs merge the source ref into the cache branch and split
#     again; git-subtree's `find_existing_splits` reads those trailers and
#     skips already-processed history.
#
# A previous version layered a persistent .git/ocb-subtree-cache on top to
# also seed git-subtree's per-run cachedir. That wrapper had a fatal race
# (the watcher seeded files after find_existing_splits ran but before
# cache_set, causing "cache for X already exists" on every second run) and
# is gone. If you find a stale `.git/ocb-subtree-cache/` directory, delete
# it — nothing reads it anymore.
######################################################################
