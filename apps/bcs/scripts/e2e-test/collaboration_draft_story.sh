#!/usr/bin/env bash
# Real HTTP/CLI journey: author once, share exact bytes, revise and revoke.
E2E_TESTS_STORIES+=(story_collaboration_draft_delivery)
if [[ -n "$(mock_view_base_url)" ]]; then
    E2E_TESTS_STORIES+=(story_collaboration_draft_group_launch)
fi

story_collaboration_draft_group_launch() {
    info "Story: a Human opens a shared Draft and launches it with their own Bots"
    local result=0
    python3 "$SCRIPT_DIR/collaboration_draft_group_story.py" \
        --base-url "$BCS_API_BASE_URL" --human "$BCS_MOCK_USER_ID" \
        --mock-sink-url "$(mock_sink_base_url)" || result=$?
    assert_eq "shared Draft preview and Human group creation journey" "$result" "0"
}

story_collaboration_draft_delivery() {
    info "Story: an author delivers immutable YAML revisions without a chat Session"
    local directory result=0
    directory="$(mktemp -d)"
    _collaboration_draft_delivery "$directory" || result=$?
    rm -rf "$directory"
    assert_eq "Draft delivery journey completes" "$result" "0"
}

_collaboration_draft_delivery() {
    local directory="$1" id owner_token
    id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
    python3 - "$SCRIPT_DIR/../../tests/fixtures/collaboration-definition-draft/indentation-regression.json" "$directory" <<'PY'
import json, pathlib, sys
source = json.loads(pathlib.Path(sys.argv[1]).read_text())["revision"]["definition_yaml"].encode()
root = pathlib.Path(sys.argv[2])
(root / "original.yaml").write_bytes(source)
(root / "updated.yaml").write_bytes(source + b"\n# second revision\n")
PY
    _cli_story_run "existing offline preview still emits a schema 1 snapshot" PM collaboration preview \
        "$directory/original.yaml" || return 1
    assert_json_eq "offline preview retains schema 1" "$BCS_CLI_STDOUT" snapshot.schemaVersion 1
    printf '%s' "$BCS_CLI_STDOUT" | python3 -c \
        'import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(json.load(sys.stdin)["snapshot"]["definitionYaml"].encode())' \
        "$directory/offline.yaml" || return 1
    assert_ok "offline preview preserves the incident source" cmp "$directory/original.yaml" "$directory/offline.yaml"
    _cli_story_run "author uploads the incident file" PM collaboration draft create \
        "$directory/original.yaml" --draft-id "$id" --title "Draft delivery regression" || return 1
    assert_json_eq "saved source has 2174 bytes" "$BCS_CLI_STDOUT" size_bytes 2174
    assert_json_eq "saved source validates" "$BCS_CLI_STDOUT" validation.validation.valid true
    assert_json_eq "first revision is immutable" "$BCS_CLI_STDOUT" revision 1
    [[ "$BCS_CLI_STDOUT" != *readToken* ]] || return 1
    owner_token="$(get_bot_token PM)"
    [[ -n "$owner_token" ]] || return 1

    api_request_headers GET "/collaboration/drafts/$id" '' "Authorization: Bearer $owner_token"
    require_status "author reads Draft metadata" 200 || return 1
    assert_json_eq "metadata points to revision one" "$RESPONSE" head_revision 1
    api_request_headers GET "/collaboration/drafts/$id/revisions/1" ''
    require_status "a card holder reads without Bot credentials or Session membership" 200 || return 1
    assert_json_eq "shared read preserves incident hash" "$RESPONSE" content_hash \
        761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7
    assert_contains "shared source is not cached" "$RESPONSE_HEADERS" no-store
    api_request_headers POST "/collaboration/drafts/$id/revisions/1/validate" ''
    require_status "card holder validates the stored source" 200 || return 1
    assert_json_eq "shared validation binds revision one" "$RESPONSE" revision 1
    api_request_headers DELETE "/collaboration/drafts/$id" ''
    require_status "public reader cannot delete" 401 || return 1

    _cli_story_run "author downloads exact original bytes" PM collaboration draft get "$id" \
        --revision 1 --output "$directory/download.yaml" || return 1
    assert_ok "download matches incident file byte for byte" cmp "$directory/original.yaml" "$directory/download.yaml"
    _cli_story_run "author validates the fixed revision" PM collaboration draft validate "$id" --revision 1 || return 1
    _cli_story_run "author appends an explicit new revision" PM collaboration draft update "$id" \
        "$directory/updated.yaml" --expected-revision 1 || return 1
    assert_json_eq "update advances exactly once" "$BCS_CLI_STDOUT" revision 2
    _cli_story_run "author reissues the historical card" PM collaboration draft preview "$id" --revision 1 || return 1
    assert_json_eq "historical preview still returns the original bytes" "$BCS_CLI_STDOUT" size_bytes 2174
    assert_json_eq "reissuing a card preserves its fixed version" "$BCS_CLI_STDOUT" reference.revision 1
    api_request_headers POST "/collaboration/drafts/$id/revisions" \
        '{"expected_revision":1,"definition_yaml":"different"}' "Authorization: Bearer $owner_token"
    require_status "stale writer cannot replace the newer revision" 409 || return 1

    _cli_story_run "author revokes only the second revision" PM collaboration draft revoke "$id" --revision 2 || return 1
    _cli_story_run "repeated revocation is idempotent" PM collaboration draft revoke "$id" --revision 2 || return 1
    api_request_headers GET "/collaboration/drafts/$id/revisions/2" ''
    require_status "revoked card becomes unavailable" 404 || return 1
    api_request_headers GET "/collaboration/drafts/$id/revisions/1" ''
    require_status "old shared revision remains readable" 200 || return 1
    _cli_story_run "author deletes the disposable Draft" PM collaboration draft delete "$id" || return 1
    api_request_headers GET "/collaboration/drafts/$id/revisions/1" ''
    require_status "deletion revokes every historical card" 404 || return 1
    api_request_headers PUT "/collaboration/drafts/$id" \
        '{"definition_yaml":"late request"}' "Authorization: Bearer $owner_token"
    require_status "late create cannot revive a deleted Draft" 404 || return 1
}
