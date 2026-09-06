# ARCA engine CLI tool endpoints

## Summary

A bot owner can declare command-line tools in a bot's configuration manifest,
and on an ARCA-family bot those tools actually appear in the container and
become callable by the agent. The platform half of this shipped; the engine
half was never built, so the feature is inert on every ARCA engine today.

## Motivation

W9 (#1477) delivered `cli_tools` end to end on the platform: fetch, pin
verification, archive member selection, architecture validation, the
management API, the apply materialiser, and the delivery port that calls the
engine. What it could not deliver is the engine's side of that call.

The result is a feature that reads as delivered and does nothing. Every apply
of a manifest that declares `cli_tools` on an ARCA bot reaches the engine,
finds no CLI endpoints, and fails the category — cleanly and with a correct
report, but the tools never arrive. Because a category is written
all-or-nothing, a single declared tool is enough to make `cli_tools` fail for
that bot on every apply, forever.

teclaw needs no endpoints for delivery — its artifact carries the tools — so
ARCA is the only family blocked, and it is the family that carries today's
users.

The contract the engine must satisfy is already written and agreed: engine
requirements §4 A2, plus the engine handoff specification issued to both
engine teams.

## User Stories

- As a bot owner, I want a tool I declared in my manifest to be present and
  executable in my bot's container, so that the agent can actually run it.
- As a bot owner, I want a tool I removed from my manifest to disappear from
  the container, so that the manifest is the whole truth about what the bot
  has.
- As a bot owner, I want the apply report to tell me *which* of my declared
  tools failed and why, so that one bad declaration does not leave me guessing
  across the whole set.
- As an operator, I want to ask a bot what tools it actually has on disk, so
  that I can find drift between what the platform believes and what is really
  there.

## Acceptance Criteria

- [x] Installing a tool by name makes that command present and executable in
      the bot's container, and leaves every other command untouched.
- [x] The engine decides the directory, sets the executable bit, and makes the
      tool reachable by the agent — all within the single install call. The
      platform sends no shell command and no permission change.
- [x] Deleting a command removes it; deleting a command that was never there
      reports success.
- [x] Replacing the set treats the request as the desired end state: commands
      not named in it are removed. An empty set is a valid request meaning
      "this bot has no commands", and clears them all.
- [x] A replace response carries a per-command verdict for **every** command
      the request named. A command the request named but the response omits
      makes the whole call unreadable to the platform, by design — silence is
      never read as success.
- [x] A replace call may report some commands succeeded and others failed in
      the same response, and that is an ordinary 200, not an error status.
- [x] Listing reports what is actually on disk at that moment — never a replay
      of the last install or the last artifact received. It surfaces partial
      delivery, manual edits made inside the container, restores from an old
      snapshot, and tools the engine itself dropped.
- [x] A bot with no commands lists an empty set, not an error.
- [x] A refusal is unambiguous: either a non-success status, or a success
      status whose envelope reports failure. A tool the engine could not
      install is never reported as installed.
- [x] The engine does not re-verify the content hash the platform already
      enforced.
- [x] A tool binary at the platform's single-file ceiling can be carried by
      both the install request and the download response without exceeding any
      body limit.
- [x] An engine build that does not support CLI tools refuses in a way the
      platform reads as a refusal, rather than appearing to succeed.
- [ ] **Deferred — needs a live ARCA bot.** Applying a manifest that declares
      `cli_tools` results in a succeeded `cli_tools` category in the apply
      report, with the tools present in the container. Every layer it depends
      on is covered (service, router, wire contract against the platform's
      parser), but the end-to-end assertion needs a deployed bot and belongs on
      the platform side. Recorded as a follow-up.

## In Scope

- The write behaviours: install one, delete one, replace the whole set.
- The read behaviours: list what is present, fetch one tool's bytes back.
- Support across the ARCA-family engines this repository ships.
- Declaring whether an engine build supports the capability at all.
- Tests covering each behaviour above, including the partial-failure and
  empty-set cases.

## Out of Scope

- **PATH injection.** The agent is told the location by a skill and calls the
  tool by absolute path. This is a recorded v1 trade-off, not an oversight.
- **Sending only changed bytes on a replace.** The whole set's bytes ride on
  every replace. Accepted because an apply that changes nothing never reaches
  the endpoint at all; changing it is a contract change held for v2.
- **teclaw.** It needs no write endpoints; its artifact is the delivery.
- **Any change to the platform side.** The caller exists and is merged.
- **The in-container operations CLI** (`install-skill` and friends), held for
  v2 under its own contract.

## Resolved

- **Contract surface.** Shipped as the superset: five endpoints, and `md5` on
  every `list` entry. `§4 A2` has been updated to match, resolving the
  discrepancy it asked to have reported.
- **Refusal status.** `501`, via the engine's existing capability guard. `§4 A2`
  now records this alongside the reserved meaning of `404`.
- **Engine coverage.** `openclaw` and `claude_code` — the only `Engine`
  subclasses in this repository. `aicoding` and `hermes` carry ports, not
  engines; if the corp build ships them they each need a binding, which this
  change does not cover.
- **Where the tools land.** Not settled in code, and deliberately so: the
  community `docker/` tree is not what production deploys, so the directory is
  a deployment knob (`BOT_CLI_DIR_<ENGINE>`, `BOT_CLI_DIR`, `ENGINE_CLI_DIRS`)
  with a per-bot workspace-sibling default. Claude Code's production location
  is still to be confirmed with the deployment owners.

## Follow-ups

- **End-to-end assertion on a live ARCA bot**: an apply declaring `cli_tools`
  reports the category succeeded and the tools are present. Platform-side.
- **Confirm Claude Code's production tool directory** with the deployment
  owners, then pin it via `BOT_CLI_DIR_CLAUDE_CODE` or an `ENGINE_CLI_DIRS`
  entry.
- **`aicoding` and `hermes`** need the same binding if the corp build ships
  them as engines.
- **PATH injection**, which makes the workspace-sibling placement load-bearing
  rather than merely tidy.
