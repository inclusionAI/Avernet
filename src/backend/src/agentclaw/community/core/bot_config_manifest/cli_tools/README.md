# CLI tools — platform-managed command-line tools for a bot (W9, #1477)

A `cli_tools` entry declares one command. The platform fetches its bytes, pins
them by digest, checks they are an x86-64 executable, keeps its own copy, and
asks the bot's engine to install it **by name**. The engine chooses the
directory, sets the executable bit and exposes the tool to the agent — all of
it inside its own `install` call, so no container path crosses the boundary in
either direction and this package contains no `chmod`, no shell command and no
tools directory.

Two callers, one implementation: the management API under
`/openapi/v1/bots/{bot_id}/cli-tools` and the manifest's `cli_tools`
materialiser both call `CliToolService`. Neither reimplements a step. Backend
code never reaches the feature through the platform's own HTTP endpoints.

- `models.py` — the ORM model and record behind `ac_bot_cli_tool`: what the
  platform asked for, per bot, per command.
- `store.py` — `CliToolStore`: the platform's copy of the bytes in the
  `bot-data` object store. It exists because on teclaw the composed artifact
  *is* the delivery, so a live update or a manifest apply has to reference the
  tool now, and gathering from the engine then would be circular.
- `declarations.py` — `CliToolDecl` / `CliToolOutcome` / `CliToolDrift`: what a
  caller declares and what one operation reports back.
- `context.py` — `CliToolContext`: who and what an operation runs as. It also
  satisfies `apply/entry_fetch.py`'s `FetchContext`, which is what lets an
  HTTP-driven install fetch through the same funnel a manifest apply does.
- `verify.py` — `verify_amd64_elf` and `select_subpath`. The digest answers
  "are these the bytes you asked for"; these answer "can this machine run
  them" and "which file in the archive is the command".
- `delivery_port.py` — the per-family boundary. Two shapes: `install` /
  `delete` for one live edit, and `replace_all` for "this is the entire set".
  Every signature is name-addressed, and there is deliberately no `get`.
- `arca_port.py` — one call to the engine's CLI endpoints, over the adapter
  transport. `replace_all` is the whole-set endpoint, whose response reports
  per name so the apply report stays per entry.
- `teclaw_port.py` — no engine CLI call at all: the composed artifact is the
  delivery, exactly as `mcp` is. All three write methods are therefore the same
  operation — push one freshly composed artifact — and whether this port makes
  that push depends on who owns the end of the operation.
- `service.py` — `CliToolService`: fetch → digest → unpack → select → verify →
  md5 → store → deliver → record, plus the full-override `replace_all` whose
  removals come from the table rather than from the engine's listing.

## Why the order in `install` is the design

The OSS write comes **before** delivery because on teclaw it is the delivery,
and on ARCA it is what makes a redelivery possible without re-fetching a source
URL that may have rotated.

The **row is written before the family is told**, which rev 8 inverted (spec
D-14). It has to be: teclaw's port composes the artifact *from* this table, so
a port called first would transmit the previous set. The property the old order
gave for free — never record a tool the engine refused — is preserved instead
by **rolling the row back**: a single install undoes its insert, a single
remove puts its row back, and each leaves the object the surviving row points
at. An object stored for a delivery that then failed is discarded — its key is
derived, not recorded, so it is collected there or never.

A **full override does not roll back**. Its rows are the desired state, the
report says per tool what the engine refused, `drift()` shows the mismatch and
the next apply re-sends. Partial failure is already an apply's normal shape,
and unwinding N rows would add a failure mode of its own.

## Who tells the family, and how many times

A manifest apply calls the port **once**, with the whole desired set — not once
per tool. A loop of deletes and installs would put intermediate states on the
wire, and since removals precede installs the container would first be told it
had lost tools it was about to regain. An apply where every declaration already
converged calls the port **not at all**.

On teclaw the push is the artifact, and two callers own two different ends:
the management API has no closing step, so its binding carries the redeliver
and the port pushes; a manifest apply ends at `TeclawDelivery.finish`, which
pushes one artifact covering every category it wrote, so its binding leaves the
redeliver unset. That is why the service factory has three keys — `arca`,
`teclaw`, `teclaw-live` — and not two.

Boundary metadata lives in the parent package's `README.md`
(`core/bot_config_manifest`), which this package is part of.
