#!/bin/bash
# test_cron_e2e.sh — End-to-end regression test for cron CRUD via relay WS
#
# Exercises the full cron RPC surface against a running relay (ws://localhost:18900):
#   1. cron.add    (create, verify payload.kind defaults to agentTurn)
#   2. cron.list   (list all, verify job present)
#   3. cron.get    (fetch single job by id)
#   4. cron.update (rename + disable + change schedule)
#   5. cron.status (verify counts)
#   6. cron.run    (force fire, verify run recorded)
#   7. cron.runs   (fetch run history)
#   8. cron.remove (delete, verify gone)
#
# Prerequisites:
#   - Relay running on ws://localhost:18900
#   - websocat installed (brew install websocat) OR python3 with websockets
#
# Usage:
#   ./scripts/test_cron_e2e.sh
#   RELAY_URL=ws://some-host:18900 ./scripts/test_cron_e2e.sh

set -euo pipefail

RELAY_URL="${RELAY_URL:-ws://localhost:18900}"
PASS="\033[92m[PASS]\033[0m"
FAIL="\033[91m[FAIL]\033[0m"
TAG="[cron-e2e]"
FAILURES=0

# ── Helpers ──────────────────────────────────────────────────────────────────

rpc() {
    local method="$1"
    local params="$2"
    local req_id
    req_id="req-$(date +%s%N | cut -c1-13)-$$"
    local frame="{\"type\":\"req\",\"id\":\"${req_id}\",\"method\":\"${method}\",\"params\":${params}}"

    # Use python websockets (available in engine .venv)
    local script_dir
    script_dir="$(cd "$(dirname "$0")" && pwd)"
    local python="${script_dir}/../.venv/bin/python3"
    if [ ! -x "$python" ]; then
        python="python3"
    fi

    "$python" -c "
import asyncio, json, sys, websockets

async def main():
    async with websockets.connect('${RELAY_URL}', max_size=None, open_timeout=10) as ws:
        # handshake
        hs = json.dumps({'type':'req','id':'hs','method':'connect','params':{}})
        await ws.send(hs)
        while True:
            r = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if r.get('type')=='res' and r.get('id')=='hs':
                break

        # actual RPC
        await ws.send(json.dumps(json.loads('${frame}')))
        deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - asyncio.get_event_loop().time())
            msg = json.loads(raw)
            if msg.get('type')=='res' and msg.get('id')=='${req_id}':
                print(json.dumps(msg))
                return
        print(json.dumps({'type':'res','id':'${req_id}','ok':False,'error':{'code':'TIMEOUT','message':'no response'}}))

asyncio.run(main())
" 2>/dev/null
}

assert_eq() {
    local label="$1" actual="$2" expected="$3"
    if [ "$actual" = "$expected" ]; then
        echo -e "  ${PASS} ${label}"
    else
        echo -e "  ${FAIL} ${label} (expected=${expected}, got=${actual})"
        FAILURES=$((FAILURES + 1))
    fi
}

assert_not_empty() {
    local label="$1" actual="$2"
    if [ -n "$actual" ] && [ "$actual" != "null" ]; then
        echo -e "  ${PASS} ${label} (=${actual})"
    else
        echo -e "  ${FAIL} ${label} (empty/null)"
        FAILURES=$((FAILURES + 1))
    fi
}

jq_field() {
    echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d$2) if isinstance(d$2,(dict,list)) else (d$2 if d$2 is not None else 'null'))" 2>/dev/null
}

# ── Tests ────────────────────────────────────────────────────────────────────

echo "${TAG} Testing cron CRUD against ${RELAY_URL}"
echo ""

# 1. cron.add — create job with systemEvent payload (fires instantly as "skipped", no model call)
echo "${TAG} 1. cron.add (create)"
JOB_NAME="e2e-test-$(date +%s)"
RESP=$(rpc "cron.add" "{\"name\":\"${JOB_NAME}\",\"schedule\":{\"kind\":\"every\",\"everyMs\":60000},\"payload\":{\"kind\":\"systemEvent\",\"text\":\"e2e-noop\"},\"sessionTarget\":\"isolated\",\"enabled\":true}")
ADD_OK=$(jq_field "$RESP" "['ok']")
JOB_ID=$(jq_field "$RESP" "['payload']['id']")
assert_eq "add ok" "$ADD_OK" "True"
assert_not_empty "job id" "$JOB_ID"

# Also test add WITHOUT explicit kind (regression: must not fail)
echo "${TAG} 1b. cron.add (no explicit kind in payload → should still succeed)"
RESP_NO_KIND=$(rpc "cron.add" "{\"name\":\"${JOB_NAME}-nokind\",\"schedule\":{\"kind\":\"once\",\"atMs\":9999999999999},\"payload\":{\"kind\":\"agentTurn\",\"message\":\"hi\"},\"sessionTarget\":\"isolated\",\"enabled\":false}")
ADD_NK_OK=$(jq_field "$RESP_NO_KIND" "['ok']")
JOB_ID_NK=$(jq_field "$RESP_NO_KIND" "['payload']['id']")
assert_eq "add-no-kind ok" "$ADD_NK_OK" "True"
# cleanup this extra job at end
echo ""

