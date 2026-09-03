# TaskRunner Pre-Push Design Guard

- **Status:** Implemented
- **Phase:** 1 — local developer guard
- **Date:** 2026-09-03
- **Target baseline:** `origin/dev`, unless the repository's existing pre-push
  merge-target configuration selects another `<remote>/<branch>`
- **Protected owner identity:** local Git email `regrecall@gmail.com`

## 1. Decision Summary

Add a small, deterministic AST guard to the repository's existing pre-push
pipeline. The guard protects selected structural surfaces of `TaskRunner` and
rejects a normal `git push` when a committed change modifies those surfaces.

Phase 1 is deliberately a local development aid, not a security boundary or a
server CI gate. Git hooks are installed per worktree and can be bypassed with
`git push --no-verify`. The configured owner email is also locally mutable.
Those limitations are accepted for this phase and must remain explicit in user
documentation and diagnostics.

Phase 1 does not call Codex, inspect pull requests, validate GitHub approvals,
or support waivers. Those capabilities belong to a later server-side phase.

## 2. Problem

The Backend Task framework contains structural seams that downstream callers
and integrations already rely on. In particular, `TaskRunner` is the execution
boundary used to inject delivery behavior and start a batch of dispatched task
nodes. Casual changes to its class shape or selected method signatures can
propagate into the orchestration engine, dependency wiring, test doubles, and
production integrations.

The repository already has a pre-push pipeline, but it currently selects broad
module gates from changed paths. It does not distinguish a flexible method-body
refactor from a structural change to an explicitly protected class or method.

The first increment needs a fast local signal at push time without expanding
the existing heavy-test default or requiring a model, network service, or new
developer credential.

## 3. Goals

1. Reject a normal push when a committed change modifies the protected
   `TaskRunner` class structure or one of its protected method interfaces.
2. Permit implementation-only edits inside method bodies.
3. Reuse the existing immutable merge-base and head SHAs supplied by the
   pre-push hook.
4. Run in both lint-only and full-CI pre-push modes.
5. Produce deterministic, English diagnostics that identify the protected
   symbol and the structural difference.
6. Allow the configured local owner identity to skip only this new guard.
7. Fail open on guard configuration, extraction, or parser errors, while making
   the degraded result visible as a warning.

## 4. Non-Goals

- An unbypassable repository policy.
- Protection against a contributor changing their local Git email.
- Protection against `git push --no-verify` or an uninstalled hook.
- Pull-request review, GitHub branch protection, or required status checks.
- Codex or another model participating in the Phase 1 decision.
- Waiver creation, approval, expiry, or verification.
- Detecting semantic behavior changes inside method bodies.
- Discovering omitted protected classes or methods automatically.
- Reviewing other files in the Backend Task package.
- Self-protection against a local contributor editing the guard, its manifest,
  or the hook before pushing.

## 5. Existing Pre-Push Contract

The implementation must extend, rather than replace, the current flow:

1. `.githooks/pre-push` resolves the configured merge target. Its precedence
   remains `AVERNET_PRE_PUSH_MERGE_TARGET`, Git configuration, then
   `origin/dev`.
2. The hook fetches the target and resolves it to an immutable commit.
3. For each non-deletion ref, it computes `git merge-base <local> <target>`.
4. It calls `scripts/ci/pre_push.sh --base <base> --head <local>`.
5. The dispatcher continues to own the existing SAST, test, coverage, and
   singlebox selection behavior.

The TaskRunner guard consumes the already resolved `base` and `head`. It must
not independently select a different comparison range or fall back to the root
commit.

This means every pushed branch is compared with the intended merge target, not
only a direct push to `dev`.

## 6. Protected-Surface Manifest

The machine-readable source of truth should be a versioned JSON document. JSON
keeps the guard on Python's standard library and avoids adding a YAML parser to
the default pre-push path. The
initial content is logically equivalent to:

```json
{
  "version": 1,
  "packages": [
    {
      "package": "agentclaw.community.core.task.task_runner",
      "classes": [
        {
          "class": "agentclaw.community.core.task.task_runner.task_runner.TaskRunner",
          "methods": ["__init__", "set_delivery", "start_run"]
        }
      ]
    }
  ]
}
```

The manifest identifies symbols only. It does not duplicate parameter lists,
return annotations, decorators, or source text. Those facts are extracted from
the base and head revisions.

Phase 1 does not enforce authorship of manifest changes. Repository-level
ownership enforcement is deferred because the local identity signal is not a
trustworthy authorization mechanism.

