# Running the exported next-generation frontend in Singlebox

Singlebox defaults to `apps/frontend` (legacy). Updating `apps/frontend-nextgen`
by itself does not change the running UI. Select `FRONTEND_VARIANT=nextgen`
for **every** setup/start/stop/status invocation, or persist it in the checkout's
untracked `singlebox/.env.local`. Do not copy secrets between worktrees.

## Full public product (`FRONTEND_FORM=open`) and the collab assembly

`apps/frontend-nextgen` keeps the complete public source and its default Open
routes. The 2026-11-03 collab-form work adds a build-time `FRONTEND_FORM` switch
inside nextgen (`open` by default; `collab` selects a collaboration-only page
assembly from the same source). Singlebox regression therefore pins the full
product explicitly:

```bash
FRONTEND_VARIANT=nextgen FRONTEND_FORM=open bash singlebox/singlebox.sh start all
```

The collab assembly builds from the same public source via `npm run
build:collab` / `npm run dev:collab` for local checks; it is not a Singlebox
frontend entry. The independent internal collaboration site is a separate
OCB-nextgen artifact (`apps/frontend-nextgen/specs/2026-11-03-collab-form/export-plan.md`,
Tasks 17–20): its exporter writes a new output directory and never replaces
`apps/frontend-nextgen`, prunes this source tree, or involves legacy
`apps/frontend`. It never becomes the public Singlebox source directory —
Singlebox keeps serving the complete public product above. The full-source
startup evidence from export-plan Task 20 is recorded in the
"Full-source validation record (2026-11-04)" below; the earlier
"Local validation record (2026-09-10)" was the pre-collab-form evidence.

## FRONTEND_VARIANT=teamclaw: the external internal-UI checkout

`apps/frontend-nextgen` is the exported open-core subset of the TeamClaw UI —
internal-only capabilities are deliberately stripped by the exporter. To run
the **full internal frontend** (the product UI under development), use the
`teamclaw` variant, which serves an external checkout through the same
Gateway composition:

```bash
# singlebox/.env.local of the Avernet checkout:
FRONTEND_VARIANT=teamclaw
TEAMCLAW_DIR=~/IdeaProjects/teamClawPre/teamclaw   # path to the internal checkout
# TEAMCLAW_FRONTEND_AUTOUPDATE=0                    # default 1; see below
```

Then `bash singlebox/singlebox.sh start frontend` (or `start all`) will:

1. **Auto-update the checkout**: `git fetch origin`, then fast-forward the
   current branch to its upstream — only when the tree is clean; a dirty or
   detached checkout is left untouched with a warning naming the exact
   `git merge --ff-only` command to run yourself. `TEAMCLAW_FRONTEND_AUTOUPDATE=0`
   fetches and reports only. Updating never blocks startup.
2. Install dependencies when missing or stale (`npm install
   --legacy-peer-deps` — the internal graph is not lockfile-pinned and carries
   sibling peer ranges that a plain install refuses).
3. Start the dev server with the same Gateway defaults as nextgen
   (`TEAMCLAW_GW_BASE`/`ADMIN`/private-chat/clawweb/aixharness → the Singlebox
   Gateway; `TEAMCLAW_DEV_USER=001` matching the `/_dev/login` identity), then
   run it through the standard readiness check (root element + `/umi.js`).

The checkout's branch is yours to pick (the sprint branch is the usual line);
auto-update follows whatever branch the checkout is on. The internal-only
planes (private chat, clawweb, aix harness) have no singlebox counterpart —
their panels fail visibly at the gateway instead of dangling on a placeholder.

### Pull the checkout on demand (`frontend-pull`)

The startup sync is best-effort and never blocks. Its operator-facing twin is
`bash singlebox/singlebox.sh frontend-pull`: it refuses loudly instead of
warning — a dirty tree is never stashed or overwritten, a detached HEAD is
refused, and a branch without an upstream is a hard refusal (the command
never guesses a ref to pull toward). The advance is ff-only toward the
checkout's own upstream; if the branches diverged, resolve manually in the
checkout (`git -C "$TEAMCLAW_DIR" checkout <branch>` also re-arms the
upstream-based startup sync). It re-runs the dependency install afterwards
with the same contract as startup (`OCB_SKIP_FRONTEND_INSTALL=1` skips; a
failed install warns but keeps the pull green).

## Sprint rollover (iteration switch)

When the sprint moves to a new `sprint_teamclaw_*` branch, record the switch —
the tool validates the branch exists on the checkout's remote (ls-remote,
anchored under `refs/heads/`) before any write, and never touches the checkout
itself:

