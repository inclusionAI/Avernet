#!/usr/bin/env bash
# scripts/modules/model_config.sh — Singlebox OpenClaw model config resolver
[[ -n "${_MODEL_CONFIG_SH_LOADED:-}" ]] && return 0
_MODEL_CONFIG_SH_LOADED=1

singlebox_model_config_output_file() {
    printf '%s\n' "${SINGLEBOX_MODEL_CONFIG_FILE:-${DEP_DIR}/openclaw/model-config/openclaw.json}"
}

singlebox_mock_model_base_url() {
    printf 'http://127.0.0.1:%s\n' "${SINGLEBOX_MOCK_MODEL_PORT:-18080}"
}

singlebox_mock_model_pid_file() {
    printf '%s\n' "${DEP_DIR}/openclaw/model-config/mock-model.pid"
}

singlebox_mock_model_is_ready() {
    local base_url="${1:-$(singlebox_mock_model_base_url)}"
    curl --noproxy '*' -fsS "${base_url}/health" 2>/dev/null |
        jq -e '
            .status == "ok"
            and .service == "singlebox-mock-model"
            and (keys | sort) == ["service", "status"]
        ' >/dev/null
}

singlebox_mock_model_start() {
    SINGLEBOX_MOCK_MODEL_STARTED_BY_COMMAND=0
    [ "${SINGLEBOX_MODEL_CONFIG_MODE:-}" = "mock" ] || return 0

    local base_url pid_file log_file pid command
    base_url="$(singlebox_mock_model_base_url)"
    pid_file="$(singlebox_mock_model_pid_file)"
    log_file="${LOG_DIR}/mock-model.log"

    if singlebox_mock_model_is_ready "$base_url"; then
        log_info "Mock model server is ready: ${base_url}"
        return 0
    fi

    mkdir -p "$(dirname "$pid_file")" "$LOG_DIR"
    if [ -f "$pid_file" ]; then
        pid="$(tr -d '\r\n' < "$pid_file")"
        case "$pid" in
            ''|*[!0-9]*)
                rm -f "$pid_file"
                ;;
            *)
                if kill -0 "$pid" 2>/dev/null; then
                    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
                    case "$command" in
                        *"${PROJECT_ROOT}/scripts/modules/mock_model_server.py"*)
                            log_error "Owned mock model server PID ${pid} is running but is not ready at ${base_url}."
                            log_error "Run 'restart all' to apply a mode or port change."
                            ;;
                        *)
                            log_error "Mock model PID file points to another process: ${pid}."
                            ;;
                    esac
                    return 1
                fi
                rm -f "$pid_file"
                ;;
        esac
    fi

    python3 "${PROJECT_ROOT}/scripts/modules/mock_model_server.py" \
        --port "${SINGLEBOX_MOCK_MODEL_PORT:-18080}" >"$log_file" 2>&1 &
    pid=$!

    local attempt
    for attempt in $(seq 1 100); do
        if kill -0 "$pid" 2>/dev/null && singlebox_mock_model_is_ready "$base_url"; then
            printf '%s\n' "$pid" > "$pid_file"
            SINGLEBOX_MOCK_MODEL_STARTED_BY_COMMAND=1
            log_info "Mock model server started: ${base_url}"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 0.05
    done

    log_error "Mock model server failed to start; log: ${log_file}"
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
    return 1
}

singlebox_mock_model_stop() {
    local pid_file pid command attempt
    pid_file="$(singlebox_mock_model_pid_file)"
    [ -f "$pid_file" ] || return 0
    pid="$(tr -d '\r\n' < "$pid_file")"
    case "$pid" in
        ''|*[!0-9]*)
            rm -f "$pid_file"
            return 0
            ;;
    esac
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pid_file"
        return 0
    fi
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    case "$command" in
        *"${PROJECT_ROOT}/scripts/modules/mock_model_server.py"*) ;;
        *)
            log_warn "Refusing to stop PID ${pid}; it is not the singlebox mock model server."
            return 0
            ;;
    esac
    if ! kill "$pid" 2>/dev/null; then
        log_error "Failed to stop mock model server PID ${pid}; preserving ${pid_file}."
        return 1
    fi
    for attempt in $(seq 1 100); do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$pid_file"
            log_info "Mock model server stopped."
            return 0
        fi
        sleep 0.05
    done
    log_error "Mock model server PID ${pid} did not exit; preserving ${pid_file}."
    return 1
}

