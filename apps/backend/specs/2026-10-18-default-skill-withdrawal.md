# Default Skill withdrawal and complete shared-link convergence

## Accepted scope

Default Set edits update metadata without fan-out or proactive Runtime delivery.
On the next effective read, remove only Installations with recorded DEFAULT
provenance whose Default source is no longer effective and which no active Set
still supplies. Preserve DIRECT and historical UNKNOWN installations. Ordinary
Set and Manifest source conversions write provenance in the Installation UoW.
Provenance is an additive one-to-one table, not another effective-state reader.
There is no historical attribution/backfill. Deploy the additive SQL before
upgrading Backend; application rollback leaves the table in place.

Every complete filesystem Skill projection reconciles unselected Repo/Center
active symlinks. Local links remain user-writable and are removed only by the
existing explicit retirement contract. Real directories, external links and
structural corpus bridges remain outside automatic cleanup. Repo roots include
Legacy, Pool and descriptor-owned bridges; Center uses canonical Pool Center.
Only unlink active entries; never remove corpus bytes. A nested source symlink
escaping a corpus remains external. Classification includes dangling links.

No new request parameter, cleanup mode, Engine manifest or task queue is added.
The same closure runs after an ordinary capability mutation and after restart.
Published Service Bots continue to consume frozen Artifacts via new releases.

## Completeness and failure semantics

The declaration is authoritative only after parsing/validation succeeds. Do not
derive deletion from a download/preparation-success subset: pending Center
packages still protect their declared link names. Empty valid declarations
clear shared links; failed DB reads must not produce an empty declaration.
Explicit retired_mappings keep their exact-identity Local/Repo/Center behavior.
Publishing and verification use the same closure. Cleanup failure is unresolved,
never CONVERGED. Reapplying the latest snapshot after a crash is idempotent.

Installation origin distinguishes DEFAULT, DIRECT and SKILL_SET. Missing origin
means UNKNOWN. Default materialization never overwrites DIRECT/UNKNOWN origin.
A permitted direct claim on an existing Installation updates its origin before
returning idempotent success. Source removal and Installation removal share a
transaction; compensation restores provenance with the previous desired state.
All identity predicates include tenant, env, owner and Bot; Bot id alone is not
unique. Default source membership ids are unnecessary for this current-state
algorithm: no delayed retirement event can target an earlier membership.

## Propagation and validation

Community Backend owns provenance model, SQL/UoW and Reader synchronization.
Community Engine owns read-only corpus classification and full-snapshot cleanup.
OCB composes both through its Avernet gitlink; simulator must build this exact
source for Backend and Engine and prove file readback, not just service health.
Existing Engine images retain older preservation behavior until upgraded.
Targeted tests cover Default removal in both reader modes, Direct conversion,
UNKNOWN preservation, owner/tenant scoping, transaction rollback, source restore,
Legacy/Pool layouts, explicit Local removal, dangling and external entries,
empty snapshots, failed preparation, validation and unlink failures.


Steady-state filesystem delivery sends the resolved plan directly to Engine without
acquiring a Backend cache lease or re-reading the plan in a delivery guard.
Engine retains its existing per-runtime serialization. This does not order
concurrent snapshots by desired-state revision: a delayed old request may apply
after a newer request. No fencing token or new recovery mechanism is introduced.
Existing Desktop recovery continues to retry unresolved runtime delivery.
Operator-managed PostgreSQL/SQLite use the matching dialect SQL beside the
MySQL migration. Rollout order: additive schema, Engine image, then Backend.


## Simulator verification

The OCB simulator used the modified Backend against its real MariaDB and an
isolated Arca Engine container carrying both repositories' current sources.
Production Installation UoW/resolver and Backend mapping client delivered full
snapshots over the real Engine HTTP route. File readback verified Default
withdrawal with Direct retained, Legacy/Pool Repo and canonical Center pruning,
Adapter restart with an injected stale link, valid empty snapshots, Local and
external preservation, and explicit Local retirement without deleting bytes.
Only dedicated test metadata was changed, then removed. This verifies Skill
projection and Adapter restart; it does not claim a new BaaS/Gateway allocation,
model conversation or published Service release was exercised.

Review follow-up rebuilt the OCB simulator Backend and matched installed source
hashes. A controlled probe using its real Redis cache verified a cache fault
returns Retry and the next attempt converges; template_type and template_config
physical-identity changes reject old plans in both directions without sending.
This probe uses controlled Bot/asset reads and transport, not a new live Bot.
