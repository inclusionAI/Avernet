-- Migration v14: Add lease-based flow control columns.
-- Target: MySQL / ZDAS (OceanBase)
--
-- Design principle: flow control manages concurrency resources (slots) ONLY.
-- It does NOT modify flow_runs.status — flow state is owned exclusively by the
-- Controller. When a lease expires, we release the slot (allowing other flows to
-- acquire it) but never mark the flow as failed. This tolerates transient exceeding
-- of concurrency limits (e.g., after network jitter) rather than risking false kills.
--
-- Lease lifecycle:
--   acquire  → INSERT slot with lease_expires_at = now + TTL (60s)
--   heartbeat → UPDATE lease_expires_at = now + TTL every 30s
--   expire   → DELETE slot where lease_expires_at < now (resource only, no flow status)
--   release  → DELETE slot on normal completion (Controller owned)
--
-- What happens on lease expiry (e.g., process crash):
--   1. Slot is released → other flows can use it
--   2. Original flow's Controller eventually finishes → tries to release its slot
--      → slot already gone (by lease expiry), that's fine, release is idempotent
--   3. Worst case: concurrency limit temporarily exceeded by 1 — acceptable
--
-- What happens on network jitter (heartbeat fails temporarily):
--   1. Lease expires → slot released
--   2. Original Controller continues running (flow status untouched)
--   3. Heartbeat recovers → renewLeases finds no rows to renew (slot already gone)
--   4. Controller finishes normally → releases slot (no-op, already gone)
--   5. At worst: concurrency temporarily exceeded — self-correcting as flows finish
--
-- Compliance: ALTER TABLE ADD INDEX for ODC/OceanBase compatibility,
--             indexed columns use BIGINT with COMMENT.

-- Lease expiry timestamp (Unix seconds).
-- 0 = legacy row acquired before lease mechanism exists (never auto-expires,
--     managed by releaseOrphanedSlots until all instances upgraded).
-- >0 = lease-based slot; auto-released after this time if heartbeat stops.
ALTER TABLE flow_control_slots
  ADD COLUMN lease_expires_at BIGINT NOT NULL DEFAULT 0
  COMMENT '租约过期时间（Unix秒）。0=旧数据,>0=租约模式,过期后仅释放slot不修改流程状态。Heartbeat每30s续租,TTL=60s';

-- Renewal counter for monitoring. Increments on each successful heartbeat.
-- Low renew_count relative to uptime indicates heartbeat issues.
ALTER TABLE flow_control_slots
  ADD COLUMN renew_count INT NOT NULL DEFAULT 0
  COMMENT '续租次数，每次heartbeat续租+1，仅用于监控调测';

-- Index for expired-lease cleanup: find slots where lease has expired.
-- Query pattern: WHERE instance_id = ? AND lease_expires_at > 0 AND lease_expires_at < now
ALTER TABLE flow_control_slots ADD INDEX idx_fc_slots_lease_expiry (instance_id, lease_expires_at);