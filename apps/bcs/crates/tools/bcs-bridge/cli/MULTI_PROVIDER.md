# Multiple Providers in one bridge

One user still has **one active configuration and one bridge process**. A
configuration may now contain up to 64 Providers and 256 Bots, all belonging to
one BCS deployment. Profiles select configurations; startup does not scan or
combine other profiles. Simultaneous staging/production connections and hot
reload are not supported.

## Existing users

- Legacy top-level `provider_id`, `[plugin]`, and `[[bot]]` TOML remains readable.
  Starting it does not rewrite the file or call registration.
- Stop the bridge before modifying registered configuration. Registering a token
  for a **different Provider** adds that Provider to the same file. It does not
  move or re-register the old Bot.
- The first successful cross-Provider registration writes `schema_version = 2`
  with `[[provider]]` / `[[provider.bot]]`, after a private backup beside the
  original file. Further edits keep V2. Single-Provider V1 appends remain V1.
- Old UUIDs, refs, tokens, engine settings, workspaces and database paths are
  retained. An omitted legacy `state_path` retains its actual default,
  `~/.bcn-bridge/bridge-state.sqlite3`, even for a custom configuration location.
- New Providers get independent randomly named SQLite files. Databases are not
  copied or merged. This change introduces no SQL schema migration.
- Registration failure leaves the original TOML intact. If registration succeeds
  but local installation fails, retain the reported private recovery file: do
  not blindly retry registration. Writes use locking, an original-content check,
  private temporary files, fsync and atomic replacement.

## Register and operate

```bash
bcs-bridge stop
bcs-bridge --config /absolute/path/bridge.toml register \
  --token '<new-provider-registration-token>' --bot-name 'Additional Bot' \
  --api-url https://bcs.example/api --upstream-url wss://bcs.example/ws/bot \
  --engine codex
bcs-bridge --config /absolute/path/bridge.toml start --daemon
bcs-bridge status --json
```

Endpoints for an existing Provider cannot silently change on append. New
Providers use their own explicit endpoints or distribution defaults, not those
of an unrelated existing Provider. The public distribution compares endpoint
host/port; distributions can implement `DeployProfile::endpoint_scope` for
known same-deployment API/WebSocket aliases. Unknown external aliases are not a
multi-environment feature.

For a Provider already in the file:

- Subbot registration appends another Bot, with an exclusive working directory.
- Ordinary Providers require `--add-bot` for explicit additional registration.
  Server-side Provider/identity restrictions still apply.
- A matching named profile or V2 ordinary Bot can be reused without a POST.
  `--bot-id` selects an existing Bot for unambiguous reuse or replacement; it
  never implicitly creates a Bot.
- `--replace-existing --bot-id <id>` changes one ordinary Bot's settings while
  retaining its binding. It cannot change a V2 Provider/ref or settings shared
  by sibling Bots. Subbot replacement is not supported.
- Successful register/reuse prints a credential-free `Bot ID: <id>` line for
  onboarding scripts. Name is never used as identity.

## V2 example

```toml
schema_version = 2

[[provider]]
provider_id = "existing-provider"
provider_auth = "static_bearer"
mode = "plugin"
state_path = "existing-state.sqlite3"
api_url = "https://bcs.example/api"
[provider.plugin]
url = "wss://bcs.example/ws/bot"
[[provider.bot]]
provider_bot_ref = "worker"
bot_id = "existing-bot-id"
token = "<existing-bot-token>"
engine = "codex"
cwd = "/workspace/existing"
[provider.bot.registration]
provider_auth = "static-bearer"
api_url = "https://bcs.example/api"

[[provider]]
provider_id = "additional-provider"
provider_auth = "static_bearer"
mode = "plugin"
state_path = "additional-state.sqlite3"
api_url = "https://bcs.example/api"
[provider.plugin]
url = "wss://bcs.example/ws/bot"
[[provider.bot]]
provider_bot_ref = "worker" # The same ref is valid in a different Provider.
bot_id = "additional-bot-id"
token = "<additional-bot-token>"
engine = "claude-code"
cwd = "/workspace/additional"
[provider.bot.registration]
provider_auth = "static-bearer"
api_url = "https://bcs.example/api"
```

Provider `provider_auth` describes the runtime policy. Bot `registration`
metadata records the resolver actually used for that Bot; it does not override
sibling identity policy. Missing historical metadata is not inferred from an
engine name or new registration token.

## Isolation and observability

Each Provider owns its AppState, database, sessions, inject receipts, runs and
interaction registry. Duplicate Provider IDs/Bot IDs, aliased databases,
subbot working directories and exclusive desktop resources are rejected before
runtime starts. Each plugin Bot reconnects independently. A fatal Bot connection
failure does not cancel healthy siblings or other Providers.

Gateway Providers may share one listener. Requests are routed by the declared
`to_bot.provider_id` and then authenticated against only that Provider's token.
Plugin tokens are never used as webhook credentials.

`status` retains its legacy first process-status line. `status --json` adds
`health` and credential-free `bots` with Provider ID, ref, Bot ID, engine and
connection state. Process `running` means initialized, **not remote online**.
Check the selected Bot's `online` (plugin) / `listening` (gateway) state; aggregate
health is `ready`, `connecting` or `degraded`. Gateway listening does not prove
external webhook reachability. These observations are not a desktop channel
health check or proof that an engine can complete a turn.

Subbot children get their own Actor context. Ordinary engine children have
inherited `BCN_AUTH_MODE` / `BCN_ACTOR_BOT_ID` removed; unrelated proxy, CA and
parent authentication environment is retained.

## Desktop resources and rollback

`init/deinit --engine qwenwork --bot-id <BCS-bot-id>` selects one Bot across
Providers; the selector is not the desktop channel's Bot ID. One physical
QwenWork installation/channel or Codex desktop socket cannot be claimed by two
Bots. `EngineFactory::resources` declares exclusive paths/addresses without
starting an engine; subprocess engines default to no claims. QwenWork shutdown
waits for its listener to release so preflight does not occupy runtime ports.

Existing split profile files are **not automatically merged**. Select the
intended file explicitly. A manual consolidation must stop the bridge, back up
all files, preserve absolute state/workspace paths and validate identities,
environments and physical resources. Never re-register Bots to consolidate.

To roll back, stop the new bridge and restore the pre-upgrade V1 backup with a
compatible old binary. Keep new Provider configurations/databases for recovery;
restoring a file does not undo cloud registration. Do not point an old binary at
V2 or assume arbitrary historical versions can read newer database schemas.
