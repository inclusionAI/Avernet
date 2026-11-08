#!/usr/bin/env bash
# singlebox/modules/demo_bot.sh — Default singlebox backend-created demo bot
[[ -n "${_DEMO_BOT_SH_LOADED:-}" ]] && return 0
_DEMO_BOT_SH_LOADED=1

DEMO_BOT_LOG="${LOG_DIR}/demo_bot.log"

demo_bot_defaults() {
    DEMO_BOT_NAME="${SINGLEBOX_DEMO_BOT_NAME:-developer}"
    DEMO_BOT_DESC="${SINGLEBOX_DEMO_BOT_DESC:-Local demo bot for OpenOCB Singlebox}"
    DEMO_ENTITY_ID="${SINGLEBOX_DEMO_ENTITY_ID:-mock-user}"
    DEMO_ENTITY_TYPE="${SINGLEBOX_DEMO_ENTITY_TYPE:-staff}"
    DEMO_ENGINE_TYPE="${SINGLEBOX_DEMO_ENGINE_TYPE:-openclaw}"
    DEMO_BOT_TYPE="${SINGLEBOX_DEMO_BOT_TYPE:-personal}"
    DEMO_TEMPLATE_TYPE="${SINGLEBOX_DEMO_TEMPLATE_TYPE:-normalCC}"
    DEMO_BACKEND_PORT="${BACKEND_PORT:-8888}"
    DEMO_GATEWAY_PORT="${GATEWAY_PORT:-8889}"
    DEMO_BOT_READY_TIMEOUT_SECONDS="${SINGLEBOX_DEMO_BOT_READY_TIMEOUT_SECONDS:-120}"
    DEMO_BOT_READY_POLL_INTERVAL_SECONDS="${SINGLEBOX_DEMO_BOT_READY_POLL_INTERVAL_SECONDS:-2}"
    DEMO_BBS_SUBSCRIPTION_TIMEOUT_SECONDS="${SINGLEBOX_BBS_SUBSCRIPTION_TIMEOUT_SECONDS:-30}"
    DEMO_BBS_SUBSCRIPTION_POLL_INTERVAL_SECONDS="${SINGLEBOX_BBS_SUBSCRIPTION_POLL_INTERVAL_SECONDS:-1}"

    DEMO_BBS_SKILL_ROOT="${SINGLEBOX_BBS_SKILL_ROOT:-${PROJECT_ROOT}/singlebox/skills/infra}"
    DEMO_BBS_SKILL_NAMES=(
        "bbs-browse"
        "bbs-topic-handler"
        "bbs-create-topic"
    )

    DEMO_BBS_BOT_NAMES=(
        "社区产品官"
        "社区工程官"
    )
    DEMO_BBS_BOT_DESCRIPTIONS=(
        "从用户目标、使用场景、需求边界、验收标准和最小可用范围评估社区主题"
        "从实现可行性、技术风险、系统边界、验证方式和最小实现路径评估社区主题"
    )

    DEMO_BBS_TOPIC_CLIENT_REQUEST_IDS=(
        "singlebox-bbs-demo-topic-01"
        "singlebox-bbs-demo-topic-02"
        "singlebox-bbs-demo-topic-03"
        "singlebox-bbs-demo-topic-04"
    )
    DEMO_BBS_TOPIC_TITLES=(
        "BBS 要不要开放「@ 指定 Bot 参与讨论」？"
        "社区首页要不要默认提供「新手指引」？"
        "主题发布后是否允许作者编辑正文？"
        "开启周期巡检后，要不要显示「Bot 正在查看」？"
    )
    DEMO_BBS_TOPIC_BODIES=(
        "BBS 目前默认按 Bot 自己的巡检范围读取社区。如果允许 Human 在主题中 @ 指定某个 Bot，可能会更容易得到定向回答，也可能让讨论失去多视角碰撞。请分别从产品和工程角度回答：1 这个能力值得开放吗？结论是什么？2 第一版应该限定到什么范围？3 哪一个风险必须先治理？欢迎给出结论、理由和一个可执行建议。"
        "第一次进入社区的人通常关心三件事：怎么发布主题、Bot 多久会回应、什么时候可以结帖。如果首页固定展示新手指引，可能降低上手门槛，也可能占据主内容空间。请分别从产品和工程角度回答：1 新手指引应该默认展示，还是第一次进入后不再展示？2 指引里哪三句话最有价值？3 第一版用最简单、不侵入主流程的方式该怎么实现？欢迎给出结论、理由和一个可执行建议。"
        "开放正文编辑可以减少表达误差；但 Bot 回复过后再修改正文，可能让已有楼层显得答非所问。请分别从产品和工程角度回答：1 编辑能力值得第一版开放吗？2 编辑时间窗口应该怎么定？3 编辑后如何保证楼层上下文和通知不误导？欢迎给出结论、理由和一个可执行建议。"
        "开启周期巡检后，Bot 最晚 30 分钟才会读取社区。没有状态提示时，发布者可能以为系统没有工作；显示状态提示太早，又可能像虚假承诺。请分别从产品和工程角度回答：1 等待提示是必要能力还是第一版可以不做？2 哪种提示文案最诚实？3 状态应该由哪个服务事件触发和清除？欢迎给出结论、理由和一个可执行建议。"
    )
}

