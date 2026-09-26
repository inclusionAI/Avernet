-- 015_add_bot_internal_attributes.sql — persistent Provider Bot attributes.
-- Keep these fields outside bot_info so reads and partial updates share one schema.
ALTER TABLE bcs_bots
    ADD COLUMN user_visibility varchar(32) NOT NULL DEFAULT 'protected' COMMENT '用户可见性(public/protected/private)',
    ADD COLUMN friend_ext JSON DEFAULT NULL COMMENT '好友扩展信息(JSON object)',
    ADD COLUMN friend_check_in_strategy varchar(32) NOT NULL DEFAULT 'APPROVAL' COMMENT '好友申请策略(OPEN/APPROVAL/DEPT_FREE)';
