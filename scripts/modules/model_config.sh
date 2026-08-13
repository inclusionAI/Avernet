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
            all|baas|bots|bcs_bots|merchant_hybrid)
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

singlebox_model_config_apply_model_override() {
    local output_file="$1"
    local model_id="${SINGLEBOX_MODEL_ID_OVERRIDE:-}"
    local provider_id="${SINGLEBOX_MODEL_PROVIDER_ID_OVERRIDE:-}"
    local max_tokens=8192

    [ -n "$model_id" ] || return 0
    if [[ "$model_id" == *$'\t'* || "$model_id" == *$'\r'* || "$model_id" == *$'\n'* ]]; then
        log_error "SINGLEBOX_MODEL_ID_OVERRIDE must be a single-line model ID."
        return 1
    fi
    if [[ "$provider_id" == *$'\t'* || "$provider_id" == *$'\r'* || "$provider_id" == *$'\n'* ]]; then
        log_error "SINGLEBOX_MODEL_PROVIDER_ID_OVERRIDE must be a single-line provider ID."
        return 1
    fi
    if [ "$model_id" = "Kimi-K2.6" ]; then
        # The merchant manager's first BCS dispatch may require more than the
        # generic 8192-token budget after loading its isolated task ledger.
        max_tokens=16384
    fi

    local tmp_file
    tmp_file="${output_file}.tmp.$$"
    if ! jq --arg requested_model "$model_id" --arg requested_provider "$provider_id" --argjson max_tokens "$max_tokens" '
      def default_provider:
        (.agents.defaults.model.primary? // "")
        | if type == "string" and contains("/") then split("/")[0] else "" end;
      ($requested_provider // "") as $requested_provider
      | (if $requested_provider != "" then $requested_provider else default_provider end) as $provider
      | if $provider == "" or (.models.providers[$provider] | type) != "object" then
          error("model override provider is missing from the imported runtime model config")
        else . end
      | .models.providers[$provider].models = (.models.providers[$provider].models // [])
      | if (.models.providers[$provider].models | any(.id == $requested_model)) then .
        else .models.providers[$provider].models += [{
          id: $requested_model,
          name: $requested_model,
          reasoning: false,
          input: ["text"],
          cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0},
          contextWindow: 200000,
          maxTokens: $max_tokens
        }] end
      | .models.providers[$provider].models |= map(
          if .id == $requested_model then .maxTokens = $max_tokens else . end
        )
      | .agents.defaults.model.primary = ($provider + "/" + $requested_model)
      | .agents.defaults.models = ((.agents.defaults.models // {}) + {($provider + "/" + $requested_model): {}})
    ' "$output_file" > "$tmp_file"; then
        rm -f "$tmp_file"
        log_error "Failed to apply singlebox model override."
        return 1
    fi
    mv "$tmp_file" "$output_file"
    chmod 600 "$output_file"
    log_info "Singlebox runtime Bot model override: ${provider_id:-imported-default-provider}/${model_id}"
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
            ;;
        home)
            singlebox_model_config_write_home "$output_file" || return 1
            ;;
        mock)
            singlebox_model_config_write_mock "$output_file" || return 1
            log_warn "Singlebox model config mode is mock; bots use fixed-format local model replies."
            ;;
    esac

    singlebox_model_config_apply_model_override "$output_file" || return 1

    SINGLEBOX_MODEL_CONFIG_MODE="$mode"
    SINGLEBOX_MODEL_CONFIG_FILE="$output_file"
    OPENCLAW_MODEL_CONFIG_SOURCE="$output_file"
    SINGLEBOX_MODEL_CONFIG_PREPARED=1
    export SINGLEBOX_MODEL_CONFIG_MODE SINGLEBOX_MODEL_CONFIG_FILE OPENCLAW_MODEL_CONFIG_SOURCE SINGLEBOX_MODEL_CONFIG_PREPARED

    log_info "Singlebox model config mode: ${mode}"
    log_info "Singlebox model config: ${output_file}"
}