demo_bot_backend_base_url() {
    demo_bot_defaults
    printf 'http://127.0.0.1:%s\n' "${DEMO_BACKEND_PORT}"
}

demo_bot_bcs_base_url() {
    printf 'http://127.0.0.1:%s\n' "${BCS_PORT}"
}

demo_bot_bcs_bot_id() {
    local backend_bot_id="$1"
    demo_bot_defaults
    printf '%s:%s\n' "$backend_bot_id" "$DEMO_ENTITY_ID"
}

demo_bot_json_payload() {
    demo_bot_defaults
    jq -n \
        --arg bot_name "$DEMO_BOT_NAME" \
        --arg bot_desc "$DEMO_BOT_DESC" \
        --arg entity_id "$DEMO_ENTITY_ID" \
        --arg entity_type "$DEMO_ENTITY_TYPE" \
        --arg engine_type "$DEMO_ENGINE_TYPE" \
        --arg bot_type "$DEMO_BOT_TYPE" \
        --arg template_type "$DEMO_TEMPLATE_TYPE" \
        '{
            bot_name: $bot_name,
            bot_desc: $bot_desc,
            entity_id: $entity_id,
            entity_type: $entity_type,
            engine_type: $engine_type,
            bot_type: $bot_type,
            template_type: $template_type
        }'
}


# ---------------------------------------------------------------------------
# BBS demo initialization: Topic + two role-specific Bot subscriptions
# ---------------------------------------------------------------------------

bbs_demo_array_count() {
    local array_name="$1"
    local value
    value="$(eval "printf '%s\\n' \"\${#${array_name}[@]}\"" 2>/dev/null || true)"
    case "$value" in
        ''|*[!0-9]*) return 1 ;;
        *) printf '%s\n' "$value" ;;
    esac
}

demo_bbs_validate_definitions() {
    local bot_count topic_count
    bot_count="$(bbs_demo_array_count DEMO_BBS_BOT_NAMES)" || return 1
    topic_count="$(bbs_demo_array_count DEMO_BBS_TOPIC_TITLES)" || return 1
    [ "$bot_count" -ge 2 ] || {
        log_error "BBS demo requires at least two role-specific bots"
        return 1
    }
    [ "$bot_count" -eq "$(bbs_demo_array_count DEMO_BBS_BOT_DESCRIPTIONS)" ] || {
        log_error "BBS demo bot names and descriptions must be paired"
        return 1
    }
    [ "$topic_count" -gt 0 ] || {
        log_error "BBS demo requires at least one initial topic"
        return 1
    }
    [ "$topic_count" -eq "$(bbs_demo_array_count DEMO_BBS_TOPIC_CLIENT_REQUEST_IDS)" ] || {
        log_error "BBS demo topic ids and titles must be paired"
        return 1
    }
    [ "$topic_count" -eq "$(bbs_demo_array_count DEMO_BBS_TOPIC_BODIES)" ] || {
        log_error "BBS demo topic titles and bodies must be paired"
        return 1
    }
    [ "${#DEMO_BBS_SKILL_NAMES[@]}" -eq 3 ] || {
        log_error "BBS demo requires exactly the three local BBS skills"
        return 1
    }
    local skill_name skill_file
    for skill_name in "${DEMO_BBS_SKILL_NAMES[@]}"; do
        skill_file="${DEMO_BBS_SKILL_ROOT}/${skill_name}/SKILL.md"
        if [ ! -f "$skill_file" ]; then
            log_error "BBS demo skill manifest is missing: $skill_file"
            return 1
        fi
    done
}

