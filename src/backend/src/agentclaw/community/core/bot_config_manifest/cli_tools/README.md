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
- `delivery_port.py` — the per-family boundary: `install` / `delete` / `list` /
  `replace_all`, every signature name-addressed, and deliberately no `get`.
- `arca_port.py` — one call to the engine's CLI endpoints, over the adapter
  transport.
- `teclaw_port.py` — no engine call at all: the artifact refs are the delivery,
  exactly as `mcp` is composed and delivered.
- `service.py` — `CliToolService`: fetch → digest → unpack → select → verify →
  md5 → store → deliver → record, plus the full-override `replace_all` whose
  removals come from the table rather than from the engine's listing.

## Why the order in `install` is the design

The OSS write comes **before** delivery because on teclaw it is the delivery,
and on ARCA it is what makes a redelivery possible without re-fetching a source
URL that may have rotated. The row is written **last** because the table is the
platform's claim that the bot has the tool, and a claim made before the engine
accepted it is a claim that can be false. Nothing is recorded for a step that
failed, and an object stored for a delivery that then failed is discarded —
its key is derived, not recorded, so it is collected there or never.

Boundary metadata lives in the parent package's `README.md`
(`core/bot_config_manifest`), which this package is part of.
