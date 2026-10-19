#!/usr/bin/env bash
# BCS runtime configuration generation; sourced by bcs.sh.
resolve_bcs_config_file() {
    local config_dir="$1"

    if [ -n "${MOLTIS_BCS_CONFIG:-}" ]; then
        echo "${MOLTIS_BCS_CONFIG}"
    elif [ -f "${config_dir}/bcs-config.toml" ]; then
        echo "${config_dir}/bcs-config.toml"
    else
        echo "${config_dir}/bcs-config-local.toml"
    fi
}

toml_sed_replacement() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//&/\\&}"
    value="${value//|/\\|}"
    printf '%s' "$value"
}

resolve_bcs_local_llm_api_key_reference() {
    # 以下为安全注释COSEC：仅解析明确的环境变量引用，避免将占位符传给 BCS Judge。
    if [ "${OPENCLAW_OPENAI_API_KEY:-}" != "OPENAI_API_KEY" ]; then
        return 0
    fi

    if [ -z "${OPENAI_API_KEY:-}" ]; then
        log_error "OPENCLAW_OPENAI_API_KEY references OPENAI_API_KEY, but OPENAI_API_KEY is not set."
        log_error "Set OPENAI_API_KEY in the launch environment, or set OPENCLAW_OPENAI_API_KEY to a real key in ${PROJECT_ROOT}/singlebox/.env.local."
        return 1
    fi

    OPENCLAW_OPENAI_API_KEY="$OPENAI_API_KEY"
    export OPENCLAW_OPENAI_API_KEY
    log_info "Resolved BCS Judge credential reference from OPENAI_API_KEY."
}

