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

- [ ] Installing a tool by name makes that command present and executable in
      the bot's container, and leaves every other command untouched.
- [ ] The engine decides the directory, sets the executable bit, and makes the
      tool reachable by the agent — all within the single install call. The
      platform sends no shell command and no permission change.
- [ ] Deleting a command removes it; deleting a command that was never there
      reports success.
- [ ] Replacing the set treats the request as the desired end state: commands
      not named in it are removed. An empty set is a valid request meaning
      "this bot has no commands", and clears them all.
- [ ] A replace response carries a per-command verdict for **every** command
      the request named. A command the request named but the response omits
      makes the whole call unreadable to the platform, by design — silence is
      never read as success.
- [ ] A replace call may report some commands succeeded and others failed in
      the same response, and that is an ordinary 200, not an error status.
- [ ] Listing reports what is actually on disk at that moment — never a replay
      of the last install or the last artifact received. It surfaces partial
      delivery, manual edits made inside the container, restores from an old
      snapshot, and tools the engine itself dropped.
- [ ] A bot with no commands lists an empty set, not an error.
- [ ] A refusal is unambiguous: either a non-success status, or a success
      status whose envelope reports failure. A tool the engine could not
      install is never reported as installed.
- [ ] The engine does not re-verify the content hash the platform already
      enforced.
- [ ] A tool binary at the platform's single-file ceiling can be carried by
      both the install request and the download response without exceeding any
      body limit.
- [ ] An engine build that does not support CLI tools refuses in a way the
      platform reads as a refusal, rather than appearing to succeed.
- [ ] Applying a manifest that declares `cli_tools` to an ARCA bot results in a
      succeeded `cli_tools` category in the apply report, and the tools present
      in the container.

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

## Open Questions

- **The two written contracts disagree on surface area.** Engine requirements
  §4 A2 specifies four endpoints. The engine handoff specification issued to
  the engine teams specifies five — it adds fetching a single tool's bytes back
  — and additionally requires a content hash on every entry the list returns,
  which §4 A2 does not mention. The handoff document states the repository
  wins on conflict and asks for discrepancies to be reported; this is that
  report. *Proceeding with the superset*, since the extra read is additive, has
  no platform caller yet, and the hash is what makes drift detection able to
  catch a same-named binary that was swapped. Needs a ruling on which document
  gets corrected.
- **How an unsupported engine build should refuse.** The contract pins one
  specific status to mean "this engine build has no CLI endpoints". The
  engine's existing capability guard refuses with a different status. Both
  reach the platform as a refusal and neither can be mistaken for success, so
  this is not a correctness risk — but the contract text and the code will
  disagree unless one is amended. *Proceeding with the engine's existing
  guard*, on the grounds that consistency within the engine matters more than
  the letter of a status code, and that its status is the more accurate signal.
- **Whether every ARCA engine must support this in this iteration**, or whether
  one leads and the rest declare it unsupported until they choose a directory.
  *Proceeding with all of them*, since the only per-engine difference is the
  directory constant.
