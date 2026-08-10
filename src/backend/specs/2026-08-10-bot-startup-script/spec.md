# Per-Bot Startup Script

## Summary

A bot gains a startup script that belongs to the bot rather than to any one
container, and that the platform runs every time one of the bot's containers
starts. Owners read, replace, and clear it through the public API, and read back
what the last run did — its exit status and its output, per container instance.
The script is how a team provisions its own environment inside the bot: install
a CLI, drop in a plugin, preload a skill.

## Motivation

Anything installed by hand inside a running bot container is lost the moment the
container is recreated. A restart, a republish, or a scale-out all come back
with a bare image, so every one of those events means provisioning by hand
again, and a scaled bot has no way to make its instances identical. Teams
operating bots on the platform have asked for a supported way to attach that
provisioning to the bot itself (issue #926).

The platform can already run a script when a container starts, but that
mechanism is not available for this. It carries the platform's own startup
sequence — bootstrap, engine install, service start, watchdog — as a single
chained command whose failure marks the container failed. User-supplied content
cannot be added to that chain: a mistyped install command would stop the agent
from ever starting. What is missing is a **second, separate stage** that runs the
bot's own script, reports its own result, and cannot take the agent down with it.

The second gap is coverage. The existing mechanism re-runs on container start
for only one of the platform's container providers; on the others it runs at
creation and never again, which is the opposite of what "runs on every start"
promises. And a provider that cannot run scripts at all silently does nothing —
acceptable for an internal detail, not for a public API where the caller cannot
otherwise tell a completed install from one that never happened.

## User Stories

- As a bot owner, I want to store a provisioning script on my bot, so that every
  container my bot starts comes up with my tools already installed.
- As a bot owner, I want to replace or clear that script and restart the bot, so
  that I can iterate on my provisioning without rebuilding an image.
- As a bot owner, I want to read the exit status and output of the last run, so
  that a failed install is visible to me instead of surfacing later as a
  mysteriously broken agent.
- As the operator of a scaled bot, I want per-instance results, so that I can
  see when one instance's install failed and the rest succeeded.
- As a bot owner whose bot runs somewhere the script cannot execute, I want the
  API to tell me so, so that I do not believe provisioning happened when it did
  not.
- As a bot owner, I want a failed script to leave my agent running, so that a
  bad script costs me a feature and not the bot.

## Acceptance Criteria

- [ ] A bot has at most one startup script. It can be read, replaced, and
      cleared through the public API, and a bot that has never had one reads as
      empty rather than as an error.
- [ ] Replacing or clearing the script does not disturb a running container. The
      change takes effect the next time a container for that bot starts.
- [ ] When a container for a bot with a stored script starts, the script runs —
      on the first start and on every subsequent start, including after a
      restart, a republish, and for each instance created by a scale-out.
- [ ] The script runs as its own stage, after the platform's own startup
      sequence and after the agent is serving. A non-zero exit, a timeout, or a
      crash of the script leaves the agent running and reachable.
- [ ] A failed run is recorded and readable: exit status, output, and when it
      ran. The recorded output is bounded, and a caller can tell a truncated
      output from a complete one.
- [ ] The API reports the last run per container instance, so that a scaled bot
      whose instances disagree reports the disagreement rather than a single
      summarized answer.
- [ ] The script runs to completion or is stopped at a stated timeout; the
      timeout is part of the published contract and a stopped run is recorded as
      such, distinguishable from a script that exited non-zero on its own.
- [ ] The API states, per bot, whether that bot's container provider can run a
      startup script at all. Where it cannot, the caller gets an explicit answer
      — never a stored script that silently never runs.
- [ ] The published contract states that the script runs on every start and must
      therefore be idempotent; the platform does not attempt to detect or
      suppress repeat runs.
- [ ] Only a bot's operators may read or write its startup script, and every
      write records who changed it and when.
- [ ] A script larger than the published size limit is refused at write time with
      a message naming the limit, rather than failing later at run time.
- [ ] Values a script needs but must not carry in its body — registry tokens,
      package credentials — are supplied to the run by reference, so that reading
      the stored script never discloses them.
- [ ] Every operation uses the same response envelope, security model, and error
      shape as the rest of the public bots group.

## In Scope

- Public API to read, replace, and clear a bot's startup script, and to read the
  result of its last run.
- A startup-script execution stage, separate from the platform's own startup
  sequence, with its own result reporting.
- Running that stage on **every** container start — including on the container
  providers where a restart currently skips it.
- Per-instance results for scaled bots.
- An explicit capability answer for bots whose provider cannot run scripts.
- Supplying secrets to a run by reference rather than in the script body.

## Out of Scope

- Executing the script on container providers that support neither command
  execution nor restart today. Those bots get the explicit unsupported answer;
  making them capable is separate work, in part outside this repository.
- Executing the script for personal bots. Their containers are provisioned by a
  different path that does not re-run on restart, so honoring "every start"
  there is a change to that path and is deferred to a follow-up. The API answers
  for personal bots the same way it answers for any bot it cannot yet run on.
- Binding the script to a service bot's publication stage. One script per bot,
  used by whichever stage starts.
- Streaming or live-tailing a run. Results are read after the fact.
- A script library, templating, sharing between bots, or any content the
  platform authors on the caller's behalf.
- Changing what the platform's own startup sequence does.

## Open Questions

- What is the size limit for a script body, and the timeout for a run?
- Which interpreters are permitted — is the script always run by a shell, or may
  it declare its own interpreter?
- Is network egress during a run expected to work under a bot's existing
  outbound restrictions, or does provisioning need its own allowance?
- Does the operator bar for writing the script equal the bar for restarting a
  bot, or is it higher — a script is arbitrary code inside the container, and the
  two are not obviously the same permission.
- How long are run results retained, and how many past runs are kept?
