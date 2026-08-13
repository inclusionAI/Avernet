#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"

cleanup() {
    rm -rf "$TMP"
}
trap cleanup EXIT

export HOME="$TMP/home"
mkdir -p "$HOME/.claude"
printf '%s\n' '{"env":{"ANTHROPIC_MODEL":"test-model"}}' > "$HOME/.claude/settings.json"

CLAUDE_PROFILE="$TMP/claude-profile"
cp -R "$ROOT/scripts/4bots_merchant_operations_profile_for_claude" "$CLAUDE_PROFILE"
mkdir -p "$TMP/claude-config" "$TMP/claude-workspace"
python3 - "$CLAUDE_PROFILE/bots.json" "$TMP" <<'PY'
import json
import sys

path, temp_root = sys.argv[1:]
with open(path, encoding='utf-8') as stream:
    profile = json.load(stream)
runtime = profile['bots'][0]['runtime']
runtime['claude_config_dir'] = f'{temp_root}/claude-config'
runtime['workspace'] = f'{temp_root}/claude-workspace'
with open(path, 'w', encoding='utf-8') as stream:
    json.dump(profile, stream)
PY

export BOTS_PROFILE_DIR="$ROOT/scripts/4bots_merchant_operations_profile"
export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"
export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"
export SINGLEBOX_MODEL_CONFIG_MODE="home"
# shellcheck source=/dev/null
source "$ROOT/scripts/singlebox.sh"

