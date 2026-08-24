# Group Provider Tags Backend-Only PR Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update PR #1218 onto the latest `dev`, retain all backend provider-tag behavior, and remove every `src/frontend/**` change from the PR diff.

**Architecture:** Rebase the existing backend feature and scope commits onto `origin/dev`. Preserve the upstream Group eventing/opening-message implementation while layering participant tags into the same DTO, migration, and Group Store paths. Keep BCS OpenAPI/protocol definitions as the authoritative backend contract and restore frontend files exactly to the target branch state.

**Tech Stack:** Rust, Serde, SQLite/MySQL migrations, OpenAPI YAML, Git/GitHub Actions.

---

### Task 1: Rebase onto the latest backend baseline

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/src/migrations.rs`
- Modify: `src/bcs/crates/services/bcs-group-store/src/lib.rs`

- [ ] **Step 1: Verify the exact rebase boundary**

Run:

```bash
git fetch origin dev codex/bcs-group-provider-tags
git status -sb
git rev-list --left-right --count origin/dev...HEAD
git merge-tree --write-tree --name-only origin/dev HEAD
```

Expected: a clean worktree, the feature branch ahead by the two local commits, and conflicts limited to the three files listed above.

- [ ] **Step 2: Start the rebase**

Run:

```bash
git rebase origin/dev
```

Expected: the feature commit stops on conflicts in the DTO, SQLite migration registry, and Group Store.

- [ ] **Step 3: Merge the DTO behavior**

In `group.rs`, preserve upstream `opening_message`, event subscriptions, `ApplicationError`, and `CreateGroupRequest::into_parts`. Add the backend participant tag contract to the upstream shape:

```rust
pub struct ParticipantRequest {
    pub actor_id: String,
    pub role: ParticipantRole,
    #[serde(default)]
    pub tags: Vec<String>,
}
```

Map `tags: participant.tags` into `CreateParticipant` without removing any upstream fields.

- [ ] **Step 4: Merge migration numbering without collisions**

In `migrations.rs`, preserve upstream SQLite versions 9 (`eventing`), 10 (`eventing_plaintext_endpoint`), and 11 (`group_opening_message`). Register participant tags as version 12:

```rust
SqliteMigration {
    version: 12,
    name: "group_participant_tags",
},
```

Keep `tags_json TEXT DEFAULT NULL` in the baseline participant table and route version 12 to the idempotent `add_sqlite_group_participant_tags_schema` function. Update migration expectations from version 9 to version 12.

- [ ] **Step 5: Merge Group Store persistence**

Preserve all upstream `opening_message_json` and eventing changes. In every participant query and row mapping, retain `tags_json`/`p_tags_json`, deserialize it through `deserialize_participant_tags`, serialize tag arrays through parameter-bound database values, and keep historical `NULL` values mapped to an empty list. Do not interpolate tag values into SQL.

- [ ] **Step 6: Complete the rebase**

Run:

```bash
git add -- \
  src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs \
  src/bcs/crates/bootstrap/bcs/src/migrations.rs \
  src/bcs/crates/services/bcs-group-store/src/lib.rs
git diff --cached --check
GIT_EDITOR=true git rebase --continue
```

Expected: both existing commits replay successfully and `git rev-list --left-right --count origin/dev...HEAD` reports `0 2`.

### Task 2: Remove frontend changes and align migration artifacts

**Files:**
- Modify: `src/frontend/src/pages/GroupChat/types.ts`
- Modify: `src/frontend/src/services/backend-api/BcnController.ts`
- Rename: `src/bcs/migrations/mysql/009_group_participant_tags.sql` to `src/bcs/migrations/mysql/011_group_participant_tags.sql`
- Modify: `src/bcs/docs/plans/2026-08-19-group-participant-provider-tags-design.md`

- [ ] **Step 1: Restore the frontend tree to `origin/dev`**

Remove only the provider-tag fields introduced by this PR from the two frontend files. Verify the result:

```bash
git diff origin/dev...HEAD -- src/frontend
```

Expected: no output.

- [ ] **Step 2: Move the MySQL migration behind upstream migrations**

Rename the additive migration to `011_group_participant_tags.sql`; keep its SQL unchanged:

```sql
ALTER TABLE bcs_group_participants
    ADD COLUMN IF NOT EXISTS tags_json TEXT DEFAULT NULL;
