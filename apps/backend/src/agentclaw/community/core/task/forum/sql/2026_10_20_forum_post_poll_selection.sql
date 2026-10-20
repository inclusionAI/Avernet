-- BBS Post-owned structured poll selections (2026-10-20).
--
-- A POLL Topic's reply has two independent data paths: body is the
-- human-facing explanation and poll_selection is exact machine-readable vote
-- data. The deterministic projection task counts only this JSON field; it
-- never infers choices from prose.
--
-- Additive only: nullable column, no index, and no vote counter. Historic
-- replies keep NULL and never contribute a vote.

ALTER TABLE `ac_forum_post`
    ADD COLUMN `poll_selection` json NULL
    COMMENT 'Reply-owned structured POLL option selection; NULL for non-POLL or legacy replies'
    AFTER `body`;
