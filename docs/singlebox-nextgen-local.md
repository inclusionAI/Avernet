# Running the exported next-generation frontend in Singlebox

Singlebox defaults to `src/frontend` (legacy). Updating `src/frontend-nextgen`
by itself does not change the running UI. Select `FRONTEND_VARIANT=nextgen`
for **every** setup/start/stop/status invocation, or persist it in the checkout's
untracked `.env.local`. Do not copy secrets between worktrees.

## FRONTEND_VARIANT=teamclaw: the external internal-UI checkout

`src/frontend-nextgen` is the exported open-core subset of the TeamClaw UI —
internal-only capabilities are deliberately stripped by the exporter. To run
the **full internal frontend** (the product UI under development), use the
`teamclaw` variant, which serves an external checkout through the same
Gateway composition:

```bash
# .env.local of the Avernet checkout:
FRONTEND_VARIANT=teamclaw
TEAMCLAW_DIR=~/IdeaProjects/teamClawPre/teamclaw   # path to the internal checkout
# TEAMCLAW_FRONTEND_AUTOUPDATE=0                    # default 1; see below
```

Then `bash scripts/singlebox.sh start frontend` (or `start all`) will:

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
git -C "$TARGET" diff --stat -- src/frontend-nextgen
cat "$TARGET/src/frontend-nextgen/OPEN_CORE_MANIFEST.json"
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
HOST=127.0.0.1 FRONTEND_VARIANT=nextgen bash scripts/singlebox.sh start frontend

# Full local stack (creates local runtime and bots). In nextgen mode the all
# group includes Gateway; legacy startup order remains unchanged.
FRONTEND_VARIANT=nextgen bash scripts/singlebox.sh start all

# After a validated new export, rebuild the dev-server module graph.
FRONTEND_VARIANT=nextgen bash scripts/singlebox.sh restart frontend

# Stop the selected UI BEFORE selecting another one: foreign/other-checkout
# listeners are deliberately not killed by the frontend launcher.
FRONTEND_VARIANT=nextgen bash scripts/singlebox.sh stop frontend
FRONTEND_VARIANT=legacy bash scripts/singlebox.sh start frontend
```

Default URL: `http://127.0.0.1:8000/`. Use `FRONTEND_PORT` to select a different
port. This is Singlebox's local Umi dev-server path, not an Nginx/Docker release.
The launcher checks the selected root element and `/umi.js`, refusing the Umi
`Bundling` placeholder. On macOS it uses Python 3 `setsid` + `execvp` for detached
processes; Linux retains the existing Perl launcher. No authentication policy
is changed by frontend selection.

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
banner prints this URL for the nextgen variant when `GATEWAY_AUTH_MOCK=1`.

## API contract and verification boundary

Nextgen defaults are configured at the Singlebox composition root:

| Frontend configuration | Default |
| --- | --- |
| `TEAMCLAW_GW_BASE` | `http://127.0.0.1:${GATEWAY_PORT:-8889}` |
| `TEAMCLAW_ADMIN_BASE` | same Gateway (routes bots to Backend) |
| `TASK_ENGINE_UPSTREAM` | same Gateway (routes tasks to Backend) |
| `BCS_ENDPOINT_PRE` / `BCS_ENDPOINT_PROD` | `http://127.0.0.1:${BCS_PORT:-21000}` |

Explicit environment settings take precedence over these generated defaults.
Singlebox loads `.env.local` before composing them. Changes require a frontend
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
bash scripts/test_singlebox_frontend_variant.sh
bash scripts/test_singlebox_detached_session.sh
bash scripts/test_singlebox_service_guards.sh
bash scripts/test_singlebox_default_mode.sh
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
