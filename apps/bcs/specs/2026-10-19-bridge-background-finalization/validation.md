# Background finalization implementation validation

Date: 2026-10-19. Scope: public BCS adapter; no runtime rollout or live bridge restart.

Publication instruction (2026-10-20): the user authorized commit, push and a PR to
`dev`, with no new verification. Results below are from the earlier implementation
phase; tests and verification hooks are not rerun for publication.

Rebase update (2026-10-20): integrated `dev` at `489a5d863d35a6d98f5d0169288ead9829c552d1`.
The startup conflict was resolved by retaining the new per-bot caller environment
and adding the completion event flag only for native observation. The observer's
fallback restart also retains the original caller environment. Tests were not
rerun after this rebase, following the user's no-verification instruction; the
results below apply to the pre-rebase implementation.

## Default-mode update (2026-10-22)

At the user's request, `hook_idle` is now the default for Claude-compatible
constructors and factories when `completion_mode` is omitted. Explicit `legacy`
and `observe` overrides remain supported, and unsupported native profiles still
fail closed. See the bridge CLI README for current configuration and rollback.
This changes the earlier opt-in rollout decision, not the supported version or
compatibility evidence. The following results are the historical 2026-10-19/20
validation, not a claim of new live verification or deployment.

Current default-change checks (2026-10-22):

- Public bridge core/app/CLI: **405 passed, 2 failed, 3 ignored** via
  `cargo test --locked --offline --no-fail-fast -p bcs-bridge-core -p bcs-bridge-app -p bcs-bridge-cli -- --test-threads=1`.
  The two unchanged CLI pipe tests fail because this host keeps pipes at 4096
  bytes and denies `F_SETPIPE_SZ` with `EPERM`; no pipe code/test was weakened.
- Included regressions: **16 completion tests**, **18 WebSocket tests**, and
  **10 session-recovery tests** passed. Constructor/factory defaults, explicit
  legacy/observe, failed-result cause propagation over BCN V2/V3, cancellation,
  restart/reconnect and approval paths are covered by synthetic protocol peers.
- Internal bridge startup tests: **3 passed** using
  `cargo test --locked --offline --manifest-path apps/bcs-internal/Cargo.toml -p bcs-bridge --test start -- --test-threads=1`
  from the OCB workspace. The cfuse-cc fixture now exercises hook initialization
  without `completion_mode`; the cfuse-codex test remains unchanged.
- No new live-model verification, running configuration edit or bridge restart.
  `git diff --check` passes; all changed source files remain below 1000 lines.

## Original implementation and configuration

The adapter supports `completion_mode = "legacy"` (default), `"observe"`, or
`"hook_idle"` in the bot's `engine_options` table. For example:

```toml
[[bot]]
provider_bot_ref = "worker"
engine = "cfuse-cc" # internal distribution; public equivalent: claude-code
cwd = "/path/to/workspace"
[bot.engine_options]
completion_mode = "hook_idle"
```

This is an explicit opt-in for the tested cfuse 2.6.44 / Claude Code 2.1.199
combination. Runtime init rejects other native versions. Unknown modes/options
fail factory validation. Rollback changes the mode for newly created engines/runs;
it does not change or kill an active process. Do not enable this broadly yet.

Native SDK `initialize` registers a Stop callback on the process stdin/stdout
control protocol. It avoids the verified duplicate `--settings` collision and
needs no helper executable, socket, user settings changes or transcript reads.
One process handles one bridge run and one business input. Its unique registered
callback, request IDs, session ID, init boundaries and ordered stdout establish
scope. `prompt_id` alone does not: the blocked-Stop experiment reused it.
Any intervening main activity invalidates the old snapshot/result. Late/duplicate
callbacks and unsupported evidence fail explicitly. Successful finalization still
returns one TurnOutcome through the existing run loop.

In hook_idle, mapper init-pop, XML delivery, launch-receipt and inline-result
consumption heuristics are disabled. Tool start/result name correlation remains.
Legacy/observe keep the prior completion gate; observe does not wait for new
completion evidence once legacy can return. If observer initialization fails, the driver logs that observation is unavailable
and restarts without it **before sending any business input**. This preserves
legacy execution and never duplicates a prompt, but launcher/SessionStart hooks
can execute twice in this exceptional startup path. Observe retains legacy line
acceptance; hook_idle never falls back. Every callback ACK is bounded/cancelable.

## Evidence gathered

- Fresh original Claude tests: **53 passed**, before and after the mechanical
  split into driver, mapper, permission handling and two test modules.
- New deterministic driver tests: **12 passed**, covering the seven-task incident,
  live tasks at idle, queued wake, blocked Stop, empty final, explicit config,
  cancellation, observe completion point, trace privacy, and a table of missing,
  null, stale, duplicate, wrong-session, unsupported, oversized and late evidence.
- Red/green observed: unsupported mode; no-idle fell through to the old watchdog;
  malformed input leaked to raw trace; unterminated oversized input reached the
  watchdog. The added tests failed first, then passed after implementation.
