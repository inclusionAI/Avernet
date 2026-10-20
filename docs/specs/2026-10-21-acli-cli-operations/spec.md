# acli CLI operations: per-stage, async-capable, cancellable

Status: DRAFT for review. Builds on the acli CLI API
(`apps/backend/specs/2026-10-15-openapi-v1-acli-clis/`, PRs #2745, #2891,
#2911, #2928). Target branch: `REL20261021`.

## Problem

- **Installs are slow.** Containers keep tools on NAS, so installing a CLI
  with many files can take minutes. Today the engine gives acli 300 s and
  callers give up much earlier: a client failed after 60 s. There is no way
  to start an install and come back for the result.
- **No idempotency in the container.** A second request for the same tool
  runs acli again behind a lock; a dropped HTTP request kills acli halfway.
- **No cancellation**, and no way to see what is in progress.
- **No stage.** The API always acts on the draft runtime. A service bot also
  has verify and online runtimes, each its own container.
- **Grant vs. container.** The Passport grant is one scope per bot, while
  each stage's container has its own installs. "Grant matches container"
  cannot hold for three containers at once.

## Decisions

| # | Decision |
| --- | --- |
| D1 | One set of endpoints. An optional `async` parameter: omitted = sync, `true` = async. Same on engine and backend. |
| D2 | Sync calls wait for the operation to end, returning as soon as it does, for at most **30 s**; then they answer "still running". |
| D3 | The engine runs **at most one acli operation per container** (acli cannot run in parallel). No queue. |
| D4 | Idempotency is on the operation: the same action on the same CLI joins the operation in progress. A different operation while one is in progress is a conflict (409). |
| D5 | Cancellation, per action: `install/cancel` and `uninstall/cancel`, keyed by CLI name (there is at most one operation). A running operation is killed and its effect reverted, so the container ends as it was before the operation. |
| D6 | Backend runs every add and remove as a task-queue task, idempotency key `acli:{bot_pk}:{stage}`: at most one active operation per bot runtime. The task payload is fixed at enqueue. |
| D7 | `stage` parameter: `draft`, `verify`, `online` for service bots, default `draft`. Personal (non-service) bots must omit it; passing it is a 422. |
| D8 | The engine is the source of truth for what is installed. Listing a stage's CLIs asks that stage's engine; a stage without a running container is `409 not_running`. |
| D9 | **Grants are never revoked by remove.** Add grants before it installs. The invariant is: installed in any stage ⇒ granted. Known limitation: a removed CLI keeps its Passport grant. |
| D10 | The task runs the operation regardless of the caller; the API's sync mode only decides how long the HTTP call waits. |

## Engine contract

All under `/api/cli/acli`. Responses use the engine's `ApiResponse`
envelope. `404` still means only "this engine build has no such endpoint".

### Operation

At most one per container, kept with the last finished one:

```json
{
  "action": "install",
  "name": "deepinsight-cli",
  "state": "running",
  "started_at": "…",
  "finished_at": null,
  "result": null,
  "error": null
}
```

| `state` | Meaning |
| --- | --- |
| `running` | acli is running |
| `cancelling` | acli was killed; the engine is restoring the CLI's earlier state |
| `succeeded` | done; `result` set |
| `failed` | definite failure; `error = {code, message}` |
| `cancelled` | stopped and reverted |
| `interrupted` | the engine restarted mid-operation; outcome unknown, safe to submit again |

`result` is `{name, command, description, version, already_installed}` for
install and `{name, uninstalled}` for uninstall.

### Endpoints

| Endpoint | Body | Behaviour |
| --- | --- | --- |
| `POST /install` | `{name, async?}` | Start or join (see table below). |
| `POST /uninstall` | `{name, async?}` | Same. |
| `POST /install/cancel` | `{name}` | Cancel the install of `name` (sync only). |
| `POST /uninstall/cancel` | `{name}` | Cancel the uninstall of `name` (sync only). |
| `GET /operation` | | The current operation, else the last finished one, else `null`. |

Start or join:

| Slot | Request | Answer |
| --- | --- | --- |
| empty | install or uninstall | start it |
| busy with the same action on the same `name` | same | join it (no new acli run) |
| busy with anything else | any | `409 engine_busy` + the current operation |
| — | invalid `name` | `400`, nothing started |

Then:

- **Sync** (`async` omitted): wait for the operation to end, at most 30 s,
  returning as soon as it ends. Ended: `200`, `success` true with `result`,
  or false with `error` and the operation. Still running: `202` with the
  operation.
- **Async** (`async: true`): `202` with the operation immediately.
- Calling again joins, so a sync call after a `202` keeps waiting. Once the
  slot is empty, the installed list is authoritative: installing a tool that
  is installed returns at once with `already_installed: true` and runs no
  acli.

### Cancellation

| Slot | `install/cancel` | `uninstall/cancel` |
| --- | --- | --- |
| install of `name` running | kill acli's process group → `cancelling` → revert → `cancelled` | `409 not_active` |
| uninstall of `name` running | `409 not_active` | mirror |
| `cancelling` for `name` | join and wait | join and wait |
| empty, or another tool | `409 not_active` | `409 not_active` |

- **Revert** restores what the engine recorded when the operation started
  (whether the tool was installed then): a cancelled install that landed
  anyway is uninstalled; a cancelled uninstall that removed the tool is
  reinstalled (the market's current version).
- A revert that fails ends `failed` with `revert_failed`, and the tool's
  actual state is reported.
- Cancel is sync: it waits for `cancelled`, at most 30 s, else `202` with
  `cancelling`; calling it again keeps waiting.

### Running the operation

- The operation runs to its end or its own limit (**30 min**), whoever is
  watching. A caller disconnecting never stops it.
- acli runs with a fixed argument list, in its own process group, with
  `HOME=<parent of install dir>/acli-home`, as today.
- The slot is saved to a file under `acli-home` (persisted volume). On
  engine start, a `running` or `cancelling` operation becomes `interrupted`
  and its stored process group is killed if it is still alive.
- Uninstall keeps today's behaviour: match by name or command, uninstall,
  list again to confirm.

## Backend contract

The same routes on `/openapi/v1/bots/…` and the internal `/api/bots/…`.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `…/{bot_id}/clis?stage=` | Installed CLIs of that stage, from its engine, with `identity_mode` from Passport and the `default` flag. `409 not_running` if the stage has no running container. |
| POST | `…/{bot_id}/clis?stage=&async=` | Add: body `{cli_code}`. |
| DELETE | `…/{bot_id}/clis/{cli_code}?stage=&async=` | Remove. Default CLIs still refused. |
| POST | `…/{bot_id}/clis/{cli_code}/install/cancel?stage=` | Forwarded to the stage's engine. |
| POST | `…/{bot_id}/clis/{cli_code}/uninstall/cancel?stage=` | Same. |
| GET | `…/{bot_id}/cli-operations?stage=` | The stage's current or last operation. |
| GET | `…/market/clis` | Unchanged. |

`stage` (D7): service bots `draft` (default) / `verify` / `online`; any
`stage` on a personal bot is `422`. The stage resolves to its container the
same way the existing engine-runtime routes do (`RuntimeStage`,
`resolve_for_binding`). Access rules are unchanged: list needs MEMBER; add,
remove and cancel need the owner and the edit lock.

### Operation task

- `TaskQueueService.enqueue(task_type="acli_clis.operation",
  idempotency_key=f"acli:{bot_pk}:{stage}", payload=…)`.
- Payload, fixed at enqueue: `{action, cli_code, bot_id, owner_id, stage,
  actor_id, identity_mode}`.
- `created=False` (a task is live for this runtime):
  - same action and `cli_code` → join it;
  - otherwise → `409 operation_conflict` with the live task's
    `{action, cli_code}`.
- Handler:
  - **add**: grant `cli_code` if not granted (read back) → submit the
    install to the engine with `async` → `Reschedule(~3 s)` while the
    engine operation is running → end.
  - **remove**: submit the uninstall with `async` → reschedule until it
    ends. No revoke (D9).
  - The engine's terminal state decides the task's: `succeeded` →
    `Complete`; `failed`, `cancelled`, `interrupted` → `Fail("<code>:
    <message>")`. An `interrupted` engine operation is resubmitted once
    (the engine operations are idempotent) before failing.
  - `deadline_at` ≈ 40 min (above the engine's 30). Every step checks
    actual state first, so a re-run after a lost lease converges.

### API responses

`operation` here is `{action, cli_code, stage, status, step, error?}`:

- `status`: `running`, `succeeded`, `failed`, `cancelled`.
- `step`: `granting`, `installing`, `uninstalling`, `cancelling`. Derived:
  from the engine's operation while it is active, otherwise from the
  task's progress.

| Case | Answer |
| --- | --- |
| Sync, ended within 30 s | `200` with today's data (`{cli_code, command, version}` / `{deleted}`), or the mapped error |
| Sync after 30 s, or `async=true` | `202` with `operation` |
| Another operation live for this runtime | `409 operation_conflict` |
| Add of a CLI already granted and installed in that stage | `200` |
| Remove of a CLI not installed in that stage | `200` |

Cancel: forwarded to the stage's engine, then waits up to 30 s for the task
to end. A cancel that reaches the task before the engine operation has
started (the brief `granting` step) is `409 not_active`.

## Out of scope

- Per-stage grants (D9 limitation).
- Revoking grants of removed CLIs.
- teclaw support (its contract page is updated to the new engine API).

## Delivery

Against `REL20261021`:

1. Engine: operation slot, async flag, cancel with revert, `GET /operation`,
   restart handling.
2. Backend: stage parameter, operation task, async flag, cancel, operation
   status, list from the engine, on both APIs.
3. Docs: API docs (EN/ZH), acli spec/plan, teclaw engine contract page.