## 7. Structural Rules

### 7.1 Protected class

For `TaskRunner`, reject the push when the head revision:

- removes or renames the class;
- changes its base classes;
- changes its class decorators;
- adds, removes, or changes a class-level assigned or annotated field; or
- adds any directly declared method, including private and dunder methods.

Changing only the body of an existing, non-protected method is allowed.
Deleting an unprotected method is not independently guarded in Phase 1. A
rename of an unprotected method is rejected because it introduces a new method
name on the protected class.

"Class-level field" means an `Assign` or `AnnAssign` node directly inside the
class body. Instance attributes assigned inside `__init__` or another method
are method-body implementation details and are not compared in Phase 1.

### 7.2 Protected methods

For `__init__`, `set_delivery`, and `start_run`, reject the push when the head
revision:

- removes or renames the method;
- changes `def` to `async def` or the reverse;
- changes method decorators;
- changes parameter names, order, positional/keyword kind, variadic kind,
  default values, or annotations; or
- changes the return annotation.

The method fingerprint excludes the executable body and docstring. Comments,
whitespace, formatting, local variables, control flow, called functions, and
other body-only changes are allowed by this guard.

### 7.3 Missing symbols

Missing symbols have asymmetric handling:

- If a protected symbol is absent from the base revision, emit a configuration
  warning and pass this guard. This is a fail-open response to a stale manifest.
- If the symbol exists in the base revision but is absent from the head
  revision, treat it as a protected deletion or rename and reject the push.

## 8. AST Comparison Model

The guard should use Python's standard-library `ast` module and require no new
third-party dependency.

For each protected source module:

1. Read the base and head blobs with `git show <sha>:<path>`.
2. Parse both blobs with `ast.parse`.
3. Resolve the configured top-level class and its directly declared methods.
4. Build normalized structural records with location metadata removed.
5. Compare the records according to Section 7.
6. Report all violations in one run rather than stopping at the first one.

Normalized AST rendering must exclude `lineno`, `col_offset`, `end_lineno`, and
`end_col_offset` so formatting-only movement does not change a fingerprint.
Default values, annotations, bases, fields, and decorators retain their AST
shape because they are protected structure.

The guard must not import or execute either revision of the Backend module.
Static parsing avoids import-time side effects and missing runtime dependencies.

## 9. Owner Bypass

Before running the TaskRunner comparison, read:

```bash
git config user.email
```

If and only if its exact output is `regrecall@gmail.com`, skip the TaskRunner
guard and produce no TaskRunner-specific output. The bypass is intentionally
silent.

The bypass must not exit the complete pre-push dispatcher. Existing Backend
SAST, unit-test, coverage, singlebox, and other module gates continue normally.

This is a cooperative convenience, not authentication. Any local user can
change the value, and the design makes no stronger security claim.

## 10. Failure Semantics

The guard has two result categories:

### Policy violation

A successfully extracted and compared protected structure differs according to
Section 7. The guard exits non-zero. `scripts/ci/pre_push.sh` treats the command
as required and prevents the normal push.

The error must include:

- the source path;
- the fully qualified class and method, where applicable;
- a stable rule identifier;
- a concise old-versus-new structural summary; and
- a reminder that `TaskRunner` is a protected design surface.

### Guard failure

Manifest loading, Git blob extraction, JSON validation, AST parsing, or an
unexpected internal exception fails. The guard writes an English warning to
stderr and exits zero, allowing the push to continue.

A syntax error may still be rejected independently by the repository's
existing Python SAST/lint gate. The TaskRunner guard must not change that
behavior.

## 11. Integration Point

`scripts/ci/pre_push.sh` should invoke the guard as an always-on lightweight
required step, passing its existing `--base` and `--head` values. The invocation
must not be placed behind `run_heavy` and must not depend on
`OCB_PRE_PUSH_RUN_CI=1`.

The guard may return immediately when neither the protected source file nor the
manifest is present in the committed diff. Calling it unconditionally keeps
dispatch behavior simple while preserving a cheap fast path internally.

The new invocation must not replace or weaken the existing Backend gate. A
Backend change continues through SAST and, when selected, the existing heavy
checks after the TaskRunner decision.

### 11.1 Expected implementation files

The later implementation should remain a small CI-tooling change:

- add `scripts/ci/task_design_guard.py` for manifest loading, Git blob
  extraction, AST normalization, comparison, diagnostics, and exit semantics;
