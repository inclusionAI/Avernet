#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# utils.sh uses PROJECT_ROOT/SCRIPT_DIR-style vars from singlebox; define minimally.
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/utils.sh"

FAILS=0
fail() { printf 'FAIL: %s\n' "$*" >&2; FAILS=$((FAILS + 1)); }
assert_eq() {
  local actual="$1" expected="$2" msg="$3"
  [ "$actual" = "$expected" ] || fail "${msg}: expected [${expected}], got [${actual}]"
}
assert_contains() {
  printf '%s' "$1" | grep -F -- "$2" >/dev/null || fail "expected to contain [$2], got: $1"
}

test_mode_default_is_source() {
  unset BCN_PLUGIN_SOURCE
  assert_eq "$(bcn_plugin_mode)" "source" "default mode"
}
test_mode_npm() {
  assert_eq "$(BCN_PLUGIN_SOURCE=npm bcn_plugin_mode)" "npm" "npm mode"
}
test_mode_invalid_fails() {
  local out rc
  out="$(BCN_PLUGIN_SOURCE=bogus bcn_plugin_mode 2>&1)"; rc=$?
  [ "$rc" -ne 0 ] || fail "invalid mode should exit non-zero"
  assert_contains "$out" "source, npm"
}
test_version_default_latest() {
  unset BCN_PLUGIN_VERSION
  assert_eq "$(bcn_plugin_version)" "latest" "default version"
}
test_npm_spec() {
  unset BCN_PLUGIN_VERSION
  assert_eq "$(bcn_plugin_npm_spec)" "@avernet-plugin/openclaw-channel-bcn@latest" "npm spec"
}
test_resolve_npm_dir_uses_ext_root() {
  local tmp; tmp="$(mktemp -d)"
  mkdir -p "${tmp}/openclaw-channel-bcn"
  assert_eq "$(OPENCLAW_EXTENSIONS_ROOT="$tmp" bcn_plugin_resolve_npm_dir)" \
    "${tmp}/openclaw-channel-bcn" "resolve from ext root"
  rm -rf "$tmp"
}
test_resolve_npm_dir_ignores_ext_root_symlink() {
  local tmp; tmp="$(mktemp -d)"
  local ext="${tmp}/standalone/extensions"
  local global="${tmp}/.openclaw/extensions/openclaw-channel-bcn"
  mkdir -p "$ext" "$global"
  ln -s "$global" "${ext}/openclaw-channel-bcn"

  assert_eq "$(OPENCLAW_EXTENSIONS_ROOT="$ext" HOME="$tmp" bcn_plugin_resolve_npm_dir)" \
    "$global" "resolve should ignore ext root symlink and use physical npm dir"
  rm -rf "$tmp"
}
test_resolve_npm_dir_missing_fails() {
  local tmp rc; tmp="$(mktemp -d)"
  OPENCLAW_EXTENSIONS_ROOT="$tmp" HOME="$tmp" bcn_plugin_resolve_npm_dir >/dev/null 2>&1; rc=$?
  [ "$rc" -ne 0 ] || fail "resolve should fail when dir missing"
  rm -rf "$tmp"
}
test_ensure_npm_installs_and_resolves() {
  local tmp; tmp="$(mktemp -d)"
  local bindir="${tmp}/bin"; mkdir -p "$bindir"
  local ext="${tmp}/ext"
  # Stub openclaw: on "plugins install", create the plugin dir; record args.
  cat > "${bindir}/openclaw" <<STUB
#!/usr/bin/env bash
echo "\$@" >> "${tmp}/openclaw.args"
if [ "\$1" = "plugins" ] && [ "\$2" = "install" ]; then
  echo "installing... progress noise"
  mkdir -p "${ext}/openclaw-channel-bcn"
  echo "installed."
fi
exit 0
STUB
  chmod +x "${bindir}/openclaw"

  local out
  out="$(PATH="${bindir}:$PATH" OPENCLAW_EXTENSIONS_ROOT="$ext" HOME="$tmp" \
        BCN_PLUGIN_VERSION=latest bcn_plugin_ensure_npm)"
  assert_eq "$out" "${ext}/openclaw-channel-bcn" "ensure_npm resolves dir"
  assert_contains "$(cat "${tmp}/openclaw.args")" "plugins install npm:@avernet-plugin/openclaw-channel-bcn@latest --force --pin"
  rm -rf "$tmp"
}
test_ensure_npm_missing_openclaw_fails() {
  local tmp rc; tmp="$(mktemp -d)"
  # Empty PATH dir so openclaw is not found.
  PATH="${tmp}/nope" OPENCLAW_EXTENSIONS_ROOT="$tmp" HOME="$tmp" bcn_plugin_ensure_npm >/dev/null 2>&1; rc=$?
  [ "$rc" -ne 0 ] || fail "ensure_npm should fail without openclaw"
  rm -rf "$tmp"
}
test_ensure_symlink_creates_and_is_idempotent() {
  local tmp; tmp="$(mktemp -d)"
  local target="${tmp}/t"; mkdir -p "$target"
  local link="${tmp}/link"
  ensure_bcn_symlink "$target" "$link" 0 >/dev/null 2>&1
  assert_eq "$(readlink "$link")" "$target" "symlink created"
  ensure_bcn_symlink "$target" "$link" 0 >/dev/null 2>&1
  assert_eq "$(readlink "$link")" "$target" "symlink idempotent"
  rm -rf "$tmp"
}
test_clean_removes_symlink_to_global_npm_dir_with_custom_ext_root() {
  local tmp; tmp="$(mktemp -d)"
  local ext="${tmp}/standalone/extensions"
  local global="${tmp}/.openclaw/extensions/openclaw-channel-bcn"
  local link="${ext}/openclaw-channel-bcn"
  mkdir -p "$ext" "$global"
  ln -s "$global" "$link"

  (
    PROJECT_ROOT="${PROJECT_ROOT}"
    BCS_DIR="${PROJECT_ROOT}/src/bcs"
    LOG_DIR="${tmp}/logs"
    DEP_DIR="${tmp}/dep"
    OPENCLAW_EXTENSIONS_ROOT="$ext"
    HOME="$tmp"
    mkdir -p "$LOG_DIR" "$DEP_DIR"
    . "${SCRIPT_DIR}/utils.sh"
    . "${SCRIPT_DIR}/modules/bcs.sh"
    remove_owned_bcn_plugin_symlink >/dev/null 2>&1
  ) || fail "cleanup should succeed for npm symlink"

  [ ! -L "$link" ] || fail "cleanup should remove symlink pointing to global npm dir"
  rm -rf "$tmp"
}

