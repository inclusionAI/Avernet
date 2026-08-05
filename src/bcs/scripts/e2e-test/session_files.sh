#!/bin/bash
# session_files.sh — E2E story for the session shared-file workspace.
#
# Drives the full file lifecycle through the `bcs-cli session file` subcommands
# (upload / list / capabilities / download / share / delete) plus the three
# HTTP-only endpoints (GET file metadata, GET /sessions/shared-file meta,
# GET /sessions/shared-file/content) so the adapter endpoint-coverage and
# bcs-cli leaf-command-coverage gates both reach 100%. Uses the local storage
# backend wired by singlebox standalone.
#
# Endpoint coverage map (11 session-file endpoints):
#   POST /sessions/{sid}/files                         <- session file upload (prepare)
#   GET  /sessions/{sid}/files                         <- session file list
#   GET  /sessions/{sid}/files/capabilities            <- session file capabilities
#   GET  /sessions/{sid}/files/{file_id}               <- curl metadata (no CLI leaf)
#   DELETE /sessions/{sid}/files/{file_id}             <- session file delete
#   PUT  /sessions/{sid}/files/{file_id}/content       <- session file upload (PUT)
#   GET  /sessions/{sid}/files/{file_id}/content       <- session file download
#   POST /sessions/{sid}/files/{file_id}/complete      <- session file upload (complete)
#   POST /sessions/{sid}/files/{file_id}/share         <- session file share
#   GET  /sessions/shared-file                         <- curl shared meta
#   GET  /sessions/shared-file/content                 <- curl shared content
# CLI leaf coverage map (6 `session file <leaf>` leaves).

story_session_file_workspace() {
    info "Story: session file workspace (upload/list/cap/download/share/shared/delete)"

    if ! ensure_cli_token PM; then
        warn "no PM token; skipping session-file story"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi

    local SF_GID="" SF_TMP SF_DL SF_SHARE
    SF_TMP="$(mktemp -t bcs_sf_src.XXXXXX)"
    SF_DL="$(mktemp -t bcs_sf_dl.XXXXXX)"
    SF_SHARE="$(mktemp -t bcs_sf_share.XXXXXX)"
    printf 'bcs-e2e-session-file-payload\n' > "$SF_TMP"

    _sf_cleanup() {
        _cli_delete_group "$SF_GID"
        rm -f "$SF_TMP" "$SF_DL" "$SF_SHARE"
    }

    # --- setup: a PM-driven group + a session in it -------------------------
    SF_GID="$(_cli_create_group)"
    if [[ -z "$SF_GID" ]]; then
        fail "setup: create-group failed"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); _sf_cleanup; return
    fi
    if ! bcs_cli PM session create --group "$SF_GID" --title "file-ws"; then
        fail "setup: session create failed (exit $BCS_CLI_EXIT)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); _sf_cleanup; return
    fi
    local SID
    SID="$(printf '%s' "$BCS_CLI_STDOUT" | grep -oE '[A-Za-z0-9_-]+:[a-f0-9]{8}' | head -1)"
    if [[ -z "$SID" ]]; then
        fail "setup: could not parse session id from session create"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); _sf_cleanup; return
    fi

    # --- 1. upload (prepare -> PUT -> complete) -----------------------------
    # JSON mode so we can parse file_id from the complete-upload response.
    local FID=""
    if BCS_CLI_FORCE_JSON=1 bcs_cli PM session file upload --session "$SID" --path "$SF_TMP"; then
        FID="$(printf '%s' "$BCS_CLI_STDOUT" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin); print(d.get("file_id","") or "")
except Exception: print("")')"
    else
        fail "session file upload failed (exit $BCS_CLI_EXIT): $(printf '%s' "${BCS_CLI_STDERR:-$BCS_CLI_STDOUT}" | head -c 160)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); _sf_cleanup; return
    fi
    if [[ -z "$FID" ]]; then
        fail "session file upload returned no file_id: $(printf '%s' "$BCS_CLI_STDOUT" | head -c 160)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); _sf_cleanup; return
    fi
    pass "session file upload ok (file_id=$FID)"; TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # --- 2. list (human output includes the uploaded file_id) ----------------
    if bcs_cli PM session file list --session "$SID"; then
        assert_contains "session file list includes uploaded file" "$BCS_CLI_STDOUT" "$FID"
    else
        fail "session file list failed (exit $BCS_CLI_EXIT)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    # --- 3. capabilities (JSON; assert local backend) -----------------------
    if BCS_CLI_FORCE_JSON=1 bcs_cli PM session file capabilities --session "$SID"; then
        local stor
        stor="$(printf '%s' "$BCS_CLI_STDOUT" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin); print(d.get("storage","") or "")