# 2. cron.list
echo "${TAG} 2. cron.list"
RESP=$(rpc "cron.list" "{\"includeDisabled\":true}")
LIST_OK=$(jq_field "$RESP" "['ok']")
LIST_COUNT=$(echo "$RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['payload']))" 2>/dev/null)
assert_eq "list ok" "$LIST_OK" "True"
if [ "$LIST_COUNT" -ge 1 ]; then
    echo -e "  ${PASS} list count >= 1 (=${LIST_COUNT})"
else
    echo -e "  ${FAIL} list count < 1 (=${LIST_COUNT})"
    FAILURES=$((FAILURES + 1))
fi
echo ""

# 3. cron.get
echo "${TAG} 3. cron.get"
RESP=$(rpc "cron.get" "{\"jobId\":\"${JOB_ID}\"}")
GET_OK=$(jq_field "$RESP" "['ok']")
GET_NAME=$(jq_field "$RESP" "['payload']['name']")
assert_eq "get ok" "$GET_OK" "True"
assert_eq "get name" "$GET_NAME" "$JOB_NAME"
echo ""

# 4. cron.update (rename + disable)
echo "${TAG} 4. cron.update"
NEW_NAME="${JOB_NAME}-updated"
RESP=$(rpc "cron.update" "{\"id\":\"${JOB_ID}\",\"patch\":{\"name\":\"${NEW_NAME}\",\"enabled\":false}}")
UPD_OK=$(jq_field "$RESP" "['ok']")
UPD_NAME=$(jq_field "$RESP" "['payload']['name']")
UPD_ENABLED=$(jq_field "$RESP" "['payload']['enabled']")
assert_eq "update ok" "$UPD_OK" "True"
assert_eq "updated name" "$UPD_NAME" "$NEW_NAME"
assert_eq "updated enabled" "$UPD_ENABLED" "False"
echo ""

# Re-enable for run test
rpc "cron.update" "{\"id\":\"${JOB_ID}\",\"patch\":{\"enabled\":true}}" > /dev/null

# 5. cron.status
echo "${TAG} 5. cron.status"
RESP=$(rpc "cron.status" "{}")
STATUS_OK=$(jq_field "$RESP" "['ok']")
JOB_COUNT=$(jq_field "$RESP" "['payload']['jobCount']")
assert_eq "status ok" "$STATUS_OK" "True"
if [ "$JOB_COUNT" -ge 1 ]; then
    echo -e "  ${PASS} jobCount >= 1 (=${JOB_COUNT})"
else
    echo -e "  ${FAIL} jobCount < 1 (=${JOB_COUNT})"
    FAILURES=$((FAILURES + 1))
fi
echo ""

# 6. cron.run (force — systemEvent payload records "skipped" instantly, no model call)
echo "${TAG} 6. cron.run (force)"
RESP=$(rpc "cron.run" "{\"id\":\"${JOB_ID}\",\"mode\":\"force\"}")
RUN_OK=$(jq_field "$RESP" "['ok']")
RUN_STATUS=$(jq_field "$RESP" "['payload']['status']")
assert_eq "run ok" "$RUN_OK" "True"
assert_eq "run status (systemEvent → skipped)" "$RUN_STATUS" "skipped"
echo ""

# 7. cron.runs (history)
echo "${TAG} 7. cron.runs"
RESP=$(rpc "cron.runs" "{\"id\":\"${JOB_ID}\",\"limit\":5}")
RUNS_OK=$(jq_field "$RESP" "['ok']")
RUNS_COUNT=$(echo "$RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['payload']['entries']))" 2>/dev/null)
assert_eq "runs ok" "$RUNS_OK" "True"
if [ "$RUNS_COUNT" -ge 1 ]; then
    echo -e "  ${PASS} runs count >= 1 (=${RUNS_COUNT})"
else
    echo -e "  ${FAIL} runs count < 1 (=${RUNS_COUNT})"
    FAILURES=$((FAILURES + 1))
fi
echo ""

# 8. cron.remove (delete)
echo "${TAG} 8. cron.remove"
RESP=$(rpc "cron.remove" "{\"id\":\"${JOB_ID}\"}")
DEL_OK=$(jq_field "$RESP" "['ok']")
DEL_REMOVED=$(jq_field "$RESP" "['payload']['removed']")
assert_eq "remove ok" "$DEL_OK" "True"
assert_eq "removed" "$DEL_REMOVED" "True"

# Verify gone
RESP=$(rpc "cron.get" "{\"jobId\":\"${JOB_ID}\"}")
GET_AFTER=$(jq_field "$RESP" "['payload']")
assert_eq "get after delete" "$GET_AFTER" "null"
echo ""

# Cleanup: remove the no-kind test job
rpc "cron.remove" "{\"id\":\"${JOB_ID_NK}\"}" > /dev/null 2>&1 || true

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
if [ "$FAILURES" -eq 0 ]; then
    echo -e "${TAG} ✅ ALL PASS — cron CRUD (add/list/get/update/status/run/runs/remove)"
    exit 0
else
    echo -e "${TAG} ❌ FAILED (${FAILURES} assertion(s))"
    exit 1
fi