- add `scripts/ci/task_design_guard.json` for the protected-surface manifest;
- add `scripts/ci/tests/test_task_design_guard.py` for comparator and command
  behavior;
- update `scripts/ci/pre_push.sh` to invoke the guard with its resolved base and
  head; and
- extend the existing pre-push dispatcher or hook tests only where integration
  behavior needs coverage.

No production Backend package should import the guard. It is repository tooling
and must remain outside `agentclaw` runtime code.

## 12. User Experience

Allowed change:

```text
TaskRunner design guard passed: protected structure is unchanged
```

Blocked change:

```text
TaskRunner design guard failed
TRG003 agentclaw.community.core.task.task_runner.task_runner.TaskRunner.start_run
parameter structure changed: (toDoTaskList: list[TaskNode]) -> (nodes: Sequence[TaskNode])
TaskRunner is a protected design surface; revert the structural change before pushing.
```

Guard failure:

```text
warning: TaskRunner design guard could not parse the head revision; guard skipped
```

No waiver instructions should appear in Phase 1 because Phase 1 has no waiver
mechanism.

## 13. Test Strategy

### 13.1 Comparator unit tests

Use small source fixtures to prove that the normalized comparison:

- allows method-body, local-variable, comment, whitespace, and formatting
  changes;
- rejects a new public, private, or dunder method;
- rejects class removal or rename;
- rejects base-class and class-decorator changes;
- rejects class-level field name, annotation, or value changes;
- rejects protected-method removal or rename;
- rejects parameter name, order, kind, default, and annotation changes;
- rejects return-annotation, sync/async, and method-decorator changes;
- warns and passes when the protected class or method is already absent from
  the base revision; and
- rejects a symbol present in base but absent in head.

### 13.2 Command tests

Exercise the command against temporary Git repositories to prove that it:

- compares the supplied immutable base and head revisions;
- does not inspect uncommitted working-tree changes;
- reports all detected violations;
- exits non-zero only for confirmed policy violations;
- warns and exits zero for invalid manifest, Git extraction, and AST failures;
  and
- silently returns success when `git config user.email` is exactly
  `regrecall@gmail.com`.

### 13.3 Pre-push integration tests

Extend the existing pre-push dispatcher tests to prove that:

- the TaskRunner guard runs in lint-only mode;
- the TaskRunner guard runs in full-CI mode;
- an owner bypass skips only the new guard;
- existing Backend SAST dispatch remains active after an owner bypass;
- a rejected guard result prevents the push; and
- unrelated module dispatch behavior is unchanged.

## 14. Acceptance Criteria

Phase 1 is complete when:

- the manifest identifies exactly `TaskRunner`, `__init__`, `set_delivery`, and
  `start_run`;
- every structural rule in Section 7 has a passing regression test;
- a body-only edit to any protected method can be pushed normally;
- a protected structural edit is rejected in the default lint-only pre-push
  mode;
- `regrecall@gmail.com` silently bypasses only the TaskRunner guard;
- guard failures warn and allow the push;
- the existing hook and dispatcher tests remain green; and
- the hook installation documentation still accurately states that hooks are
  installed per worktree and may be skipped by Git.

## 15. Architecture Alignment and Limitations

This guard supports Architecture Constitution Rules 16 and 17 by distinguishing
a selected constrained surface from flexible implementation details and by
making structural changes visible before push. It also follows the existing
pre-push merge-base contract instead of inventing a conflicting change range.

It does not satisfy the constitution's CI, conformance-test, structural-review,
or waiver requirements by itself. Because it is local, bypassable, and
fail-open on internal errors, it must not be presented as proof that a pull
request is architecture-compliant.

Protecting a concrete class is an intentionally narrow Phase 1 policy. It does
not reclassify `TaskRunner` as a Service API or Plugin API, and it does not
replace the existing `DeliveryPort` or Task service contracts.

## 16. Deferred Phase 2

A later design may move enforcement to pull requests and add:

- a required GitHub status check for PRs targeting `dev`;
- Codex review through the official Codex GitHub Action;
- a protected, owner-maintained contract manifest;
- GitHub identity and approval verification for `@regrecall`;
- structured, head-bound waivers;
- sticky PR review comments and source annotations;
- fail-closed configuration validation; and
- protection against local hook bypass and guard self-modification.

Phase 2 must be reviewed as a separate security and governance boundary. It
must not silently inherit the cooperative identity or fail-open assumptions of
Phase 1.
