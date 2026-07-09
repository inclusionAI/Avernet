#!/usr/bin/env bash
# scripts/modules/model_config.sh — Singlebox OpenClaw model config resolver
[[ -n "${_MODEL_CONFIG_SH_LOADED:-}" ]] && return 0
_MODEL_CONFIG_SH_LOADED=1

singlebox_model_config_output_file() {
    printf '%s\n' "${SINGLEBOX_MODEL_CONFIG_FILE:-${DEP_DIR}/openclaw/model-config/openclaw.json}"
}

singlebox_model_config_prompt() {
    local selection=""
    while true; do
        {
            echo "Choose model config mode:"
            echo "  1) mock     Start without real model replies"
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

    if [ -t 0 ] && [ -t 1 ]; then
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
}

singlebox_model_config_home_source() {
    printf '%s\n' "${OPENCLAW_MODEL_CONFIG_SOURCE:-${OPENCLAW_CONFIG_FILE:-$HOME/.openclaw/openclaw.json}}"
}

singlebox_model_config_write_home() {
    local output_file="$1"
    local source
    source="$(singlebox_model_config_home_source)"

    if [ ! -f "$source" ]; then
        log_error "Home model config not found: ${source}"
        return 1
    fi
    if ! jq -e . "$source" >/dev/null 2>&1; then
        log_error "Home model config is not valid JSON: ${source}"
        return 1
    fi
    if ! jq -e '(.models? != null) or (.agents.defaults.model? != null) or (.agents.defaults.models? != null) or (.agents.defaults.imageModel? != null)' "$source" >/dev/null; then
        log_error "Home model config has no model fields: ${source}"
        return 1
    fi

    jq '{
      models: (.models // {mode: "merge", providers: {}}),
      agents: {
        defaults: (
          {}
          + (if .agents.defaults.model? != null then {model: .agents.defaults.model} else {} end)
          + (if .agents.defaults.models? != null then {models: .agents.defaults.models} else {} end)
          + (if .agents.defaults.imageModel? != null then {imageModel: .agents.defaults.imageModel} else {} end)
        )
      }
    }' "$source" > "$output_file"
}

singlebox_model_config_write_mock() {
    local output_file="$1"
    jq -n '{
      models: {
        mode: "merge",
        providers: {}
      },
      agents: {
        defaults: {
          models: {}
        }
      }
    }' > "$output_file"
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
            log_warn "Singlebox model config mode is mock; bots can join BCN but cannot produce real model replies."
            ;;
    esac

    SINGLEBOX_MODEL_CONFIG_MODE="$mode"
    SINGLEBOX_MODEL_CONFIG_FILE="$output_file"
    OPENCLAW_MODEL_CONFIG_SOURCE="$output_file"
    SINGLEBOX_MODEL_CONFIG_PREPARED=1
    export SINGLEBOX_MODEL_CONFIG_MODE SINGLEBOX_MODEL_CONFIG_FILE OPENCLAW_MODEL_CONFIG_SOURCE SINGLEBOX_MODEL_CONFIG_PREPARED

    log_info "Singlebox model config mode: ${mode}"
    log_info "Singlebox model config: ${output_file}"
}
