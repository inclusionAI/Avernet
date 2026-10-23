# Automatic planning ownership

`createWorkflowAutomation().tick()` now claims one planning lease per database and execution environment before scanning policies. PRE and PROD have different identities. The existing journal stores one `auto_planner` record per environment (empty workflow ID, dedicated task ID); no schema migration or real workflow is created.

- The unique record key and `state_version` compare-and-swap choose one owner. Other replicas skip automatic planning, but may still drain the independent notification queue. This does not eliminate notification configuration reads.
- A lease lasts five minutes and is renewed at workflow boundaries. A failed/crashed owner can be replaced after expiry. A stale owner cannot update the cursor or start another workflow without a valid checkpoint. There is no transaction held over network I/O.
- Workflow IDs are sorted and the last visited ID is persisted. Changing replicas or restarting does not repeatedly prioritize the first ten workflows. Each pass visits at most ten.
- A completed/released pass enforces a shared fifteen-second cooldown. This is a planning opportunity, not a schedule to generate a task every fifteen seconds; daily due checks remain authoritative.
- Existing workflow-cycle and individual analysis/draft claims, stable request IDs, policy checks and result recovery remain the dispatch safety boundary. This is not an exactly-once guarantee for external providers. Long-running work is not forcibly cancelled when a planner lease expires.
- Disabled, never-dispatched analysis/drafts are excluded before the ready-page LIMIT, without repeated claim/status writes. Already-dispatched work can still settle; an unsubmitted next batch remains paused when automatic drafts are off. Actively leased jobs are also excluded before LIMIT.

## Diagnostics and rollout

Search `[workflow-background]` with `job=workflow_automation`. `reason=PLANNER_NOT_CLAIMED` means another owner or the cooldown prevented planning; `errorCode=AUTOMATION_PLANNER_LEASE_LOST` fences an expired/stale owner. Correlate environment, instance, PID and runId. Existing per-job analysis/draft dispatch logs remain available. Do not log the lease token or business payloads.

The new `auto_planner` journal record's `cursor`, `untilMs`, `nextAtMs` and `state_version` support read-only ownership/cadence checks. Do not clear it to force a run during incident diagnosis. Replica clocks must be synchronized, as with existing automation job leases.

This change does not reconnect the stopped planner to the public Host, restore broad repair/history scans, enable workflow switches, or change deployment configuration. Enablement remains a separate canary decision after the asynchronous Git prerequisites are integrated. During a mixed-version rollout, old code does not honor the new planner lease, so do not enable planners until all participating instances run the guarded implementation. Keep the existing job claims in place.
