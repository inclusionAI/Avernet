# `agentclaw.community.core.bot_startup_script`

A provisioning script that belongs to a **bot** rather than to any one of its
containers, run as part of every container start the platform composes.

The problem it exists for: anything installed by hand inside a running container
is lost the moment that container is recreated. A restart, a republish or a
scale-out all come back with a bare image, so provisioning gets redone by hand
every time and a scaled bot has no way to make its instances identical (#926).

**The script is appended to the start command the backend already composes**, in
`BaasService._get_start_cmd`. It travels inside the existing
`after_create_cmd_hook` and is written into the container by the wrapper that
was already there. Nothing downstream knows about it: no `DeployConfig` field,
no dispatcher change, no callback, no run table.

That reuse is the design and it has one consequence stated plainly rather than
discovered later: the platform sees **one** command, so a start has **one** exit
status. The caller's script and the platform's boot share it. Separating them
would take a distinct execution stage with its own result channel, and is
deferred.

## What this module decides, and what it deliberately does not

It owns three rules the repository does not:

- **the size cap** (`MAX_SCRIPT_BYTES`) — refused at write time, so a caller
  learns the limit instead of discovering it inside a container;
- **the audit width** (`MAX_MODIFIER_CHARS`) — the actor is composed by the
  caller and can exceed its column unaided, so it is bounded here rather than at
  whichever caller composes a prefix today;
- **"absent is not an error"** — a bot that never had a script reads as empty.
  The payload-build path composes a shell string and must never branch on
  `None`.

**Support is a property of the bot, answered without touching its container.**
`_support.resolve_support` asks one question — does this bot's container get its
start command from `_build_create_bot_payload`? — because that is the only place
the stored script is resolved. Two provisioners answer no: **teclaw**, whose
container skips `apply_device` entirely, and **desktop**, whose start command is
built on a separate path. Everything else is supported.

The engine half of that question is delegated to
`TeclawProvisionService.is_teclaw` through `TeclawEngineTestProtocol` — a
one-method view declared here rather than a direct import, because importing
that class closes a real cycle (`teclaw_provision_service` → `service_bot` →
`bot_management` → back). The narrow protocol is not tidiness; it is what keeps
this module importable on its own.

Support never reads the live binding. An earlier version keyed on the resolved
`device_provider` and needed a third "we could not find out" state purely to
cover that lookup failing — which made an unrelated blip look like a verdict
about the bot. The cost of dropping it is real and is written into `_support.py`
and the public docs: a legacy ARCA-direct bot is no longer refused, so a script
stored on one will not run.

## Tenancy is load-bearing here

`ac_bots` is itself tenant-scoped, so a `bot_id` is unique only *within* a
tenant — legacy `default` bots carry documented residual collision on that
identifier. Without `avernet_tenant` in this table's key, two such bots share one
script row, and either tenant could overwrite the other's script and have it
**execute** in the other's container on its next start. The column, the guard
registration and the tenant's place in the uniqueness key are all one mechanism;
none of the three works alone.

The uniqueness key is budgeted against InnoDB's 3072-byte index limit, which is
why it is keyed on `(avernet_tenant, script_key)` — a sha256 of
`(env, entity_id, bot_id)` — rather than on those columns directly. `entity_id`
alone is 1024 utf8mb4 characters, 4096 bytes, past the cap on its own.

Narrowing `entity_id` to fit was the first attempt and was wrong: it matched the
column to the index instead of to `ac_bots.entity_id`, so a bot with a longer
entity id could not have a script stored at all. Hashing bounds the key and
leaves the column matching its source. SQLite enforces neither the index limit
nor `VARCHAR` widths, so neither failure was visible locally; an arithmetic test
guards the index budget.

## Where the HTTP seam is

Nothing here reads a framework, a request, or an HTTP status (Rule 7). The
public surface is `adapters/http/openapi_v1/bots/` (`GET` / `PUT` / `DELETE` on
`/openapi/v1/bots/{bot_id}/startup-script`), and the Service API contract the
adapter depends on is `api/bot_startup_script_service.py`.

`BaasService` holds only `StartupScriptReaderProtocol`, a read-only view. The
code that builds shell strings should not be able to reach the write side.

## Context Boundary

```yaml
purpose: Own a bot's startup script — its storage, its limits, and which bots can run one at all.
provides:
  - BotStartupScriptService
  - BotStartupScriptRecord
  - BotStartupScriptModel
  - StartupScriptReaderProtocol
  - TeclawEngineTestProtocol
  - StartupScriptTooLargeError
  - MAX_SCRIPT_BYTES
  - MAX_MODIFIER_CHARS
consumes:
  - "BotStartupScriptRepositoryProtocol (core.repository) — persistence for the one table"
  - "TeclawEngineTestProtocol (bound to core.bot_management TeclawProvisionService) — the single definition of 'runs in a teclaw container'"
consumed_by:
  - "core/service_bot (BaasService) — reads the body while composing a container start command, through the read-only protocol"
  - "adapters/http/openapi_v1/bots — the public read/replace/clear surface"
internal_dependencies:
  - agentclaw.community.api.bot_startup_script_service
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.log
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```

### Change impact

A change here changes **what executes inside a customer's container**, so it is
closer to an authorization boundary than to a data model.

**The composition, not this module, is what makes a script safe.** The body is
base64-encoded before it reaches a shell — never quoted into one — so no quote,
`$(...)` or heredoc delimiter in a caller's script can break out; and base64's
alphabet has no braces, so the downstream `{token}` / `{client_id}` substitution
cannot reach inside it. It runs as `admin` under `su`, because the hook itself
runs as root. Its exit status is discarded and the platform's is re-asserted,
because the wrapper reads the *last* command's code and a trailing `|| true`
would report SUCCESS for every failed boot. Changing any one of those four
changes the security properties, not the style.

**The body is not confidential.** It is stored as written and logged in
recoverable form downstream — the device service logs the first 1024 characters
of the rendered hook, and the encoded body usually begins inside that window.
Backend-side elision covers the backend's own log and nothing else. Anyone
tempted to relax "no secrets in the body" should read that first.