demo_bbs_find_existing() {
    local bot_name="$1"
    local base_url response
    base_url="$(demo_bot_backend_base_url)"
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
            -H "x-user-id: ${DEMO_ENTITY_ID}" \
            "${base_url}/api/bots?user_id=${DEMO_ENTITY_ID}&entity_id=${DEMO_ENTITY_ID}&entity_type=${DEMO_ENTITY_TYPE}&page=1&page_size=100" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    [ -n "$response" ] || return 0

    printf '%s\n' "$response" | jq -r \
        --arg name "$bot_name" \
        --arg entity_id "$DEMO_ENTITY_ID" \
        '
            (.data.bots // .data.items // .data.list // .data.records // .data // [])
            | if type == "array" then . else [] end
            | map(select((.bot_name == $name) and (.entity_id == $entity_id) and ((.is_delete // 0) == 0)))
            | .[0].bot_id // empty
        ' 2>/dev/null || true
}

demo_bbs_create() {
    local bot_name="$1"
    local bot_desc="$2"
    local base_url payload response
    base_url="$(demo_bot_backend_base_url)"
    payload="$(
        jq -n \
            --arg bot_name "$bot_name" \
            --arg bot_desc "$bot_desc" \
            --arg entity_id "$DEMO_ENTITY_ID" \
            --arg entity_type "$DEMO_ENTITY_TYPE" \
            --arg engine_type "$DEMO_ENGINE_TYPE" \
            --arg bot_type "$DEMO_BOT_TYPE" \
            --arg template_type "$DEMO_TEMPLATE_TYPE" \
            '{
                bot_name: $bot_name,
                bot_desc: $bot_desc,
                entity_id: $entity_id,
                entity_type: $entity_type,
                engine_type: $engine_type,
                bot_type: $bot_type,
                template_type: $template_type
            }'
    )"
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 60 -fsS \
            -X POST "${base_url}/api/bots?user_id=${DEMO_ENTITY_ID}" \
            -H "Content-Type: application/json" \
            -H "x-user-id: ${DEMO_ENTITY_ID}" \
            -d "$payload" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
    printf '%s\n' "$response" | jq -r '.data.bot_id // .data.bot.bot_id // .bot_id // empty' 2>/dev/null || true
}

demo_bbs_skill_package() {
    local skill_name="$1"
    local skill_dir package_file path
    local entries=()

    skill_dir="${DEMO_BBS_SKILL_ROOT}/${skill_name}"
    [ -f "${skill_dir}/SKILL.md" ] || {
        log_error "BBS demo skill manifest is missing: ${skill_dir}/SKILL.md"
        return 1
    }
    for path in SKILL.md agents references scripts; do
        [ -e "${skill_dir}/${path}" ] && entries+=("$path")
    done
    package_file="$(mktemp -d "${TMPDIR:-/tmp}/bbs-skill-${skill_name}.XXXXXX")/${skill_name}.zip" || return 1
    if ! (cd "$skill_dir" && zip -qr "$package_file" "${entries[@]}"); then
        rm -rf "$(dirname "$package_file")"
        log_error "Failed to package BBS demo skill: ${skill_name}"
        return 1
    fi
    printf '%s\n' "$package_file"
}

demo_bbs_skill_upload() {
    local backend_bot_id="$1"
    local skill_name="$2"
    local base_url package_file response skill_id

    base_url="$(demo_bot_gateway_base_url)"
    package_file="$(demo_bbs_skill_package "$skill_name")" || return 1
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 30 -sS             -X POST             "${base_url}/openapi/v1/bots/${backend_bot_id}/skills?user_id=${DEMO_ENTITY_ID}&owner_id=${DEMO_ENTITY_ID}"             -H "x-dev-user: ${DEMO_ENTITY_ID}"             -H "Content-Type: application/zip"             --data-binary "@${package_file}"             2>>"${DEMO_BOT_LOG}" || true
    )"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
    skill_id="$(printf '%s\n' "$response" | jq -r         --arg name "$skill_name"         '.data.skill.skill_id // empty
        | select(length > 0)' 2>/dev/null || true)"
    if ! printf '%s\n' "$response" | jq -e         --arg name "$skill_name"         --arg skill_id "$skill_id"         '
            (.code == 200000 or .code == 201000)
            and .data.skill.name == $name
            and .data.skill.skill_id == $skill_id
        ' >/dev/null 2>&1; then
        rm -rf "$(dirname "$package_file")"
        log_error "Failed to upload BBS demo skill ${skill_name} for ${backend_bot_id}. Check ${DEMO_BOT_LOG}"
        return 1
    fi
    rm -rf "$(dirname "$package_file")"
    printf '%s\n' "$skill_id"
}

demo_bbs_skill_activate() {
    local backend_bot_id="$1"
    local skill_id="$2"
    local skill_name="$3"
    local base_url response

    base_url="$(demo_bot_gateway_base_url)"
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 30 -sS             -X POST             "${base_url}/openapi/v1/bots/${backend_bot_id}/skills/${skill_id}/activate?user_id=${DEMO_ENTITY_ID}&owner_id=${DEMO_ENTITY_ID}"             -H "x-dev-user: ${DEMO_ENTITY_ID}"             2>>"${DEMO_BOT_LOG}" || true
    )"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
    if ! printf '%s\n' "$response" | jq -e         --arg name "$skill_name"         '
            .code == 200000
            and .data.skill.name == $name
            and .data.skill.active == true
            and .data.runtime_projection.status == "CONVERGED"
            and .data.runtime_projection.pending_count == 0
        ' >/dev/null 2>&1; then
        log_error "Failed to activate BBS demo skill ${skill_name} for ${backend_bot_id}. Check ${DEMO_BOT_LOG}"
        return 1
    fi
}

demo_bot_gateway_base_url() {
    demo_bot_defaults
    printf 'http://127.0.0.1:%s\n' "${DEMO_GATEWAY_PORT}"
}

demo_bbs_gateway_ready() {
    local base_url
    base_url="$(demo_bot_gateway_base_url)"
    curl --noproxy '*' --connect-timeout 1 --max-time 2 -fsS \
        "${base_url}/health" >/dev/null 2>&1
}

demo_bbs_require_gateway() {
    local base_url
    base_url="$(demo_bot_gateway_base_url)"
    if ! demo_bbs_gateway_ready; then
        log_error "Gateway is not ready at ${base_url}. BBS demo skills are uploaded through Gateway; start all or gateway first."
        return 1
    fi
}

demo_bbs_skills_install() {
    local backend_bot_id="$1"
    local skill_name skill_id

    for skill_name in "${DEMO_BBS_SKILL_NAMES[@]}"; do
        skill_id="$(demo_bbs_skill_upload "$backend_bot_id" "$skill_name")" || {
            log_error "BBS demo start failed while uploading skill ${skill_name}"
            return 1
        }
        if ! demo_bbs_skill_activate "$backend_bot_id" "$skill_id" "$skill_name"; then
            return 1
        fi
    done
    log_info "BBS demo skills activated: ${#DEMO_BBS_SKILL_NAMES[@]}"
}

demo_bbs_topic_payload() {
    local client_request_id="$1"
    local title="$2"
    local body="$3"
    jq -n \
        --arg author_id "$DEMO_ENTITY_ID" \
        --arg client_request_id "$client_request_id" \
        --arg title "$title" \
        --arg body "$body" \
        '{
            author_type: "HUMAN",
            author_id: $author_id,
            author_display_name: "DemoUser",
            client_request_id: $client_request_id,
            title: $title,
            body: $body
        }'
}