```bash
./singlebox/apps/frontend/frontend_sprint_branch.sh --list          # declared branch + remote sprint heads
./singlebox/apps/frontend/frontend_sprint_branch.sh sprint_teamclaw_S...
git -C "$TEAMCLAW_DIR" fetch && git -C "$TEAMCLAW_DIR" checkout sprint_teamclaw_S...
./singlebox/singlebox.sh frontend-pull
```

Avernet has no submodule to carry the tracked branch in-tree, so
`frontend_sprint_branch.sh` records it in a per-operator state file
(`~/.config/teamclaw-frontend/branch`; `FRONTEND_SPRINT_CONFIG` overrides the
location, and a `FRONTEND_SPRINT_BRANCH` env var overrides the file until
unset). The remote it validates against is the checkout's own origin — the
same remote `frontend-pull` fetches. The two manual steps are the operator's
decision points: the fetch + checkout switches the external checkout onto the
new sprint branch, and `frontend-pull` advances it (re-running the dependency
install). ff-only caveat: if the new sprint branch does not contain the old
one's tip (branches diverged), `frontend-pull` refuses rather than guessing —
the `git -C "$TEAMCLAW_DIR" checkout <new-sprint>` above is the resolution,
which also re-arms the upstream-based startup auto-update.

## Sync the source, not an unrelated checkout

The upstream TeamClaw repository owns the export. Use its clean, pinned source
worktree and its existing exporter, not a recursive copy of internal source.
With `SOURCE` set to that worktree and `TARGET` to a clean Avernet worktree:

```bash
# Fetch the intended upstream branch in the source repository first, then
# create a detached worktree at its fetched SHA. Do not switch a dirty checkout.
git -C "$SOURCE" rev-parse HEAD
git -C "$SOURCE" status --short
(cd "$SOURCE" && npm run export:avernet -- --avernet-dir "$TARGET")
git -C "$TARGET" diff --stat -- apps/frontend-nextgen
cat "$TARGET/apps/frontend-nextgen/OPEN_CORE_MANIFEST.json"
```

Use Node 22 and public npm dependencies for the export. The exporter runs source
isolation, install, typecheck, lint, tests, build and pack checks before rsync.
It rejects an unclean source or target frontend directory. Do not weaken that
guard or discard local changes: review and commit your previous export, or use
a new clean target worktree before the next export. Inspect the diff for
Avernet-only fixes that must first be reconciled upstream. `sourceCommit` in
the manifest is the authoritative upstream revision, not the Avernet branch
name. Internal-only capabilities are intentionally excluded.

## Start / update / roll back

From the target Avernet root:

```bash
# Frontend only; existing backend services must be available independently.
HOST=127.0.0.1 FRONTEND_VARIANT=nextgen bash singlebox/singlebox.sh start frontend

# Full local stack (creates local runtime and bots). In nextgen mode the all
# group includes Gateway; legacy startup order remains unchanged.
FRONTEND_VARIANT=nextgen bash singlebox/singlebox.sh start all

# After a validated new export, rebuild the dev-server module graph.
FRONTEND_VARIANT=nextgen bash singlebox/singlebox.sh restart frontend

# Stop the selected UI BEFORE selecting another one: foreign/other-checkout
# listeners are deliberately not killed by the frontend launcher. `stop all`
# always includes the Gateway regardless of the current variant (a stack
# started as nextgen must stay stoppable from a shell without FRONTEND_VARIANT
# set), so only the per-service stop needs the selected variant spelled out.
FRONTEND_VARIANT=nextgen bash singlebox/singlebox.sh stop frontend
FRONTEND_VARIANT=legacy bash singlebox/singlebox.sh start frontend
```

Gateway stop shares the same safety contract: `stop gateway` / `stop all` only
kills processes it can verify against the Gateway directory (pidfile, port
listener, command pattern — each cwd-checked), and if `GATEWAY_PORT` is still
held by a process outside the checkout after that, the stop **warns naming the
holder and refuses to kill it** (stop it yourself, or reassign `GATEWAY_PORT`).
The gateway's own `app.sh stop` does not have this guard — prefer the singlebox
stop paths.

Default URL: `http://127.0.0.1:8000/`. Use `FRONTEND_PORT` to select a different
port. This is Singlebox's local Umi dev-server path, not an Nginx/Docker release.
The launcher checks the selected root element and `/umi.js`, refusing the Umi
`Bundling` placeholder. On macOS it uses Python 3 `setsid` + `execvp` for
detached processes when a working python3 is available, falling back to the
bundled Perl launcher otherwise (the `/usr/bin/python3` xcode-select stub on a
CLT-less host is not a working Python); Linux retains the existing Perl
launcher. No authentication policy is changed by frontend selection.