except Exception: print("")')"
        assert_eq "session file capabilities (storage=local)" "$stor" "local"
    else
        fail "session file capabilities failed (exit $BCS_CLI_EXIT)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    # --- 4. get file metadata via HTTP (no CLI leaf for this endpoint) -------
    local _meta code body
    _meta="$(mktemp -t bcs_sf_meta.XXXXXX)"
    code="$(curl -s -o "$_meta" -w '%{http_code}' \
        -H "Authorization: Bearer $BCS_CLI_TOKEN" \
        "${BCS_API_BASE_URL}/sessions/${SID}/files/${FID}")" || code="000"
    body="$(cat "$_meta" 2>/dev/null)"; rm -f "$_meta"
    assert_eq "GET /sessions/{sid}/files/{file_id} -> 200" "$code" "200"
    if [[ "$code" = "200" ]]; then
        local mid
        mid="$(printf '%s' "$body" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin); print(d.get("file_id","") or "")
except Exception: print("")')"
        assert_eq "metadata file_id matches uploaded" "$mid" "$FID"
    fi

    # --- 5. download via CLI; verify bytes round-trip ------------------------
    if bcs_cli PM session file download --session "$SID" --file-id "$FID" --out "$SF_DL" \
       && cmp -s "$SF_TMP" "$SF_DL"; then
        pass "session file download ok (bytes match upload)"; TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "session file download failed or bytes mismatch (exit $BCS_CLI_EXIT)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # --- 6. share via CLI; extract share_token for the shared-file curls -----
    local TOK=""
    if BCS_CLI_FORCE_JSON=1 bcs_cli PM session file share --session "$SID" --file-id "$FID"; then
        TOK="$(printf '%s' "$BCS_CLI_STDOUT" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin); print(d.get("share_token","") or "")
except Exception: print("")')"
    else
        fail "session file share failed (exit $BCS_CLI_EXIT)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); _sf_cleanup; return
    fi
    if [[ -z "$TOK" ]]; then
        fail "session file share returned no share_token: $(printf '%s' "$BCS_CLI_STDOUT" | head -c 160)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); _sf_cleanup; return
    fi
    pass "session file share ok"; TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # --- 7. shared-file meta via HTTP (token-only, no auth; hides session_id) -
    local _sm
    _sm="$(mktemp -t bcs_sf_sm.XXXXXX)"
    code="$(curl -s -o "$_sm" -w '%{http_code}' \
        "${BCS_API_BASE_URL}/sessions/shared-file?token=${TOK}")" || code="000"
    body="$(cat "$_sm" 2>/dev/null)"; rm -f "$_sm"
    assert_eq "GET /sessions/shared-file -> 200" "$code" "200"
    if [[ "$code" = "200" ]]; then
        local has_sid
        has_sid="$(printf '%s' "$body" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin); print("1" if "session_id" in d else "0")
except Exception: print("1")')"
        assert_eq "shared-file meta hides session_id" "$has_sid" "0"
    fi

    # --- 8. shared-file content via HTTP (token-only; bytes match upload) ----
    code="$(curl -s -L -o "$SF_SHARE" -w '%{http_code}' \
        "${BCS_API_BASE_URL}/sessions/shared-file/content?token=${TOK}")" || code="000"
    assert_eq "GET /sessions/shared-file/content -> 200" "$code" "200"
    if [[ "$code" = "200" ]]; then
        if cmp -s "$SF_TMP" "$SF_SHARE"; then
            pass "shared-file content bytes match upload"; TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            fail "shared-file content bytes mismatch"; TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi

    # --- 9. delete via CLI (204) --------------------------------------------
    if bcs_cli PM session file delete --session "$SID" --file-id "$FID"; then
        pass "session file delete ok"; TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "session file delete failed (exit $BCS_CLI_EXIT)"; TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    _sf_cleanup
}