test_help_mentions_flag() {
  local out
  out="$(bash "${SCRIPT_DIR}/singlebox.sh" --help 2>&1)"
  assert_contains "$out" "--bcn-plugin-source"
  assert_contains "$out" "BCN_PLUGIN_SOURCE"
}
test_invalid_mode_flag_errors() {
  local out rc
  out="$(bash "${SCRIPT_DIR}/singlebox.sh" --bcn-plugin-source bogus status 2>&1)"; rc=$?
  [ "$rc" -ne 0 ] || fail "invalid --bcn-plugin-source should exit non-zero"
  assert_contains "$out" "source, npm"
}

test_setup_bcn_plugin_npm_links_installed_dir() {
  local tmp; tmp="$(mktemp -d)"
  local bindir="${tmp}/bin"; mkdir -p "$bindir"
  local ext="${tmp}/ext"
  cat > "${bindir}/openclaw" <<STUB
#!/usr/bin/env bash
if [ "\$1" = "plugins" ] && [ "\$2" = "install" ]; then mkdir -p "${ext}/openclaw-channel-bcn"; fi
exit 0
STUB
  chmod +x "${bindir}/openclaw"

  # bcs.sh expects PROJECT_ROOT/BCS_DIR etc.; source utils then bcs in a subshell.
  local out
  out="$(
    PROJECT_ROOT="${PROJECT_ROOT}" BCS_DIR="${PROJECT_ROOT}/src/bcs" \
    PATH="${bindir}:$PATH" OPENCLAW_EXTENSIONS_ROOT="$ext" HOME="$tmp" \
    BCN_PLUGIN_SOURCE=npm bash -c '
      . "'"${SCRIPT_DIR}"'/utils.sh"
      . "'"${SCRIPT_DIR}"'/modules/bcs.sh"
      setup_bcn_plugin >/dev/null 2>&1
      readlink "'"$ext"'/openclaw-channel-bcn" 2>/dev/null || echo NONE
    '
  )"
  # Native install placed it AT the link path, so setup should treat it as present (no self-link).
  # Either the path is a real dir (no symlink) or a symlink to itself is avoided; assert dir exists.
  [ -d "${ext}/openclaw-channel-bcn" ] || fail "npm setup did not produce plugin dir"
  rm -rf "$tmp"
}

