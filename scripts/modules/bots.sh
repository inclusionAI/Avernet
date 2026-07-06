#!/usr/bin/env bash
# scripts/modules/bots.sh — Local 5 OpenClaw bot gateway module
[[ -n "${_BOTS_SH_LOADED:-}" ]] && return 0
_BOTS_SH_LOADED=1

bots_stack_script() {
    local stack_script="${BCS_DIR}/scripts/start_bcs_bots.sh"
    if [ ! -x "$stack_script" ]; then
        log_error "5bot stack script not executable: ${stack_script}"
        return 1
    fi
    echo "$stack_script"
}

BOTS_DYNAMIC_PROFILE_FILES=(
    "SOUL.md"
    "AGENTS.md"
    "IDENTITY.md"
    "USER.md"
    "TOOLS.md"
    "HEARTBEAT.md"
    "MEMORY.md"
    "BOOTSTRAP.md"
    "OKR.md"
    "OUTPUT.md"
    "RULES.md"
    "SAFETY.md"
    "KNOWLEDGE.md"
)

bots_dynamic_enabled() {
    [ -n "${BOTS_PROFILE_DIR:-}" ]
}

bots_dynamic_profile_dir() {
    local dir="${BOTS_PROFILE_DIR:-}"
    [ -n "$dir" ] || return 1
    case "$dir" in
        /*) printf '%s\n' "$dir" ;;
        *) printf '%s/%s\n' "$PROJECT_ROOT" "$dir" ;;
    esac
}

bots_dynamic_manifest() {
    printf '%s/bots.json\n' "$(bots_dynamic_profile_dir)"
}

bots_dynamic_group_key() {
    local dir base safe_base hash
    dir="$(bots_dynamic_profile_dir)"
    base="$(basename "$dir")"
    safe_base="$(printf '%s' "$base" | tr -c 'A-Za-z0-9_-' '-')"
    if command -v shasum >/dev/null 2>&1; then
        hash="$(printf '%s' "$dir" | shasum -a 256 | awk '{print substr($1,1,12)}')"
    else
        hash="$(printf '%s' "$dir" | cksum | awk '{print $1}')"
    fi
    printf '%s-%s\n' "${safe_base:-bots}" "$hash"
}

bots_dynamic_log_file() {
    printf '%s/bots_%s.log\n' "$LOG_DIR" "$(bots_dynamic_group_key)"
}

bots_dynamic_workspace_root() {
    printf '%s\n' "${OPENCLAW_WORKSPACE_ROOT:-${BCS_DIR}/bcs_bots_test_dir}"
}

bots_dynamic_workspace_dir() {
    local name="$1"
    local profile="$2"
    local source="${3:-$profile}"
    local root
    root="$(bots_dynamic_workspace_root)"
    case "${OPENCLAW_WORKSPACE_LAYOUT:-profile-source}" in
        profile)
            printf '%s/%s\n' "$root" "$profile"
            ;;
        profile-source)
            printf '%s/%s/workspace\n' "$root" "$source"
            ;;
        *)
            printf '%s/%s/workspace\n' "$root" "$name"
            ;;
    esac
}

bots_bcn_plugin_load_dir() {
    local plugin_src="${PROJECT_ROOT}/src/plugin/packages/openclaw-channel-bcn"
    local plugin_package="${plugin_src}/package"
    if [ -f "${plugin_src}/openclaw.plugin.json" ] && [ -f "${plugin_src}/dist/esm/index.js" ]; then
        printf '%s\n' "$plugin_src"
    elif [ -f "${plugin_package}/openclaw.plugin.json" ] && [ -f "${plugin_package}/dist/esm/index.js" ]; then
        printf '%s\n' "$plugin_package"
    else
        printf '%s\n' "$plugin_src"
    fi
}

bots_dynamic_specs() {
    local manifest
    manifest="$(bots_dynamic_manifest)"
    jq -r '
      . as $root
      | ($root.port_start | tonumber) as $start
      | ($root.port_step | tonumber) as $step
      | ($root.scopes // "local") as $default_scopes
      | $root.bots
      | to_entries[]
      | .key as $idx
      | .value as $bot
      | [
          $bot.name,
          $bot.profile,
          ($start + ($idx * $step) | tostring),
          $bot.source,
          $bot.summary,
          $bot.domains,
          $bot.skills,
          ($bot.scopes // $default_scopes)
        ]
      | @tsv
    ' "$manifest"
}

bots_dynamic_count() {
    jq -r '.bots | length' "$(bots_dynamic_manifest)"
}

bots_dynamic_validate_manifest() {
    if ! check_command jq; then
        log_error "jq not found. Install jq before using --profile-dir."
        return 1
    fi

    local dir manifest
    dir="$(bots_dynamic_profile_dir)"
    manifest="$(bots_dynamic_manifest)"

    if [ ! -d "$dir" ]; then
        log_error "Bot profile directory not found: ${dir}"
        return 1
    fi
    if [ ! -f "$manifest" ]; then
        log_error "Bot manifest not found: ${manifest}"
        return 1
    fi
    if ! jq empty "$manifest" >/dev/null 2>&1; then
        log_error "Bot manifest is not valid JSON: ${manifest}"
        return 1
    fi
    if ! jq -e '
        .version == 1
        and (.port_start | type == "number")
        and (.port_step | type == "number")
        and (.port_step > 0)
        and (.bots | type == "array")
        and (.bots | length > 0)
        and all(.bots[];
            (.source | type == "string" and length > 0)
            and (.profile | type == "string" and test("^[A-Za-z0-9_-]+$"))
            and (.name | type == "string" and length > 0)
            and (.summary | type == "string" and length > 0)
            and (.domains | type == "string" and length > 0)
            and (.skills | type == "string" and length > 0)
        )
      ' "$manifest" >/dev/null; then
        log_error "Invalid bot manifest schema: ${manifest}"
        log_error "Required root fields: version=1, port_start, port_step, bots[]."
        log_error "Required per-bot fields: source, profile, name, summary, domains, skills."
        log_error "Runtime profile must match: [A-Za-z0-9_-]"
        return 1
    fi

    local seen_profiles="" seen_ports="" has_error=false
    local name profile port source summary domains skills scopes source_dir file
    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        case "$source" in
            ""|*/*|*..*)
                log_error "${name}: source must be a direct child directory name, got: ${source}"
                has_error=true
                ;;
        esac
        source_dir="${dir}/${source}"
        if [ ! -d "$source_dir" ]; then
            log_error "${name}: source directory not found: ${source_dir}"
            has_error=true
        else
            for file in "${BOTS_DYNAMIC_PROFILE_FILES[@]}"; do
                if [ ! -f "${source_dir}/${file}" ]; then
                    log_error "${name}: required profile file missing: ${source_dir}/${file}"
                    has_error=true
                fi
            done
        fi

        case "$port" in
            ''|*[!0-9]*)
                log_error "${name}: computed port is not numeric: ${port}"
                has_error=true
                ;;
            *)
                if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
                    log_error "${name}: computed port out of range: ${port}"
                    has_error=true
                fi
                ;;
        esac

        case " ${seen_profiles} " in
            *" ${profile} "*)
                log_error "Duplicate bot runtime profile in manifest: ${profile}"
                has_error=true
                ;;
        esac
        case " ${seen_ports} " in
            *" ${port} "*)
                log_error "Duplicate computed bot port in manifest: ${port}"
                has_error=true
                ;;
        esac
        seen_profiles="${seen_profiles} ${profile}"
        seen_ports="${seen_ports} ${port}"
    done < <(bots_dynamic_specs)

    [ "$has_error" = false ]
}