singlebox_model_config_required_for_services() {
    local service
    for service in "$@"; do
        case "$service" in
            all|baas|bots|bcs_bots|hybrid|merchant_hybrid)
                return 0
                ;;
        esac
    done
    return 1
}

singlebox_mock_model_stop_required_for_services() {
    local service
    for service in "$@"; do
        if [ "$service" = "all" ]; then
            return 0
        fi
    done
    return 1
}

singlebox_model_config_prompt() {
    local selection=""
    while true; do
        {
            echo "Choose model config mode:"
            echo "  1) mock     Use fixed-format local model replies"
            echo "  2) manual   Use values from .env.local"
            echo "  3) home     Import model fields from ~/.openclaw/openclaw.json"
            echo ""
            printf "Select [1-3]: "
        } >&2
        read -r selection || return 1
        case "$selection" in
            1|mock)
                printf '%s\n' "mock"
                return 0
                ;;
            2|manual)
                printf '%s\n' "manual"
                return 0
                ;;
            3|home)
                printf '%s\n' "home"
                return 0
                ;;
            *)
                echo "Invalid selection: ${selection}" >&2
                ;;
        esac
    done
}

singlebox_model_config_select_mode() {
    local mode="${SINGLEBOX_MODEL_CONFIG_MODE:-}"
    if [ -n "$mode" ]; then
        case "$mode" in
            mock|manual|home)
                printf '%s\n' "$mode"
                return 0
                ;;
            *)
                log_error "Invalid SINGLEBOX_MODEL_CONFIG_MODE: ${mode}"
                log_error "Valid values: mock, manual, home"
                return 1
                ;;
        esac
    fi

    if [ -t 0 ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then
        singlebox_model_config_prompt </dev/tty
        return
    fi

    log_warn "No SINGLEBOX_MODEL_CONFIG_MODE set and no interactive terminal detected; using mock mode." >&2
    printf '%s\n' "mock"
}

singlebox_model_config_require_manual_env() {
    local missing=()
    [ -n "${OPENCLAW_OPENAI_BASE_URL:-}" ] || missing+=("OPENCLAW_OPENAI_BASE_URL")
    [ -n "${OPENCLAW_OPENAI_API_KEY:-}" ] || missing+=("OPENCLAW_OPENAI_API_KEY")
    [ -n "${OPENCLAW_OPENAI_MODEL_ID:-}" ] || missing+=("OPENCLAW_OPENAI_MODEL_ID")

    if [ "${#missing[@]}" -gt 0 ]; then
        log_error "Missing required model config for manual mode:"
        local name
        for name in "${missing[@]}"; do
            log_error "  ${name}"
        done
        log_error "Set them in ${PROJECT_ROOT}/.env.local or choose SINGLEBOX_MODEL_CONFIG_MODE=mock."
        return 1
    fi
}

singlebox_model_config_thinking_enabled() {
    case "${OPENCLAW_ENABLE_THINKING:-false}" in
        1|true|TRUE|yes|YES|on|ON)
            printf 'true\n'
            ;;
        0|false|FALSE|no|NO|off|OFF|'')
            printf 'false\n'
            ;;
        *)
            log_error "Invalid OPENCLAW_ENABLE_THINKING: ${OPENCLAW_ENABLE_THINKING}"
            log_error "Valid values: true, false"
            return 1
            ;;
    esac
}

