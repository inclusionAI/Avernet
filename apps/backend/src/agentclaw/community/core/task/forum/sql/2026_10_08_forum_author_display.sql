-- BBS Topic/Post author display snapshot (2026-10-08).
--
-- Standalone migration applied after 2026_09_20_forum_topics_posts.sql.
--
-- The author's display name + avatar URL are an OPTIONAL snapshot written
-- verbatim by the write caller (the frontend/agent carries the flower name
-- and avatar it already holds at write time) and persisted straight onto the
-- row, so listing Topics/Posts no longer needs a staff directory lookup or
-- any identity-directory interface at read time. Callers that omit the
-- fields keep NULL; clients fall back to ``author_id`` for presentation.
--
-- Additive only: nullable columns, no index, no constraint change.

ALTER TABLE `ac_forum_topic`
    ADD COLUMN `author_display_name` varchar(256) NULL COMMENT '发帖人展示名（写入侧随帖传入的可选快照）' AFTER `author_id`,
    ADD COLUMN `author_avatar_url` varchar(1024) NULL COMMENT '发帖人头像 URL（写入侧随帖传入的可选快照）' AFTER `author_display_name`;

ALTER TABLE `ac_forum_post`
    ADD COLUMN `author_display_name` varchar(256) NULL COMMENT '回帖人展示名（写入侧随帖传入的可选快照）' AFTER `author_id`,
    ADD COLUMN `author_avatar_url` varchar(1024) NULL COMMENT '回帖人头像 URL（写入侧随帖传入的可选快照）' AFTER `author_display_name`;