demo_bbs_topics_seed() {
    local base_url index payload response topic_id
    base_url="$(demo_bot_backend_base_url)"

    for index in "${!DEMO_BBS_TOPIC_TITLES[@]}"; do
        payload="$(
            demo_bbs_topic_payload \
                "${DEMO_BBS_TOPIC_CLIENT_REQUEST_IDS[$index]}" \
                "${DEMO_BBS_TOPIC_TITLES[$index]}" \
                "${DEMO_BBS_TOPIC_BODIES[$index]}"
        )"
        response="$(
            curl --noproxy '*' --connect-timeout 2 --max-time 15 -fsS \
                -X POST "${base_url}/api/v1/collaboration/tasks/bbs/topics" \
                -H "Content-Type: application/json" \
                -d "$payload" \
                2>>"${DEMO_BOT_LOG}" || true
        )"
        printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
        topic_id="$(printf '%s\n' "$response" | jq -r '.data.topic_id // empty' 2>/dev/null || true)"
        if [ -z "$topic_id" ]; then
            log_error "Failed to initialize BBS demo topic: ${DEMO_BBS_TOPIC_TITLES[$index]}. Check ${DEMO_BOT_LOG}"
            return 1
        fi
    done

    log_info "BBS demo topics initialized: ${#DEMO_BBS_TOPIC_TITLES[@]}"
}

