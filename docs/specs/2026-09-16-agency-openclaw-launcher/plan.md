# Agency OpenClaw Launcher Implementation Plan

**Goal:** Start selected agency profiles as isolated local OpenClaw Bots in BCS.
**Architecture:** Local CLI orchestrator plus profile/config and runtime helpers;
reuse existing BCS wire protocols and the BCN plugin, leave install.sh unchanged.
**Tech stack:** Bash entry point, Python 3.11+, PyYAML, OpenClaw CLI, unittest.
**Spec:** [spec.md](spec.md)

## Constraints

No service/core API changes, default OpenClaw mutation, repository script execution,
or global formatters. Every new source file stays below 1,000 lines. Only external
CLI/network boundaries are stubbed. Preserve state but terminate owned processes.

## Tasks

- [x] Add executable regression tests before implementation. Tests launch the real
  entry point against temporary profiles and a local HTTP registration server.
  Assert two independent config/state/session trees and distinct gateway ports;
  interrupt the launcher and verify process exit. Repeat and assert no new Bot
  registrations. A missing entry point must fail these tests initially.
- [x] Implement agency_profiles.py: safe frontmatter parsing, contained selection,
  content digests, lossless role rendering, strict model-input allowlist and
  isolated config generation. Reject changed source snapshots before networking.
- [x] Implement agency_runtime.py: private atomic writes, state lock, no-redirect
  HTTP transport, registration recovery marker, redacted failures, child command
  environment and process-group supervision. Test rejected/ambiguous registration
  and plugin/probe failures before adding each behavior.
- [x] Implement agency_launcher.py and launch-agency.sh: argument validation,
  prepare all selections, install plugin into each state root, register identities,
  start foreground Gateways, probe connectivity, publish descriptions and supervise.
  Reuse persisted ports and credentials on restart; Ctrl+C stops owned children.
- [x] Add Chinese usage/troubleshooting documentation and README links. Run new
  unittest suite, original shell suite, syntax checks, diff whitespace check and
  modified-source line counts. State explicitly that live integration is untested.

## Validation evidence (2026-09-16)

- New launcher integration suite: 15 tests passed, using a real launcher process,
  temporary directories, a loopback HTTP stub and an executable OpenClaw stub.
- Existing installer regression: all 6 tests passed. Its pre-existing OpenClaw
  fixture emitted a table for `plugins list --json`; updated only that fixture to
  emit the current JSON contract. The production `install.sh` is unchanged.
- Ruff check passed for all added Python source/test files; shell entry help runs.
- Installed OpenClaw 2026.5.12 accepted both generated base configuration and
  generated BCS channel configuration with the locally built BCN plugin present.
  These were isolated `config validate --json` checks, not a live Gateway test.
- Shell syntax, whitespace and source-size checks passed; largest modified source
  file is 334 lines (limit: 1,000).
- Final self-review caught a readiness race: BCN sets connected on socket-open,
  before bot.connect authentication. A failing regression demonstrated the false
  success; readiness now also requires a nonempty probe sessionToken.
- No live BCS registration, npm plugin installation, paid model call, or end-to-end
  reply test was performed. No Rust code changed; Cargo tests were not required.
- Changes remain uncommitted for user review.

## Follow-up: convenient defaults (2026-09-16)

Implemented the user's requested CLI refinements:

- `--token` takes the same Human registration token as install.sh, overrides the
  environment, and is mutually exclusive with `--token-file`.
- Default state root is `~/.bcs/agency`. Existing roots are not automatically moved.
- Omitted `--agency-dir` clones the requested public repository into the state
  root atomically; subsequent invocations verify/reuse it without pulling.
- Omitted `--model-config` reads only model settings from
  `~/.openclaw/openclaw.json`. Missing default config fails before clone/register;
  explicit overrides remain supported and the source config is never modified.
- New tests first reproduced missing flag/default functionality, then passed with
  the implementation. Final suite: **22 passed**; legacy installer: **6 passed**;
  Ruff, entry-point help, shell syntax and Python compilation passed. Largest
  touched source is now 443 lines, still below the 1,000-line limit.
- Git clone behavior was tested through an executable boundary double, including
  partial-clone cleanup, cache reuse and refusal to overwrite unrelated data.
  No real GitHub clone, live Bot registration or model request was performed.

## Follow-up: OpenClaw plugin install CAS recovery (2026-09-16)

- Reproduced the reported failure outside the user's instance with OpenClaw
  2026.5.12 and `@avernet-plugin/openclaw-channel-bcn@1.0.23`. The package was
  installed and loadable, but BCN setup-entry wrote `channels.bcs` while the
  OpenClaw installer held an older config hash, causing `config changed since
  last load` during the install transaction.
- A preseeded unknown channel was tested and rejected by OpenClaw, so that
  workaround was not used.
- Command output now goes to private `0600` per-instance diagnostic logs. For
  the exact CAS conflict only, the launcher verifies the requested plugin is
  `loaded`, then runs the ordinary `plugins enable` command. It never retries
  installation, uses `--force`, or bypasses dangerous-code scanning.
- Real isolated verification installed version 1.0.23, recovered/verified the
  first run, and reused it on the second run without BCS registration.
- Added three conflict-specific regressions plus private-log coverage. Full
  launcher suite: **25 passed**. No user token was used.

## Follow-up: capability metadata is not network readiness (2026-09-16)

- Real user evidence showed both Bots completed authenticated WebSocket `bot.connect`
  and persisted their expected Bot IDs, while the first immediate `/bots/onboard`
  response did not confirm capability metadata. The previous launcher incorrectly
  treated that secondary metadata operation as a fatal network failure and stopped
  both Gateways.
- Capability publication now attempts up to three times with bounded delays. An
  unconfirmed or HTTP-failed result is stored in private
  `bcs-onboard-last-response.json`, reported as `capability metadata pending`, and
  retried on the next launch. Successful publication removes the diagnostic.
- Authenticated BCN readiness remains strict: connected channel plus nonempty session
  token is still required. Only profile name/summary/domain convergence is nonfatal.
- Added regressions for repeated `onboarded=false`, HTTP 503, state retention and
  next-launch convergence. No remote query using stored Bot credentials was made.

## Follow-up: interactive overwrite and global BCS identity choice (2026-09-17)

- Changed source profiles are confirmed one by one in CLI order. `No` keeps the
  saved local profile and continues; `Yes` backs up and replaces role files.
- After every local decision, one final question applies BCS re-registration to
  all selected instances with sessions. Default/no reuses all; old remote Bots are
  retained. `--yes` and `--reregister-bcs` provide independent non-interactive
  choices.
- Batch re-registration obtains every replacement credential before changing any
  local session, backs up old sessions, and retains private recovery records when
  a later remote registration fails.
- Tests cover per-profile yes/no decisions, one global question, all four choice
  combinations, non-interactive flags, profile/session backups, identity reuse,
  all-profile re-registration and partial-batch failure. Final launcher suite on
  2026-09-17: **35 passed**; legacy installer regression: **6 passed**; Ruff,
  shell/Python syntax, diff and source-size checks passed.
