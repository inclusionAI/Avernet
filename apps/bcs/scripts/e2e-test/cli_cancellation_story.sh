#!/bin/bash
# Bounded wait/cancel assertions for the terminal CLI story. Sourced, not executed.
# --wait-until running also accepts completed; completion may race the cancel.
_cli_story_wait_and_cancel_chat() {
    _cli_story_run "operator opens a bounded wait-until chat" PM chat \
        --bot-uuid "$BOT_ENG_UUID" --message "terminal wait check $$" \
        --wait-until running --timeout-ms 15000 || return 1
    local waiting_json="$BCS_CLI_STDOUT" waiting_run_id
    waiting_run_id=$(printf '%s' "$waiting_json" | python3 -c '
import json, sys
try:
    run = json.load(sys.stdin)
    if (isinstance(run, dict) and isinstance(run.get("run_id"), str)
            and run["run_id"] and run.get("state") in ("running", "completed")):
        print(run["run_id"])
except (ValueError, TypeError):
    pass
')
    assert_not_empty "wait-until chat returns a running/completed run id" "$waiting_run_id"
    [[ -n "$waiting_run_id" ]] || return 1

    _cli_story_run "operator cancels the observed chat" PM chat-run cancel \
        --run-id "$waiting_run_id" || return 1
    local cancel_matches
    cancel_matches=$(python3 - "$waiting_json" "$BCS_CLI_STDOUT" <<'PY'
import json, sys
try:
    waiting, cancelled = (json.loads(value) for value in sys.argv[1:])
    valid = isinstance(cancelled, dict) and cancelled.get("run_id") == waiting["run_id"] and (
        (waiting["state"] == "running" and cancelled.get("state") == "cancelled"
         and cancelled.get("cancelled") is True)
        or (cancelled.get("state") == "completed" and cancelled.get("cancelled") is False)
    )
except (ValueError, TypeError):
    valid = False
print("true" if valid else "false")
PY
)
    # Keep the original four assertions, but validate identity and the boolean
    # flag as well as state; command success alone is not a cancellation result.
    assert_eq "cancel preserves run identity and reports cancellation or completed no-op" \
        "$cancel_matches" "true"
    [[ "$cancel_matches" == "true" ]]
}