merchant_hybrid_validate_profiles
[[ "$CLAUDE_BOTS_STATE_FILE" == *"claude_bots.merchant_hybrid.state.json" ]]
[[ "$BCS_BAAS_PROVIDER_STATE_FILE" == *"bcs_baas_provider.merchant_hybrid.state.json" ]]
[[ "$(bots_dynamic_count)" == "3" ]]
[[ "$(bots_dynamic_specs | cut -f4 | tr '\n' ' ')" == *"merchant-operations"* ]]
[[ "$(bots_dynamic_specs | cut -f4 | tr '\n' ' ')" != *"platform-data"* ]]
[[ "$(bots_dynamic_specs | awk -F '\t' '$4 == "platform-supply-chain" { print $3 }')" == "30631" ]]
[[ "$(claude_bots_entries | awk -F $'\x1f' '{ print $1 }')" == "platform-data" ]]
[[ "$(claude_bots_entries | awk -F $'\x1f' '{ print $4 }')" == "18913" ]]
[[ "$(claude_bots_entries | awk -F $'\x1f' '{ print $9 }')" == "bypassPermissions" ]]
[[ "$(claude_bots_entries | awk -F $'\x1f' '{ print $7 }')" == "Kimi-K2.6" ]]
grep -Fq 'AskUserQuestion' "$CLAUDE_PROFILE/platform-data/CLAUDE.md"
grep -Fq '读取回执不是本轮可见回复的终点' "$BOTS_PROFILE_DIR/merchant-operations/AGENTS.md"
grep -Fq 'INITIAL_CONTEXT_READY' "$BOTS_PROFILE_DIR/merchant-operations/AGENTS.md"
grep -Fq '初始化等待优先规则' "$BOTS_PROFILE_DIR/merchant-operations/BOOTSTRAP.md"
grep -Fq '不得输出 `INITIAL_CONTEXT_READY` 或等待另一条店主消息' "$BOTS_PROFILE_DIR/merchant-operations/AGENTS.md"
grep -Fq '绝不输出 `INITIAL_CONTEXT_READY`，也不等待另一条店主消息' "$BOTS_PROFILE_DIR/merchant-operations/BOOTSTRAP.md"
grep -Fq '`PRIVATE_INTAKE` 只用 `bcs_discover_bots` 与 `bcs_create_manager_worker_group`' "$BOTS_PROFILE_DIR/merchant-operations/RULES.md"
grep -Fq '禁止读取 `.bcs/session.json`、设置/传递 token、调用 raw HTTP/curl 或启动 singlebox/default bot 脚本' "$BOTS_PROFILE_DIR/merchant-operations/RULES.md"
grep -Fq '不得委派给 `subagents`、`sessions_spawn` 或子 Agent' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq '唯一入口必须分配给第一轮的一名 required Worker' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq 'Claude Code 数据 Worker 的每个 `bot_task` 必须显式设置至少 `600000` 毫秒' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq '`runtime.state_machine.defaults.node_timeout_ms >= 600000`' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq 'AUTHORING_PREFLIGHT={manager_judges:3,round_1_outcomes:[approved,revise],round_2_outcomes:[approved,revise],round_3_outcomes:[approved,blocked],worker_judges:0,human_judge:1,human_outcomes:[accepted,changes_requested],accepted_marker:1,changes_marker:1,blocked_marker:1,final_output:1}' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq '营销、数据、供应三个 required Worker 各恰有三个 `bot_task`' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq '不得从 Bot-only context/roster 预判、查询或询问 Present Human' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq 'BCS 的 run 返回和随后 HumanInput execution 是人类在场/等待的唯一真实依据' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq '`sender_id=bcs_state_machine`、正文含 `[State Machine Task]` 且当前角色为 manager 是等价的状态机节点信号' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq '不得调用 `bcs_assign_task`、建群、`bcs_task_complete`、CLI、shell、read/edit 或任何其他工具' "$BOTS_PROFILE_DIR/merchant-operations/AGENTS.md"
grep -Fq '截至 2026-08-07 已通过包装、剩余有效期和批次验收' "$BOTS_PROFILE_DIR/merchant-operations/KNOWLEDGE.md"
grep -Fq '不得要求先执行外部动作才进入 HumanInput' "$BOTS_PROFILE_DIR/merchant-operations/AGENTS.md"
grep -Fq '实际下单、到货回执和到货复验属于 `pending_external_actions`，不是方案证据缺失' "$BOTS_PROFILE_DIR/platform-supply-chain/RULES.md"
grep -Fq '本次激活禁止重复全文读取它们' "$BOTS_PROFILE_DIR/merchant-operations/AGENTS.md"
grep -Fq 'schema_read_receipt={schema_path,read_at,last_heading}' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq '本门店公开门市价只可能是王牌剪发 80 元/次、护理套餐' "$BOTS_PROFILE_DIR/merchant-operations/KNOWLEDGE.md"
grep -Fq '360 元/套；32 元/次和 180 元/套只属于下节 `PRIVATE_SECRET` 变动履约成本' "$BOTS_PROFILE_DIR/merchant-operations/KNOWLEDGE.md"
grep -Fq 'skills/bcs-coordination/references/custom-collaboration-schema.md' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq 'max_tokens = 8192' "$ROOT/src/bcs/configs/bcs-config-local.toml"
grep -Fq 'timeout_ms = 540000' "$ROOT/src/bcs/configs/bcs-config-local.toml"
grep -Fq 'structured_output = "tool_call"' "$ROOT/src/bcs/configs/bcs-config-local.toml"
grep -Fq 'MERCHANT_STABILITY_HUMAN_RESPONSE_TIMEOUT_MS' "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq 'humanResponseTimeoutMs' "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq 'waitForInitialContextReady' "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq '测试私聊已建立，等待下一条店主消息' "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq 'hasFixedActivityPeriod' "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq 'incompatible_contracts=' "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq 'assertWorkerGroupContract' "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq '最大核销量是当前版本的待履约契约上限，不是销量预测' "$CLAUDE_PROFILE/platform-data/RULES.md"
grep -Fq '活动执行周期与券/套餐有效期是独立字段' "$BOTS_PROFILE_DIR/merchant-operations/AGENTS.md"
grep -Fq '`checks_status` 等会在状态机内变化的派生状态不得写入 run input' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq '采购预付总额只进入现金占用校验，不得再从贡献毛利中扣除' "$BOTS_PROFILE_DIR/merchant-operations/AGENTS.md"
grep -Fq '耗材单位成本只能在变动履约成本中计入一次' "$BOTS_PROFILE_DIR/merchant-operations/KNOWLEDGE.md"
grep -Fq '每个 Manager judge 的直接上游必须同时包含本轮营销、数据、供应三个 Worker' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq '"care_total_units":120' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"
grep -Fq 'manager judge is missing a direct Worker upstream' "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq '不是待决项，也不是销量预测' "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq "caffeinate" "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq 'terminalWorkerError' "$ROOT/scripts/test_merchant_hybrid_anniversary_stability.mjs"
grep -Fq 'SINGLEBOX_MODEL_ID_OVERRIDE' "$ROOT/scripts/modules/model_config.sh"
# Local command runners can terminate the parent process group after the
# launcher returns. Every merchant_hybrid background entrypoint must establish
# its own session before exec so a successful start remains usable.
grep -Fq 'perl -MPOSIX=setsid' "$ROOT/scripts/modules/backend.sh"
grep -Fq 'perl -MPOSIX=setsid' "$ROOT/src/baas/scripts/app.sh"
grep -Fq 'perl -MPOSIX=setsid' "$ROOT/scripts/modules/bcs.sh"
grep -Fq 'perl -MPOSIX=setsid' "$ROOT/scripts/modules/bots.sh"
grep -Fq 'perl -MPOSIX=setsid' "$ROOT/scripts/modules/bcs_baas_provider.sh"
grep -Fq 'perl -MPOSIX=setsid' "$ROOT/scripts/modules/frontend.sh"
if grep -Fq '没有 Present Human 时不得试跑' "$BOTS_PROFILE_DIR/merchant-operations/TOOLS.md"; then
  echo 'merchant manager profile still blocks run from inferred Human presence' >&2
  exit 1
