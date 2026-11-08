# Plan: Public API — acli-managed CLIs on a bot

Spec: [spec.md](spec.md)

## Approach

Three layers, each extending something that already exists:

1. **Grant** — AgentPass, through the existing `CliPassportScopeReconciler`
   (`core/mcp/services/cli_passport_scope.py`). `updatePassport` overwrites a
   bot's whole MCP + CLI scope, so the reconciler stays its only writer; it
   gains `add_cli` / `remove_cli`.
2. **Install** — the bot's running engine adapter runs `acli`, reached over
   the `DeviceAdapterTransport` channel that `ArcaCliToolPort` already uses.
3. **API** — a public router under `/openapi/v1/bots/{bot_id}/clis`.

There is no platform-side catalog: the acli market is the catalog, and
AgentPass is the only record of what a bot has. Installed programs persist on
`/home/admin` (shared storage), so there is no reinstall-on-boot work.

## acli facts this plan relies on

Verified by hand against acli 2.0.7 in a production sandbox (2026-10-15):

| Fact | Evidence |
| --- | --- |
| `acli install <name> --install-dir <dir> --yes --json` installs by market name and returns `name`, `command`, `cli.description`, `version`, `installPath`, `success` | `deepinsight-cli` → `/tmp/acli-t/di` |
| A binary product lands in `<dir>/<command>`; an npm product given a `<dir>` ending in `bin` lands in `<dir>/<command>` with its package in `<dir>/../lib` | `yuntu-cli` → `/tmp/acli-npm2/bin/yuntu-cli` |
| acli records installs in `$HOME/.acli/tools`; overriding `HOME` moves that record and its `~/.profile` `PATH` edit | `HOME=/tmp/acli-home` |
| `acli tool uninstall <name> --yes --json` removes the program; without `--yes` it refuses | file gone from `/tmp/acli-t` |
| `acli tool list --json` lists installed tools by `name` | — |
| `acli list --keyword k --page-no n --page-size s --json` pages the market: `list[{name, command, description, version}]`, `total`, `hasMore` | — |
| No login is needed for any of the above | — |
| `/home/admin` is shared storage (`virtiofs`); `/root` is not | `findmnt` |

## Components

### Engine adapter (`engine/adapter/src/engine/community/`)

New `core/acli/runner.py` — `AcliRunner`, the only code that executes acli:

- Every call is `asyncio.create_subprocess_exec` with a fixed argv (never a
  shell), `--json`, a timeout (300 s install/uninstall, 30 s market/list), and
  `HOME=<acli_home>` added to the inherited environment.
- The runner is built with the engine's install directory, the constant
  `ACLI_INSTALL_DIR` (`/home/admin/.agentclaw/bin`). It is fixed, not
  configurable: the agent image puts only this directory on `PATH`.
- `install(name)` → `acli install <name> --install-dir <dir> --yes --json`;
  returns `{name, command, description, version}` from the JSON.
- `uninstall(name)` → first `acli tool list --json`; if `name` (or a
  tool whose command is `name`) is absent, success with `uninstalled: false`
  (nothing of ours to delete); otherwise `acli tool uninstall <name> --yes
  --json`, then `acli tool list --json` again to confirm it is gone. Still
  listed → `uninstall_failed`; a confirmation listing that fails →
  `bad_output`, because the program may already be gone and must not be
  re-granted.
- Installs and uninstalls run one at a time. A request the platform
  abandoned keeps running in the engine, so its cleanup uninstall waits for
  it and then sees what it installed.
- `acli_home` is `<parent of the install directory>/acli-home`, so acli's record sits
  beside the programs on persisted storage.
- Inputs validated before any process starts: `name` against
  `^[A-Za-z0-9@][A-Za-z0-9._@/-]{0,255}$`.
- Failure → `AcliError(code, message)` with `code ∈ {acli_unavailable,
  not_found, install_failed, uninstall_failed, timeout, bad_output}`.
  `message` is acli's `error` field, credential-masked, at most 512 chars.
  Raw stdout/stderr are logged truncated (4 KiB) and masked, never returned.

`api/cli/router.py` (205 lines) gains, with that router's conventions
(refusal = `200` + `success:false` + `error`; `404` only for "endpoint absent"):