## Local login (singlebox only)

Singlebox wires no OAuth provider, so the nextgen login modal has nothing to
navigate to and `/openapi/v1/auth/user` answers "auth not configured" (the
provider step is what is missing, not the caller's). The sanctioned local
identity is the Gateway's `dev_cookie` strategy: it resolves a `staff_id`
cookie into a signed principal for every forwarded route. `/_dev/login` on the
Gateway sets those cookies, so logging in is a URL, not a DevTools instruction:

```bash
# Open in a browser on the SAME host you browse the workbench from
# (cookie jars are host-scoped and port-agnostic):
open "http://127.0.0.1:8889/_dev/login?next=8000"
```

The page redirects back to the workbench with the identity armed. It 404s
outside `SERVER_ENV` local/dev/test, mirroring the strategy's own gating;
`?staff_id=`/`?nick_name=` accept only cookie-safe validated values. The ready
banner prints this URL for every non-legacy variant (the `dev_cookie` strategy
it arms is not gated by `GATEWAY_AUTH_MOCK`, and the Gateway is routinely started
by a separate invocation whose env the frontend start cannot see). The banner
URL uses `127.0.0.1`, the canonical host above — do not mix `localhost` and
`127.0.0.1`: cookie jars are host-scoped, so an identity armed on one hostname
is invisible to a tab on the other.

## API contract and verification boundary

Nextgen defaults are configured at the Singlebox composition root:

| Frontend configuration | Default |
| --- | --- |
| `TEAMCLAW_GW_BASE` | `http://127.0.0.1:${GATEWAY_PORT:-8889}` |
| `TEAMCLAW_ADMIN_BASE` | same Gateway (routes bots to Backend) |
| `TASK_ENGINE_UPSTREAM` | same Gateway (routes tasks to Backend) |
| `BCS_ENDPOINT_PRE` / `BCS_ENDPOINT_PROD` | `http://127.0.0.1:${BCS_PORT:-21000}` |

Explicit environment settings take precedence over these generated defaults.
Singlebox loads `singlebox/.env.local` before composing them. Changes require a frontend
restart because the local Umi proxy and `define` settings are startup inputs.
Gateway's upstream configuration must separately agree with any custom backend
ports; changing a frontend proxy does not reconfigure Gateway.

The exported optional legacy private-chat (`/api`, `/proxypass`), workflow and
Aixcore proxy settings are **not** claims that these services exist in
Singlebox. Configure their existing `TEAMCLAW_PRIVATE_CHAT_MANAGEMENT_BASE`,
`TEAMCLAW_PRIVATE_CHAT_SESSION_BASE`, and `TEAMCLAW_CLAWWEB_BASE` settings only
against confirmed compatible services. Do not route every path to Backend or
invent successful responses. Authentication providers and credentials must be
configured using the existing BCS/Gateway contract. Never bypass login to
claim end-to-end success.

Verify separately:

1. Export checks pass and the manifest matches the fetched source SHA.
2. New UI renders without runtime exceptions, and the browser serves the new
   bundle after a hard reload (not an old tab or another checkout's server).
3. Real `/openapi/v1/auth/user` and `/openapi/v1/auth/url` responses, login,
   bot listing and the required workflows operate against actual services.
   Connection-refused/504 is a backend-availability failure, not UI success.

Local regression commands:

```bash
bash singlebox/verity/test_singlebox_frontend_variant.sh
bash singlebox/verity/test_singlebox_detached_session.sh
bash singlebox/verity/test_singlebox_service_guards.sh
bash singlebox/verity/test_singlebox_default_mode.sh
```

## Local validation record (2026-09-10)

- Upstream source: `261f7741ad760b3887ad7812fc9b9a484aff6c8d`.
- Full exporter: passed (326 suites, 2322 tests; typecheck, lint, build,
  source isolation and pack checks passed).
- The four shell regression commands above passed; shell syntax and
  `git diff --check` passed.
- Singlebox nextgen frontend started. A fresh Chrome session rendered the new
  root, reached the login action and raised no page runtime exceptions.
- Both `/openapi/v1/auth/user` and `/openapi/v1/auth/url` returned 504 because
  the local Gateway/backend stack was not running. Authenticated bot/chat
  workflows and full Singlebox coverage were **not** verified. No mock auth
  or synthetic backend responses were introduced for the browser check.

## Full-source validation record (export-plan Task 20, 2026-11-04)

Recorded at Avernet commit `53d9ea5f57` (collab-form public surface
complete). Everything below was exercised from this checkout on 2026-11-04;
no mock auth, no synthetic backend responses, and no skipped launcher
readiness contract were involved.

### Verity gates

- `bash singlebox/verity/test_singlebox_frontend_variant.sh` — PASS, all
  7 scenarios (variant resolution incl. `nextgen → apps/frontend-nextgen` +
  `dev:local` + root id `root`; upstream defaults/overrides; teamclaw
  mapping/auto-update; dev-login banner; frontend-pull refusal semantics;
  locked-arm install policy).
- `bash singlebox/verity/test_singlebox_variant_gate.sh` — PASS (status
  probes stay usable under broken variants; starts fail fast before any
  service work; bcs_frontend refuses non-legacy variants; gateway_stop keeps
  the owned-process contract; frontend-pull routes as a command).

### Actual frontend source and commands

- Source path: `$ROOT/apps/frontend-nextgen` (the complete public nextgen
  tree at HEAD), launcher command `npm run dev:local`,
  `FRONTEND_ROOT_ID=root`, workbench `http://127.0.0.1:8000/`.
- Startup: `FRONTEND_VARIANT=nextgen FRONTEND_FORM=open bash
  singlebox/singlebox.sh start all` → **rc=0**, full standalone stack
  ready (baas, backend, bcsfuse, BCS on 21000, 5 OpenClaw bots, demo bot,
  Gateway on 8889, nextgen frontend on 8000). Readiness is the launcher's
  own HTTP contract: `<div id="root">` + a 10.7 MB `/umi.js`, no bundling
  placeholder. Gateway `/health` → 200.

### Live HTTP-level smoke (full stack up)

- Login: `http://127.0.0.1:8889/_dev/login?next=8000` arms the signed
  `staff_id` cookie (the Gateway `dev_cookie` strategy resolved it for every
  forwarded route). `/openapi/v1/auth/user` and `/openapi/v1/auth/url`
  answer the documented local contract `OAuth is not configured`
  (HTTP 404, `auth_not_configured`) — Singlebox wires no OAuth provider, so
  the provider step is the missing piece, not the caller.
- Collaboration flow, through the frontend origin
  (`127.0.0.1:8000/openapi/...` → Gateway → BCS) with the dev principal:
  group list → `20000 OK` (initially empty); **group create** (chat
  strategy, driver `bot_13a68ef8` CEO + participant `human_001`) → `20100
  Created`, `bcs_grp_10dbeb93d89b40069e1e84a8f020cae4`, listing shows
  `total: 1`; external-bot `bots/mine` → 200 with the 5 onboarded
  `bot_*` profiles plus `human_001`, all `online`.
- Non-collaboration Open surface: `/bot-workshop` and
  `/workspace/collaboration` → 200; the dev bundle contains the open-form
  route markers (e.g. `bot-workshop` × 8); BBS task list
  (`/openapi/v1/collaboration/tasks/bbs/list`, a route the collab form
  cuts) → `200000 OK`. Absent optional remote services were not confused
  with bundle failures.
- The legacy `bcs_frontend` composite was not used for any of this.

### What this record does not cover

- Browser-rendered interactions (sidebar contents, dialogs, WebSocket
  push, side panels) had no browser session in this run; readiness and the
  flows above are HTTP-level. They remain part of the collab-form live
  acceptance and recorded network trace (spec acceptance criteria 2–5, 8).
- This machine has no Docker CLI: the exported-tree image build/startup and
  the private-deployment smoke stay UNVERIFIED here and are owned by the
  private deployment environment (OCB
  `apps/frontend-nextgen/deploy/collab/README.md`).
- All services started for this record were stopped with `stop all`;
  no listener or orphan process was left behind.

### Legacy-omission disposable check

A disposable `git worktree add --detach` copy at `53d9ea5f57` with **only**
`apps/frontend` deleted (the real legacy directory was untouched):

- `FRONTEND_VARIANT=nextgen` startup through the same launcher: deps
  install, `dev:local`, readiness contract — FRONTEND READY on port 8001,
  `/` root element, `/umi.js` 200, `/bot-workshop` 200, collab/open route
  markers present in the bundle.
- `FRONTEND_VARIANT=legacy` startup fails loudly at its own prerequisites
  (rc=1, `apps/frontend/scripts/ci_test.sh: No such file or directory`
  → `[ERROR] Prerequisites not met for frontend`) — no silent fallback.
- Both verity scripts above pass in full inside the copy (7/7 and PASS):
  nextgen selection, upstreams, readiness and lifecycle contracts have no
  runtime dependency on the legacy directory; `singlebox`'s only
  `apps/frontend` references are the legacy/default variant mapping, the
  warn-and-degrade lifecycle fallback, and the legacy-only bcs_frontend
  composite. The worktree was removed afterwards.
