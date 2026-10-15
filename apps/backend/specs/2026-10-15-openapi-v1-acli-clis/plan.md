# Plan: Public API — acli-managed CLIs on a bot

Spec: [spec.md](spec.md)

## Approach

Three layers, each extending something that already exists rather than adding
a parallel mechanism:

1. **Grant** — AgentPass, through the existing `CliPassportScopeReconciler`
   (`core/mcp/services/cli_passport_scope.py`). `updatePassport` overwrites the
   bot's whole MCP + CLI scope, so the reconciler stays the only writer; it
   gains `add_cli` / `remove_cli`, both built on the existing `_build_snapshot`
   and `build_passport_resource_scope(additional_cli_items=…, removed_cli_codes=…)`.
2. **Install** — the bot's running engine adapter, over the same
   `DeviceAdapterTransport` channel `ArcaCliToolPort` already uses for the
   manifest `cli-tools` push. Two new engine endpoints run `acli install` /
   remove the executable.
3. **API** — a new public router under `/openapi/v1/bots/{bot_id}/clis` plus a
   catalog read, registered in the same inventories every public route is.

Restart reinstall is untouched: it stays with the external boot installer,
which reads the same AgentPass grant (spec, Out of Scope).

## Components

### Backend — manifest resolver (`core/mcp/services/cli_capabilities.py`, 239 lines)

Add read-only accessors; no new parsing rules:

- `catalog_items() -> list[CliItem]` — code, name, description, default identity.
- `install_spec(code) -> CliInstallSpec | None` — frozen dataclass:
  `package` (the acli identifier, i.e. the `install.argv` tail after `install`),
  `executable`, `install_dir` (manifest `managed_bin_dir`). The loader keeps
  these today only long enough to validate them; it now stores them.
- `default_cli_codes(engine_type, template_type) -> frozenset[str]` — what
  `required_cli_items` already computes, as codes.

`managed_bin_dir` is also validated as absolute with no `..` (the external
installer already enforces this; the backend currently ignores the field).

### Backend — reconciler (`core/mcp/services/cli_passport_scope.py`, 333 lines)

- `add_cli(*, bot, cli_code) -> CliScopeReconcileResult` — snapshot + the
  catalog item as an addition. History-first merge keeps an existing entry's
  identity, so re-adding is a no-op on the grant.
- `remove_cli(*, bot, cli_code) -> CliScopeReconcileResult` — snapshot with
  `removed_cli_codes={cli_code}`.
- Both reuse the existing update + structured logging path in `reconcile`; the
  shared body is factored into a private `_write(snapshot, …)` so the three
  public methods log identically.

The internal `DELETE /skill-sets/{id}/clis/{code}` keeps its own code path; it
is not migrated in this change.

### Backend — application service (new `core/acli_clis/`)

`AcliCliService` orchestrates; it holds no policy the pieces below already own.

| Method | Steps |
| --- | --- |
| `list_catalog()` | resolver `catalog_items()` |
| `list_bot_clis(bot_id, owner_id, actor_id)` | load bot → reconciler `current_passport_cli_items` → annotate `default` (profile codes), `managed` (in catalog) |
| `add(bot_id, owner_id, actor_id, cli_code, lock_epoch)` | guard → catalog check → `add_cli` → port `install` → result with `install_status` |
| `remove(bot_id, owner_id, actor_id, cli_code, lock_epoch)` | guard → held? → not default? → `remove_cli` → port `uninstall` (only for catalog tools) → result with `uninstall_status` |

Guard = exactly the checks `CallerIdentityService.update_cli_call_type` applies
today: actor is the owner; bot is `service` + `ACTIVE`; the reconciler supports
the bot's profile. The collaborator lock epoch is resolved by the router the
same way the call-type route does. The check is lifted into a small shared
helper in `core/caller_identity/` rather than copied (that service is 789
lines and must not grow).

Order of operations and failure handling:

- **Add:** grant first, then install. Grant failure → error, nothing installed.
  Install failure → `200` with `install_status: failed` and a safe reason; the
  grant stays (spec). No running container → `install_status: not_running`.
- **Remove:** revoke first, then uninstall. Revoke failure → error, nothing
  touched. Uninstall failure → `200` with `uninstall_status: failed`.

Domain errors (new, `core/acli_clis/errors.py`): `CliNotInCatalogError`,
`CliNotHeldError`, `DefaultCliRemovalError`; permission/read-only/sync errors
reuse the caller-identity ones so the shared HTTP status map already covers
them.

### Backend — install port

- Protocol `AcliInstallPort` in `core/acli_clis/port.py`:
  `install(bot_id, owner_id, spec) -> InstallOutcome`,
  `uninstall(bot_id, owner_id, spec) -> InstallOutcome`.
  `InstallOutcome.status ∈ {installed, uninstalled, failed, not_running}` +
  optional `reason`. The port never raises for an engine refusal; the service
  needs the outcome, not an exception, because the grant has already changed.
- `ArcaAcliInstallPort` — structured like `ArcaCliToolPort`: resolve the bot's
  device, POST the engine endpoint, map responses:
  - resolver reports no device → `not_running`
  - `404` (engine build without the endpoint) → `failed`, reason
    `engine_unsupported`
  - non-2xx / `success:false` / timeout → `failed`, engine message (already
    masked engine-side)
