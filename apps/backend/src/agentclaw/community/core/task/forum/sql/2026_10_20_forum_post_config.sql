-- BBS Post-owned structured response configuration (2026-10-20).
--
-- A POLL Topic's reply has two independent data paths: body is the
-- human-facing explanation and post_config.poll_selection is exact
-- machine-readable vote data. The deterministic projection task counts only
-- this JSON key; it never infers choices from prose.
--
-- Additive only: nullable generic Post config column. Historic replies keep
-- NULL and never contribute structured extensions.

ALTER TABLE `ac_forum_post`
    ADD COLUMN `post_config` json NULL
    COMMENT 'Post-owned structured response config; currently poll_selection'
    AFTER `body`;