demo_bbs_subscription_payload() {
    local note="$1"
    jq -n --arg note "$note" '{note: $note}'
}

demo_bbs_subscription_persisted() {
    local backend_bot_id="$1"
    local base_url response

    base_url="$(demo_bot_backend_base_url)"
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
            -X GET \
            -H "x-user-id: ${DEMO_ENTITY_ID}" \
            "${base_url}/api/v1/collaboration/tasks/bots/${backend_bot_id}/bbs/browse-subscription" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    [ -n "$response" ] || return 1
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
    printf '%s\n' "$response" | jq -e \
        --arg bot_id "$backend_bot_id" \
        --arg mode "openclaw" \
        '.code == 200000
            and (.data.bot_id == $bot_id)
            and (.data.mode == $mode)' >/dev/null 2>&1
}

demo_bbs_subscription_wait_persisted() {
    local backend_bot_id="$1"
    local started_at elapsed

    started_at=$SECONDS
    while true; do
        if demo_bbs_subscription_persisted "$backend_bot_id"; then
            return 0
        fi
        elapsed=$((SECONDS - started_at))
        if [ "$elapsed" -ge "$DEMO_BBS_SUBSCRIPTION_TIMEOUT_SECONDS" ]; then
            log_error "BBS Browse-Loop did not persist for ${backend_bot_id} within ${DEMO_BBS_SUBSCRIPTION_TIMEOUT_SECONDS}s. Check ${DEMO_BOT_LOG}"
            return 1
        fi
        sleep "$DEMO_BBS_SUBSCRIPTION_POLL_INTERVAL_SECONDS"
    done
}

demo_bbs_subscription_enable() {
    local backend_bot_id="$1"
    local note="$2"
    local base_url payload response returned_bot_id mode
    base_url="$(demo_bot_backend_base_url)"
    payload="$(demo_bbs_subscription_payload "$note")"
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 30 -fsS \
            -X POST "${base_url}/api/v1/collaboration/tasks/bots/${backend_bot_id}/bbs/browse-subscription?owner_user_id=${DEMO_ENTITY_ID}" \
            -H "Content-Type: application/json" \
            -H "x-user-id: ${DEMO_ENTITY_ID}" \
            -d "$payload" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"

    if ! printf '%s\n' "$response" | jq -e \
        --arg bot_id "$backend_bot_id" \
        --arg mode "openclaw" \
        '(.code == 200000 or .code == 201000 or .code == 202000)
            and (.data.bot_id == $bot_id)
            and (.data.mode == $mode)' \
        >/dev/null 2>&1; then
        log_error "BBS Browse-Loop did not initialize for ${backend_bot_id}. Check ${DEMO_BOT_LOG}"
        return 1
    fi

    if printf '%s\n' "$response" | jq -e '.code == 202000' >/dev/null 2>&1; then
        demo_bbs_subscription_wait_persisted "$backend_bot_id"
    fi
}

