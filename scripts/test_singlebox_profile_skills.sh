#!/usr/bin/env bash
# Exercise profile skill delivery through start/restart without running services.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPORARY="$(mktemp -d)"
trap 'rm -rf "$TEMPORARY"' EXIT
BCS_DIR="${PROJECT_ROOT}/src/bcs"
BCS_PORT=21000
LOG_DIR="${TEMPORARY}/logs"
DEP_DIR="${TEMPORARY}/dependencies"
BOTS_PROFILE_DIR="${TEMPORARY}/source profiles"
OPENCLAW_PROFILE_ROOT="${TEMPORARY}/profiles"
OPENCLAW_PROFILE_PREFIX=""
OPENCLAW_WORKSPACE_ROOT="${TEMPORARY}/workspaces"
OPENCLAW_WORKSPACE_LAYOUT=profile
BCS_BOTS_PRESERVE_FILES=1
BOTS_EXCLUDED_PROFILE_SOURCE=""
mkdir -p "$LOG_DIR" "$DEP_DIR" "$BOTS_PROFILE_DIR"

source "${SCRIPT_DIR}/utils.sh"
source "${SCRIPT_DIR}/modules/bcs.sh"
source "${SCRIPT_DIR}/modules/bots.sh"

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
assert_content() {
    [ -f "$1" ] || fail "missing file: $1"
    [ "$(cat "$1")" = "$2" ] || fail "unexpected content: $1"
}

cat > "${BOTS_PROFILE_DIR}/bots.json" <<'JSON'
{
  "version": 1,
  "name": "Profile skills",
  "port_start": 30801,
  "port_step": 10,
  "bots": [
    {"source": "referee", "profile": "host", "name": "Host", "summary": "Host", "domains": "game", "skills": "metadata-only"},
    {"source": "player", "profile": "guest", "name": "Guest", "summary": "Guest", "domains": "game", "skills": "metadata-only"},
    {"source": "empty", "profile": "empty", "name": "Empty", "summary": "Empty", "domains": "game", "skills": "metadata-only"}
  ]
}
JSON
for role in referee player empty; do
    mkdir -p "${BOTS_PROFILE_DIR}/${role}"
    for file in AGENTS.md IDENTITY.md KNOWLEDGE.md; do
        printf '%s\n' "$role" > "${BOTS_PROFILE_DIR}/${role}/${file}"
    done
done
for profile in host guest empty; do
    mkdir -p "${OPENCLAW_PROFILE_ROOT}/${profile}/.bcs"
    printf '%s\n' 'preserved config' > "${OPENCLAW_PROFILE_ROOT}/${profile}/openclaw.json"
    printf '%s\n' 'preserved session' > "${OPENCLAW_PROFILE_ROOT}/${profile}/.bcs/session.json"
done

HOST_SOURCE="${BOTS_PROFILE_DIR}/referee/skills"
HOST_WORKSPACE="${OPENCLAW_WORKSPACE_ROOT}/host"
GUEST_WORKSPACE="${OPENCLAW_WORKSPACE_ROOT}/guest"
mkdir -p "${HOST_SOURCE}/game/scripts" "${HOST_SOURCE}/game/references" \
    "${HOST_SOURCE}/retired" "${HOST_SOURCE}/bcs-coordination" \
    "${BOTS_PROFILE_DIR}/player/skills/game" "${HOST_WORKSPACE}/skills/local-only"
printf '%s\n' 'host v1' > "${HOST_SOURCE}/game/SKILL.md"
printf '%s\n' '#!/bin/sh' 'exit 0' > "${HOST_SOURCE}/game/scripts/play.sh"
chmod +x "${HOST_SOURCE}/game/scripts/play.sh"
printf '%s\n' 'obsolete reference' > "${HOST_SOURCE}/game/references/old.md"
printf '%s\n' 'hidden reference' > "${HOST_SOURCE}/game/.hidden"
printf '%s\n' 'retired skill' > "${HOST_SOURCE}/retired/SKILL.md"
printf '%s\n' 'profile override' > "${HOST_SOURCE}/bcs-coordination/SKILL.md"
printf '%s\n' 'guest v1' > "${BOTS_PROFILE_DIR}/player/skills/game/SKILL.md"
printf '%s\n' 'local skill' > "${HOST_WORKSPACE}/skills/local-only/SKILL.md"

