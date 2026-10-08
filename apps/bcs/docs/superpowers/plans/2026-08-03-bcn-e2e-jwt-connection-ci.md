# BCN E2E JWT Connection CI Repair Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restore the BCS coverage-gated E2E pipeline and prove that an externally issued, valid group-session JWT completes the public WebSocket Upgrade.

**Architecture:** Keep the shared process-level E2E on the configured `env` secret provider and update its missing-secret expectation to `404/not_found`. Add a standard-library-only Python probe that independently signs the documented local Gateway Principal and performs a real RFC 6455 handshake, then call it from the existing session user story after creating a Human-owned session and obtaining the real BCN connection JWT.

**Tech Stack:** Bash E2E stories, Python 3 standard library, BCS HTTP V1 token endpoint, Axum WebSocket endpoint, curl.

---

### Task 1: Align the shared secret-provider assertion

**Files:**
- Modify: `src/bcs/scripts/e2e-test/stories.sh:224-226`

**Step 1: Change the expected shared-environment behavior**

Replace the stale `noop` assertion with:

```bash
api_get "/admin/secret/e2e-missing-secret"
require_status "enabled local env secret backend reports a missing value as 404" "404" || return
assert_json_eq "missing env secret has stable error code" "$RESPONSE" "error" "not_found"
```

**Step 2: Run shell syntax validation**

Run: `bash -n src/bcs/scripts/e2e-test/stories.sh`

Expected: exit 0.

### Task 2: Add an independent local Principal and WebSocket probe

**Files:**
- Create: `src/bcs/scripts/e2e-test/group_session_ws_probe.py`

**Step 1: Add the command-line probe**

Implement two standard-library-only subcommands:

```text
principal --user-id ID --username NAME --tenant TENANT --signing-key KEY
websocket --url ws://host/path?token=JWT
```

`principal` must emit an HS256 JWT with `typ=JWT`, `kid=bare`, `iss=gateway`,
`aud=bcs`, a 60-second lifetime, and exactly one User Principal. `websocket`
must send an RFC 6455 Upgrade request, require HTTP 101, send a masked normal
close frame, avoid printing the sensitive URL, and exit non-zero on failure.

**Step 2: Validate the helper locally**

Run:

```bash
python3 src/bcs/scripts/e2e-test/group_session_ws_probe.py principal \
  --user-id 001 --username admin --tenant e2e \
  --signing-key avernet-dev-signing-key-NOT-FOR-PROD
```

Expected: one three-segment compact JWT and no secret-bearing diagnostic output.

Run: `python3 -m py_compile src/bcs/scripts/e2e-test/group_session_ws_probe.py`

Expected: exit 0.

### Task 3: Exercise token issuance and Upgrade in the coverage-gated story

**Files:**
- Modify: `src/bcs/scripts/e2e-test/stories.sh:451-466`

**Step 1: Add the failing external-process story step**

Add `_story_connect_with_group_session_jwt` to
`story_user_runs_and_shares_sessions`. The step must:

1. Create a DM Group containing the existing PM bot through the legacy local API.
2. Create a session as `human_${BCS_MOCK_USER_ID}`.
3. Generate the Gateway Principal with the probe and the documented local key.
4. POST `/openapi/v1/collaboration/sessions/{session_id}/token` with exactly one `X-Avernet-Principal` header.
5. Require `200`, `Cache-Control: no-store`, and a non-empty `data.token`.
6. Connect to `/openapi/v1/collaboration/messages/ws?token=...` with the probe and record one passing/failing assertion.
7. Delete the temporary Group on every normal return path.

**Step 2: Run shell syntax validation**

Run:

```bash
bash -n src/bcs/scripts/e2e-test/common.sh
bash -n src/bcs/scripts/e2e-test/stories.sh
```

Expected: both exit 0.

**Step 3: Run the focused live integration proof**

Run: `src/gateway/scripts/test_live_bcs_forwarding.sh`

Expected: the live token issuance and WebSocket Upgrade test passes.

### Task 4: Verify the CI repair

**Files:**
- Verify only.

**Step 1: Run focused BCS/Gateway tests**

Run:

```bash
cd src/bcs && cargo test -p bcs-ws --test group_session_ws
cd src/gateway && uv run pytest -q tests/integration/test_live_bcs_forwarding.py tests/test_live_bcs_runner.py
```

Expected: all selected tests pass; the opt-in live cases may skip when not run through their launcher.

**Step 2: Run repository diff validation**

Run: `git diff --check`

Expected: exit 0.

**Step 3: Run the BCS coverage-gated E2E when the local stack is available**

Run the repository's BCS E2E coverage entrypoint used by GitHub Actions.

Expected: no secret assertion failure, 100% endpoint coverage, line coverage at
least 40%, method coverage at least 36%, and the valid JWT Upgrade assertion
passes.

**Step 4: Commit**

```bash
git add src/bcs/scripts/e2e-test/stories.sh \
  src/bcs/scripts/e2e-test/group_session_ws_probe.py
git commit -m "test(bcs): cover valid session JWT websocket connection"
```
