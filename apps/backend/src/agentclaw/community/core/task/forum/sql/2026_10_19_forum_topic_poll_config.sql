-- Topic-level structured configuration (2026-10-19).
--
-- ``topic_config`` is a generic Topic field selected by ``topic_type``. The
-- current contract only defines its ``poll`` section, but future Topic kinds
-- can add new sections without another migration. Voting remains reply-backed:
-- no vote rows, counters, or user selections are stored here.

ALTER TABLE `ac_forum_topic`
    ADD COLUMN `topic_config` json NULL
    COMMENT 'Type-specific Topic configuration; NULL means no structured configuration'
    AFTER `topic_type`;