prepare_bcs_runtime_config_resources() {
    local config_dir="$1"
    local seeds_src="${BCS_DIR}/seeds"
    local seeds_dst="${config_dir}/seeds"
    local assets_src="${BCS_DIR}/assets"
    local assets_dst="${config_dir}/assets"

    if [ -d "$seeds_src" ]; then
        rm -rf "$seeds_dst"
        mkdir -p "$seeds_dst"
        cp -R "${seeds_src}/." "$seeds_dst/" || return 1
    fi

    rm -rf "$assets_dst"
    if [ -d "$assets_src" ]; then
        mkdir -p "$assets_dst"
        local asset_dir asset_name
        for asset_dir in "${assets_src}"/*; do
            [ -d "$asset_dir" ] || continue
            asset_name="$(basename "$asset_dir")"
            if [ -d "${asset_dir}/dist" ]; then
                mkdir -p "${assets_dst}/${asset_name}"
                cp -R "${asset_dir}/dist" "${assets_dst}/${asset_name}/" || return 1
            else
                log_warn "BCS asset ${asset_name} has no dist directory; skipping runtime asset copy"
            fi
        done
    fi
}

prepare_bcs_runtime_config() {
    local template="${BCS_DIR}/configs/bcs-config-local.toml"
    local config_dir="${BCS_CONFIG_DIR:-${BCS_RUNTIME_CONFIG_DIR}}"
    local base_config="${config_dir}/bcs-config.toml"
    local local_config="${config_dir}/bcs-config-local.toml"
    local bcs_url="http://127.0.0.1:${BCS_PORT}"
    local bcs_bind="${BCS_BIND:-}"
    local bcs_botchat_url="${BCS_BOTCHAT_URL:-}"
    local bcs_mock_user_id="${BCS_MOCK_USER_ID:-}"
    local bcs_mock_user_name="${BCS_MOCK_USER_NICK_NAME:-}"
    if [ "${BCS_SERVER_ENV:-}" = "local" ] && [ -z "${BCS_E2E_MOCK_BASE_URL:-}" ]; then
        resolve_bcs_local_llm_api_key_reference || return 1
    fi
    # BCS_BOT_PROFILE_DIR is the explicit override. If the caller used the
    # dynamic --profile-dir form (BOTS_PROFILE_DIR), wire the same source
    # directory into BCS so bot context files can be found by bcs-fusion.
    local bcs_bot_profile_dir="${BCS_BOT_PROFILE_DIR:-${BOTS_PROFILE_DIR:-}}"
    if [ -n "$bcs_bot_profile_dir" ]; then
        case "$bcs_bot_profile_dir" in
            /*) ;;
            *) bcs_bot_profile_dir="${PROJECT_ROOT}/${bcs_bot_profile_dir}" ;;
        esac
        if [ ! -d "$bcs_bot_profile_dir" ]; then
            log_warn "Configured bot profile directory does not exist: ${bcs_bot_profile_dir}"
        fi
    fi

    if [ "${BCS_SERVER_ENV:-}" = "dev" ]; then
        if [ -f "${BCS_DIR}/configs/bcs-config-dev.toml" ]; then
            template="${BCS_DIR}/configs/bcs-config-dev.toml"
        elif [ -f "${BCS_DIR}/configs/bcs-config.toml" ]; then
            template="${BCS_DIR}/configs/bcs-config.toml"
        else
            log_warn "BCS dev config not found; falling back to local config template"
        fi
    fi

    if [ ! -f "$template" ]; then
        log_error "BCS config template not found: ${template}"
        return 1
    fi

    mkdir -p "$config_dir"
    cp "$template" "$base_config"
    cp "$template" "$local_config"

    local sed_args=()
    local escaped_bcs_url
    escaped_bcs_url="$(toml_sed_replacement "$bcs_url")"
    # Only rewrite the top-level BCS listener port. Redis/MySQL datasource
    # sections also contain a `port` key and must keep their own values.
    sed_args+=("-e" "1,/^\\[/{s|^port = [0-9][0-9]*$|port = ${BCS_PORT}|;}")
    sed_args+=("-e" "s|^bcs_endpoint = \".*\"$|bcs_endpoint = \"${escaped_bcs_url}\"|")

    if [ -n "$bcs_bind" ]; then
        local escaped_bind
        escaped_bind="$(toml_sed_replacement "$bcs_bind")"
        sed_args+=("-e" "s|^bind = \".*\"$|bind = \"${escaped_bind}\"|")
    fi
    if [ -n "$bcs_botchat_url" ]; then
        local escaped_botchat_url
        escaped_botchat_url="$(toml_sed_replacement "$bcs_botchat_url")"
        sed_args+=("-e" "s|^botchat_url = \".*\"$|botchat_url = \"${escaped_botchat_url}\"|")
    fi
    if [ -n "$bcs_mock_user_id" ]; then
        local escaped_mock_user_id
        escaped_mock_user_id="$(toml_sed_replacement "$bcs_mock_user_id")"
        sed_args+=("-e" "s|^mock_user_id = \".*\"$|mock_user_id = \"${escaped_mock_user_id}\"|")
    fi
    if [ -n "$bcs_mock_user_name" ]; then
        local escaped_mock_user_name
        escaped_mock_user_name="$(toml_sed_replacement "$bcs_mock_user_name")"
        sed_args+=("-e" "s|^mock_user_name = \".*\"$|mock_user_name = \"${escaped_mock_user_name}\"|")
    fi
    if [ -n "$bcs_bot_profile_dir" ]; then
        local escaped_bcs_bot_profile_dir
        escaped_bcs_bot_profile_dir="$(toml_sed_replacement "$bcs_bot_profile_dir")"
        # Use # as the sed delimiter because the path contains /. Escape the
        # trailing $ because # would otherwise make it "$#" (positional param
        # count) inside the double-quoted script.
        sed_args+=("-e" "s#^bots_base_dir = \".*\"\$#bots_base_dir = \"${escaped_bcs_bot_profile_dir}\"#")
    fi
    if [ "${BCS_SERVER_ENV:-}" = "local" ]; then
        # Local singlebox starts with the judge disabled. A complete environment
        # configuration below may opt it back in. Never carry a plaintext key
        # from a developer-edited template into the generated runtime config.
        sed_args+=(
            "-e" "/^\[llm\]/,/^\[/{s|^type = \".*\"$|type = \"none\"|;/^api_key = /d;}"
        )
    fi
    if [ -n "${BCS_E2E_MOCK_BASE_URL:-}" ]; then
        local escaped_e2e_judge_url
        escaped_e2e_judge_url="$(toml_sed_replacement "${BCS_E2E_MOCK_BASE_URL}/v1")"
        sed_args+=(
            "-e" "/^\[llm\]/,/^\[/{s|^type = \".*\"$|type = \"openai_compatible\"|;s|^base_url = \".*\"$|base_url = \"${escaped_e2e_judge_url}\"|;s|^api_key_env = \".*\"$|api_key_env = \"BCS_E2E_JUDGE_API_KEY\"|;s|^model = \".*\"$|model = \"e2e-judge\"|;s|^timeout_ms = [0-9][0-9]*$|timeout_ms = 30000|;}"
            "-e" "/^\[security.outbound_url\]/,/^\[/{s|^block_private_networks = .*|block_private_networks = false|;s|^allow_loopback = .*|allow_loopback = true|;}"
        )
        log_info "Enabling local Provider/Judge mock for BCS E2E"
        # E2E also drives the friend work-order ports against the same mock:
        # BCS notifies friend events at /api/v1/work-orders/events and posts
        # TC friend grants to /api/internal/bot-friend-auth/sync with the
        # forwarded principal re-addressed as iss=bcs — the runtime contract
        # the changed-line coverage gate measures. The template never carries
        # this key (empty would fail friend_work_order_base_url validation),
        # so inject it as a new top-level entry just before the first
        # [section]; awk is used (not sed) so the insertion stays portable
        # across BSD/GNU sed line-insert syntax.
        local escaped_e2e_friend_url
        escaped_e2e_friend_url="$(toml_sed_replacement "${BCS_E2E_MOCK_BASE_URL}")"
        for config_file in "$base_config" "$local_config"; do
            tmp_file="${config_file}.friend.tmp"
            if ! awk -v entry="friend_work_order_base_url = \"${escaped_e2e_friend_url}\"" '
                BEGIN { inserted = 0 }
                !inserted && /^\[/ { print entry; print ""; inserted = 1 }
                { print }
            ' "$config_file" > "$tmp_file"; then
                rm -f "$tmp_file"
                return 1
            fi
            mv "$tmp_file" "$config_file" || return 1
        done
    elif [ "${BCS_SERVER_ENV:-}" = "local" ]; then
        local bcs_llm_env_count=0
        local bcs_llm_missing=()
        local bcs_llm_env_name
        for bcs_llm_env_name in \
            OPENCLAW_OPENAI_BASE_URL \
            OPENCLAW_OPENAI_API_KEY \
            OPENCLAW_OPENAI_MODEL_ID; do
            if [ -n "${!bcs_llm_env_name:-}" ]; then
                bcs_llm_env_count=$((bcs_llm_env_count + 1))
            else
                bcs_llm_missing+=("${bcs_llm_env_name}")
            fi
        done

        if [ "$bcs_llm_env_count" -eq 3 ]; then
            local escaped_bcs_llm_base_url
            local escaped_bcs_llm_model
            escaped_bcs_llm_base_url="$(toml_sed_replacement "${OPENCLAW_OPENAI_BASE_URL}")"
            escaped_bcs_llm_model="$(toml_sed_replacement "${OPENCLAW_OPENAI_MODEL_ID}")"
            sed_args+=(
                "-e" "/^\[llm\]/,/^\[/{s|^type = \".*\"$|type = \"openai_compatible\"|;s|^base_url = \".*\"$|base_url = \"${escaped_bcs_llm_base_url}\"|;s|^api_key_env = \".*\"$|api_key_env = \"OPENCLAW_OPENAI_API_KEY\"|;s|^model = \".*\"$|model = \"${escaped_bcs_llm_model}\"|;}"
            )
            log_info "Configuring the local BCS LLM judge from OPENCLAW_OPENAI_*"
        elif [ "$bcs_llm_env_count" -gt 0 ]; then
            log_warn "Ignoring incomplete OPENCLAW_OPENAI_* config for the local BCS LLM judge; missing: ${bcs_llm_missing[*]}"
        fi
    fi
    if [ "${HYBRID_CLAUDE_ACTIVE:-${MERCHANT_HYBRID_ACTIVE:-0}}" = "1" ]; then
        # The opt-in local Provider bridge listens on 127.0.0.1. Keep private
        # networks blocked while allowing this one local callback route.
        sed_args+=(
            "-e" "/^\[security.outbound_url\]/,/^\[/{s|^block_private_networks = .*|block_private_networks = true|;s|^allow_loopback = .*|allow_loopback = true|;}"
        )
        log_info "Allowing loopback-only BCS Provider callbacks for hybrid Claude mode"
    fi

    local config_file tmp_file
    for config_file in "$base_config" "$local_config"; do
        tmp_file="${config_file}.tmp"
        if ! sed "${sed_args[@]}" "$config_file" > "$tmp_file"; then
            rm -f "$tmp_file"
            return 1
        fi
        mv "$tmp_file" "$config_file" || return 1
    done

    if [ "${HYBRID_CLAUDE_ACTIVE:-${MERCHANT_HYBRID_ACTIVE:-0}}" = "1" ]; then
        # This checkout's already-built BCS binary predates the optional
        # OpenAPI-v1 config schema. Keep the tracked template untouched and
        # remove only that unsupported section from the generated local copy.
        # Without this, BCS falls back after parsing fails and misleadingly
        # reports that no config file was supplied.
        for config_file in "$base_config" "$local_config"; do
            tmp_file="${config_file}.tmp"
            if ! sed '/^\[openapi_v1\]/,/^\[/{ /^\[openapi_v1\]/d; /^\[/{ p; }; d; }' "$config_file" > "$tmp_file"; then
                rm -f "$tmp_file"
                return 1
            fi
            mv "$tmp_file" "$config_file" || return 1
        done
        log_info "Removed unsupported OpenAPI-v1 block from hybrid BCS runtime config"
    fi

    if [ -n "${SINGLEBOX_QWENWORK_PROVIDER_STATE:-}" ]; then
        for config_file in "$base_config" "$local_config"; do
            "${QWENWORK_PYTHON:-python3}" "${SCRIPT_DIR}/qwenwork_provider.py" config \
                --state "$SINGLEBOX_QWENWORK_PROVIDER_STATE" --config "$config_file" || return 1
        done
    fi
    prepare_bcs_runtime_config_resources "$config_dir" || return 1

    BCS_CONFIG_DIR="$config_dir"
    export BCS_CONFIG_DIR
    log_info "Prepared BCS runtime config from ${template} -> ${base_config}"
}