demo_bot_find_existing() {
    demo_bot_defaults
    local base_url response
    base_url="$(demo_bot_backend_base_url)"
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
            -H "x-user-id: ${DEMO_ENTITY_ID}" \
            "${base_url}/api/bots?user_id=${DEMO_ENTITY_ID}&entity_id=${DEMO_ENTITY_ID}&entity_type=${DEMO_ENTITY_TYPE}&page=1&page_size=100" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    [ -n "$response" ] || return 0

    printf '%s\n' "$response" | jq -r \
        --arg name "$DEMO_BOT_NAME" \
        --arg entity_id "$DEMO_ENTITY_ID" \
        '
            (.data.bots // .data.items // .data.list // .data.records // .data // [])
            | if type == "array" then . else [] end
            | map(select((.bot_name == $name) and (.entity_id == $entity_id) and ((.is_delete // 0) == 0)))
            | .[0].bot_id // empty
        ' 2>/dev/null || true
}

demo_bot_create() {
    demo_bot_defaults
    local base_url payload response
    base_url="$(demo_bot_backend_base_url)"
    payload="$(demo_bot_json_payload)"
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 60 -fsS \
            -X POST "${base_url}/api/bots?user_id=${DEMO_ENTITY_ID}" \
            -H "Content-Type: application/json" \
            -H "x-user-id: ${DEMO_ENTITY_ID}" \
            -d "$payload" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
    printf '%s\n' "$response" | jq -r '.data.bot_id // .data.bot.bot_id // .bot_id // empty' 2>/dev/null || true
}

demo_bot_wait_ready() {
    local backend_bot_id="$1"
    local base_url response bot_status error_message started_at elapsed
    demo_bot_defaults
    base_url="$(demo_bot_backend_base_url)"
    started_at=$SECONDS

    while true; do
        response="$(
            curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
                -H "x-user-id: ${DEMO_ENTITY_ID}" \
                "${base_url}/api/bots/${backend_bot_id}/status?owner_id=${DEMO_ENTITY_ID}" \
                2>>"${DEMO_BOT_LOG}" || true
        )"

        if [ -n "$response" ]; then
            printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
            if printf '%s\n' "$response" | jq -e '.success == true and .data.is_ready == true' >/dev/null 2>&1; then
                return 0
            fi

            bot_status="$(printf '%s\n' "$response" | jq -r '.data.bot_status // empty' 2>/dev/null || true)"
            error_message="$(printf '%s\n' "$response" | jq -r '.data.error_message // empty' 2>/dev/null || true)"
            if [ "$bot_status" = "FAILED" ] || [ -n "$error_message" ]; then
                log_error "Demo bot backend status failed: bot_id=${backend_bot_id}, status=${bot_status:-unknown}, error=${error_message:-none}"
                return 1
            fi
        fi

        elapsed=$((SECONDS - started_at))
        if [ "$elapsed" -ge "$DEMO_BOT_READY_TIMEOUT_SECONDS" ]; then
            log_error "Timed out waiting for demo bot backend readiness: bot_id=${backend_bot_id}"
            return 1
        fi
        sleep "$DEMO_BOT_READY_POLL_INTERVAL_SECONDS"
    done
}

demo_bot_connect_bcs() {
    local backend_bot_id="$1"
    local bcs_bot_id base_url payload response
    bcs_bot_id="$(demo_bot_bcs_bot_id "$backend_bot_id")"
    base_url="$(demo_bot_bcs_base_url)"
    payload="$(jq -n --arg bot_id "$bcs_bot_id" '{bot_id: $bot_id, protocol_version: 2}')"

    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
            -X POST "${base_url}/bots/connect" \
            -H "Content-Type: application/json" \
            -d "$payload" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"

    if printf '%s\n' "$response" | jq -e --arg bot_id "$bcs_bot_id" '.bot_uuid == $bot_id' >/dev/null 2>&1; then
        return 0
    fi
    if printf '%s\n' "$response" | grep -qi 'already'; then
        return 0
    fi
    return 1
}

demo_bot_admin_onboard_bcs() {
    local backend_bot_id="$1"
    local bcs_bot_id base_url payload response
    bcs_bot_id="$(demo_bot_bcs_bot_id "$backend_bot_id")"
    base_url="$(demo_bot_bcs_base_url)"
    payload="$(
        jq -n \
            --arg bot_id "$bcs_bot_id" \
            --arg name "$DEMO_BOT_NAME" \
            --arg summary "$DEMO_BOT_DESC" \
            '{bot_id: $bot_id, name: $name, summary: $summary, hidden: true}'
    )"

    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
            -X POST "${base_url}/admin/bots/onboard" \
            -H "Content-Type: application/json" \
            -d "$payload" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
    printf '%s\n' "$response" | jq -e '.onboarded == true' >/dev/null 2>&1
}

# Return 0 when the BCS entry is exact, 1 when it is missing or requires
# product-side onboarding, and 2 for fatal transport, authentication, or
# contract errors.
demo_bot_verify_bcn() {
    local backend_bot_id="$1"
    local bcs_bot_id base_url response_file response http_status curl_rc actual_bot_id actual_name actual_summary
    bcs_bot_id="$(demo_bot_bcs_bot_id "$backend_bot_id")"
    base_url="$(demo_bot_bcs_base_url)"
    response_file="$(mktemp)" || {
        log_error "Failed to allocate a temporary file for demo bot verification"
        return 2
    }

    curl_rc=0
    http_status="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 10 -sS \
            -o "$response_file" \
            -w '%{http_code}' \
            -H "X-Mock-User-Id: ${BCS_MOCK_USER_ID:-001}" \
            -H "X-Mock-Nick-Name: ${BCS_MOCK_USER_NICK_NAME:-admin}" \
            "${base_url}/bots/${bcs_bot_id}" \
            2>>"${DEMO_BOT_LOG}"
    )" || curl_rc=$?
    response="$(<"$response_file")"
    rm -f "$response_file"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"

    if [ "$curl_rc" -ne 0 ]; then
        log_error "Failed to query demo bot from local BCS: curl exit ${curl_rc}. Check ${DEMO_BOT_LOG}"
        return 2
    fi

    case "$http_status" in
        200) ;;
        404)
            return 1
            ;;
        401|403)
            log_error "BCS rejected demo bot verification with HTTP ${http_status}; singlebox local mock human authentication must be enabled"
            return 2
            ;;
        *)
            log_error "Unexpected HTTP ${http_status:-unknown} while verifying demo bot in local BCS"
            return 2
            ;;
    esac

    if ! printf '%s\n' "$response" | jq -e 'type == "object"' >/dev/null 2>&1; then
        log_error "BCS returned invalid JSON while verifying demo bot: ${bcs_bot_id}"
        return 2
    fi

    actual_bot_id="$(printf '%s\n' "$response" | jq -r '.bot_uuid // empty')"
    if [ "$actual_bot_id" != "$bcs_bot_id" ]; then
        log_error "BCS demo bot identity mismatch: expected=${bcs_bot_id}, actual=${actual_bot_id:-missing}"
        return 2
    fi

    if printf '%s\n' "$response" | jq -e \
        --arg name "$DEMO_BOT_NAME" \
        --arg summary "$DEMO_BOT_DESC" \
        '.capabilities.name == $name and .capabilities.summary == $summary' \
        >/dev/null 2>&1; then
        return 0
    fi

    actual_name="$(printf '%s\n' "$response" | jq -r '.capabilities.name // empty')"
    actual_summary="$(printf '%s\n' "$response" | jq -r '.capabilities.summary // empty')"
    log_warn "BCS demo bot metadata needs repair: name=${actual_name:-missing}, summary=${actual_summary:-missing}"
    return 1
}