singlebox_model_config_apply_thinking_policy() {
    local output_file="$1"
    local thinking_enabled thinking_default temporary_file
    thinking_enabled="$(singlebox_model_config_thinking_enabled)" || return 1
    if [ "$thinking_enabled" = "true" ]; then
        thinking_default="medium"
    else
        thinking_default="off"
    fi
    temporary_file="${output_file}.thinking.$$"

    (
        umask 077
        jq \
            --argjson thinking_enabled "$thinking_enabled" \
            --arg thinking_default "$thinking_default" '
          def model_provider_id($model_ref):
            $model_ref | split("/")[0];
          def model_id($model_ref):
            $model_ref | split("/")[1:] | join("/");
          def needs_bailian_glm_thinking_override($config; $model_ref):
            ($config.models.providers[model_provider_id($model_ref)] // {}) as $provider
            | (($provider.api // "") == "openai-completions"
               or ($provider.api // "") == "openai")
              and (($provider.baseUrl // "" | ascii_downcase)
                   | contains("aliyuncs.com/compatible-mode/"))
              and ((model_id($model_ref) | ascii_downcase)
                   | test("(^|/)glm-(4\\.(5|6|7)|5([.-]|$))"));
          def set_bailian_glm_thinking_override:
            if type != "object" then {} else . end
            | .params = (if (.params? | type) == "object" then .params else {} end)
            | .params.extra_body = (
                if (.params.extra_body? | type) == "object" then .params.extra_body else {} end
              )
            | .params.extra_body.enable_thinking = $thinking_enabled;
          def toggle_thinking_object:
            if type != "object" then .
            else
              (if has("enable_thinking") then
                 .enable_thinking = $thinking_enabled
               else . end)
              | (if (.thinking? | type) == "object" then
                   if $thinking_enabled then
                     .thinking |= del(.type)
                     | if .thinking == {} then del(.thinking) else . end
                   else
                     .thinking.type = "disabled"
                   end
                 else . end)
            end;
          def toggle_model_params:
            if type != "object" then .
            else
              toggle_thinking_object
              | (if (.extra_body? | type) == "object" then
                   .extra_body |= toggle_thinking_object
                 else . end)
              | (if (.extraBody? | type) == "object" then
                   .extraBody |= toggle_thinking_object
                 else . end)
              | (if (.chat_template_kwargs? | type) == "object"
                       and (.chat_template_kwargs | has("enable_thinking")) then
                   .chat_template_kwargs.enable_thinking = $thinking_enabled
                 else . end)
              | (if (.chatTemplateKwargs? | type) == "object"
                       and (.chatTemplateKwargs | has("enable_thinking")) then
                   .chatTemplateKwargs.enable_thinking = $thinking_enabled
                 else . end)
            end;
          . as $source_config
          | (.agents.defaults.model.primary? // null) as $primary_model
          | .agents = (if (.agents? | type) == "object" then .agents else {} end)
          | .agents.defaults = (
              if (.agents.defaults? | type) == "object" then .agents.defaults else {} end
            )
          | .agents.defaults.thinkingDefault = $thinking_default
          | if (.agents.defaults.models? | type) == "object" then
              .agents.defaults.models |= with_entries(
                .value |= (
                  if type == "object" and (.params? | type) == "object" then
                    .params |= toggle_model_params
                  else . end
                )
              )
            else . end
          | if ($primary_model | type) == "string"
               and needs_bailian_glm_thinking_override($source_config; $primary_model) then
              .agents.defaults.models = (
                if (.agents.defaults.models? | type) == "object" then
                  .agents.defaults.models
                else
                  {}
                end
              )
              | .agents.defaults.models[$primary_model] = (
                  (.agents.defaults.models[$primary_model] // {})
                  | set_bailian_glm_thinking_override
                )
            else . end
          | if (.agents.defaults.models? | type) == "object" then
              .agents.defaults.models |= with_entries(
                .key as $model_ref
                | if needs_bailian_glm_thinking_override($source_config; $model_ref) then
                    .value |= set_bailian_glm_thinking_override
                  else . end
              )
            else . end
        ' "$output_file" > "$temporary_file"
    ) || {
        rm -f "$temporary_file"
        return 1
    }
    mv "$temporary_file" "$output_file"
    chmod 600 "$output_file"
}

singlebox_model_config_write_manual() {
    local output_file="$1"
    local provider_id="${OPENCLAW_OPENAI_PROVIDER_ID:-openai-compatible}"
    local model_id="${OPENCLAW_OPENAI_MODEL_ID}"
    local model_name="${OPENCLAW_OPENAI_MODEL_NAME:-$model_id}"
    local model_api="${OPENCLAW_OPENAI_MODEL_API:-openai-completions}"

    (
        umask 077
        jq -n \
            --arg provider_id "$provider_id" \
            --arg base_url "$OPENCLAW_OPENAI_BASE_URL" \
            --arg api_key "$OPENCLAW_OPENAI_API_KEY" \
            --arg model_id "$model_id" \
            --arg model_name "$model_name" \
            --arg model_api "$model_api" \
            '{
          models: {
            mode: "merge",
            providers: {
              ($provider_id): {
                baseUrl: $base_url,
                apiKey: $api_key,
                api: $model_api,
                models: [
                  {
                    id: $model_id,
                    name: $model_name,
                    reasoning: false,
                    input: ["text"],
                    cost: {
                      input: 0,
                      output: 0,
                      cacheRead: 0,
                      cacheWrite: 0
                    },
                    contextWindow: 200000,
                    maxTokens: 8192
                  }
                ]
              }
            }
          },
          agents: {
            defaults: {
              model: {
                primary: ($provider_id + "/" + $model_id)
              },
              models: {
                ($provider_id + "/" + $model_id): {
                  alias: $model_name
                }
              }
            }
          }
            }' > "$output_file"
    ) || return 1
    chmod 600 "$output_file"
}

singlebox_model_config_home_source() {
    printf '%s\n' "${OPENCLAW_MODEL_CONFIG_SOURCE:-${OPENCLAW_CONFIG_FILE:-$HOME/.openclaw/openclaw.json}}"
}

singlebox_model_config_confirm_home_import() {
    local source="$1"
    local confirmed="${SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED:-}"
    local answer=""

    case "$confirmed" in
        1|true|TRUE|yes|YES|y|Y)
            return 0
            ;;
        0|false|FALSE|no|NO|n|N)
            log_error "Home model config import was not confirmed."
            return 1
            ;;
    esac

    if [ -t 0 ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then
        {
            echo "Home mode will read model fields from:"
            echo "  ${source}"
            echo "This may include local model base URLs and API keys."
            printf "Continue? [y/N]: "
        } >&2
        read -r answer </dev/tty || return 1
        case "$answer" in
            Y|y)
                return 0
                ;;
            *)
                log_error "Home model config import cancelled."
                return 1
                ;;
        esac
    fi

    log_error "Home mode requires confirmation before reading ${source}."
    log_error "Run interactively and enter Y, or set SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED=1."
    return 1
}

singlebox_model_config_write_home() {
    local output_file="$1"
    local source
    source="$(singlebox_model_config_home_source)"

    singlebox_model_config_confirm_home_import "$source" || return 1

    if [ ! -f "$source" ]; then
        log_error "Home model config not found: ${source}"
        return 1
    fi
    if ! jq -e . "$source" >/dev/null 2>&1; then
        log_error "Home model config is not valid JSON: ${source}"
        return 1
    fi
    if ! jq -e '
      def agent_defaults:
        if (.agents? | type) == "object" and (.agents.defaults? | type) == "object"
        then .agents.defaults
        else {}
        end;
      (.models? != null)
      or (agent_defaults.model? != null)
      or (agent_defaults.models? != null)
      or (agent_defaults.imageModel? != null)
    ' "$source" >/dev/null; then
        log_error "Home model config has no model fields: ${source}"
        return 1
    fi

    (
        umask 077
        jq '
        def agent_defaults:
          if (.agents? | type) == "object" and (.agents.defaults? | type) == "object"
          then .agents.defaults
          else {}
          end;
        {
          models: (.models // {mode: "merge", providers: {}}),
          agents: {
            defaults: (
              {}
              + (if agent_defaults.model? != null then {model: agent_defaults.model} else {} end)
              + (if agent_defaults.models? != null then {models: agent_defaults.models} else {} end)
              + (if agent_defaults.imageModel? != null then {imageModel: agent_defaults.imageModel} else {} end)
            )
          }
        }' "$source" > "$output_file"
    ) || return 1
    chmod 600 "$output_file"
}

singlebox_model_config_write_mock() {
    local output_file="$1"
    local base_url
    base_url="$(singlebox_mock_model_base_url)/v1"
    (
        umask 077
        jq -n --arg base_url "$base_url" '{
          models: {
            mode: "merge",
            providers: {
              "singlebox-mock": {
                baseUrl: $base_url,
                apiKey: "singlebox-local",
                api: "openai-completions",
                models: [
                  {
                    id: "singlebox-mock",
                    name: "singlebox-mock",
                    reasoning: false,
                    input: ["text"],
                    cost: {
                      input: 0,
                      output: 0,
                      cacheRead: 0,
                      cacheWrite: 0
                    },
                    contextWindow: 200000,
                    maxTokens: 8192
                  }
                ]
              }
            }
          },
          agents: {
            defaults: {
              model: {
                primary: "singlebox-mock/singlebox-mock"
              },
              models: {
                "singlebox-mock/singlebox-mock": {
                  alias: "singlebox-mock"
                }
              }
            }
          }
        }' > "$output_file"
    ) || return 1
    chmod 600 "$output_file"
}

singlebox_model_config_export_llm_env() {
    # Export bcsfuse LLM env vars from the openclaw home config.
    # This lets bcsfuse reuse the same model/key that singlebox bots use in
    # home mode, without manually duplicating them in .env.local.
    local source_file primary provider_id model_name base_url api_key api_type
    source_file="$(singlebox_model_config_home_source)"

    if [ ! -f "$source_file" ]; then
        return 0
    fi
    if ! command -v jq >/dev/null 2>&1; then
        log_warn "jq not found; cannot export LLM env from ${source_file}"
        return 0
    fi

    primary="$(jq -r '.agents.defaults.model.primary // empty' "$source_file")"
    if [ -z "$primary" ]; then
        return 0
    fi

    provider_id="${primary%%/*}"
    model_name="${primary#*/}"

    base_url="$(jq -r --arg p "$provider_id" '.models.providers[$p].baseUrl // empty' "$source_file")"
    api_key="$(jq -r --arg p "$provider_id" '.models.providers[$p].apiKey // empty' "$source_file")"
    api_type="$(jq -r --arg p "$provider_id" '.models.providers[$p].api // "anthropic"' "$source_file")"

    if [ -z "$base_url" ] || [ -z "$api_key" ]; then
        return 0
    fi

    case "$api_type" in
        openai-completions|openai)
            api_type="openai"
            ;;
        *)
            api_type="anthropic"
            ;;
    esac

    # Respect explicit LLM_*_MODEL env vars; otherwise use the primary model
    # from the openclaw config so that endpoint, token, and model stay consistent.
    local preferred_model="$model_name"

    export LLM_BASE_URL="${LLM_BASE_URL:-$base_url}"
    export LLM_AUTH_TOKEN="${LLM_AUTH_TOKEN:-$api_key}"
    export LLM_API_TYPE="${LLM_API_TYPE:-$api_type}"
    export LLM_FAST_MODEL="${LLM_FAST_MODEL:-$preferred_model}"
    export LLM_BALANCED_MODEL="${LLM_BALANCED_MODEL:-$preferred_model}"
    export LLM_REASONING_MODEL="${LLM_REASONING_MODEL:-$preferred_model}"
    export LLM_LONG_CONTEXT_MODEL="${LLM_LONG_CONTEXT_MODEL:-$preferred_model}"
    export LLM_EXTRACTION_MODEL="${LLM_EXTRACTION_MODEL:-$preferred_model}"
    export LLM_DEFAULT_TIMEOUT_MS="${LLM_DEFAULT_TIMEOUT_MS:-120000}"
    export LLM_REASONING_TIMEOUT_MS="${LLM_REASONING_TIMEOUT_MS:-600000}"

    log_info "Exported bcsfuse LLM config from ${source_file} (provider=${provider_id}, api_type=${api_type}, model=${model_name})"
}

singlebox_model_config_export_manual_llm_env() {
    # When singlebox uses manual mode for the OpenClaw bot config, derive bcsfuse
    # LLM settings from the same OPENCLAW_OPENAI_* env vars. This mirrors the
    # home-mode export: base URL, token, and all model selectors are reused so
    # users do not have to duplicate them in .env.local.
    if [ "${SINGLEBOX_MODEL_CONFIG_MODE:-}" != "manual" ]; then
        return 0
    fi

    local base_url="${OPENCLAW_OPENAI_BASE_URL:-}"
    local api_key="${OPENCLAW_OPENAI_API_KEY:-}"
    local model_id="${OPENCLAW_OPENAI_MODEL_ID:-}"

    if [ -z "$base_url" ] || [ -z "$api_key" ] || [ -z "$model_id" ]; then
        return 0
    fi

    if { [ -z "${LLM_BASE_URL:-}" ] || [ "${LLM_BASE_URL}" = "change_me" ]; }; then
        export LLM_BASE_URL="$base_url"
    fi
    if { [ -z "${LLM_AUTH_TOKEN:-}" ] || [ "${LLM_AUTH_TOKEN}" = "change_me" ]; }; then
        export LLM_AUTH_TOKEN="$api_key"
    fi
    if { [ -z "${LLM_API_TYPE:-}" ] || [ "${LLM_API_TYPE}" = "change_me" ]; }; then
        export LLM_API_TYPE="openai"
    fi

    local preferred_model="$model_id"
    export LLM_FAST_MODEL="${LLM_FAST_MODEL:-$preferred_model}"
    export LLM_BALANCED_MODEL="${LLM_BALANCED_MODEL:-$preferred_model}"
    export LLM_REASONING_MODEL="${LLM_REASONING_MODEL:-$preferred_model}"
    export LLM_LONG_CONTEXT_MODEL="${LLM_LONG_CONTEXT_MODEL:-$preferred_model}"
    export LLM_EXTRACTION_MODEL="${LLM_EXTRACTION_MODEL:-$preferred_model}"
    export LLM_DEFAULT_TIMEOUT_MS="${LLM_DEFAULT_TIMEOUT_MS:-120000}"
    export LLM_REASONING_TIMEOUT_MS="${LLM_REASONING_TIMEOUT_MS:-600000}"

    log_info "Exported bcsfuse LLM config from manual env (api_type=openai, model=${model_id})"
}

singlebox_model_config_prepare() {
    local mode output_file output_dir
    if [ "${SINGLEBOX_MODEL_CONFIG_PREPARED:-}" = "1" ]; then
        return 0
    fi

    mode="$(singlebox_model_config_select_mode)" || return 1
    output_file="$(singlebox_model_config_output_file)"
    output_dir="$(dirname "$output_file")"
    mkdir -p "$output_dir"

    case "$mode" in
        manual)
            singlebox_model_config_require_manual_env || return 1
            singlebox_model_config_write_manual "$output_file" || return 1
            singlebox_model_config_export_manual_llm_env || true
            ;;
        home)
            singlebox_model_config_write_home "$output_file" || return 1
            singlebox_model_config_export_llm_env || true
            ;;
        mock)
            singlebox_model_config_write_mock "$output_file" || return 1
            log_warn "Singlebox model config mode is mock; bots use fixed-format local model replies."
            ;;
    esac
    singlebox_model_config_apply_thinking_policy "$output_file" || return 1

    SINGLEBOX_MODEL_CONFIG_MODE="$mode"
    SINGLEBOX_MODEL_CONFIG_FILE="$output_file"
    OPENCLAW_MODEL_CONFIG_SOURCE="$output_file"
    SINGLEBOX_MODEL_CONFIG_PREPARED=1
    export SINGLEBOX_MODEL_CONFIG_MODE SINGLEBOX_MODEL_CONFIG_FILE OPENCLAW_MODEL_CONFIG_SOURCE SINGLEBOX_MODEL_CONFIG_PREPARED

    log_info "Singlebox model config mode: ${mode}"
    log_info "Singlebox model config: ${output_file}"
    log_info "Singlebox model thinking default: $(jq -r '.agents.defaults.thinkingDefault' "$output_file")"
}
