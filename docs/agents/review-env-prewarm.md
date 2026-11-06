# Review environment prewarm

Frontend code review spends most of its wall time before it looks at a single
line of the diff: a reviewer needs `apps/frontend-nextgen` installed and
`max setup` generated before `check:open-core`, `typecheck` or `jest` can run.
`npm ci` from an empty npm cache costs minutes on a fresh machine, and it used
to happen only after a pull request arrived.

`devops/review/prewarm_review_env.sh` moves that cost ahead of the review.

## What it prepares

One warm copy of `apps/frontend-nextgen/node_modules` in a cache directory
outside the repository, keyed by the `package-lock.json` contents and the Node
major/minor series:

```text
${PREWARM_CACHE_DIR:-${XDG_CACHE_HOME:-~/.cache}/ocb-review-env}/<key>/apps/frontend-nextgen/node_modules
```

The key covers both inputs that can change the install: a lockfile change (a
dependency bump on the pull request) or a Node upgrade (a different native
addon ABI). Editing application code never invalidates the warm tree.

The install happens in a scratch checkout laid out at the repository-relative
path. That layout matters: `max setup` resolves `@avernet/a2a-chat-client` as
`../../packages/a2a-chat-client`, so installing `apps/frontend-nextgen` in
isolation fails its `postinstall`. The prewarm reproduces the layout instead of
special-casing the dependency.

## Usage

Prepare the warm tree once, for the commit you will review (or for the
integration branch you review from):

```bash
devops/review/prewarm_review_env.sh
```

A review worktree, or any worktree that has no own `node_modules`, then links
the warm tree and generates its own `.umi`:

```bash
devops/review/prewarm_review_env.sh --link-only
```

`--link-only` never deletes anything. It refuses to replace an existing real
`node_modules` directory, and fails with a clear message when no warm tree
exists for the current key. Generated `.umi` output stays per-worktree because
`max setup` rewrites it from the worktree's own sources.

Other modes: `--print-key` prints the cache key, `--path` prints the warm tree
path. `PREWARM_CACHE_DIR` overrides the cache root.

## Measured effect

Measured on this repository, `apps/frontend-nextgen`, Node 26.0.0:

| Path | Wall time |
| --- | --- |
| `npm ci` from an empty npm cache (cold machine) | 4m 6s |
| `prewarm_review_env.sh` with a populated npm cache | 42s |
| `--link-only` against a warm tree | 10s |

The warm path was verified end to end in a clean worktree with no
`node_modules` of its own: `check:open-core` passed, `npm run typecheck`
passed, and `npx jest test/adapters` reported 59 passed.

Most of the remaining cost is `npm ci` itself; the warm tree indexes the
packages once so each later worktree links instead of installing.

## Caveats

- The warm tree is shared. Packages that write into their own directory at
  runtime (caches such as `node_modules/.cache`) are shared too; the umi logger
  under that path is append-only diagnostics and is not read by gates.
- Reviewers whose only need is a diff can skip this entirely. The prewarm helps
  when a review actually runs the frontend gates.
- The script covers `apps/frontend-nextgen`. A review that touches another
  module (`apps/bcs`, `apps/backend`, ...) follows that module's own setup; the
  prewarm is not a general-purpose stack warmup.