fi
! grep -Fq 'bcs-cli --json discover --query' "$BOTS_PROFILE_DIR/merchant-operations/RULES.md"

# BAAS/backend modules change cwd while starting. Relative --claude-profile-dir
# must remain anchored to the checkout for the later Claude-bot phase.
CLAUDE_PROFILE_DIR="scripts/4bots_merchant_operations_profile_for_claude"
cd "$ROOT/src/baas"
claude_profile_validate_config
[[ "$(claude_profile_dir)" == "$ROOT/scripts/4bots_merchant_operations_profile_for_claude" ]]
cd "$ROOT"
CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"

rm -rf "$TMP/claude-config"
claude_relay_ensure_config_dir platform-data "$TMP/claude-config"
[[ -d "$TMP/claude-config" ]]

python3 - "$CLAUDE_PROFILE/bots.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as stream:
    profile = json.load(stream)
profile['bots'][0]['runtime']['permission_mode'] = 'plan'
with open(path, 'w', encoding='utf-8') as stream:
    json.dump(profile, stream)
PY
if merchant_hybrid_validate_profiles; then
    echo 'plan permission mode unexpectedly accepted' >&2
    exit 1
fi
python3 - "$CLAUDE_PROFILE/bots.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as stream:
    profile = json.load(stream)
profile['bots'][0]['runtime']['permission_mode'] = 'bypassPermissions'
with open(path, 'w', encoding='utf-8') as stream:
    json.dump(profile, stream)
PY

unset BOTS_EXCLUDED_PROFILE_SOURCE
[[ "$(bots_dynamic_count)" == "4" ]]
export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"

python3 - "$CLAUDE_PROFILE/bots.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as stream:
    profile = json.load(stream)
profile['bots'][0]['runtime']['relay_port'] = 18914
with open(path, 'w', encoding='utf-8') as stream:
    json.dump(profile, stream)
PY
if merchant_hybrid_validate_profiles; then
    echo 'invalid Claude relay port unexpectedly accepted' >&2
    exit 1
fi
python3 - "$CLAUDE_PROFILE/bots.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as stream:
    profile = json.load(stream)
profile['bots'][0]['runtime']['relay_port'] = 18913
with open(path, 'w', encoding='utf-8') as stream:
    json.dump(profile, stream)
PY

events="$TMP/events"
check_prereqs_for_services() { return 0; }
merchant_hybrid_port_preflight() { return 0; }
print_local_stack_ready_banner() { printf '%s\n' 'ready' >> "$events"; }
for service in claude_relays baas backend bcs bots claude_bots bcs_baas_provider frontend; do
    eval "${service}_start() { printf '%s\\n' 'start:${service}' >> \"\$events\"; }"
    eval "${service}_stop() { printf '%s\\n' 'stop:${service}' >> \"\$events\"; }"
    eval "${service}_ready() { return 0; }"
done

merchant_hybrid_start
expected_start=$'start:claude_relays\nstart:baas\nstart:backend\nstart:bcs\nstart:bots\nstart:claude_bots\nstart:bcs_baas_provider\nstart:frontend\nready'
[[ "$(cat "$events")" == "$expected_start" ]]

: > "$events"
merchant_hybrid_stop
expected_stop=$'stop:frontend\nstop:bcs_baas_provider\nstop:claude_bots\nstop:bots\nstop:bcs\nstop:backend\nstop:baas\nstop:claude_relays'
[[ "$(cat "$events")" == "$expected_stop" ]]

: > "$events"
claude_bots_start() { printf '%s\n' 'start:claude_bots' >> "$events"; return 23; }
if merchant_hybrid_start; then
    echo 'merchant_hybrid unexpectedly succeeded after Claude bot failure' >&2
    exit 1
fi
expected_rollback=$'start:claude_relays\nstart:baas\nstart:backend\nstart:bcs\nstart:bots\nstart:claude_bots\nstop:bots\nstop:bcs\nstop:backend\nstop:baas\nstop:claude_relays'
[[ "$(cat "$events")" == "$expected_rollback" ]]

dispatch_events="$TMP/dispatch-events"
merchant_hybrid_prereqs() {
    # Reproduce a composite prerequisite checker that uses the common `svc`
    # loop variable. The outer dispatcher must preserve merchant_hybrid.
    svc=frontend
}
merchant_hybrid_start() { printf '%s\n' 'start:merchant_hybrid' >> "$dispatch_events"; }
frontend_start() { printf '%s\n' 'start:frontend' >> "$dispatch_events"; }
start_service merchant_hybrid
[[ "$(cat "$dispatch_events")" == 'start:merchant_hybrid' ]]

echo 'merchant_hybrid dual-profile shell tests passed'