# Keep filesystem preparation and manifest/source mapping real; isolate network,
# process control, and model configuration from this delivery regression test.
resolve_bcs_server_env() { :; }
ensure_local_no_proxy() { :; }
bots_dynamic_group_fully_running() { return 1; }
bots_dynamic_check_ports_free() { :; }
setup_bcn_plugin() { :; }
bots_bcn_plugin_load_dir() { printf '%s\n' "${TEMPORARY}/plugin"; }
bots_dynamic_model_source_has_fields() { return 1; }
bots_dynamic_config_has_required_model() { :; }
bots_dynamic_config_has_bcs_core_tools() { :; }
bots_dynamic_config_matches() { :; }
bcs_health_ready() { :; }
bots_dynamic_preflight_existing_sessions() { :; }
bots_dynamic_capture_session_uuids() { :; }
bots_dynamic_validate_session_uuids() { :; }
bots_dynamic_wait_ready() { :; }
bots_dynamic_onboard() { :; }
bots_dynamic_fusion_enabled() { return 1; }
bots_dynamic_stop() { printf '%s\n' stop >> "${TEMPORARY}/events"; }
sleep() { :; }
bots_dynamic_start_openclaw() {
    local workspace
    workspace="$(bots_dynamic_workspace_dir "$1" "$2" "$5")"
    if [ "$2" = host ]; then
        assert_content "${workspace}/skills/game/SKILL.md" "$EXPECTED_HOST_CONTENT"
    fi
    printf 'start %s\n' "$2" >> "${TEMPORARY}/events"
}

EXPECTED_HOST_CONTENT='host v1'
bots_start
assert_content "${HOST_WORKSPACE}/skills/game/SKILL.md" 'host v1'
assert_content "${GUEST_WORKSPACE}/skills/game/SKILL.md" 'guest v1'
assert_content "${HOST_WORKSPACE}/skills/game/.hidden" 'hidden reference'
[ -x "${HOST_WORKSPACE}/skills/game/scripts/play.sh" ] || fail 'script lost executable permission'
[ ! -e "${HOST_WORKSPACE}/skills/metadata-only" ] || fail 'capability metadata used as a skill source'
cmp "${BCS_DIR}/crates/tools/bcs-cli/bcs-coordination/SKILL.md" \
    "${HOST_WORKSPACE}/skills/bcs-coordination/SKILL.md"
[ -f "${OPENCLAW_WORKSPACE_ROOT}/empty/skills/bcs-coordination/SKILL.md" ] || fail 'profile without skills failed'

# A subsequent start must overwrite content even when config preservation is on.
printf '%s\n' 'host v2' > "${HOST_SOURCE}/game/SKILL.md"
rm "${HOST_SOURCE}/game/references/old.md"
printf '%s\n' 'new reference' > "${HOST_SOURCE}/game/references/new.md"
rm -rf "${HOST_SOURCE}/retired"
mkdir -p "${HOST_SOURCE}/new skill"
printf '%s\n' 'new skill' > "${HOST_SOURCE}/new skill/SKILL.md"
mkdir -p "${TEMPORARY}/linked-game"
printf '%s\n' 'external skill' > "${TEMPORARY}/linked-game/SKILL.md"
rm -rf "${HOST_WORKSPACE}/skills/game"
ln -s "${TEMPORARY}/linked-game" "${HOST_WORKSPACE}/skills/game"
EXPECTED_HOST_CONTENT='host v2'
bots_start
assert_content "${TEMPORARY}/linked-game/SKILL.md" 'external skill'
[ ! -L "${HOST_WORKSPACE}/skills/game" ] || fail 'runtime skill still links to external content'
assert_content "${HOST_WORKSPACE}/skills/game/references/new.md" 'new reference'
assert_content "${HOST_WORKSPACE}/skills/new skill/SKILL.md" 'new skill'
[ ! -e "${HOST_WORKSPACE}/skills/game/references/old.md" ] || fail 'removed file survived refresh'
[ ! -e "${HOST_WORKSPACE}/skills/retired" ] || fail 'removed profile skill survived refresh'
assert_content "${HOST_WORKSPACE}/skills/local-only/SKILL.md" 'local skill'

printf '%s\n' 'host v3' > "${HOST_SOURCE}/game/SKILL.md"
EXPECTED_HOST_CONTENT='host v3'
: > "${TEMPORARY}/events"
bots_restart
assert_content "${TEMPORARY}/events" $'stop\nstart host\nstart guest\nstart empty'
for profile in host guest empty; do
    assert_content "${OPENCLAW_PROFILE_ROOT}/${profile}/openclaw.json" 'preserved config'
    assert_content "${OPENCLAW_PROFILE_ROOT}/${profile}/.bcs/session.json" 'preserved session'
done