```

Update the design document to state SQLite v12 and MySQL 011 instead of migration 009.

- [ ] **Step 3: Fold the scope changes into the current local commit**

Run:

```bash
git add -- \
  src/frontend/src/pages/GroupChat/types.ts \
  src/frontend/src/services/backend-api/BcnController.ts \
  src/bcs/migrations/mysql/009_group_participant_tags.sql \
  src/bcs/migrations/mysql/011_group_participant_tags.sql \
  src/bcs/docs/plans/2026-08-19-group-participant-provider-tags-design.md
git diff --cached --check
git commit --amend --no-verify --no-edit
```

Expected: the PR tree has no `src/frontend/**` diff and contains only backend/docs changes.

### Task 3: Validate the merged backend change

**Files:**
- Test: `src/bcs/crates/services/bcs-collaboration-runtime/tests/runtime_progression.rs`
- Test: `src/bcs/crates/services/bcs-message-flow/tests/contract_message_flow.rs`
- Test: `src/bcs/crates/application/v1/bcs-app-session/tests/v1_session_service.rs`
- Test: `src/bcs/crates/bootstrap/bcs/src/migrations.rs`

- [ ] **Step 1: Compile every BCS test target**

Run from `src/bcs`:

```bash
CARGO_INCREMENTAL=0 cargo test --workspace --no-run
```

Expected: exit code 0. Do not run `cargo fmt`.

- [ ] **Step 2: Run conflict-sensitive and tag-flow tests**

Run from `src/bcs`:

```bash
CARGO_INCREMENTAL=0 cargo test -p bcs-collaboration-runtime
CARGO_INCREMENTAL=0 cargo test -p bcs-message-flow --test contract_message_flow web_send_to_provider_with_session_id_uses_explicit_bcs_session_id
CARGO_INCREMENTAL=0 cargo test -p bcs-app-session --test v1_session_service create_session_inherits_parent_group_participants_without_request_roster
CARGO_INCREMENTAL=0 cargo test -p bcs --lib migrations::tests
python3 scripts/validate_openapi_contract.py --root api-contracts/v1
```

Expected: all selected Rust tests pass and all OpenAPI operations validate.

- [ ] **Step 3: Verify final scope**

Run:

```bash
git diff --check origin/dev...HEAD
git diff --name-only origin/dev...HEAD | rg '^src/frontend/'
git rev-list --left-right --count origin/dev...HEAD
```

Expected: `git diff --check` succeeds, the frontend search prints nothing, and the branch is not behind `origin/dev`.

### Task 4: Publish and restart CI monitoring

**Files:**
- No source files; GitHub PR #1218 metadata and branch only.

- [ ] **Step 1: Push with an explicit lease**

Resolve and validate the current remote branch SHA, then run:

```bash
git push --no-verify --force-with-lease origin codex/bcs-group-provider-tags
```

Expected: remote head equals local `HEAD` and PR #1218 becomes mergeable once GitHub recomputes it.

- [ ] **Step 2: Confirm CI started for the new head**

Query the PR head, check runs, and Actions runs through GitHub APIs. Expected: the new head SHA is visible and Unit Tests, E2E Tests, and Singlebox Coverage are queued or running.

- [ ] **Step 3: Reuse the existing heartbeat**

Update `avernet-pr-1218-ci` rather than creating a duplicate. It must poll every five minutes, follow the current PR head SHA, notify once at terminal state, and stop afterward.