test_mode_default_is_source
test_mode_npm
test_mode_invalid_fails
test_version_default_latest
test_npm_spec
test_resolve_npm_dir_uses_ext_root
test_resolve_npm_dir_ignores_ext_root_symlink
test_resolve_npm_dir_missing_fails
test_ensure_npm_installs_and_resolves
test_ensure_npm_missing_openclaw_fails
test_ensure_symlink_creates_and_is_idempotent
test_clean_removes_symlink_to_global_npm_dir_with_custom_ext_root
test_help_mentions_flag
test_invalid_mode_flag_errors
test_setup_bcn_plugin_npm_links_installed_dir

test_load_dir_source_mode() {
  local out
  out="$(
    PROJECT_ROOT="${PROJECT_ROOT}" bash -c '
      . "'"${SCRIPT_DIR}"'/utils.sh"
      . "'"${SCRIPT_DIR}"'/modules/bots.sh"
      BCN_PLUGIN_SOURCE=source bots_bcn_plugin_load_dir
    '
  )"
  assert_contains "$out" "src/bcs/crates/plugins/openclaw-channel-bcn"
}
test_load_dir_npm_mode() {
  local tmp; tmp="$(mktemp -d)"; mkdir -p "${tmp}/openclaw-channel-bcn"
  local out
  out="$(
    PROJECT_ROOT="${PROJECT_ROOT}" OPENCLAW_EXTENSIONS_ROOT="$tmp" HOME="$tmp" bash -c '
      . "'"${SCRIPT_DIR}"'/utils.sh"
      . "'"${SCRIPT_DIR}"'/modules/bots.sh"
      BCN_PLUGIN_SOURCE=npm bots_bcn_plugin_load_dir
    '
  )"
  assert_eq "$out" "${tmp}/openclaw-channel-bcn" "npm load dir"
  rm -rf "$tmp"
}
test_stack_script_forwards_mode() {
  # bots_run_stack_script forwards env by running the stack script; assert the
  # forwarding lines exist in the source (integration-by-inspection).
  assert_contains "$(cat "${SCRIPT_DIR}/modules/bots.sh")" "BCN_PLUGIN_SOURCE=\"\${BCN_PLUGIN_SOURCE:-source}\""
  assert_contains "$(cat "${SCRIPT_DIR}/modules/bots.sh")" "BCN_PLUGIN_VERSION=\"\${BCN_PLUGIN_VERSION:-latest}\""
}

test_stack_script_has_npm_branch() {
  local src; src="$(cat "${PROJECT_ROOT}/src/bcs/scripts/start_bcs_bots.sh")"
  assert_contains "$src" 'BCN_PLUGIN_SOURCE="${BCN_PLUGIN_SOURCE:-source}"'
  assert_contains "$src" 'if [ "$BCN_PLUGIN_SOURCE" = "npm" ]; then'
  # build must be skipped in npm mode
  assert_contains "$src" '[ "$BCN_PLUGIN_SOURCE" != "npm" ]'
}

