# Worker coordination persistence

This module implements MembershipPort, ShardLeasePort and SessionStorePort using
public DocumentCas, StrictLease and ZSet contracts. It contains no backend client,
HTTP transport, environment access or vendor commands. Construction rejects any
missing capability before registering or claiming work.

Owner records and checkpoints each use independent private versions. Claims,
handoffs, sealing, business commits and recovery control writes use exact EQ.
The cached confirmed document permits a normal commit with one write and no
read. Conflicts reload/revalidate; idempotent success reloads the authoritative
owner before returning a receipt. Storage versions never prove a business commit.

A checkpoint contains the public envelope and a private bounded attempt ledger
for its current business head (64 distinct recovery attempts; overflow fails).
A business commit resets both the failure streak and ledger. A seal preserves
both. Known missing checkpoints fail; fresh absence follows SPEC scheme A.
Unsupported checkpoint formats fail before sealing. No initialization marker is
introduced.

Discovery registers the role index before the lease; orphan index records are
ignored. The index has stable member-key continuation and bounded pages. The
strict lease implementation supplies stable termination; a missing or failed
read is Unknown, never evidence for fault takeover.

Handoff timers start at receiver method entry before storage I/O, use local
monotonic time and retain at most one request per shard
with a 4096-shard ceiling. Retries do not restart them. A lost timer means expired
protection, not permission to skip a session seal. The confirmed checkpoint
cache has a 4096-session ceiling; reserve precedes creation. Definite
Fenced/Deferred observations release their slots. The local holder is separate
from an observed document owner so successful transfer can release the source
without a late release deleting the new holder. Unknown writes and known
checkpoint-loss evidence survive errors. The embedding
runtime can release a quiescent session's cache with `SessionStorePort::release_local`;
active or uncertain work must retain its entry. Capacity exhaustion rejects new
work rather than growing memory without a bound.

## Integration and migration

Admission creates spec/task through the same DocumentCas layout that Worker
reads. Legacy GT helpers remain only for other independently validated paths.
Do not mix legacy JSON or GT writers with these records. Initial adoption uses
an isolated namespace with all writers on the new protocol; existing namespaces
require a separate offline migration/recovery procedure. This change does not
automatically migrate live data or enable the Worker in existing bootstrap.

The provider's lease implementation must pass the strict contract before this
store can start. A document-CAS-only backend is insufficient. The test-only model
is a fault-injection fixture, never a production fallback.
