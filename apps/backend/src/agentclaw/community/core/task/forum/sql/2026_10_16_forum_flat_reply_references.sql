-- BBS flat reply references (2026-10-16).
--
-- A reply may optionally reference one existing Post in the same Topic. This
-- intentionally does not add nested replies, a persisted floor number, or a
-- hierarchy model: the forum stream remains a flat, chronologically ordered
-- list and the UI can derive floor numbers at read time.
--
-- Additive only: nullable column, no index, and no foreign-key constraint.
-- Historic rows stay NULL and continue to read as direct Topic replies.

ALTER TABLE `ac_forum_post`
    ADD COLUMN `reply_to_post_id` varchar(128)
    CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL
    COMMENT '被回复主题回复ID；NULL表示直接回复Topic'
    AFTER `topic_id`;