demo_bot_ensure_bcn() {
    local backend_bot_id="$1"
    local bcs_bot_id verify_rc=0
    bcs_bot_id="$(demo_bot_bcs_bot_id "$backend_bot_id")"

    demo_bot_verify_bcn "$backend_bot_id" || verify_rc=$?
    if [ "$verify_rc" -eq 0 ]; then
        return 0
    fi
    if [ "$verify_rc" -ne 1 ]; then
        return "$verify_rc"
    fi

    log_warn "Demo bot is not onboarded in BCS yet: ${bcs_bot_id}. Use the product Onboard action after the BCN plugin connects."
    return 1
}

demo_bot_start() {
    mkdir -p "${LOG_DIR}"
    : > "${DEMO_BOT_LOG}"
    demo_bot_defaults
    demo_bbs_validate_definitions || return 1

    backend_ready || {
        log_error "Backend is not ready; cannot initialize the BBS demo"
        return 1
    }
    bcs_ready || {
        log_error "BCS is not ready; cannot finish singlebox startup"
        return 1
    }
    demo_bbs_require_gateway || return 1

    demo_bbs_topics_seed || return 1

    DEMO_BBS_BOT_IDS=()
    local index backend_bot_id bot_name bot_desc
    for index in "${!DEMO_BBS_BOT_NAMES[@]}"; do
        bot_name="${DEMO_BBS_BOT_NAMES[$index]}"
        bot_desc="${DEMO_BBS_BOT_DESCRIPTIONS[$index]}"
        backend_bot_id="$(demo_bbs_find_existing "$bot_name")"
        if [ -n "$backend_bot_id" ]; then
            log_info "Reusing existing BBS demo bot: ${bot_name} (${backend_bot_id})"
        else
            log_info "Creating BBS demo bot through backend: ${bot_name}"
            backend_bot_id="$(demo_bbs_create "$bot_name" "$bot_desc")"
        fi

        if [ -z "$backend_bot_id" ]; then
            log_error "Failed to create or locate BBS demo bot: ${bot_name}. Check ${DEMO_BOT_LOG}"
            return 1
        fi
        if ! demo_bot_wait_ready "$backend_bot_id"; then
            log_error "BBS demo bot backend did not become ready: ${bot_name} (${backend_bot_id})"
            return 1
        fi

        DEMO_BBS_BOT_IDS+=("$backend_bot_id")
    done

    for index in "${!DEMO_BBS_BOT_IDS[@]}"; do
        backend_bot_id="${DEMO_BBS_BOT_IDS[$index]}"
        if ! demo_bbs_skills_install "$backend_bot_id"; then
            log_error "BBS demo start failed while installing skills for ${DEMO_BBS_BOT_NAMES[$index]}"
            return 1
        fi
        if ! demo_bbs_subscription_enable \
            "$backend_bot_id" \
            "${DEMO_BBS_BOT_DESCRIPTIONS[$index]}"; then
            log_error "BBS demo start failed while enabling subscription for ${DEMO_BBS_BOT_NAMES[$index]}"
            return 1
        fi
        log_info "BBS Browse-Loop initialized for ${DEMO_BBS_BOT_NAMES[$index]}"
    done

    log_info "BBS demo initialized: ${#DEMO_BBS_BOT_IDS[@]} bot subscriptions, ${#DEMO_BBS_TOPIC_TITLES[@]} topics"
}

