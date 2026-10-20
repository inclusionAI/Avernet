-- BBS Topic-derived analysis projection (2026-10-19).
--
-- This one nullable JSON field stores the Topic's backend-generated summary,
-- tags, and poll aggregate as a single derived read model. It is written by the
-- background analysis task, not by the public vote write path.

ALTER TABLE `ac_forum_topic`
    ADD COLUMN `analysis_result` json NULL
    COMMENT 'Topic-derived analysis result: summary, tags, and poll_stats; NULL means not generated yet'
    AFTER `topic_config`;
