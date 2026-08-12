# Per-Bot Startup Script

## Summary

A bot gains a startup script that belongs to the bot rather than to any one
container, and that the platform runs as part of the container's start sequence.
Owners read, replace, and clear it through the public API. The script is how a
team provisions its own environment inside the bot: install a CLI, drop in a
plugin, preload a skill.

## Motivation

Anything installed by hand inside a running bot container is lost the moment the
container is recreated. A restart, a republish, or a scale-out all come back with
a bare image, so every one of those events means provisioning by hand again, and
a scaled bot has no way to make its instances identical. Teams operating bots on
the platform have asked for a supported way to attach that provisioning to the
bot itself (issue #926).

The platform already runs a script when a container starts — the boot sequence
carried in `after_create_cmd_hook`, executed inside the container by a generated
wrapper that reports its result back and drives the device to ACTIVE or FAILED.
This feature **reuses that mechanism** rather than building a second one: the
bot's script is appended to the sequence, isolated so that its failure cannot
change the outcome of the boot.

That choice is deliberate and it has a price, stated here rather than
discovered later: the platform sees one script, so it reports **one combined
result** for the whole container start. A caller cannot read the exit status of
their own script separately from the platform's. Separating them is a larger
change — a distinct execution stage, its own result channel, its own timeout —
and is deferred until someone needs it.

## User Stories

- As a bot owner, I want to store a provisioning script on my bot, so that every
  container my bot starts comes up with my tools already installed.
- As a bot owner, I want to replace or clear that script and restart the bot, so
  that I can iterate on my provisioning without rebuilding an image.
- As a bot owner, I want my script to finish before the bot is reported ready, so
  that a bot that is serving is a bot that is provisioned.
- As a bot owner, I want a failed or slow script to leave my agent running, so
  that a bad script costs me a feature and not the bot.
- As a bot owner, I want to know that the script is stored but cannot run for my
  bot, rather than believing provisioning happened when it did not.

## Acceptance Criteria

- [x] A bot has at most one startup script. It can be read, replaced, and
      cleared through the public API, and a bot that has never had one reads as
      empty rather than as an error.
- [x] Replacing or clearing the script does not disturb a running container. The
      change takes effect the next time the platform composes a start sequence
      for that bot — that is, on the next create or restart.
- [x] The script runs as part of the container's start sequence, after the
      platform's own boot steps have completed and **before** the bot is
      reported ready.
- [x] A non-zero exit, a crash, or a timeout in the script does not change the
      outcome of the container start. The bot reaches its normal running state.
- [x] The script is stopped at a stated timeout, and the timeout is small enough
      that the whole start sequence stays within the platform's start budget.
- [x] The script body cannot alter the platform's boot steps, whatever it
      contains — including quotes, shell metacharacters, heredoc delimiters, and
      placeholder-shaped text.
- [~] ~~A caller can read the result of the last container start for each of the
      bot's instances.~~ **Descoped at review.** Built, then removed: resolving
      which start to report from the bot record works only for a personal bot or
      a *draft* service bot, so a published service bot would get an empty answer
      indistinguishable from a real one. The contract now states that no such API
      exists and that the container log is the only place to see a run's output.
- [x] The published contract states that the script runs on every start the
      platform composes and must therefore be idempotent; the platform does not
      attempt to detect or suppress repeat runs.
- [x] The published contract states which bots the script cannot run for, and the
      API reports that per bot rather than storing a script that silently never
      runs.
- [x] Only a bot's operators may read or write its startup script, subject to the
      same authorization every other own-bot operation in the group carries, and
      every write records who changed it and when.
- [x] A script larger than the published size limit is refused at write time with
      a message naming the limit, rather than failing later at run time.
- [x] A bot with no stored script produces a start sequence byte-identical to
      today's.

## In Scope

- Public API to read, replace, and clear a bot's startup script.
- Appending the stored script to the platform's start sequence, isolated so its
  failure and its runtime cannot affect the boot.
- An explicit answer for bots the script cannot run for.

## Out of Scope

- **Any API for reading a run's result.** Descoped at review, and the reason is
  not only the combined exit status: identifying *which* start to report from the
  bot record works for a personal bot and a draft service bot, but not for a
  published one. Both problems belong to the same follow-up — a result channel
  with its own design — and the container log
  (`/home/admin/logs/startup_script.log`) is the only place to look until then.
- **Re-running on providers whose restart does not re-run the start sequence.**
  The script inherits the existing behavior exactly: it re-runs wherever the
  platform's own boot sequence re-runs, and does not where it does not. Today
  that means it re-runs on a provider whose restart is implemented as
  destroy-and-create, and not on providers that restart a container in place.
  Closing that gap is separate work with its own risks.
- **Bots whose container is not provisioned through the shared start sequence** —
  teclaw. Those get the contract and an explicit unsupported answer.
- Supplying secrets to the run by reference. For now the contract states that
  secrets must not be placed in the script body, and provides no alternative.
- Binding the script to a service bot's publication stage. One script per bot.
- Streaming or live-tailing a run. Results are read after the fact.
- A script library, templating, or sharing between bots.
- Changing what the platform's own boot steps do.

## Open Questions

- What is the size limit for a script body, and the timeout for a run?
- Which interpreters are permitted — is the script always run by a shell, or may
  it declare its own interpreter?
- Is network egress during a run expected to work under a bot's existing outbound
  restrictions, or does provisioning need its own allowance?
- Does the operator bar for writing the script equal the bar for restarting a
  bot, or is it higher — the script is arbitrary code inside the container.
- How should a run's result be exposed at all, given that identifying the start
  to report is unsolved for a published service bot and that the script shares one
  exit status with the platform's boot? (The read path was descoped from this
  change for exactly this reason.)
