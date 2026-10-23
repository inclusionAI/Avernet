# Repair background scan coordination

Apply `01-repair-scheduler.mysql.sql` before rolling out the coordinated Repair
and session-recovery scans on managed MySQL/ZDAS installations. Local databases
use migration 147. Version 146 remains reserved for the withdrawn automation
history migration.

`ce_repair_scheduler_leases` stores one ownership record per control-plane
environment and background job: agent execution reconciliation, recovery
maintenance, and recovery result reporting. It contains no business tasks or
Agent execution tickets. Existing Task, Step, tool-audit and result-delivery
storage and business policies remain unchanged.

A scan claims a 60-second lease using database time, renews every 20 seconds,
and checks ownership before starting more work. The shared next-run time limits
scan starts to once per 20 seconds across staggered instance timers. Expired
owners cannot renew or release a successor's lease. Missing or unavailable
coordination storage skips background scans and logs an error; it never falls
back to scanning independently in every instance. Primary HTTP creation,
callbacks and approvals retain their existing handlers.

Deploy the public implementation with the matching OCB bootstrap wiring on all
instances; older instances do not observe the scan lease. This change needs no
AIS Base or Agent Skill package update. No remote DDL or deployment is performed
by the local migration files.