test_session_bot_uuid_requires_usable_session() {
  local tmp; tmp="$(mktemp -d)"
  local funcs="${tmp}/stack-session-functions.sh"
  local profile_root="${tmp}/profiles"
  local session_file="${profile_root}/.openclaw-ceo/.bcs/session.json"

  awk '
    /^profile_dir_for\(\)/ {emit=1}
    /^workspace_dir_for\(\)/ {emit=0}
    emit {print}
  ' "${PROJECT_ROOT}/src/bcs/scripts/start_bcs_bots.sh" > "$funcs"
  mkdir -p "$(dirname "$session_file")"

  cat > "$session_file" <<JSON
{"bot_uuid":"default:545716","token":"saved-token","bcs_url":"ws://127.0.0.1:21000/ws/bot"}
JSON
  local bot_uuid
  bot_uuid="$(
    OPENCLAW_PROFILE_ROOT="$profile_root"
    OPENCLAW_PROFILE_PREFIX=".openclaw-"
    BCS_URL="ws://127.0.0.1:21000/ws/bot"
    . "$funcs"
    session_bot_uuid_for ceo
  )"
  assert_eq "$bot_uuid" "default:545716" "usable session should preserve bot identity"

  cat > "$session_file" <<JSON
{"bot_uuid":"default:545716","token":"","bcs_url":"ws://127.0.0.1:21000/ws/bot"}
JSON
  bot_uuid="$(
    OPENCLAW_PROFILE_ROOT="$profile_root"
    OPENCLAW_PROFILE_PREFIX=".openclaw-"
    BCS_URL="ws://127.0.0.1:21000/ws/bot"
    . "$funcs"
    session_bot_uuid_for ceo
  )"
  assert_eq "$bot_uuid" "" "session without token must not pin bot identity"

  cat > "$session_file" <<JSON
{"bot_uuid":"default:545716","token":"saved-token","bcs_url":"ws://127.0.0.1:29999/ws/bot"}
JSON
  bot_uuid="$(
    OPENCLAW_PROFILE_ROOT="$profile_root"
    OPENCLAW_PROFILE_PREFIX=".openclaw-"
    BCS_URL="ws://127.0.0.1:21000/ws/bot"
    . "$funcs"
    session_bot_uuid_for ceo
  )"
  assert_eq "$bot_uuid" "" "session for a different BCS URL must not pin bot identity"

  rm -rf "$tmp"
}

test_stack_config_allows_plugin_path_refresh() {
  local tmp; tmp="$(mktemp -d)"
  local funcs="${tmp}/stack-match-functions.sh"
  awk '
    /^profile_dir_for\(\)/ {emit=1}
    /^load_bot_ports\(\)/ {emit=0}
    emit {print}
  ' "${PROJECT_ROOT}/src/bcs/scripts/start_bcs_bots.sh" > "$funcs"

  local profile_root="${tmp}/profiles"
  local workspace_root="${tmp}/workspaces"
  local source_plugin="${PROJECT_ROOT}/src/bcs/crates/plugins/openclaw-channel-bcn"
  local npm_plugin="${tmp}/extensions/openclaw-channel-bcn"
  mkdir -p "${profile_root}/.openclaw-ceo" "${workspace_root}/ceo/workspace" "$npm_plugin"
  cat > "${profile_root}/.openclaw-ceo/openclaw.json" <<JSON
{
  "agents": {
    "defaults": {
      "workspace": "${workspace_root}/ceo/workspace"
    }
  },
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "ws://127.0.0.1:21000/ws/bot"
    }
  },
  "gateway": {
    "port": 30001,
    "mode": "local"
  },
  "plugins": {
    "load": {
      "paths": [
        "$source_plugin"
      ]
    }
  }
}
JSON

  (
    OPENCLAW_PROFILE_ROOT="$profile_root"
    OPENCLAW_PROFILE_PREFIX=".openclaw-"
    OPENCLAW_WORKSPACE_ROOT="$workspace_root"
    OPENCLAW_WORKSPACE_LAYOUT="profile-source"
    BCS_URL="ws://127.0.0.1:21000/ws/bot"
    BCN_PLUGIN_LOAD_DIR="$npm_plugin"
    . "$funcs"
    bot_config_base_matches_local "CEO" "ceo" "30001" "ceo"
  ) || fail "plugin path change should still match stack ownership"

  (
    OPENCLAW_PROFILE_ROOT="$profile_root"
    OPENCLAW_PROFILE_PREFIX=".openclaw-"
    OPENCLAW_WORKSPACE_ROOT="$workspace_root"
    OPENCLAW_WORKSPACE_LAYOUT="profile-source"
    BCS_URL="ws://127.0.0.1:21000/ws/bot"
    BCN_PLUGIN_LOAD_DIR="$npm_plugin"
    . "$funcs"
    ! bot_config_matches_local "CEO" "ceo" "30001" "ceo"
  ) || fail "plugin path change should require profile config refresh"

  rm -rf "$tmp"
}