- Wired in the four DI device modules next to `CliPassportScopeReconciler`.

### Backend — public router (new `adapters/http/openapi_v1/bots/acli_clis.py` + `schemas_acli_clis.py`)

| Method | Path | Body / result |
| --- | --- | --- |
| GET | `/openapi/v1/bots/clis/catalog` | `[{cli_code, cli_name, cli_desc}]` |
| GET | `/openapi/v1/bots/{bot_id}/clis` | `[{cli_code, cli_name, cli_desc, identity_mode, default, managed}]` |
| POST | `/openapi/v1/bots/{bot_id}/clis` | `{cli_code}` → `{cli_code, granted: true, install_status, install_reason?}` |
| DELETE | `/openapi/v1/bots/{bot_id}/clis/{cli_code}` | → `{cli_code, revoked: true, uninstall_status, uninstall_reason?}` |
| PATCH | `/openapi/v1/bots/{bot_id}/clis/{cli_code}/call-type` | unchanged |

The catalog route is mounted before the `{bot_id}` wildcard, following the
`/openapi/v1/bots/mcp` precedent. Bot-scoped routes use
`require_granted_addressed_bot` like `bot_mcp_router`.

Error mapping: not in catalog → `422`; not held → `404`; default removal →
`409`; permission → `403`; read-only/unsupported profile → existing caller
identity status; AgentPass failure → existing `CallerCliSyncError` status.

Registration — every place the call-type route already appears:
`openapi_v1/__init__.py`, `authorization_rules.py` (MEMBER read, owner-only
write), `admission_routes.py`, route/endpoint inventories, the shared HTTP
status map, and the response-schema snapshots.

### Engine adapter (`engine/adapter/src/engine/community/`)

- New `core/acli/installer.py` — `AcliInstaller`:
  - `install(package, executable, install_dir)`: validate all three (same code
    regex as the backend; `install_dir` absolute, no `..`); run
    `["acli", "install", package, "--install-dir", install_dir, "--yes"]` with
    `asyncio.create_subprocess_exec` (argv only, no shell), 300 s timeout; then
    probe `[install_dir/executable, "--version"]`. Both flags were verified
    against acli 2.0.7 in a production container.
  - `uninstall(executable, install_dir)`: unlink `install_dir/executable`;
    absent counts as success, matching `LocalCliToolsService.delete`.
  - acli missing from `PATH` → failure with reason `acli_unavailable` (the
    case in this repo's own image, which does not ship acli).
  - stdout/stderr captured, truncated to 4 KiB, credential-masked, logged; the
    response carries only a short reason.
- `api/cli/router.py` (205 lines) gains `POST /api/cli/acli/install` and
  `POST /api/cli/acli/uninstall`, keeping that router's response conventions
  (refusal is `200` + `success:false`; `404` stays "endpoint absent").
- `Capability.CLI_ACLI_INSTALL` / `CLI_ACLI_UNINSTALL`, declared supported by
  the openclaw and claude_code engines.

**Install directory comes from the backend, not the engine.** This departs from
the manifest `cli-tools` rule ("the engine owns placement") on purpose: the
directory must be the one the external boot installer uses and has put on
supervisor's `PATH`, and the manifest is that single source. A second copy in
engine config would drift exactly as the device YAML did.

### Docs

- `apps/backend/docs/bot-config-manifest/engine-requirements.zh-CN.md` §4: add
  the two acli endpoints and the install-directory exception.
- `apps/backend/docs/openapi-v1/README.md` (+ `.zh-CN`): add the CLI routes.

## Testing

- **Resolver:** accessors, `managed_bin_dir` validation.
- **Reconciler:** add keeps MCP items and existing CLI identities; re-add is a
  no-op; remove drops only the target; AgentPass failure propagates.
- **Service (fake reconciler + fake port):** every acceptance criterion in the
  spec, including install `failed` / `not_running`, default-removal refusal,
  non-catalog refusal, owner-only guard.
- **Port:** fake transport — each response class maps to the right outcome.
- **Engine:** installer with a fake `acli` script on a temp `PATH`
  (install/probe success, install failure, probe failure, acli missing, bad
  names, absent file on uninstall); router contract tests in the style of
  `test_cli_router.py`.
- **Public API:** endpoint tests in `tests/community/adapters/http/openapi_v1/`;
  the existing authorization/admission inventory and endpoint-coverage gates
  must pass with the new routes registered.

Runs: backend focused suites for the touched packages plus
`tests/community/adapters/http/openapi_v1/` and the architecture tests; engine
`core/acli` + `api` tests. Singlebox E2E cannot exercise a real install (no
acli in the open-source image); stated in the PR.

## Risks

- **Lost update on AgentPass.** Read-modify-write of the whole scope; two
  concurrent mutations for one bot can drop one. Mitigated by the collaborator
  lock epoch the call-type route already uses; not fully closed.
- **`~/.profile` side effect.** acli appends a `PATH` line to the running
  user's `~/.profile` on every install. Harmless for the engine (it uses its
  own `PATH`) but accumulates. Left as is; see open question.
- **Restart gap in production** until the external installer's manifest is
  redeployed (spec, Out of Scope).

## Open Questions

- Should the engine suppress acli's `~/.profile` write (e.g. run it with a
  scratch `HOME`)? Not done by default because acli's own state/config
  location under `HOME` is unverified.
