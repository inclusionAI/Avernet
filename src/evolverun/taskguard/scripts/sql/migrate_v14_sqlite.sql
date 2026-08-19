-- Migration v14: Add lease-based flow control columns.
-- Target: SQLite (engine.db)
--
-- Design principle: flow control manages concurrency resources (slots) ONLY.
-- It does NOT modify flow_runs.status — flow state is owned exclusively by the
-- Controller. When a lease expires, we release the slot (allowing other flows to
-- acquire it) but never mark the flow as failed. This tolerates transient exceeding
-- of concurrency limits rather than risking false kills.
--
-- Lease lifecycle:
--   acquire  → INSERT slot with lease_expires_at = now + TTL (60s)
--   heartbeat → UPDATE lease_expires_at = now + TTL every 30s
--   expire   → DELETE slot where lease_expires_at < now (resource only, no flow status)
--   release  → DELETE slot on normal completion (Controller owned)
--
-- What happens on lease expiry (e.g., process crash):
--   Slot is released. Original flow continues. Concurrency may temporarily exceed
--   limit by 1. Self-corrects as flows finish and release their slots normally.
--
-- What happens on network jitter (heartbeat fails temporarily):
--   Lease expires → slot released. Original Controller continues running.
--   Worst case: concurrency temporarily exceeded — acceptable and self-correcting.

-- Lease expiry timestamp (Unix seconds). 0 = legacy row (never auto-expires).
ALTER TABLE flow_control_slots ADD COLUMN lease_expires_at INTEGER NOT NULL DEFAULT 0;

-- Renewal counter for monitoring. Increments on each heartbeat renewal.
ALTER TABLE flow_control_slots ADD COLUMN renew_count INTEGER NOT NULL DEFAULT 0;

-- Index for expired-lease cleanup.
CREATE INDEX IF NOT EXISTS idx_fc_slots_lease_expiry ON flow_control_slots (instance_id, lease_expires_at);