| Endpoint | Body / query |
| --- | --- |
| `POST /api/cli/acli/install` | `{name}` |
| `POST /api/cli/acli/uninstall` | `{name}` |

New capabilities `CLI_ACLI_INSTALL`, `CLI_ACLI_UNINSTALL`,
declared supported by the openclaw and claude_code engines.

### Backend — install directory

The backend does not know or send an install directory. The engine owns where
tools are installed (see the runner above); both engines use
`/home/admin/.agentclaw/bin`, the directory the start-up already puts on the
engine's `PATH`, so a tool installed there is runnable without a restart.

### Backend — reconciler (`core/mcp/services/cli_passport_scope.py`, 333 lines)

- `add_cli(*, bot, item)` — snapshot + `item` as an addition, then write.
- `remove_cli(*, bot, cli_code)` — snapshot with `removed_cli_codes`, then write.
- The update + logging body of `reconcile` moves into a private `_write` shared
  by all three; `reconcile`'s behaviour and log lines are unchanged.

### Backend — install port (`core/acli_clis/port.py`, `arca_port.py`)

`AcliPort` protocol: `install`, `uninstall`, `market`, each raising
`AcliPortError(code, message)` on failure. `ArcaAcliPort` follows
`ArcaCliToolPort`: resolve the bot's device, call the engine.

- no device for the bot → `not_running`
- `404` → `engine_unsupported`
- `success:false` → the engine's `error` code and message
- non-2xx / timeout → `engine_unreachable`

### Backend — application service (`core/acli_clis/service.py`)

Guard, run before every write: the bot is `ACTIVE` (else `409 not_running`);
the actor owns it; its engine is openclaw or claude_code. Any bot type
qualifies: device bootstrap reconciles a Passport CLI scope for every bot, and
adding a tool involves no caller identity. The identity switch keeps its own
service-bot rule (`core/caller_identity/guard.py`); `load_bot` and the
lock-epoch check are shared.
The collaborator lock epoch is resolved by the router exactly as the call-type
route does.

**Add** (install first, so an unknown name fails before AgentPass is touched):

1. guard; refuse `409 cli_already_granted` if the code is in AgentPass.
2. port `install(name)`. Failure → error, nothing to undo.
3. If acli's returned `name` ≠ requested → port `uninstall`; refuse
   `422 cli_name_mismatch` naming the market name.
4. `add_cli` with `{cli_code: name, cli_name: name, cli_desc: description,
   identity_mode: owner}`.
5. If 4 fails → port `uninstall`; return the AgentPass error. If that
   uninstall also fails → `500 cli_rollback_failed` with state
   `installed_not_granted`, logged as `acli_cli_rollback_failed`.

**Remove:**

1. guard; `404` if not in AgentPass; `409 default_cli` if in the bot's default
   profile.
2. Remember the tool's current identity mode; `remove_cli`.
3. port `uninstall`. Failure → `add_cli` with the remembered identity; return
   the uninstall error. If that re-grant fails → `500 cli_rollback_failed`
   with state `revoked_still_installed`.
4. The tool's caller/owner override row is left alone, so a later re-add
   restores the identity its owner last chose (add writes that identity,
   `owner` when there is none).

**List:** `current_passport_cli_items` + `default` from the profile
(`required_cli_items`). **Search:** port `market`.

### Backend — public router (`adapters/http/openapi_v1/bots/acli_clis.py` + `schemas_acli_clis.py`)

| Method | Path | Result |
| --- | --- | --- |
| GET | `/openapi/v1/bots/{bot_id}/clis` | `[{cli_code, cli_name, cli_desc, identity_mode, default}]` |
| GET | `/openapi/v1/bots/market/clis?keyword=&page_no=&page_size=` | `{items:[{name, command, description, version}], total, has_more}` |
| POST | `/openapi/v1/bots/{bot_id}/clis` | body `{cli_code}` → `{cli_code, command, version}` |
| DELETE | `/openapi/v1/bots/{bot_id}/clis/{cli_code}` | `{cli_code}` |
| PATCH | `/openapi/v1/bots/{bot_id}/clis/{cli_code}/call-type` | unchanged |

