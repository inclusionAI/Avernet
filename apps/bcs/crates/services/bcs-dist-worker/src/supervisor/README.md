# Independent Dispatch supervision

`DispatchSupervisor` implements [phase-one SPEC](../../../../../specs/2026-11-18-worker-runtime-phase1/spec.md)
§7.1 and §7.2 through public repository ports. It does not own an Agent client,
Runner, shard claim, connector actor, or remote retry algorithm. Host assembly
calls `tick` independently of business execution and continues driving Worker
heartbeats. A tick with no available work is a scheduling yield, not a reason to
spin synchronously.

## Outcomes and durable authority

`scan_due` supplies discoverable did hints; `get` supplies the authoritative
Dispatch record and opaque revision. Unclaimed work receives a command hint.
Claimed, sending-unknown, accepted, deadline-expired and cancel-pending work keeps
its remote obligation and is rechecked. Unknown holder liveness only schedules
a later check; it never grants takeover or invents a remote failure. There is
no remote send API in this component.

A definite terminal phase requires a matching definite outcome and retained,
nonempty terminal evidence. The immutable SessionSpec must match sid and shard;
its exact scope is used for Inbox admission. The Inbox provider additionally
checks the frozen task scope. No Task or Session control mode gates critical
supervision: Done and Blocked remain eligible for convergence. The processor's
consumed-only path prevents a late critical input from invoking an ended Task's
Runner or changing its immutable archive.

`TerminalDispatchEvidence` is the public, versioned payload. It contains the did,
definite dispatch outcome and retained evidence, with `source=dispatch_supervisor`.
It is terminal evidence, **not** the remote Result body or missing output. Completed
and Canceled emit `AgentEventKind::Final`; Failed emits `Error`. No Task status is
synthesized here.

The synthetic remote event identity uses the shared length-prefixed SHA-256
encoding of these fixed fields:

1. `dispatch-terminal:v1`
2. did UTF-8 bytes
3. stable outcome tag `completed`, `failed`, or `canceled`
4. retained evidence UTF-8 bytes

The remote ID is `dispatch-terminal-v1:<hex>`, and InputId uses the shared
`critical_agent_input_id(did, remote_event_id)` algorithm. Holder and time never
enter either identity; synthetic event holder/sequence are zero. JSON field
ordering does not change identity. Inbox computes its canonical business digest.
The entire admission request is capped at 16 KiB, and encoded terminal evidence
at 4 KiB. No start/task/session creation fields are set.

Only an Accepted or Duplicate receipt matching sid/tid/InputId authorizes exact
revision `reschedule`. A lost admission reply retains the same request; process
restart reconstructs it from immutable evidence. Conflicts, invalid evidence,
quarantine responses and timeouts leave the durable obligation intact. A stale
reschedule releases the local slot and lets the retained directory rotate back
to the current revision. Three consecutive failed attempts likewise release
only local cache; they never mark the remote dispatch ended. This prevents bad
or constantly changing entries from filling the pending queue forever.

## Work and memory bounds

Discovery and processing visits alternate. Discovery issues one `scan_due` call.
A processing visit starts at most eight concurrent jobs, each performing exactly
one repository call for its current phase (get, liveness, notify, spec, admit or
reschedule). No job serializes multiple two-second calls in one visit. Every call
has a configurable timeout, at most two seconds. Retry delays use Tokio monotonic
time, grow exponentially, and cap at five seconds. Notification failure cannot
withdraw the already durable Dispatch and is followed by a later recheck.

The queue defaults to 64 jobs and reserves 24 KiB per job (1.5 MiB total). It stores
compact facts and bounded requests, not full DispatchViews or SessionSpecs. At
most one temporary phase copy per concurrent job adds 192 KiB at the default
concurrency. Up to 1,024 shard cursors retain at most 1 KiB each, separately
from pending-job reservations. Returned provider documents have their separate provider byte caps;
the concurrency bound also bounds simultaneous reads. Discovery asks for no more
than the free entry and byte reservations, at most 128 dids. Shards and ready jobs
rotate; job originals stay in the queue throughout awaits, so canceling a tick
does not drop pending identities or leak reservations.

All ports must report the same shared ScanBudget profile. Constructor validation
rejects absent or mismatched Dispatch/Inbox profiles, IO slots above eight or a
quarter of the actual configured pool, and rates above the SPEC maxima. A denied
`try_io`/quota immediately yields rather than waiting while occupying work.
`get` charges one Dispatch log check and `get_spec` one Session check. Each other
phase still requires a shared IO permit. `scan_due` is already metered inside its
provider and is not wrapped in a second permit.

A typical nonterminal item costs one get, optional holder liveness, one notify
and one revision CAS. A terminal item costs one get, one immutable spec read,
one Inbox admission and one revision CAS. These are repository calls, **not**
single database round trips: Inbox admission follows its own journal/CAS/append
protocol; notify appends and trims a disposable command stream; membership and
reschedule may themselves read/check/CAS. Bounded phases and shared IO do not
claim constant storage cost or a measured discovery SLA.

## Verification boundary

`tests/dispatch_supervisor.rs` composes actual Coordination, Inbox, TaskFeed,
Dispatch and SQLite archive providers on StoreModel, with fault wrappers around
selected replies. It covers stable unknown retries, re-creation, Unknown holder,
missing evidence, Done/Blocked convergence, stale revision fairness, local byte
capacity, shared budget denial, shard timeout rotation and tick cancellation.
This is public runtime verification; it is not PROXY transport acceptance or a
production capacity measurement.
