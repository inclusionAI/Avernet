-- Session collection (收藏): per-participant collected flag on the side table.
-- Standard MySQL syntax (no MariaDB-only `IF NOT EXISTS`); migrations run once
-- against the recorded migration history, so idempotency is handled there.
ALTER TABLE `bcs_session_participants`
  ADD COLUMN `collected` tinyint(4) NOT NULL DEFAULT '0';

CREATE INDEX `idx_collected`
  ON `bcs_session_participants` (`env`, `group_id`, `bot_uuid`, `collected`);