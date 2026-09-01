-- Phase 86 (liveness-gated threshold-STOPPED): stop_reason on the ARCA TTL
-- renewal schedule cold table records why a row reached terminal STOPPED.
-- Value vocabulary (written by the deadline renewal scheduler):
--   lifecycle          -- confirmed gone via the device-lifecycle path
--   orphan             -- hot row proven absent on the orphan recheck
--   threshold_gone     -- platform confirmation the sandbox no longer exists
--   threshold_expired  -- expiry confirmed via remaining-hours computation
--
-- Run BEFORE the DP4 flip, in the SAME window as the 84/85 deploy
-- (84-DEPLOYMENT-CHECKLIST.md), on both pre and prod:
--   pre:  ZDAS  (MariaDB) tenant schema
--   prod: directly on the shared production schema
-- The column is nullable and adds no default writes, so the pre-flip ALTER is
-- safe on a live table; scheduler STOPPED writes fill it only after the flip.
ALTER TABLE `baas_bot_ttl_renewal_schedule`
  ADD COLUMN `stop_reason` VARCHAR(64) NULL
  COMMENT 'terminal STOPPED provenance: lifecycle/orphan/threshold_gone/threshold_expired';