- Real Rust driver against the pinned cfuse launcher: **5 scenarios passed**:
  ordinary response, resumed session, background Bash across idle/wake, in-turn
  completion without notification-delivery frames, and notification queued during
  a long answer. The ignored live test was explicitly run,
  not counted as passed by the default offline suite.
- Prior native callback probes: basic two turns, Agent, midturn, queued response.
- Additional native probes: native callbacks coexisting with an existing command
  Hook (two turns, both hooks called twice); Bash, two concurrent Bash tasks (snapshots 2→1→0),
  another Stop callback blocking once (same prompt, new request IDs and new Stop).
- Nested experiment: an Agent completed while its nested shell remained alive;
  the next Stop snapshot still included that shell. A later idle alone did not
  provide a new empty Stop/result. This is **not** evidence of supported successful
  nested completion; the adapter must not reuse the old result. Such incomplete
  evidence is an explicit unsupported/unavailable completion, not success.

All raw model transcripts/probe artifacts remain local under
`/tmp/bridge-hook-probe-20261019` and `/tmp/cfuse-finalization-work`; none are committed.
Tests in the repository use invented data, not production payloads.

## Independent review

One fresh reviewer examined tracked and untracked source. Three Important
findings were reproduced with failing tests and fixed: replayed old idle releasing
a newer candidate; observe rejecting a large valid legacy frame or unavailable
initialization; rejected observer callbacks blocking cancellation. Native init,
result and session-state UUIDs now have a bounded replay set (8192 identities).
There were no deferred minor findings. This does not certify the untested coverage
matrix below or authorize merge/deployment.

## Required checks and limits

Final public suite: **336 passed, 0 failed, 2 ignored** (core, app and CLI), using:

```sh
CARGO_TARGET_DIR=/tmp/ocb-retention-implementation-target cargo test --locked --offline -p bcs-bridge-core -p bcs-bridge-app -p bcs-bridge-cli -- --test-threads=1
```

Run from `apps/bcs`. The two ignored tests are the explicit long-stream retention
stress test and the opt-in live test; the latter was run separately and passed
all five scenarios. Logs: `/tmp/cfuse-finalization-work/public-final.log` and
`/tmp/cfuse-finalization-work/live-driver-final.log`. The first parallel run hit
`crash_after_init_recovers_session_and_inflight_injects` with `Address already in use`;
the final serial suite passed. An existing unused `BackgroundEnqueued.status`
warning remains. `git diff --check` passed; each changed/new source is under 1000 lines.

Internal `cargo test --locked --offline --manifest-path apps/bcs-internal/Cargo.toml
-p bcs-bridge --test start` is blocked before compilation: the OCB lock file is
inconsistent with the chosen newer public dev baseline. No manifests/dependencies
were changed by this fix. `check-architecture.sh` passed; its incidental lockfile
regeneration was restored. No lockfile workaround is included.

No new DB operations, remote requests per event, or transcript scans are added.
Event parsing is linear in bounded line size; state stores only one candidate,
current snapshot cardinality and bounded callback IDs. Existing task-name maps
remain scoped to the run. Input bounds are checked during streaming reads.

## Release gates

G1/G2 have evidence for the isolated native channel and ordered callback scope.
G3/G4 are partial: managed policies, background MCP/workflows, monitoring/cron,
full nested/resumed-background recovery, all concurrent engine-version combinations
and production rollout are not certified. Unknown task kinds and nonempty cron
snapshots fail closed. Missing snapshots never imply empty.

At that validation point the default remained legacy, with broader compatibility
validation planned before changing defaults. The 2026-10-22 user-requested default
change above supersedes that rollout decision; the remaining compatibility limits
still apply. No live configuration or deployment is changed by this default update.

## Acceptance matrix

| Criteria | Evidence / limit |
| --- | --- |
| AC-01/04 incident and midturn | Synthetic seven-task regression; real driver midturn success |
| AC-02 live tasks at idle | Synthetic and actual Bash/Agent probes; latest empty evidence required |
| AC-03 queued notification | Synthetic intermediate-result regression; real driver queued completion |
| AC-05/06 stale/duplicate/scope | Wrong session/callback, old/replayed idle, duplicate/late Stop/result; blocked-Stop live probe |
| AC-07/08 unknown coverage | Missing/null/unsupported snapshot and cron rejection; nested success not certified |
| AC-09 termination | Pre-cancel and cancellation while callback writer is blocked; existing transport abort suites |
| AC-10 configuration | No settings mutation; native and existing plugin command Hook coexist; managed policy not certified |
| AC-11 resources | Bounded streaming input, missing-idle timeout, callback writer cancellation, private trace redaction |
| AC-12 transports | Existing encoder, WebSocket, webhook, retention and session recovery suites |
| AC-13 modes | Config validation, observe legacy parity/unavailable startup, hook_idle no silent fallback |
| AC-14 ordinary/error/empty/HITL | Empty final and ordinary live reply; existing permission mapper/transport regression suites; hook_idle-specific real HITL not certified |