# Source links must never expose an external store or leave a live dependency
# in the active workspace. Rejection must preserve both skills and ownership.
cp -R "${HOST_WORKSPACE}/skills" "${TEMPORARY}/expected-skills"
cp -R "${HOST_WORKSPACE}/.singlebox-profile-skills" "${TEMPORARY}/expected-managed"
mkdir -p "${TEMPORARY}/external-store/private-skill"
printf '%s\n' 'external content' > "${TEMPORARY}/external-store/private-skill/SKILL.md"
assert_symlinked_source_rejected() {
    : > "${TEMPORARY}/events"
    if bots_start > "${TEMPORARY}/source-symlink.log" 2>&1; then
        fail "startup accepted a source symlink: $1"
    fi
    grep -Fq 'Profile skills must not contain symlinks:' "${TEMPORARY}/source-symlink.log" \
        || fail "missing source symlink diagnostic: $1"
    [ ! -s "${TEMPORARY}/events" ] || fail "gateway started with a source symlink: $1"
    diff -r "${TEMPORARY}/expected-skills" "${HOST_WORKSPACE}/skills"
    diff -r "${TEMPORARY}/expected-managed" "${HOST_WORKSPACE}/.singlebox-profile-skills"
    assert_content "${TEMPORARY}/external-store/private-skill/SKILL.md" 'external content'
}

ln -s "${TEMPORARY}/external-store" "${HOST_SOURCE}/shared-store"
assert_symlinked_source_rejected 'top-level skill directory'
rm "${HOST_SOURCE}/shared-store"

ln -s "${TEMPORARY}/external-store" "${HOST_SOURCE}/game/.shared-store"
assert_symlinked_source_rejected 'hidden nested directory'
rm "${HOST_SOURCE}/game/.shared-store"

ln -s "${TEMPORARY}/external-store/private-skill/SKILL.md" "${HOST_SOURCE}/game/references/external.md"
assert_symlinked_source_rejected 'nested file'
rm "${HOST_SOURCE}/game/references/external.md"

ln -s ../SKILL.md "${HOST_SOURCE}/game/references/internal.md"
assert_symlinked_source_rejected 'relative link within the profile'
rm "${HOST_SOURCE}/game/references/internal.md"

ln -s missing.md "${HOST_SOURCE}/game/references/broken.md"
assert_symlinked_source_rejected 'dangling nested link'
rm "${HOST_SOURCE}/game/references/broken.md"

mv "$HOST_SOURCE" "${TEMPORARY}/saved-source-skills"
ln -s "${TEMPORARY}/saved-source-skills" "$HOST_SOURCE"
assert_symlinked_source_rejected 'skills root directory'
rm "$HOST_SOURCE"
ln -s "${TEMPORARY}/missing-skills" "$HOST_SOURCE"
assert_symlinked_source_rejected 'dangling skills root'
rm "$HOST_SOURCE"
mv "${TEMPORARY}/saved-source-skills" "$HOST_SOURCE"

# Copy errors must fail startup before any gateway starts, preserving old skills.
(
    cp() {
        if [ "$1" = -R ]; then return 1; fi
        command cp "$@"
    }
    : > "${TEMPORARY}/events"
    if bots_start; then fail 'skill copy failure did not fail startup'; fi
    [ ! -s "${TEMPORARY}/events" ] || fail 'gateway started after skill copy failure'
    assert_content "${HOST_WORKSPACE}/skills/game/SKILL.md" 'host v3'
)

mkdir -p "${TEMPORARY}/linked-workspace"
ln -s "${HOST_WORKSPACE}/skills" "${TEMPORARY}/linked-workspace/skills"
if bots_dynamic_copy_profile_files referee "${TEMPORARY}/linked-workspace"; then
    fail 'shared skills root symlink was accepted'
fi
assert_content "${HOST_WORKSPACE}/skills/game/SKILL.md" 'host v3'

# Removing the source skills directory retires only previously delivered names.
rm -rf "$HOST_SOURCE"
bots_dynamic_copy_profile_files referee "$HOST_WORKSPACE"
[ ! -e "${HOST_WORKSPACE}/skills/game" ] || fail 'removed skills directory left profile skills active'
[ ! -e "${HOST_WORKSPACE}/skills/new skill" ] || fail 'removed skills directory left added skill active'
assert_content "${HOST_WORKSPACE}/skills/local-only/SKILL.md" 'local skill'
[ -f "${HOST_WORKSPACE}/skills/bcs-coordination/SKILL.md" ] || fail 'shared coordination skill was removed'
bots_dynamic_copy_profile_files referee "$HOST_WORKSPACE"

# Actual repository profiles must deliver all their nested skill assets as well.
BOTS_PROFILE_DIR="${PROJECT_ROOT}/scripts/6bots_undercover_game_profile"
bots_dynamic_copy_profile_files referee "${TEMPORARY}/real-referee"
diff -r "${BOTS_PROFILE_DIR}/referee/skills" "${TEMPORARY}/real-referee/skills"
bots_dynamic_copy_profile_files player-laochen "${TEMPORARY}/real-player"
diff -r "${BOTS_PROFILE_DIR}/player-laochen/skills" "${TEMPORARY}/real-player/skills"

printf 'PASS: singlebox profile skill start/restart and refresh tests\n'