test_dynamic_config_refreshes_plugin_path() {
  local tmp; tmp="$(mktemp -d)"
  local profile_root="${tmp}/profiles"
  local workspace_root="${tmp}/workspaces"
  local profile_source="${tmp}/profile-source"
  local source_plugin="${PROJECT_ROOT}/src/bcs/crates/plugins/openclaw-channel-bcn"
  local npm_plugin="${tmp}/extensions/openclaw-channel-bcn"
  local profile_dir="${profile_root}/.openclaw-ceo"
  local workspace_dir="${workspace_root}/ceo/workspace"
  mkdir -p "$profile_dir" "$workspace_dir" "$npm_plugin" "$profile_source"
  cat > "${profile_dir}/openclaw.json" <<JSON
{
  "agents": {
    "defaults": {
      "workspace": "$workspace_dir"
    }
  },
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "ws://127.0.0.1:21000/ws/bot",
      "botId": "CEO"
    }
  },
  "tools": {
    "alsoAllow": [
      "bcs_route",
      "bcs_assign_task",
      "bcs_send_task_message",
      "bcs_task_complete"
    ]
  },
  "gateway": {
    "port": 30001,
    "mode": "local"
  },
  "plugins": {
    "load": {
      "paths": [
        "$source_plugin"
      ]
    }
  }
}
JSON

  (
    PROJECT_ROOT="${PROJECT_ROOT}"
    BCS_DIR="${PROJECT_ROOT}/src/bcs"
    LOG_DIR="${tmp}/logs"
    DEP_DIR="${tmp}/dep"
    BCS_PORT=21000
    BOTS_PROFILE_DIR="$profile_source"
    OPENCLAW_PROFILE_ROOT="$profile_root"
    OPENCLAW_PROFILE_PREFIX=".openclaw-"
    OPENCLAW_WORKSPACE_ROOT="$workspace_root"
    OPENCLAW_WORKSPACE_LAYOUT="profile-source"
    mkdir -p "$LOG_DIR" "$DEP_DIR"
    . "${SCRIPT_DIR}/utils.sh"
    . "${SCRIPT_DIR}/modules/bcs.sh"
    . "${SCRIPT_DIR}/modules/bots.sh"
    bots_bcn_plugin_load_dir() { printf '%s\n' "$npm_plugin"; }
    bots_dynamic_specs() { printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "CEO" "ceo" "30001" "ceo" "CEO summary" "strategy" "routing" "production" "openclaw"; }
    bots_dynamic_copy_profile_files() { return 0; }
    bots_dynamic_setup_bcs_skill() { return 0; }
    bots_dynamic_model_source_has_fields() { return 1; }

    bots_dynamic_check_profile_configs
    bots_dynamic_setup_profile "CEO" "ceo" "30001" "ceo" "CEO summary" "strategy" "routing" "production"
  ) || fail "dynamic plugin path change should be accepted and refreshed"

  jq -e --arg plugin "$npm_plugin" '(.plugins.load.paths // []) | index($plugin) != null' \
    "${profile_dir}/openclaw.json" >/dev/null || fail "dynamic profile was not refreshed to npm plugin path"

  rm -rf "$tmp"
}

