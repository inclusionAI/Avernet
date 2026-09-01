-- Phase 86 (liveness-gated threshold-STOPPED): stop_reason on the ARCA TTL
-- renewal schedule cold table records why a row reached terminal STOPPED.
-- Value vocabulary (written by the deadline renewal scheduler):
--   lifecycle          -- confirmed gone via the device-lifecycle path
--   orphan             -- hot row proven absent on the orphan recheck
--   threshold_gone     -- platform confirmation the sandbox no longer exists
--   threshold_expired  -- expiry confirmed via remaining-hours computation
--
-- ⚠️ 事后补列：deadline 策略翻转在前（线上已运行数日），本列是补齐
-- 86 (T2/T3) 写入依赖的规范来源，非翻转前置项。表结构由用户手工管理，
-- 代码中不落 DDL（见 84-DEPLOYMENT-CHECKLIST.md Q6）。
-- Executed by hand on both pre and prod (pre/prod share one table
-- distinguished by the env column — one ALTER covers both):
--   pre:  ZDAS tenant schema
--   prod: shared production schema
-- The column is nullable and adds no default writes, so the ALTER is safe
-- on a live table; scheduler STOPPED writes fill it from the next cycle on,
-- and unregistered hot rows are backfilled by the next anti-join round.
ALTER TABLE `baas_bot_ttl_renewal_schedule`
  ADD COLUMN `stop_reason` VARCHAR(64) NULL
  COMMENT 'terminal STOPPED provenance: lifecycle/orphan/threshold_gone/threshold_expired';