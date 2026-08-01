# Pre-push Merge Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pre-push module selection compare feature changes against a freshly fetched, configurable PR merge target.

**Architecture:** `.githooks/pre-push` owns remote-target configuration, refresh, and immutable SHA resolution. It passes a per-ref merge base and head SHA to the existing `scripts/ci/pre_push.sh` dispatcher, which remains responsible only for path-based module selection and gate execution.

**Tech Stack:** Bash, Git, Python standard-library `unittest` and temporary repositories.

## Global Constraints

- Default target is exactly `origin/dev`.
- Override precedence is environment, Git config, then default.
- Target values use `<remote>/<branch>` syntax.
- Fetch the target before calculating the merge base; do not silently use a stale ref.
- Calculate `git diff merge-base..feature`, never a direct target-to-feature diff.
- Fail closed on invalid, missing, or unrelated targets.
- Deletion-only pushes skip fetch and test dispatch.
- Preserve the existing module path patterns and gate commands.

---

### Task 1: Reproduce stale-target module over-selection

**Files:**
- Create: `scripts/ci/tests/test_pre_push_hook.py`
- Test: `scripts/ci/tests/test_pre_push_hook.py`

**Interfaces:**
- Consumes: `.githooks/pre-push <remote-name> <remote-url>` and Git's four-field stdin record.
- Produces: a temporary dispatcher that reports the hook-selected `--base`/`--head` diff paths.

- [ ] **Step 1: Build a temporary Git fixture**

Create a bare remote, a publisher clone, and a developer clone. Leave the
developer's `origin/dev` at baseline `A`, advance remote `dev` to Engine commit
`B`, create a local rebase target at `B` without updating `origin/dev`, and add
a BCS-only feature commit `C` on top.

- [ ] **Step 2: Execute the real hook through a stub dispatcher**

Copy `.githooks/pre-push` into the developer clone and create this dispatcher
contract:

```bash
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base) base="$2"; shift 2 ;;
    --head) head="$2"; shift 2 ;;
  esac
done
git diff --name-only "$base" "$head"
```

Pipe a feature branch push record into the hook and assert the output contains
`src/bcs/feature.txt` but not `src/engine/target.txt`.

- [ ] **Step 3: Run the regression test and verify RED**

Run:

```bash
python3 -m unittest scripts.ci.tests.test_pre_push_hook.PrePushHookTest.test_refreshes_stale_default_target_before_selecting_modules -v
```

Expected: FAIL because the current hook uses stale `origin/dev`; the captured
diff includes `src/engine/target.txt`.

---

### Task 2: Resolve and refresh the configured merge target

**Files:**
- Modify: `.githooks/pre-push`
- Test: `scripts/ci/tests/test_pre_push_hook.py`

**Interfaces:**
- Consumes: `AVERNET_PRE_PUSH_MERGE_TARGET`, `avernet.prePush.mergeTarget`, and default `origin/dev`.
- Produces: one immutable `target_sha` per hook run and one `base_sha` per pushed branch.

- [ ] **Step 1: Add target resolution**

Resolve configuration with:

```bash
merge_target="${AVERNET_PRE_PUSH_MERGE_TARGET:-$(git config --get avernet.prePush.mergeTarget 2>/dev/null || true)}"
merge_target="${merge_target:-origin/dev}"
```

Split on the first slash, validate the remote and `refs/heads/<branch>`, fetch
the branch into `refs/remotes/<remote>/<branch>`, and resolve it with
`git rev-parse --verify '<ref>^{commit}'`.

- [ ] **Step 2: Replace stale and fallback base selection**

For every non-deletion local SHA, calculate:

```bash
base="$(git merge-base "$local_sha" "$target_sha")"
```

Return non-zero with a clear error if refresh, resolution, or merge-base fails.
Do not use `remote_sha` or the root commit as a fallback.

- [ ] **Step 3: Verify GREEN for the stale-target regression**

Run the single test from Task 1. Expected: PASS and the dispatcher receives a
range containing only `src/bcs/feature.txt`.

- [ ] **Step 4: Add configuration and error tests**

Add tests proving:

- `AVERNET_PRE_PUSH_MERGE_TARGET` overrides Git config;
- Git config overrides `origin/dev`;
- a missing configured branch rejects the push without dispatch;
- a deletion-only push succeeds without fetching or dispatching.

- [ ] **Step 5: Run the focused suite**

Run:

```bash
python3 -m unittest scripts.ci.tests.test_pre_push_hook -v
```

Expected: all pre-push hook tests pass.

---

### Task 3: Document target selection for contributors and agents

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Test: `scripts/ci/tests/test_pre_push_hook.py`

**Interfaces:**
- Consumes: the configuration and behavior implemented in Task 2.
- Produces: repository-wide instructions with copyable setup and override commands.

- [ ] **Step 1: Add the detailed AGENTS.md contract**

Document per-worktree installation, default target, both override mechanisms,
the `<remote>/<branch>` format, fetch-and-merge-base behavior, fail-closed
errors, and the existing Backend/BaaS/Engine/BCS/Frontend/singlebox path map.

- [ ] **Step 2: Add the concise CLAUDE.md reminder**

Reference the `AGENTS.md` pre-push section and include the persistent and
one-command override names without duplicating the full path table.

- [ ] **Step 3: Assert documentation contains the public contract**

Extend `test_pre_push_hook.py` to read both files and assert the default,
environment variable, Git config key, and merge-base rule are present.

- [ ] **Step 4: Run the focused suite**

Run `python3 -m unittest scripts.ci.tests.test_pre_push_hook -v`. Expected: all
tests pass.

---

### Task 4: Verify, commit, and publish the combined CI fix

**Files:**
- Verify: `.github/workflows/unit-tests.yml`
- Verify: `.githooks/pre-push`
- Verify: `scripts/ci/tests/`
- Verify: `AGENTS.md`
- Verify: `CLAUDE.md`

**Interfaces:**
- Consumes: the earlier PR Actions diff commit and Tasks 1-3.
- Produces: one branch and draft PR targeting `dev`.

- [ ] **Step 1: Run all lightweight CI-script tests**

```bash
python3 -m unittest discover -s scripts/ci/tests -p 'test_*.py' -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run shell and diff hygiene checks**

```bash
bash -n .githooks/pre-push scripts/ci/pre_push.sh scripts/install_git_hooks.sh
git diff --check origin/dev...HEAD
```

Expected: both commands exit zero.

- [ ] **Step 3: Re-run the exact stale-target scenario**

Run the focused stale-target unittest by its full test name. Expected: only
the BCS path is dispatched after the target fetch.

- [ ] **Step 4: Review scope and commit**

Inspect `git diff --stat origin/dev...HEAD` and `git diff origin/dev...HEAD`,
stage only the intended hook, tests, and docs, then commit with a focused CI
message.

- [ ] **Step 5: Push and open a draft PR**

Push `codex/fix-pre-push-target-diff` to `origin` and create a draft PR against
`dev`. The PR body must explain both root causes, both fixes, TDD red/green
evidence, and the verification commands.
