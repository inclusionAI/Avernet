# `plugins.evolution`

Runtime adapters for isolated RSI strategy execution.

- `sandbox_worker.py` adapts the platform sandbox runtime client to the
  evolution worker sandbox port.
- `genome_registry.py` adapts the platform Genome Registry to the independent
  `GenomePort` (snapshot/commit only; never moves active).
- `job_http.py`, `entrypoint.py`, `kubernetes_worker.py`, `smoke_entrypoint.py`
  and `experience_redaction.py` are deprecated import aliases to canonical
  Evolution implementations; no duplicate Worker/HTTP/security logic lives here.
- `session_experience.py` is an **explicit acceptance-only** Engine-session
  snapshot provider for 02. It uses `EngineRuntimeRelayProtocol` and the
  engine-specific `SessionKeyCodecRegistry`; it never reads Engine filesystem
  paths. No production DI binding or new public API is installed.
- Snapshot sanitization and Worker entrypoints are owned by `avernet_evolution.plugins`.

Strategy execution belongs in the isolated worker, not the backend process.
Keep credentials out of the worker environment beyond the narrowly scoped job
credentials, and keep platform SDK dependencies behind these adapters.

See the independent [Evolution guide](../../../../../../evolution/README.md) for the
host/worker lifecycle and [`docs/bot-evolution/README.zh-CN.md`](../../../../../docs/bot-evolution/README.zh-CN.md)
for the current overall status and known verification gaps.

## 02 snapshot acceptance boundary

`SessionSnapshotExperience` receives a repository-resolved, full `BotRef` and a
private SQLite path. `ingest` rechecks runtime facts and reads **one explicit
session** through the authorized relay. Source identity, stages, timestamps,
message shape and text bounds fail closed. Current Engine `tool_use` / `tool_result` roles are preserved alongside
user/assistant/system (and legacy `tool`); unknown roles and non-string content
are rejected before persistence. Only sanitized `{role, text}` turns
are stored; raw metadata/tool payloads are dropped. Recognizable credentials,
credential assignments, email and Chinese mobile numbers are redacted. This is
**not a complete production DLP policy** (for example, addresses and arbitrary
PII are not recognized).

Retries of the same snapshot are idempotent, including after reopening the DB.
Changed source data creates immutable new evidence; queries return the latest
inserted distinct snapshot per session/stage, not an overwritten historical
record. The DB is mode `0600`; the CLI creates its containing directory `0700`.
Use only synthetic conversations on a disposable Bot and keep the DB outside Git.
There is no retention/automatic cleanup job in this acceptance provider.

Queries enforce all `BotRef` coordinates, a bounded time window and result count.
The test path marks `runtime_stage=draft` as `draft`, including for a personal
Bot, and excludes it by default; `include_draft=True` is an explicit acceptance
exception, not an assertion that a personal Bot's real traffic is draft under
02's final domain contract. `online` requires a service Bot and maps to
`service`; `verify` is rejected.

This is deliberately **not** `session-export/v2` or the complete Episode schema:

- `source.export_contract` is `engine-session-api/snapshot@1` and
  `history_complete=False`: the current Engine history API can truncate upstream
  even without an explicit limit, and metadata/history are not an atomic export.
- The session-start revision is unavailable, so `revision_id=None`; filtering
  by a known revision returns no matches. Never substitute today's `active`.
- Unknown cost/tokens remain `None`, outcome remains `unknown`; raw model/tool
  metadata, attribution, full redaction categories and feedback are not provided.
  `feedback()` raises `NotImplementedError`, never a misleading successful `[]`.
- Session discovery, complete/paged export, feedback ingestion/query, governance,
  retention and production DI remain future work. Final ownership remains
  `apps/evolution` under ADR 0021, not this temporary Backend acceptance bridge.

Tests: `tests/community/plugins/evolution/test_session_experience.py` covers
source failures, write failures, stages, scope, redaction, immutable snapshots
and restart/idempotence. The real-provider HTTP Worker/Registry/Ledger integration
is in `tests/community/core/bot_genome/test_evolution_run_integration.py`.
Those tests use fixture Engine responses; they are **not live simulator proof**.
See the [platform acceptance guide](../../di/evolution_acceptance/README.md) for the
explicit live-session CLI and preconditions.

## Explicit Kubernetes acceptance worker

`avernet_evolution.plugins.kubernetes_worker` implements the existing `JobWorkerSandbox` Plugin API,
selected only by the standalone acceptance composition root, not production DI.
`create` installs the NetworkPolicy before creating a fixed-entrypoint Pod;
`exec` validates that entrypoint and waits for its exit, without remote exec.
`destroy` waits for Pod removal before deleting its policy. Failed cleanup is an
error, not a success; API failures can require manual scoped cleanup.

Build independently from the repository root (public base/dependencies):

```bash
docker build -f apps/evolution/Dockerfile -t evolution-worker:acceptance .
```

Resolve and use an immutable digest visible to the target cluster. The image
contains only the independent Evolution package and shared Genome contracts,
**not** Backend/corp configuration or credentials. Its default entrypoint is
`evolution serve` (controller). A Worker deployment must explicitly override it
with `python -m avernet_evolution.plugins.entrypoint`, or
`python -m avernet_evolution.plugins.smoke_entrypoint` for this acceptance adapter.
The Kubernetes adapter sets the command explicitly. The retired Backend Worker
Dockerfile is not a supported build entry. `smoke_entrypoint.py` bakes in only the reference identity-hardening
strategy without 07 verdict polling. It checks actual non-root UID, EROFS on a
rootfs write probe and absence of the Kubernetes SA token before claiming work.
The safe `worker.isolation.checked` stdout event is independent of the platform's
run journal. It is not a network-enforcement probe.

**Isolation tier:** untrusted strategy in a disposable non-root container. Only
three job variables are injected; no service links, SA token, host mounts,
privileged capabilities or shell injection. Readonly root, RuntimeDefault seccomp,
dropped ALL capabilities, no escalation, bounded `/tmp` (64 MiB), finite deadline
and digest-pinned image are enforced in the Pod spec. Standard spec hard limits
are 2 CPU/4096 MiB; this acceptance adapter reserves at most 1 CPU/256 MiB to fit
small shared simulators. This does not lower the hard limits or constitute a
production sizing policy. Lower spec limits also lower requests.

Ingress is denied and egress is restricted to the exact literal Job endpoint IP
and TCP port (no DNS). Policy objects alone are not proof: require an enforcing
CNI and positive/negative connection probes to a known listener. Kubernetes
node/host networking exceptions and container-kernel isolation limitations still
apply; this is not a VM boundary or a general hostile-code security certification.
The acceptance controller has only dedicated-namespace Pod/NetworkPolicy rights;
its credentials stay outside Worker env/filesystem and must be removed afterward.

The existing platform `sandbox_worker.py` is separate: it converts MiB to rounded-up
GiB and raises when destruction is unconfirmed. Its `OutBoundOperationRule` is
**not a demonstrated network ACL**, and the platform API does not express per-Pod
readonly-root settings. Those guarantees require independently reviewed/enforced
template and network policy; do not infer them from `JobWorkerSpec` alone.
