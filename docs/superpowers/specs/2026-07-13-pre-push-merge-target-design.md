# Pre-push Merge Target Design

## Goal

Make local pre-push module selection use the latest intended pull-request target,
so target-branch commits incorporated by a rebase or merge do not trigger
unrelated module unit tests.

## Problem

The hook currently computes a merge base against the cached `origin/dev` ref.
If a feature branch already contains a newer `dev` commit while the cached
remote-tracking ref is older, the resulting diff includes both the feature
change and intervening `dev` changes. Path-based dispatch can therefore run an
unrelated module, such as Engine for a BCS-only feature.

Comparing `origin/dev` directly with the feature head is not a valid fix. When
`dev` has advanced and the feature has not rebased, a direct tree-to-tree diff
also includes target-only paths. The target must be used to find the merge
base, and the feature delta must be calculated from that merge base.

## Configuration Contract

The merge target is a remote branch in `<remote>/<branch>` form.

Resolution precedence is:

1. `AVERNET_PRE_PUSH_MERGE_TARGET` for a one-command override.
2. `git config avernet.prePush.mergeTarget` for a worktree or repository
   setting.
3. `origin/dev` as the repository default.

Examples:

```bash
git config --worktree avernet.prePush.mergeTarget upstream/release/2026-07
AVERNET_PRE_PUSH_MERGE_TARGET=origin/main git push
```

## Data Flow

For the first non-deletion ref in a push, `.githooks/pre-push` will:

1. Validate and split the configured target into remote and branch names.
2. Fetch that remote branch into its remote-tracking ref.
3. Resolve the fetched ref to an immutable target commit SHA once per hook run.
4. Compute `base_sha = git merge-base(target_sha, local_sha)` for every pushed
   branch.
5. Pass immutable `base_sha` and `local_sha` values to
   `scripts/ci/pre_push.sh`.
6. Let the existing dispatcher select modules from
   `git diff --name-only base_sha local_sha`.

The hook logs the configured target, resolved target SHA, merge base, and head
SHA so unexpected module selection can be diagnosed from push output.

## Failure Behavior

The hook fails closed when the target format is invalid, the remote or branch
cannot be fetched, the fetched ref is not a commit, or no merge base exists.
It must not silently fall back to the pushed branch's old remote SHA or the
repository root, because those ranges do not model the intended PR delta.

Deletion-only pushes continue to skip code gates and do not need to fetch the
target.

## Documentation

`AGENTS.md` is the source of truth for the target configuration, diff
algorithm, module path triggers, per-worktree hook installation, and failure
behavior. `CLAUDE.md` contains the concise default and override commands and
links agents back to the detailed `AGENTS.md` section.

## Tests

An integration-style Python `unittest` suite will execute the real hook in
temporary Git repositories with a lightweight dispatcher stub. It will cover:

- a feature rebased onto a target commit while its cached target ref is stale;
- environment override precedence;
- Git config selection when no environment override exists;
- invalid or missing targets failing before module dispatch;
- deletion-only pushes skipping target fetch and dispatch.

The stale-target test must fail against the current hook by exposing both BCS
and Engine paths, then pass after the hook fetches the latest target and exposes
only the BCS path.