test_dynamic_config_copies_thinking_default() {
  local tmp; tmp="$(mktemp -d)"
  local model_source="${tmp}/model-source.json"
  local matching_config="${tmp}/matching-config.json"
  local stale_config="${tmp}/stale-config.json"
  local timeout_stale_config="${tmp}/timeout-stale-config.json"
  cat > "$model_source" <<'JSON'
{
  "models": {"mode": "merge", "providers": {}},
  "agents": {
    "defaults": {
      "model": {"primary": "test/model"},
      "models": {"test/model": {"alias": "Test Model"}},
      "thinkingDefault": "off",
      "timeoutSeconds": 600
    }
  }
}
JSON
  cat > "$matching_config" <<'JSON'
{
  "models": {"mode": "merge", "providers": {}},
  "agents": {
    "defaults": {
      "model": {"primary": "test/model"},
      "models": {"test/model": {"alias": "Test Model"}},
      "thinkingDefault": "off",
      "timeoutSeconds": 600,
      "workspace": "/runtime/workspace"
    }
  }
}
JSON
  cat > "$timeout_stale_config" <<'JSON'
{
  "models": {"mode": "merge", "providers": {}},
  "agents": {
    "defaults": {
      "model": {"primary": "test/model"},
      "models": {"test/model": {"alias": "Test Model"}},
      "thinkingDefault": "off",
      "workspace": "/runtime/workspace"
    }
  }
}
JSON
  cat > "$stale_config" <<'JSON'
{
  "models": {"mode": "merge", "providers": {}},
  "agents": {
    "defaults": {
      "model": {"primary": "test/model"},
      "models": {"test/model": {"alias": "Test Model"}},
      "workspace": "/runtime/workspace"
    }
  }
}
JSON

  local model_fields
  model_fields="$({
    PROJECT_ROOT="${PROJECT_ROOT}"
    BCS_DIR="${PROJECT_ROOT}/src/bcs"
    OPENCLAW_MODEL_CONFIG_SOURCE="$model_source"
    LOG_DIR="${tmp}/logs"
    DEP_DIR="${tmp}/dep"
    BCS_PORT=21000
    . "${SCRIPT_DIR}/modules/bots.sh"
    bots_dynamic_agent_model_fields_json
  })"
  printf '%s\n' "$model_fields" | jq -e '
    .model.primary == "test/model"
    and .models["test/model"].alias == "Test Model"
    and .thinkingDefault == "off"
    and .timeoutSeconds == 600
  ' >/dev/null || fail "dynamic profile should copy the model thinking default"

  (
    PROJECT_ROOT="${PROJECT_ROOT}"
    BCS_DIR="${PROJECT_ROOT}/src/bcs"
    OPENCLAW_MODEL_CONFIG_SOURCE="$model_source"
    LOG_DIR="${tmp}/logs"
    DEP_DIR="${tmp}/dep"
    BCS_PORT=21000
    . "${SCRIPT_DIR}/modules/bots.sh"
    bots_dynamic_config_matches_model_source "$matching_config"
  ) || fail "matching dynamic model config should be preserved"
  if (
    PROJECT_ROOT="${PROJECT_ROOT}"
    BCS_DIR="${PROJECT_ROOT}/src/bcs"
    OPENCLAW_MODEL_CONFIG_SOURCE="$model_source"
    LOG_DIR="${tmp}/logs"
    DEP_DIR="${tmp}/dep"
    BCS_PORT=21000
    . "${SCRIPT_DIR}/modules/bots.sh"
    bots_dynamic_config_matches_model_source "$stale_config"
  ); then
    fail "stale dynamic model config should be refreshed"
  fi
  if (
    PROJECT_ROOT="${PROJECT_ROOT}"
    BCS_DIR="${PROJECT_ROOT}/src/bcs"
    OPENCLAW_MODEL_CONFIG_SOURCE="$model_source"
    LOG_DIR="${tmp}/logs"
    DEP_DIR="${tmp}/dep"
    BCS_PORT=21000
    . "${SCRIPT_DIR}/modules/bots.sh"
    bots_dynamic_config_matches_model_source "$timeout_stale_config"
  ); then
    fail "dynamic model config without timeoutSeconds should be refreshed"
  fi

  rm -rf "$tmp"
}

test_load_dir_source_mode
test_load_dir_npm_mode
test_stack_script_forwards_mode
test_stack_script_has_npm_branch
test_session_bot_uuid_requires_usable_session
test_stack_config_allows_plugin_path_refresh
test_dynamic_config_refreshes_plugin_path
test_dynamic_config_copies_thinking_default

