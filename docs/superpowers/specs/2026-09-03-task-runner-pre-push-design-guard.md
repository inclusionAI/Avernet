# TaskRunner Pull-Request Design Guard

- **Status:** Implemented
- **Phase:** 1 — GitHub PR required check
- **Target branch:** `dev`
- **Owner identity:** GitHub login `regrecall`

The filename is retained from the original pre-push prototype so existing links
remain valid. This document supersedes that prototype: the guard no longer runs
from `scripts/ci/pre_push.sh` and does not intercept personal branch pushes.

## 1. Decision Summary

The repository protects selected structural surfaces of `TaskRunner` when a
pull request targets `dev`. GitHub runs a deterministic AST comparison as the
`TaskRunner design guard` status check. Repository administrators must mark
that status as required for `dev`; a workflow file alone cannot disable the
Merge button.

The workflow uses `pull_request_target` with `contents: read` and
`statuses: write`. The write permission is used only to publish the
`TaskRunner design guard` commit status on the pull request head SHA; it does
not grant permission to change code, approve, or merge a pull request. The
workflow checks out the pull request's trusted base SHA and fetches the head
only as a Git object. It never checks out, imports, or executes pull-request
code. The checker, manifest, and submitter policy therefore come from the
already accepted `dev` revision and cannot be weakened by the pull request
being evaluated.

## 2. Trigger Boundary

The workflow responds only to these `pull_request_target` activities when the
base branch is `dev`:

- `opened`
- `synchronize`
- `reopened`
- `ready_for_review`

There is no `push`, `workflow_dispatch`, or path filter. Draft pull requests
run the lightweight check immediately and rerun after each pushed commit.
Personal branch pushes do not trigger the guard.

Direct pushes to `dev` are outside this policy. Contributors with direct-push
permission can bypass the PR guard; this is an explicitly accepted limitation.

## 3. Identity Policy

The pull request author's GitHub login is the identity authority. Git commit
names, author emails, committer emails, and local Git configuration are not
used because contributors can set them locally.

Decision order:

1. `regrecall` bypasses this guard.
2. A non-owner modifying a guard control file is rejected.
3. A non-owner absent from the guarded-submitter policy skips the structural
   comparison and passes.
4. A guarded submitter receives the full TaskRunner structural comparison.

Login comparison is case-insensitive.

The guarded-submitter policy is
`docs/arch/task-design-guard-submitters.json`. Its schema is:

```json
{
  "version": 1,
  "guarded_submitters": [
    "github-login"
  ]
}
```

The list must be non-empty, contain valid GitHub logins, and contain no
case-insensitive duplicates. Only a pull request authored by `regrecall` may
change it. A policy update takes effect only after it is merged into `dev`.

## 4. Guard Control Files

A non-owner pull request is rejected with `TRG900` if it changes any of:

- `.github/workflows/task-design-guard.yml`
- `docs/arch/task-design-guard-submitters.json`
- `docs/superpowers/specs/2026-09-03-task-runner-pre-push-design-guard.md`
- `scripts/ci/task_design_guard.json`
- `scripts/ci/task_design_guard.py`

This check runs before submitter-list membership, so an unlisted account cannot
skip the guard and alter its controls in the same pull request.

## 5. Protected Surface

The protected-surface manifest remains
`scripts/ci/task_design_guard.json`. The initial policy protects:

```text
agentclaw.community.core.task.task_runner.task_runner.TaskRunner
  __init__
  set_delivery
  start_run
```

The manifest identifies symbols only. Parameter lists, annotations,
decorators, fields, and source text are extracted from the trusted base and PR
head revisions.

## 6. Structural Rules

For `TaskRunner`, reject a guarded submitter's pull request when the head:

- removes or renames the class;
- changes its base classes or class decorators;
- adds, removes, or changes a directly declared class field; or
- adds any directly declared method, including private and dunder methods.

For `__init__`, `set_delivery`, and `start_run`, reject when the head:

- removes or renames the method;
- changes `def` to `async def` or the reverse;
- changes decorators;
- changes parameter names, order, kinds, defaults, or annotations; or
- changes the return annotation.

Method bodies and docstrings are excluded from fingerprints. Comments,
whitespace, local variables, control flow, and called functions may change.

The checker uses Python's standard-library `ast` module and never imports the
Backend module. Normalized AST rendering excludes source locations so
formatting movement does not create a violation.

## 7. Result Contract

The checker has three exit statuses:

| Status | Meaning | Workflow result |
| --- | --- | --- |
| `0` | passed, owner bypass, or unlisted-author skip | pass |
| `1` | confirmed structural violation, control-file violation, or PR-head parse failure | fail |
| `2` | trusted policy, checker, or recoverable Git/environment failure | warn and pass |

A syntax error introduced in the protected PR-head source is `TRG901`, not an
internal failure. This prevents a guarded submitter from bypassing comparison
by making the file unparsable.

Exit status `2` implements the approved fail-open policy. The workflow writes
an Actions warning and a prominent warning to `$GITHUB_STEP_SUMMARY`, then
publishes a successful status whose description identifies the degraded run.
The workflow first publishes `pending`, then replaces it with `success` or
`failure` after evaluation. It publishes the status explicitly on
`github.event.pull_request.head.sha`; the `pull_request_target` workflow run
itself is associated with the trusted base revision and must not be selected as
the required check. If GitHub never schedules the workflow, no repository code
can apply fail-open; the required status remains missing until GitHub recovers
or an administrator intervenes.

## 8. Required GitHub Configuration

After the bootstrap pull request is merged and the workflow has completed at
least once, a repository administrator must configure the `dev` ruleset:

1. Open **Settings → Rules → Rulesets** (or the repository's branch protection
   page).
2. Create or edit the rule targeting `dev`.
3. Enable **Require status checks to pass**.
4. Add the exact status check **TaskRunner design guard**.
5. If GitHub offers an expected-source selector, choose **GitHub Actions**.
6. Enable **Require branches to be up to date before merging**.
7. Leave direct-push policy unchanged, per the accepted limitation.

Select the custom commit status named `TaskRunner design guard`, not the
workflow job named `Publish TaskRunner design guard`.

The first workflow PR cannot protect itself because its trusted base does not
yet contain the workflow. It must be authored and manually verified by
`regrecall`. Required-check configuration happens immediately after that first
successful run.

## 9. Diagnostics

Allowed structural change:

```text
TaskRunner design guard passed: protected structure is unchanged
```

Unlisted author:

```text
TaskRunner design guard skipped: @user is not in the guarded submitter policy
```

Blocked change:

```text
TaskRunner design guard failed
TRG104 agentclaw.community.core.task.task_runner.task_runner.TaskRunner.start_run
  parameter structure changed: ...
TaskRunner is a protected design surface; revert the structural change before merging into dev.
```

There is no waiver flow in Phase 1.

## 10. Verification

Tests must cover:

- all existing class and protected-method AST rules;
- method-body changes passing;
- `regrecall` bypass;
- guarded, unlisted, and case-insensitive submitter behavior;
- non-owner control-file changes failing before membership checks;
- trusted manifest and submitter policy being read from the base revision;
- malformed PR-head source returning `1`;
- trusted configuration or base-source failures returning `2`;
- uncommitted working-tree changes being ignored;
- removal of the pre-push invocation; and
- workflow trigger, least-privilege permission, trusted-checkout constraints,
  and explicit publication to the pull request head SHA.

## 11. Architecture Alignment

This guard supports Architecture Constitution Rules 16 and 17 by distinguishing
a selected constrained surface from flexible implementation details and by
making structural changes visible before they enter `dev`. It remains a narrow
governance policy for a concrete class; it does not reclassify `TaskRunner` as
a Service API or Plugin API and does not replace conformance tests.
