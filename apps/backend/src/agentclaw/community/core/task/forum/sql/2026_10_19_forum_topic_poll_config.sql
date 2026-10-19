-- BBS Topic-level poll configuration (2026-10-19).
--
-- This stores presentation and voting-rule configuration only. Voting itself
-- remains reply-backed: one Post expresses one author's vote and reasoning.
-- No option ids, user-vote rows, counters, or hierarchy are persisted here.
--
-- Additive only: nullable JSON, no index, no constraint change. Historic
-- discussion rows and existing POLL rows without configuration stay NULL.

ALTER TABLE `ac_forum_topic`
    ADD COLUMN `poll_config` json NULL
    COMMENT 'Topic-level poll configuration; NULL means no poll configuration'
    AFTER `topic_type`;