# Lays out a fake PROJECT_ROOT holding just the plugin source, plus an npm stub
# that records every invocation and fakes the artifacts the real one leaves
# behind. Echoes "<fake_root>|<calls_file>|<ext_dir>".
_bcn_source_fixture() {
  local tmp="$1"
  local src="${tmp}/root/src/bcs/crates/plugins/openclaw-channel-bcn"
  local bindir="${tmp}/bin" calls="${tmp}/npm-calls" ext="${tmp}/ext"
  mkdir -p "$src" "$bindir" "$ext"
  printf '{"name":"stub","version":"0.0.0"}\n' > "${src}/package.json"
  cat > "${bindir}/npm" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "${calls}"
[ "\$1 \$2" = "run build" ] && mkdir -p dist/esm && : > dist/esm/index.js
[ "\$1" = "install" ] && mkdir -p node_modules
exit 0
STUB
  chmod +x "${bindir}/npm"
  printf '%s|%s|%s\n' "${tmp}/root" "$calls" "$ext"
}

_run_setup_bcn_source() {
  local root="$1" bindir="$2" ext="$3" home="$4"
  PROJECT_ROOT="$root" BCS_DIR="${root}/src/bcs" \
  PATH="${bindir}:$PATH" OPENCLAW_EXTENSIONS_ROOT="$ext" HOME="$home" \
  BCN_PLUGIN_SOURCE=source bash -c '
    . "'"${SCRIPT_DIR}"'/utils.sh"
    . "'"${SCRIPT_DIR}"'/modules/bcs.sh"
    setup_bcn_plugin
  ' >/dev/null 2>&1
}

# The source build reduces a 2051-package dev tree to the one package that
# ships. `npm prune --omit=dev` did that by reconciling the whole tree (~7m
# measured); reinstalling prod-only reaches the same state in seconds. Pin the
# sequence so the slow form cannot come back unnoticed.
test_source_build_reinstalls_prod_deps_instead_of_pruning() {
  local tmp; tmp="$(mktemp -d)"
  local fixture root calls ext
  fixture="$(_bcn_source_fixture "$tmp")"
  root="${fixture%%|*}"; calls="$(printf '%s' "$fixture" | cut -d'|' -f2)"
  ext="${fixture##*|}"

  _run_setup_bcn_source "$root" "${tmp}/bin" "$ext" "$tmp"

  local first second third
  first="$(sed -n 1p "$calls")"
  second="$(sed -n 2p "$calls")"
  third="$(sed -n 3p "$calls")"

  assert_contains "$first" "install"
  assert_contains "$first" "--no-audit"
  assert_contains "$first" "--no-fund"
  assert_eq "$second" "run build" "build runs against the dev tree"
  assert_contains "$third" "install"
  assert_contains "$third" "--omit=dev"

  if grep -q -- "prune" "$calls"; then
    fail "source build should not call npm prune: $(cat "$calls")"
  fi
  # The dev tree must be gone before the prod install, or the reinstall would
  # leave all 2051 packages in place and the reduction would be a no-op.
  if [ -d "${root}/src/bcs/crates/plugins/openclaw-channel-bcn/node_modules/eslint" ]; then
    fail "dev dependencies survived into the copied-back tree"
  fi
  rm -rf "$tmp"
}

# The singlebox-coverage workflow caches dist/ + node_modules and relies on this
# branch to turn a cache hit into a skipped build. If the skip stops firing the
# cache silently buys nothing, so assert npm is never reached.
test_source_build_skips_when_already_built() {
  local tmp; tmp="$(mktemp -d)"
  local fixture root calls ext src
  fixture="$(_bcn_source_fixture "$tmp")"
  root="${fixture%%|*}"; calls="$(printf '%s' "$fixture" | cut -d'|' -f2)"
  ext="${fixture##*|}"
  src="${root}/src/bcs/crates/plugins/openclaw-channel-bcn"

  mkdir -p "${src}/dist/esm" "${src}/node_modules"
  : > "${src}/dist/esm/index.js"

  _run_setup_bcn_source "$root" "${tmp}/bin" "$ext" "$tmp"

  if [ -s "$calls" ]; then
    fail "prebuilt plugin should skip npm entirely, got: $(cat "$calls")"
  fi
  rm -rf "$tmp"
}

test_source_build_reinstalls_prod_deps_instead_of_pruning
test_source_build_skips_when_already_built

if [ "$FAILS" -eq 0 ]; then echo "ALL PASS"; else echo "${FAILS} FAILURE(S)"; exit 1; fi