bots_dynamic_config_matches() {
    local name="$1"
    local profile="$2"
    local port="$3"
    local source="${4:-$profile}"
    local profile_dir workspace_dir config_file plugin_path bcs_url

    profile_dir="$(bcs_bot_profile_dir "$profile")"
    workspace_dir="$(bots_dynamic_workspace_dir "$name" "$profile" "$source")"
    config_file="${profile_dir}/openclaw.json"
    plugin_path="$(bots_bcn_plugin_load_dir)"
    bcs_url="ws://127.0.0.1:${BCS_PORT}/ws/bot"

    [ -f "$config_file" ] || return 1
    jq -e \
        --arg bcs_url "$bcs_url" \
        --arg bot_id "$name" \
        --arg workspace "$workspace_dir" \
        --arg plugin_path "$plugin_path" \
        --argjson port "$port" \
        '
          .channels.bcs.enabled == true
          and .channels.bcs.bcsUrl == $bcs_url
          and .channels.bcs.botId == $bot_id
          and .agents.defaults.workspace == $workspace
          and .gateway.port == $port
          and .gateway.mode == "local"
          and ((.plugins.load.paths // []) | index($plugin_path) != null)
        ' "$config_file" >/dev/null 2>&1
}

bots_dynamic_runtime_matches() {
    local name="$1"
    local profile="$2"
    local port="$3"
    local source="${4:-$profile}"
    port_is_listening "$port" || return 1
    bots_dynamic_config_matches "$name" "$profile" "$port" "$source"
}

bots_dynamic_group_fully_running() {
    local count=0
    local name profile port source summary domains skills scopes
    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        count=$((count + 1))
        bots_dynamic_runtime_matches "$name" "$profile" "$port" "$source" || return 1
    done < <(bots_dynamic_specs)
    [ "$count" -gt 0 ]
}

bots_dynamic_model_config_source() {
    printf '%s\n' "${OPENCLAW_MODEL_CONFIG_SOURCE:-${OPENCLAW_CONFIG_FILE:-$HOME/.openclaw/openclaw.json}}"
}

bots_dynamic_model_source_has_fields() {
    local source
    source="$(bots_dynamic_model_config_source)"
    [ -f "$source" ] || return 1
    jq -e '(.models? != null) or (.agents.defaults.model? != null) or (.agents.defaults.models? != null) or (.agents.defaults.imageModel? != null)' "$source" >/dev/null
}

bots_dynamic_config_has_model_fields() {
    local config_file="$1"
    [ -f "$config_file" ] || return 1
    jq -e '(.models? != null) or (.agents.defaults.model? != null) or (.agents.defaults.models? != null) or (.agents.defaults.imageModel? != null)' "$config_file" >/dev/null
}

bots_dynamic_config_has_bcs_core_tools() {
    local config_file="$1"
    [ -f "$config_file" ] || return 1
    jq -e '
      (.tools.alsoAllow // []) as $tools
      | ["bcs_route", "bcs_assign_task", "bcs_send_task_message", "bcs_task_complete"]
      | all(. as $tool | ($tools | index($tool)) != null)
    ' "$config_file" >/dev/null
}

bots_dynamic_models_json() {
    local source
    source="$(bots_dynamic_model_config_source)"
    [ -f "$source" ] || return 0
    jq -c '.models // empty' "$source"
}

bots_dynamic_agent_model_fields_json() {
    local source
    source="$(bots_dynamic_model_config_source)"
    if [ ! -f "$source" ]; then
        printf '{}\n'
        return 0
    fi
    jq -c '
      (.agents.defaults // {}) as $defaults
      | {}
        + (if $defaults.model? != null then {model: $defaults.model} else {} end)
        + (if $defaults.models? != null then {models: $defaults.models} else {} end)
        + (if $defaults.imageModel? != null then {imageModel: $defaults.imageModel} else {} end)
    ' "$source"
}

bots_dynamic_check_profile_configs() {
    local has_error=false
    local name profile port source summary domains skills scopes profile_dir
    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        profile_dir="$(bcs_bot_profile_dir "$profile")"
        if [ -f "${profile_dir}/openclaw.json" ] && ! bots_dynamic_config_matches "$name" "$profile" "$port" "$source"; then
            log_error "${name} profile exists but does not match this --profile-dir group: ${profile_dir}"
            log_error "Expected port=${port}, BCS URL=ws://127.0.0.1:${BCS_PORT}/ws/bot, workspace=$(bots_dynamic_workspace_dir "$name" "$profile" "$source")"
            log_error "Run $(singlebox_cmd clean bots) --profile-dir ${BOTS_PROFILE_DIR} after confirming this group is disposable."
            has_error=true
        fi
    done < <(bots_dynamic_specs)
    [ "$has_error" = false ]
}

bots_dynamic_check_ports_free() {
    local has_error=false
    local name profile port source summary domains skills scopes listener
    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        if port_is_listening "$port"; then
            listener="$(port_listener_summary "$port")"
            log_error "${name} port ${port} is already in use. Current listener: ${listener}"
            has_error=true
        fi
    done < <(bots_dynamic_specs)
    [ "$has_error" = false ]
}

bots_dynamic_copy_profile_files() {
    local source="$1"
    local workspace_dir="$2"
    local source_dir
    local file

    source_dir="$(bots_dynamic_profile_dir)/${source}"
    mkdir -p "$workspace_dir"
    for file in "${BOTS_DYNAMIC_PROFILE_FILES[@]}"; do
        cp "${source_dir}/${file}" "${workspace_dir}/${file}" || return 1
    done
}

bots_dynamic_setup_bcs_skill() {
    local workspace_dir="$1"
    local skills_dir="${workspace_dir}/skills"
    local skill_source_dir="${BCS_DIR}/crates/tools/bcs-cli/bcs-coordination"

    if [ ! -d "$skill_source_dir" ]; then
        log_error "bcs-coordination skill not found: ${skill_source_dir}"
        return 1
    fi

    mkdir -p "$skills_dir"
    rm -rf "${skills_dir}/bcs-coordination"
    cp -R "$skill_source_dir" "${skills_dir}/" || return 1
}

bots_dynamic_write_openclaw_config() {
    local name="$1"
    local profile="$2"
    local port="$3"
    local summary="$4"
    local domains="$5"
    local skills="$6"
    local scopes="$7"
    local profile_dir="$8"
    local workspace_dir="$9"
    local plugin_path="${10}"
    local gateway_token="singlebox_${profile}_gateway_token"
    local bcs_url="ws://127.0.0.1:${BCS_PORT}/ws/bot"
    local models_json model_fields_json

    models_json="$(bots_dynamic_models_json)"
    model_fields_json="$(bots_dynamic_agent_model_fields_json)"

    jq -n \
        --arg workspace "$workspace_dir" \
        --arg bcs_url "$bcs_url" \
        --arg bot_id "$name" \
        --arg summary "$summary" \
        --arg domains "$domains" \
        --arg skills "$skills" \
        --arg scopes "$scopes" \
        --arg plugin_path "$plugin_path" \
        --arg gateway_token "$gateway_token" \
        --argjson models "${models_json:-null}" \
        --argjson model_fields "$model_fields_json" \
        --argjson port "$port" '
        def csv($value):
          $value
          | split(",")
          | map(gsub("^[[:space:]]+|[[:space:]]+$"; ""))
          | map(select(length > 0));
        {
          meta: {
            lastTouchedVersion: "2026.3.12"
          },
          agents: {
            defaults: ($model_fields + {
              workspace: $workspace,
              compaction: {
                mode: "safeguard"
              },
              maxConcurrent: 4,
              subagents: {
                maxConcurrent: 8
              }
            }),
            list: [
              {
                id: "main"
              }
            ]
          },
          skills: {
            allowBundled: []
          },
          tools: {
            profile: "coding",
            alsoAllow: [
              "bcs_route",
              "bcs_assign_task",
              "bcs_send_task_message",
              "bcs_task_complete"
            ]
          },
          messages: {
            ackReactionScope: "group-mentions",
            groupChat: {
              visibleReplies: "automatic"
            }
          },
          commands: {
            native: "auto",
            nativeSkills: "auto",
            restart: true,
            ownerDisplay: "raw"
          },
          session: {
            dmScope: "per-channel-peer"
          },
          hooks: {
            internal: {
              enabled: true,
              entries: {
                "boot-md": {
                  enabled: true
                }
              }
            }
          },
          channels: {
            bcs: {
              enabled: true,
              bcsUrl: $bcs_url,
              botId: $bot_id,
              botName: $bot_id,
              capabilities: {
                summary: $summary,
                domains: csv($domains),
                skills: csv($skills),
                scopes: csv($scopes)
              },
              heartbeatIntervalMs: 60000,
              reconnectIntervalMs: 5000,
              connectionTimeoutMs: 30000
            }
          },
          gateway: {
            port: $port,
            mode: "local",
            bind: "loopback",
            controlUi: {
              dangerouslyDisableDeviceAuth: true
            },
            auth: {
              mode: "token",
              token: $gateway_token
            },
            tailscale: {
              mode: "off",
              resetOnExit: false
            },
            nodes: {
              denyCommands: [
                "camera.snap",
                "camera.clip",
                "screen.record",
                "calendar.add",
                "contacts.add",
                "reminders.add"
              ]
            }
          },
          plugins: {
            load: {
              paths: [
                $plugin_path
              ]
            },
            entries: {
              "openclaw-channel-bcn": {
                enabled: true
              }
            }
          }
        } + (if $models == null then {} else {models: $models} end)
    ' > "${profile_dir}/openclaw.json"
}

bots_dynamic_setup_profile() {
    local name="$1"
    local profile="$2"
    local port="$3"
    local source="$4"
    local summary="$5"
    local domains="$6"
    local skills="$7"
    local scopes="$8"
    local profile_dir workspace_dir plugin_path

    profile_dir="$(bcs_bot_profile_dir "$profile")"
    workspace_dir="$(bots_dynamic_workspace_dir "$name" "$profile" "$source")"
    plugin_path="$(bots_bcn_plugin_load_dir)"

    mkdir -p "$profile_dir" "$workspace_dir" "$LOG_DIR"
    bots_dynamic_copy_profile_files "$source" "$workspace_dir" || return 1
    bots_dynamic_setup_bcs_skill "$workspace_dir" || return 1

    local config_file="${profile_dir}/openclaw.json"
    if [ "${BCS_BOTS_PRESERVE_FILES:-1}" = "1" ] && [ -f "$config_file" ]; then
        if bots_dynamic_model_source_has_fields && ! bots_dynamic_config_has_model_fields "$config_file"; then
            log_info "Refreshing dynamic bot profile with model config: ${profile} (${name})"
        elif ! bots_dynamic_config_has_bcs_core_tools "$config_file"; then
            log_info "Refreshing dynamic bot profile with BCS core tool allowlist: ${profile} (${name})"
        else
            log_info "Preserving existing dynamic bot profile: ${profile} (${name})"
            return 0
        fi
    fi

    if bots_dynamic_model_source_has_fields; then
        log_info "Using OpenClaw model config for ${name}: $(bots_dynamic_model_config_source)"
    else
        log_warn "No OpenClaw model config found for ${name}; bot may connect but cannot produce real model replies."
    fi

    bots_dynamic_write_openclaw_config "$name" "$profile" "$port" "$summary" "$domains" "$skills" "$scopes" "$profile_dir" "$workspace_dir" "$plugin_path"
}

bots_dynamic_start_openclaw() {
    local name="$1"
    local profile="$2"
    local port="$3"
    local log_file="$4"
    local source="${5:-$profile}"
    local profile_dir workspace_dir existing_pids pid waited=0 old_pwd bcs_cli_dir

    profile_dir="$(bcs_bot_profile_dir "$profile")"
    workspace_dir="$(bots_dynamic_workspace_dir "$name" "$profile" "$source")"
    bcs_cli_dir="${BCS_DIR}/target/debug"
    existing_pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "$existing_pids" ]; then
        log_error "${name} port ${port} is already in use by PID(s): $(echo "$existing_pids" | tr '\n' ' ' | xargs)"
        return 1
    fi

    log_info "Starting ${name} OpenClaw gateway (profile=${profile}, port=${port})..."
    old_pwd="$(pwd)"
    if ! cd "$PROJECT_ROOT"; then
        log_error "Failed to enter project root: ${PROJECT_ROOT}"
        return 1
    fi

    NODE_TLS_REJECT_UNAUTHORIZED=0 \
    BCS_IGNORE_CREDENTIALS=1 \
    OPENCLAW_GATEWAY_TOKEN="" \
    PATH="$bcs_cli_dir:$PATH" \
    BOT_DATA_DIR="$profile_dir" \
    BCS_API_BASE_URL="http://127.0.0.1:${BCS_PORT}" \
    OPENCLAW_DATA_DIR="$profile_dir" \
    OPENCLAW_STATE_DIR="$profile_dir" \
    OPENCLAW_CONFIG_PATH="$profile_dir/openclaw.json" \
    OPENCLAW_WORKSPACE_DIR="$workspace_dir" \
    nohup openclaw --profile "$profile" gateway run --port "$port" > "$log_file" 2>&1 < /dev/null &
    pid="$!"
    cd "$old_pwd" || return 1

    while [ "$waited" -lt 30 ]; do
        if port_is_listening "$port"; then
            log_info "${name} gateway started on port ${port} (PID: ${pid})"
            return 0
        fi
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done

    log_error "${name} gateway failed to start; check ${log_file}"
    return 1
}

bots_dynamic_wait_ready() {
    local max_wait="${1:-120}"
    local elapsed=0
    local missing=""
    while [ "$elapsed" -lt "$max_wait" ]; do
        local all_ready=true
        missing=""
        local name profile port source summary domains skills scopes session_file
        while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
            session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
            if ! port_is_listening "$port"; then
                all_ready=false
                missing="${missing}${name}:port ${port}; "
                continue
            fi
            if ! session_has_token "$session_file"; then
                all_ready=false
                missing="${missing}${name}:token; "
            fi
        done < <(bots_dynamic_specs)

        if [ "$all_ready" = true ]; then
            return 0
        fi

        if [ "$elapsed" -eq 0 ] || [ $((elapsed % 10)) -eq 0 ]; then
            log_info "Waiting for dynamic OpenClaw bots to become ready: ${missing}"
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    log_warn "Dynamic OpenClaw bots not ready after ${max_wait}s: ${missing}"
    return 1
}

bots_dynamic_onboard() {
    local bcs_cli="${BCS_DIR}/target/debug/bcs-cli"
    local name profile port source summary domains skills scopes profile_dir session_file token

    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        profile_dir="$(bcs_bot_profile_dir "$profile")"
        session_file="${profile_dir}/.bcs/session.json"
        token="$(bots_session_token "$session_file")"
        if [ -z "$token" ]; then
            log_error "Cannot onboard ${name}: session token not found at ${session_file}"
            return 1
        fi

        log_info "Onboarding ${name}..."
        if ! BOT_DATA_DIR="$profile_dir" BCS_API_BASE_URL="http://127.0.0.1:${BCS_PORT}" \
            "$bcs_cli" --url "http://127.0.0.1:${BCS_PORT}" onboard \
                --token "$token" \
                --name "$name" \
                --summary "$summary" \
                --domains "$domains" \
                --skills "$skills" \
                --scopes "$scopes" >> "$(bots_dynamic_log_file)" 2>&1; then
            log_error "${name} onboard failed; check $(bots_dynamic_log_file)"
            log_error "Refusing to clear session token automatically. Run $(singlebox_cmd clean bots) --profile-dir ${BOTS_PROFILE_DIR} if you intend to reset this group."
            return 1
        fi

        if ! BOT_DATA_DIR="$profile_dir" BCS_API_BASE_URL="http://127.0.0.1:${BCS_PORT}" \
            "$bcs_cli" --url "http://127.0.0.1:${BCS_PORT}" visibility set --value public >> "$(bots_dynamic_log_file)" 2>&1; then
            log_error "Failed to set visibility=public for ${name}; check $(bots_dynamic_log_file)"
            return 1
        fi
    done < <(bots_dynamic_specs)
}

bots_dynamic_capture_session_uuids() {
    local snapshot="$1"
    local sessions_dir
    sessions_dir="$(bots_session_snapshot_dir "$snapshot")"

    : > "$snapshot"
    rm -rf "$sessions_dir"
    mkdir -p "$sessions_dir"

    local name profile port source summary domains skills scopes session_file bot_uuid token_present
    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
        bot_uuid="$(bots_session_bot_uuid "$session_file")"
        if session_has_token "$session_file"; then
            token_present=1
        else
            token_present=0
        fi
        printf '%s|%s|%s|%s\n' "$name" "$profile" "$bot_uuid" "$token_present" >> "$snapshot"
        if [ -f "$session_file" ]; then
            cp "$session_file" "${sessions_dir}/${profile}.session.json"
        fi
    done < <(bots_dynamic_specs)
}

bots_dynamic_preflight_existing_sessions() {
    local token_count=0
    local missing_count=0
    local name profile port source summary domains skills scopes session_file token

    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
        token="$(bots_session_token "$session_file")"
        if [ -n "$token" ]; then
            token_count=$((token_count + 1))
        else
            missing_count=$((missing_count + 1))
        fi
    done < <(bots_dynamic_specs)

    if [ "$token_count" -eq 0 ]; then
        return 0
    fi
    if [ "$missing_count" -ne 0 ]; then
        log_error "Partial dynamic bot sessions detected: ${token_count} profile(s) have tokens, ${missing_count} do not."
        log_error "Refusing to start because that would create missing bot identities implicitly."
        log_error "Run $(singlebox_cmd clean bots) --profile-dir ${BOTS_PROFILE_DIR} if you intend to reset this group."
        return 1
    fi

    log_info "Validating existing dynamic bot session tokens before starting gateways..."
    bots_dynamic_onboard
}

bots_dynamic_validate_session_uuids() {
    local snapshot="$1"
    local changed=false
    local name profile before before_token after session_file

    while IFS='|' read -r name profile before before_token; do
        session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
        after="$(bots_session_bot_uuid "$session_file")"
        if [ -n "$before" ] && [ -n "$after" ] && [ "$after" != "$before" ]; then
            log_error "${name} bot_uuid changed during start: ${before} -> ${after}"
            changed=true
        elif [ -z "$before" ] && [ "$before_token" = "1" ] && [ -n "$after" ]; then
            log_error "${name} received a new bot_uuid during start even though an existing session token was present: ${after}"
            changed=true
        fi
    done < "$snapshot"

    if [ "$changed" = true ]; then
        log_error "Refusing to onboard newly generated bot identities."
        log_error "This usually means BCS data was cleaned while bot session tokens were kept."
        log_error "Run $(singlebox_cmd clean bots) --profile-dir ${BOTS_PROFILE_DIR} if you intend to reset this group."
        return 1
    fi

    return 0
}

bots_dynamic_setup() {
    bots_dynamic_validate_manifest || return 1
    bots_dynamic_check_ports_free || return 1
    mkdir -p "${LOG_DIR}"
    setup_bcn_plugin || return 1
    log_info "Dynamic bot profile directory is valid: $(bots_dynamic_profile_dir)"
    log_info "Dynamic bot count: $(bots_dynamic_count)"
}

bots_dynamic_start() {
    bots_dynamic_validate_manifest || return 1
    resolve_bcs_server_env
    mkdir -p "${LOG_DIR}"
    ensure_local_no_proxy
    : > "$(bots_dynamic_log_file)"

    if bots_dynamic_group_fully_running; then
        log_error "Dynamic bot group is already running from --profile-dir ${BOTS_PROFILE_DIR}."
        log_error "Use $(singlebox_cmd restart bots) --profile-dir ${BOTS_PROFILE_DIR}, or clean it with $(singlebox_cmd clean bots) --profile-dir ${BOTS_PROFILE_DIR}."
        return 1
    fi
    bots_dynamic_check_profile_configs || return 1
    bots_dynamic_check_ports_free || return 1

    setup_bcn_plugin || return 1

    local name profile port source summary domains skills scopes
    log_info "Preparing $(bots_dynamic_count) OpenClaw bot profile(s) from ${BOTS_PROFILE_DIR}..."
    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        if ! bots_dynamic_setup_profile "$name" "$profile" "$port" "$source" "$summary" "$domains" "$skills" "$scopes"; then
            log_error "Failed to prepare dynamic bot profile: ${name}"
            return 1
        fi
    done < <(bots_dynamic_specs)

    if ! bcs_health_ready; then
        log_error "BCS is not running on port ${BCS_PORT}. Profiles were prepared; start BCS first: $(singlebox_cmd start bcs)"
        return 1
    fi
    bots_dynamic_preflight_existing_sessions || return 1

    local snapshot
    snapshot="$(mktemp -t bcs-dynamic-bots-session.XXXXXX 2>/dev/null || true)"
    if [ -z "$snapshot" ]; then
        log_error "Failed to create temporary session snapshot"
        return 1
    fi
    bots_dynamic_capture_session_uuids "$snapshot"

    log_info "Starting $(bots_dynamic_count) OpenClaw bot(s) from ${BOTS_PROFILE_DIR}..."
    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        if ! bots_dynamic_start_openclaw "$name" "$profile" "$port" "$(dirname "$(bots_dynamic_log_file)")/${profile}.log" "$source"; then
            bots_restore_session_snapshot "$snapshot"
            bots_remove_session_snapshot "$snapshot"
            return 1
        fi
    done < <(bots_dynamic_specs)

    if ! bots_dynamic_wait_ready "${BCS_LOCAL_BOTS_READY_TIMEOUT:-120}"; then
        bots_restore_session_snapshot "$snapshot"
        bots_remove_session_snapshot "$snapshot"
        log_error "Dynamic OpenClaw bots did not become ready; check $(bots_dynamic_log_file)"
        return 1
    fi
    if ! bots_dynamic_validate_session_uuids "$snapshot"; then
        bots_restore_session_snapshot "$snapshot"
        bots_remove_session_snapshot "$snapshot"
        return 1
    fi
    bots_remove_session_snapshot "$snapshot"

    if bots_dynamic_onboard; then
        log_info "Dynamic OpenClaw bots onboarded"
    else
        return 1
    fi
}

bots_dynamic_stop() {
    bots_dynamic_validate_manifest || return 1
    mkdir -p "${LOG_DIR}"

    local name profile port source summary domains skills scopes
    log_info "Stopping $(bots_dynamic_count) OpenClaw bot(s) from ${BOTS_PROFILE_DIR}..."
    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        if bots_dynamic_runtime_matches "$name" "$profile" "$port" "$source"; then
            stop_port_processes_if_owned "$port" "${PROJECT_ROOT}" "${name} OpenClaw bot" || true
        elif port_is_listening "$port"; then
            log_warn "Skipping ${name} port ${port}: listener does not match this --profile-dir bot config"
        fi
    done < <(bots_dynamic_specs)
    log_info "Dynamic OpenClaw bots stopped"
}

bots_dynamic_clean() {
    bots_dynamic_validate_manifest || return 1
    bots_dynamic_stop || true

    local name profile port source summary domains skills scopes profile_dir workspace_dir
    log_info "Cleaning dynamic bot runtime data from ${BOTS_PROFILE_DIR}..."
    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        profile_dir="$(bcs_bot_profile_dir "$profile")"
        workspace_dir="$(bots_dynamic_workspace_dir "$name" "$profile" "$source")"
        rm -rf "$profile_dir" "$workspace_dir"
        rm -f "$(dirname "$(bots_dynamic_log_file)")/${profile}.log"
    done < <(bots_dynamic_specs)
    rm -f "$(bots_dynamic_log_file)"
    log_info "Dynamic bot runtime data cleaned"
}

bots_dynamic_status() {
    bots_dynamic_validate_manifest || return 1
    echo "  Bots (--profile-dir ${BOTS_PROFILE_DIR}):"

    local name profile port source summary domains skills scopes session_file bot_uuid
    while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
        session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
        bot_uuid="$(bots_session_bot_uuid "$session_file")"
        if port_is_listening "$port"; then
            if [ -n "$bot_uuid" ]; then
                echo "    ${name}: Running (port: ${port}, profile: ${profile}, bot_uuid: ${bot_uuid})"
            else
                echo "    ${name}: Port occupied (port: ${port}, profile: ${profile}, session: missing bot_uuid)"
            fi
        else
            echo "    ${name}: Stopped (port: ${port}, profile: ${profile})"
        fi
    done < <(bots_dynamic_specs)
}

bots_dynamic_ready() {
    bots_dynamic_validate_manifest || return 1
    bots_dynamic_wait_ready 5
}

bots_dynamic_prereqs() {
    local has_error=false

    echo -e "${CYAN}[bots:${BOTS_PROFILE_DIR}] Prerequisites${NC}"

    if check_openclaw_installed; then
        prereq_ok "openclaw: $(command -v openclaw)"
    else
        prereq_error "openclaw command not found. Run: ./scripts/singlebox.sh install-tools"
        has_error=true
    fi

    if check_command jq; then
        prereq_ok "jq: $(command -v jq)"
    else
        prereq_error "jq not found. Install jq before starting profile-dir bots."
        has_error=true
    fi

    if check_bcs_cli_binary; then
        prereq_ok "bcs-cli: ${BCS_DIR}/target/debug/bcs-cli"
    else
        prereq_error "bcs-cli not found. Run: $(singlebox_cmd setup bcs)"
        has_error=true
    fi

    if check_node_available; then
        prereq_ok "node: $(node --version 2>&1)"
    else
        prereq_error "Node.js >= 22 not found (required for BCN plugin). Install: brew install node@22 (macOS)"
        has_error=true
    fi

    if check_command npm; then
        prereq_ok "npm: $(npm --version 2>&1)"
    else
        prereq_error "npm not found (required for BCN plugin). Install Node.js 22+ with npm."
        has_error=true
    fi

    if bots_dynamic_validate_manifest; then
        prereq_ok "profile-dir manifest: $(bots_dynamic_manifest)"
    else
        has_error=true
    fi

    if [ "$has_error" = false ]; then
        if [ "${SINGLEBOX_COMMAND:-}" = "start" ] && bots_dynamic_group_fully_running; then
            prereq_error "Dynamic bot group is already running from --profile-dir ${BOTS_PROFILE_DIR}. Use $(singlebox_cmd restart bots) --profile-dir ${BOTS_PROFILE_DIR}, or clean it with $(singlebox_cmd clean bots) --profile-dir ${BOTS_PROFILE_DIR}."
            return 1
        fi

        local name profile port source summary domains skills scopes listener
        while IFS=$'\t' read -r name profile port source summary domains skills scopes; do
            if port_is_listening "$port"; then
                listener="$(port_listener_summary "$port")"
                prereq_error "${name} port ${port} is in use. Current listener: ${listener}"
                has_error=true
            else
                prereq_ok "${name} port ${port} available"
            fi
        done < <(bots_dynamic_specs)
    fi

    [ "$has_error" = false ]
}

bots_specs() {
    bcs_load_bot_ports
    printf '%s\n' \
        "CEO|ceo|${BOT1_PORT}" \
        "产品经理|product-manager|${BOT2_PORT}" \
        "研发|engineering|${BOT3_PORT}" \
        "验证|verification|${BOT4_PORT}" \
        "客服|customer-service|${BOT5_PORT}"
}

bots_session_bot_uuid() {
    local session_file="$1"
    [ -f "$session_file" ] || return 0
    sed -n 's/.*"bot_uuid"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$session_file" | head -n 1
}

bots_session_token() {
    local session_file="$1"
    [ -f "$session_file" ] || return 0
    sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$session_file" | head -n 1
}

bots_session_snapshot_dir() {
    local snapshot="$1"
    printf '%s.sessions\n' "$snapshot"
}

bots_capture_session_uuids() {
    local snapshot="$1"
    local sessions_dir
    sessions_dir="$(bots_session_snapshot_dir "$snapshot")"

    : > "$snapshot"
    rm -rf "$sessions_dir"
    mkdir -p "$sessions_dir"

    local spec name profile port session_file bot_uuid token_present
    while IFS='|' read -r name profile port; do
        session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
        bot_uuid="$(bots_session_bot_uuid "$session_file")"
        if session_has_token "$session_file"; then
            token_present=1
        else
            token_present=0
        fi
        printf '%s|%s|%s|%s\n' "$name" "$profile" "$bot_uuid" "$token_present" >> "$snapshot"
        if [ -f "$session_file" ]; then
            cp "$session_file" "${sessions_dir}/${profile}.session.json"
        fi
    done < <(bots_specs)
}

bots_restore_session_snapshot() {
    local snapshot="$1"
    local sessions_dir
    sessions_dir="$(bots_session_snapshot_dir "$snapshot")"

    [ -f "$snapshot" ] || return 0

    local name profile before before_token session_file backup_file
    while IFS='|' read -r name profile before before_token; do
        session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
        backup_file="${sessions_dir}/${profile}.session.json"
        if [ -f "$backup_file" ]; then
            mkdir -p "$(dirname "$session_file")"
            cp "$backup_file" "$session_file"
        else
            rm -f "$session_file"
        fi
    done < "$snapshot"
}

bots_remove_session_snapshot() {
    local snapshot="$1"
    rm -f "$snapshot"
    rm -rf "$(bots_session_snapshot_dir "$snapshot")"
}

bots_validate_session_uuids() {
    local snapshot="$1"
    local changed=false
    local name profile before before_token after session_file

    while IFS='|' read -r name profile before before_token; do
        session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
        after="$(bots_session_bot_uuid "$session_file")"
        if [ -n "$before" ] && [ -n "$after" ] && [ "$after" != "$before" ]; then
            log_error "${name} bot_uuid changed during start: ${before} -> ${after}"
            changed=true
        elif [ -z "$before" ] && [ "$before_token" = "1" ] && [ -n "$after" ]; then
            log_error "${name} received a new bot_uuid during start even though an existing session token was present: ${after}"
            changed=true
        fi
    done < "$snapshot"

    if [ "$changed" = true ]; then
        log_error "Refusing to onboard newly generated bot identities."
        log_error "This usually means BCS data was cleaned while bot session tokens were kept."
        log_error "If you intend to reset bot identities, run: $(singlebox_cmd clean bots)"
        log_error "Or reset BCS and bots together: $(singlebox_cmd clean bcs_bots)"
        return 1
    fi

    return 0
}

bots_preflight_existing_sessions() {
    local token_count=0
    local missing_count=0
    local spec name profile port session_file token

    while IFS='|' read -r name profile port; do
        session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
        token="$(bots_session_token "$session_file")"
        if [ -n "$token" ]; then
            token_count=$((token_count + 1))
        else
            missing_count=$((missing_count + 1))
        fi
    done < <(bots_specs)

    if [ "$token_count" -eq 0 ]; then
        return 0
    fi

    if [ "$missing_count" -ne 0 ]; then
        log_error "Partial bot sessions detected: ${token_count} profile(s) have tokens, ${missing_count} do not."
        log_error "Refusing to start because that would create missing bot identities implicitly."
        log_error "If you intend to reset bot identities, run: $(singlebox_cmd clean bots)"
        log_error "Or reset BCS and bots together: $(singlebox_cmd clean bcs_bots)"
        return 1
    fi

    log_info "Validating existing bot session tokens before starting gateways..."
    if bots_run_stack_script onboard >> "${BCS_BOTS_STACK_LOG}" 2>&1; then
        return 0
    fi

    log_error "Existing bot session token was rejected by BCS; refusing to start gateways or create replacement bot identities."
    log_error "If you intend to reset bot identities, run: $(singlebox_cmd clean bots)"
    log_error "Or reset BCS and bots together: $(singlebox_cmd clean bcs_bots)"
    return 1
}

bots_run_stack_script() {
    local command="$1"
    local stack_script
    stack_script="$(bots_stack_script)" || return 1

    BCS_PORT="${BCS_PORT}" \
    BCS_API_BASE_URL="http://127.0.0.1:${BCS_PORT}" \
    BCS_CONFIG_DIR="${BCS_CONFIG_DIR:-${BCS_RUNTIME_CONFIG_DIR}}" \
    BCS_DATA_DIR="${BCS_DATA_DIR:-${DEP_DIR}/bcs_data}" \
    SERVER_ENV="${BCS_SERVER_ENV}" \
    BCS_AUTH_MOCK="${BCS_AUTH_MOCK:-1}" \
    BCS_MOCK_USER_ID="${BCS_MOCK_USER_ID:-001}" \
    BCS_MOCK_USER_NICK_NAME="${BCS_MOCK_USER_NICK_NAME:-admin}" \
    BCS_MOCK_USER_CHANNEL="${BCS_MOCK_USER_CHANNEL:-mock}" \
    BCS_BOTS_PRESERVE_FILES="${BCS_BOTS_PRESERVE_FILES:-1}" \
    BCS_BOT_PORT_AUTO="${BCS_BOT_PORT_AUTO}" \
    BCS_BOT_PORTS_FILE="${BCS_BOT_PORTS_FILE}" \
    BOT1_PORT="${BOT1_PORT}" \
    BOT2_PORT="${BOT2_PORT}" \
    BOT3_PORT="${BOT3_PORT}" \
    BOT4_PORT="${BOT4_PORT}" \
    BOT5_PORT="${BOT5_PORT}" \
    OPENCLAW_PROFILE_ROOT="${OPENCLAW_PROFILE_ROOT:-}" \
    OPENCLAW_PROFILE_PREFIX="${OPENCLAW_PROFILE_PREFIX-.openclaw-}" \
    OPENCLAW_WORKSPACE_ROOT="${OPENCLAW_WORKSPACE_ROOT:-}" \
    OPENCLAW_WORKSPACE_LAYOUT="${OPENCLAW_WORKSPACE_LAYOUT:-}" \
    OPENCLAW_EXTENSIONS_ROOT="${OPENCLAW_EXTENSIONS_ROOT:-}" \
    OPENCLAW_EXTENSIONS_REPLACE_LINKS="${OPENCLAW_EXTENSIONS_REPLACE_LINKS:-}" \
    OPENCLAW_LOG_ROOT="${OPENCLAW_LOG_ROOT:-}" \
    SINGLEBOX_MODE="${SINGLEBOX_MODE:-local}" \
    BCS_BOTS_DETACHED=1 \
    RUN_ONBOARD_AFTER_START=0 \
        bash "$stack_script" "$command"
}

bots_setup() {
    if bots_dynamic_enabled; then
        bots_dynamic_setup
        return
    fi

    log_info "Setting up 5 local OpenClaw bots..."
    mkdir -p "${LOG_DIR}"

    setup_bcn_plugin || return 1

    bcs_load_bot_ports
    if [ "${BCS_BOT_PORT_AUTO}" = "1" ]; then
        bcs_assign_bot_ports
    else
        bcs_save_bot_ports
    fi

    log_info "5 local OpenClaw bots setup complete"
}

bots_start() {
    if bots_dynamic_enabled; then
        bots_dynamic_start
        return
    fi

    resolve_bcs_server_env
    mkdir -p "${LOG_DIR}"
    ensure_local_no_proxy

    local stack_script
    stack_script="$(bots_stack_script)" || return 1

    : > "${BCS_BOTS_STACK_LOG}"

    local snapshot
    snapshot="$(mktemp -t bcs-bots-session.XXXXXX 2>/dev/null || true)"
    if [ -z "$snapshot" ]; then
        log_error "Failed to create temporary session snapshot"
        return 1
    fi
    bots_capture_session_uuids "$snapshot"

    log_info "Starting 5 local OpenClaw bots..."
    if ! bots_run_stack_script start-bots >> "${BCS_BOTS_STACK_LOG}" 2>&1; then
        bots_restore_session_snapshot "$snapshot"
        bots_remove_session_snapshot "$snapshot"
        log_error "5 local OpenClaw bots failed to start; check ${BCS_BOTS_STACK_LOG}"
        diagnose_bcs_local_stack_failure "${BCS_BOTS_STACK_LOG}"
        return 1
    fi

    bcs_load_bot_ports
    if ! wait_for_bcs_local_bots_ready "${BCS_LOCAL_BOTS_READY_TIMEOUT:-120}"; then
        bots_restore_session_snapshot "$snapshot"
        bots_remove_session_snapshot "$snapshot"
        log_error "5 local OpenClaw bots did not become ready; check ${BCS_BOTS_STACK_LOG}"
        diagnose_bcs_local_stack_failure "${BCS_BOTS_STACK_LOG}"
        return 1
    fi

    if ! bots_validate_session_uuids "$snapshot"; then
        bots_restore_session_snapshot "$snapshot"
        bots_remove_session_snapshot "$snapshot"
        return 1
    fi
    bots_remove_session_snapshot "$snapshot"

    if run_bcs_local_bots_onboard_with_retry "$stack_script"; then
        log_info "5 local OpenClaw bots onboarded"
    else
        log_error "5 local OpenClaw bots onboard failed; check ${BCS_BOTS_STACK_LOG}"
        diagnose_bcs_local_stack_failure "${BCS_BOTS_STACK_LOG}"
        return 1
    fi
}

bots_stop() {
    if bots_dynamic_enabled; then
        bots_dynamic_stop
        return
    fi

    mkdir -p "${LOG_DIR}"
    bcs_load_bot_ports

    if [ -f "${BCS_BOTS_STACK_PID_FILE}" ]; then
        local stack_pid
        stack_pid="$(cat "${BCS_BOTS_STACK_PID_FILE}" 2>/dev/null || true)"
        if [ -n "$stack_pid" ] && kill -0 "$stack_pid" 2>/dev/null; then
            log_info "Stopping old BCS local 5bot stack wrapper (PID: ${stack_pid})"
            stop_process_if_owned "$stack_pid" "${PROJECT_ROOT}" "BCS local 5bot stack wrapper" || true
        fi
        rm -f "${BCS_BOTS_STACK_PID_FILE}"
    fi

    log_info "Stopping 5 local OpenClaw bots..."
    bots_run_stack_script stop-bots >> "${BCS_BOTS_STACK_LOG}" 2>&1 || true
    log_info "5 local OpenClaw bots stopped"
}

bots_restart() {
    if bots_dynamic_enabled; then
        bots_dynamic_stop
        sleep 2
        bots_dynamic_start
        return
    fi

    bots_stop
    sleep 2
    bots_start
}

bots_clean() {
    if bots_dynamic_enabled; then
        bots_dynamic_clean
        return
    fi

    mkdir -p "${LOG_DIR}"
    bcs_load_bot_ports

    log_info "Cleaning 5 local OpenClaw bot runtime data..."
    bots_run_stack_script clean-bots >> "${BCS_BOTS_STACK_LOG}" 2>&1 || \
        log_warn "5bot clean reported warnings; check ${BCS_BOTS_STACK_LOG}"
    rm -f "${BCS_BOTS_STACK_PID_FILE}"
    remove_owned_bcn_plugin_symlink
    log_info "5 local OpenClaw bot runtime data cleaned"
}

bots_status() {
    if bots_dynamic_enabled; then
        bots_dynamic_status
        return
    fi

    echo "  Bots:"
    local spec name profile port session_file bot_uuid
    while IFS='|' read -r name profile port; do
        session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
        bot_uuid="$(bots_session_bot_uuid "$session_file")"
        if port_is_listening "$port"; then
            if [ -n "$bot_uuid" ]; then
                echo "    ${name}: Running (port: ${port}, profile: ${profile}, bot_uuid: ${bot_uuid})"
            else
                echo "    ${name}: Port occupied (port: ${port}, profile: ${profile}, session: missing bot_uuid)"
            fi
        else
            echo "    ${name}: Stopped (port: ${port}, profile: ${profile})"
        fi
    done < <(bots_specs)
}

bots_ready() {
    if bots_dynamic_enabled; then
        bots_dynamic_ready
        return
    fi

    bcs_load_bot_ports
    wait_for_bcs_local_bots_ready 5
}

bots_prereqs() {
    if bots_dynamic_enabled; then
        bots_dynamic_prereqs
        return
    fi

    local has_error=false

    echo -e "${CYAN}[bots] Prerequisites${NC}"

    if check_openclaw_installed; then
        prereq_ok "openclaw: $(command -v openclaw)"
    else
        prereq_error "openclaw command not found. Run: ./scripts/singlebox.sh install-tools"
        has_error=true
    fi

    if check_command jq; then
        prereq_ok "jq: $(command -v jq)"
    else
        prereq_error "jq not found. Install jq before starting local bots."
        has_error=true
    fi

    if check_bcs_cli_binary; then
        prereq_ok "bcs-cli: ${BCS_DIR}/target/debug/bcs-cli"
    else
        prereq_error "bcs-cli not found. Run: $(singlebox_cmd setup bcs)"
        has_error=true
    fi

    if check_node_available; then
        prereq_ok "node: $(node --version 2>&1)"
    else
        prereq_error "Node.js >= 22 not found (required for BCN plugin). Install: brew install node@22 (macOS)"
        has_error=true
    fi

    if check_command npm; then
        prereq_ok "npm: $(npm --version 2>&1)"
    else
        prereq_error "npm not found (required for BCN plugin). Install Node.js 22+ with npm."
        has_error=true
    fi

    local stack_script="${BCS_DIR}/scripts/start_bcs_bots.sh"
    if [ -x "$stack_script" ]; then
        prereq_ok "5bot script: ${stack_script}"
    else
        prereq_error "5bot stack script not executable: ${stack_script}"
        has_error=true
    fi

    if [ "$has_error" = true ]; then
        return 1
    fi
    return 0
}

bots_help() {
    echo "bots - 5 local OpenClaw bot gateways, or N bots from --profile-dir <dir>"
}