The market search is mounted with the other `/openapi/v1/bots/market/*` routes,
before the `{bot_id}` wildcard. It calls the market API directly
(`core/acli_clis/market.py`, `AcliMarketSearch`): `GET <acli_market.search_url>
?status=published&q=<keyword>` answers `{success, data: [ … ]}` with every match
at once, so paging is done in the backend. The URL is deployment config (prod
and pre differ); empty means `501` not configured.
Status mapping: `not_running` → `409`; `engine_outdated` (the engine's own
route-miss `404`: a supported engine type whose build predates acli) → `409`;
`engine_unsupported` (an unsupported engine type, or an engine `501`) → `501`;
a `404` from the gateway rather than the engine → `engine_unreachable`;
`not_found`, `cli_name_mismatch` → `422`; `install_failed`,
`uninstall_failed`, `engine_unreachable`, `timeout` → `502`; guard errors reuse
the caller-identity statuses. Registration everywhere the call-type route
appears: `openapi_v1/__init__.py`, `authorization_rules.py` (MEMBER read,
owner write), `admission_routes.py`, inventories, the shared status map,
schema snapshots; DI wiring beside `CliPassportScopeReconciler` in the four
device modules.

### Backend — internal router (`adapters/http/acli_clis/router.py` + `schemas.py`)

The same four operations on `/api/bots`, for users authenticated by
`get_current_user` (added 2026-10-20). The actor is always `user.staffId`;
`entity_id` is a required query parameter that only locates the bot; the
service refuses a list to anyone below MEMBER on the bot, the same bar as the
`/openapi/v1` list route. Following
the internal caller-identity routes, the client sends its edit-lock epoch:
`lock_epoch` in the `POST` body and as a query parameter on `DELETE`.

| Method | Path | Input | Answer |
| --- | --- | --- | --- |
| GET | `/api/bots/market/clis` | `keyword`, `page_no`, `page_size` | `{items, total, has_more}` |
| GET | `/api/bots/{bot_id}/clis` | `entity_id` | `[{cli_code, cli_name, cli_desc, identity_mode, default}]` |
| POST | `/api/bots/{bot_id}/clis` | `entity_id`; body `{cli_code, lock_epoch}` | `{cli_code, command, version}` |
| DELETE | `/api/bots/{bot_id}/clis/{cli_code}` | `entity_id`, `lock_epoch` | `{cli_code, deleted}` |

Answers are plain models, as on the other internal routes. Errors are the same
domain errors with the same statuses, through the app-level map
(`adapters/http/acli_cli_status.py`). The market route is declared before the
`{bot_id}` routes.

### Docs

- `apps/backend/docs/bot-config-manifest/engine-requirements.zh-CN.md` §4: the
  three acli endpoints, and why the platform sends the install directory.
- `apps/backend/docs/openapi-v1/README.md` (+ `.zh-CN`): the CLI routes.

## Testing

- **Engine runner:** a fake `acli` script on a temp `PATH` emitting the JSON
  shapes above; covers success, each error code, timeout, missing acli, bad
  input, `HOME` override, and uninstall of a tool acli does not know.
- **Engine router:** contract tests in the style of `test_cli_router.py`.
- **Reconciler:** `add_cli` keeps MCP
  items and other CLI identities; `remove_cli` drops only the target;
  `reconcile` unchanged.
- **Port:** fake transport, one test per response class.
- **Service:** fake reconciler + fake port; every spec acceptance criterion,
  including both rollback paths and both rollback-failure states.
- **Public API:** endpoint tests plus the existing authorization, admission,
  inventory and endpoint-coverage gates.

The open-source image has no acli, so Singlebox E2E cannot run a real install;
the PR states that, and a manual run in a production sandbox is listed as a
release check.

## Risks

- **Lost update on AgentPass.** Whole-scope read-modify-write; two concurrent
  mutations on one bot can drop one. Reduced by the lock epoch the call-type
  route already uses; not fully closed.
- **Rollback failure** leaves one of two named states, reported and logged;
  AgentPass has no transactions to rule it out.
- **Tools installed outside this API** (boot defaults with `HOME=/root`) are
  unknown to the API's acli record; remove only revokes them. Defaults cannot
  be removed anyway.
