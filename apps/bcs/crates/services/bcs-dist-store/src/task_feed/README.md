# Committed TaskFeed

`StoreTaskFeed` implements the publisher and reader ports from
[Worker runtime SPEC §5](../../../../../specs/2026-11-18-worker-runtime-phase1/spec.md#5-effects输出恢复与-dispatchboard).
It uses public DocumentCas, Stream and ZSet plugins, immutable Result/Blob ports
and the same pure projection used by the Worker. It does not require native
StreamRecovery or infer deletion evidence from a basic Stream read.

An append reserves one exact source effect in the Session output-head document.
Explicit EventIds are confirmed in the main event stream and a per-Task display
stream before a persistent completion record advances the visible head. The
display stream excludes Thinking and other Tasks, bounding restore independently
of their history. Unknown appends read the exact ID and compare content; an older
ID alone never proves success. Retained completion evidence permits exact replay
after the checkpoint outbox advances. Changed content remains a conflict.

Snapshot publication checks the committed outbox, frozen Task limits, confirmed
append coverage, monotonic diagnostic usage and the original source commit. State, display
reference and coverage change in one EQ. A terminal event or snapshot requires
the matching immutable archive, including its digest and complete display.
Archive success alone does not expose a terminal Task. Ref values are resolved
and checked before projection or follow delivery.

Trimming persists a fixed scan cut and progress. Each removed event needs a
retained append completion and durable Task snapshot coverage; terminal Tasks
also need their archive. Per-Task logical boundaries precede display deletion,
and the Session boundary precedes main-stream deletion. Resume compares the
highest actually removed event, preserving valid cursors between sparse IDs.
Reads recheck logical boundaries even while native data remains present. An
interrupted trim resumes the original cut before considering a new request.
No completion, boundary or archive evidence is expired or garbage-collected.

The default RPC profile allows 128 entries, 1 MiB native page bytes, 32 pages
and a 2-second total restore/list/trim deadline. Restore's page count includes
a possible empty tail probe; exhaustion is retryable and never a partial view.
Individual I/O is bounded by 2 seconds; each EQ retry episode shares 500 ms,
at most eight attempts and 5–200 ms backoff. These are reader/provider bounds,
not measured deployment capacity or the maintenance profile from SPEC §8.2.

Follow admits at most 128 subscriptions and retains at most 256 KiB of Task
metadata per subscription. Native pages remain at most 1 MiB. Resolved delivery batches target 1 MiB
plus 64 KiB for the event/cursor envelope; a single larger output is delivered
alone. Output units, complete snapshots and cumulative output have no semantic
byte quota. Immutable Blob resolution verifies digests without an output size
cap. Pagination never skips an event that did not fit. A Gap,
terminal event, error or dropped stream releases the subscription. List cursors
bind namespace, scope and filter and advance scanned index members, including
dangling members not yet created by admission.

`tests/task_feed.rs` exercises actual store-provider logic with the public memory
adapter and SQLite archive: partial append visibility, lost replies, late replay,
Ref digest and large-output delivery, archive/terminal ordering, trim races, sparse
listing and stalled-read/CAS deadlines. These tests do not prove host throughput,
provider failover, or the complete Worker execution pipeline.
