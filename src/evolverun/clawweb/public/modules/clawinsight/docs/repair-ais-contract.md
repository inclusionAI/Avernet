# Repair authenticated execution contract

Task and Step semantics remain ce-repair/v1. The AIS Base launch adapter adds
an optional frozen aisBase configuration to new tasks. Existing tasks retain
their original launch protocol.

One invocation executes one Step. Human approval and context waiting are control
plane states. An ended execution has no decision lease and cannot claim another
Step. The next authorized operation dispatches a new invocation and a new ticket,
using persisted history, checkpoints, approved Plan and action-ledger records.

## Public task and credentials

The public launch parameter contains taskType, taskId, stepId, attempt, input,
runtime. runtime selects a package and a same-origin callback path with a bounded
heartbeat interval. No signed upload URL, bearer ticket or model API key belongs
in the public task/archive. Invocation credentials use a separate platform
parameter.

## Internal endpoints

All endpoints below are under
/api/repair/v1/internal/tasks/:taskId/steps/:stepId and require the existing
execution-scoped bearer ticket.

| Endpoint | Semantics |
| --- | --- |
| POST /ais/validate-report | Validate a successful business report without committing its terminal state; preserve output correction. |
| POST /ais/heartbeat | Renew the existing execution lease through archival. |
| POST /ais/artifacts/:name/upload-url | Authorize an upload to the server-derived canonical runtime archive key. |
| POST /ais/report | Commit Base final result plus business report and archive metadata; final ledger and business checks still apply. |

Allowed runtime archive names are artifactBundle, runtimeBundle and
openclawSessions. Each metadata record has canonical objectKey, positive size,
SHA-256 and application/gzip contentType. Successful Base finalization requires
all three. Failed executions retain any successfully uploaded runtime artifacts.

Only Base finalization can terminate a task using the new protocol. The legacy
/report endpoint remains available for progress, and for legacy tasks.

Duplicate final reports for the same current terminal Step remain idempotent.
A newly launched execution invalidates the previous execution credential.

## User artifact access

GET /api/repair/v1/tasks/:taskId/steps/:stepId/runtime-artifacts/:name requires
task owner or administrator access and validates Step ownership plus the
server-derived object key. It issues a five-minute signed download redirect with
private, no-store caching. Shared task access does not grant session archive access.

## Activation and verification

REPAIR_AIS_BASE_PRE_SNAPSHOT_ID and REPAIR_AIS_BASE_PROD_SNAPSHOT_ID select
the Base snapshot for newly created tasks in the corresponding control plane.
Unset values preserve legacy behavior; active tasks are not rewritten.

Use vitest.repair.config.ts for server-only Repair regression tests. Set
REPAIR_AIS_BASE_ROOT and REPAIR_AIS_SKILL_ROOT to the internal package checkout to
enable the cross-language protocol fixture. REPAIR_TEST_PYTHON selects Python.
The fixture replaces the model and Bot, while using real HTTP and process
boundaries. Live model/Bot behavior and the hosted execution platform require
separate acceptance.

## Initial runtime identity in bootstrap

`bootstrap.tools.runtimeUser` declares the initial operating-system username
used by this service for target `runtime_inspect` commands and approved
`container_command` actions. It comes from the same constant as the command
wrapper. The current policy is `admin`: inherited root is dropped with `su`,
and a different non-root user is rejected. This is not the product owner ID.

This field is a server execution-policy declaration, not a live observation of
UID, groups, PATH, namespace, or directory permissions. It does not establish
equality with an original OpenClaw exec or guarantee that a later write will
succeed. Consumers of an older bootstrap without this field must retain the
identity uncertainty; they must not infer `admin` or `root` from product ownership.
The additive field does not change credentials, authorization or execution rights.

## Repair execution-context self-observation

`runtime_inspect` operation `execution_context` accepts only the usual
`clientRequestId`, `purpose` and operation fields. It executes a fixed, read-only
Python 3 probe through the same initial admin wrapper and ARCA/BaaS transport as
other Repair reads. Python runs with `-I -S -B`; if unavailable, the call fails and
no package is installed. No PID, command, path, user or environment override is
accepted. Only PATH is collected from the environment, never the full environment.

The JSON stdout (`repair-execution-context/v1`) describes the probe process at
observation time: real/effective UID/GID, supplementary groups, cwd, bounded PATH,
PID/parent PID; Linux additionally attempts start ticks, boot ID, effective
capabilities and mount/user/PID namespace IDs. Unsupported, missing and oversized
fields remain explicitly unavailable; no access/write test or filesystem mutation
is performed. Normal transport redaction/truncation still applies to stdout.

`observation.executionContextBinding` is generated by the controller from the
actual Task/Step/attempt and runtime target version/fingerprint. It attributes this
call, including failed calls; it is not a success assertion and is not parsed from
stdout. `initialRuntimeUserPolicy` is the shared wrapper policy, not measured UID.
A runtime fingerprint identifies the routed target, not an OS environment version.
This observation cannot establish the original OpenClaw exec PATH, any future
Repair action identity, or directory write permission. PATH/cwd do not become
verified evidence locators; follow the existing fs_stat locator contract.