demo_bot_ready() {
    demo_bot_defaults
    demo_bbs_validate_definitions || return 1
    local index backend_bot_id
    for index in "${!DEMO_BBS_BOT_NAMES[@]}"; do
        backend_bot_id="$(demo_bbs_find_existing "${DEMO_BBS_BOT_NAMES[$index]}")"
        [ -n "$backend_bot_id" ] || return 1
        demo_bot_wait_ready "$backend_bot_id" || return 1
    done
}

demo_bot_prereqs() {
    local has_error=false
    if check_command jq; then
        prereq_ok "jq: $(command -v jq)"
    else
        prereq_error "jq not found. Install jq before starting all."
        has_error=true
    fi
    if check_command curl; then
        prereq_ok "curl: $(command -v curl)"
    else
        prereq_error "curl not found."
        has_error=true
    fi
    if check_command zip; then
        prereq_ok "zip: $(command -v zip)"
    else
        prereq_error "zip not found. Install zip before starting all."
        has_error=true
    fi
    [ "$has_error" = false ]
}

demo_bot_status() {
    demo_bot_defaults
    demo_bbs_validate_definitions || return 1
    local index backend_bot_id
    for index in "${!DEMO_BBS_BOT_NAMES[@]}"; do
        backend_bot_id="$(demo_bbs_find_existing "${DEMO_BBS_BOT_NAMES[$index]}")"
        if [ -n "$backend_bot_id" ]; then
            echo "  BBS Demo Bot:  ${DEMO_BBS_BOT_NAMES[$index]} Started"
        else
            echo "  BBS Demo Bot:  ${DEMO_BBS_BOT_NAMES[$index]} Not ready"
        fi
    done
}

demo_bot_help() {
    echo "demo_bot - BBS demo initializer (two role-specific bots, initial topics, periodic subscriptions)"
}
