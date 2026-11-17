#!/usr/bin/env bash
# Shared-link consent and membership freshness against the live V1 application.
E2E_TESTS_STORIES+=(story_invitation_membership_preflight)

story_invitation_membership_preflight() {
    info "Story: existing Group/Session members follow invitations without joining again"
    local result=0
    python3 "$SCRIPT_DIR/invitation_story.py" --base-url "$BCS_API_BASE_URL" || result=$?
    assert_eq "invitation preflight, explicit consent, scope preservation and revocation" "$result" "0"
}
