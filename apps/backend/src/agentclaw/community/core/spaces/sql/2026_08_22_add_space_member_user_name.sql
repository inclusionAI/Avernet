-- Persist the optional member-user-name snapshot accepted by the public API.
-- Membership identity and all mutations continue to use user_id exclusively.
ALTER TABLE ac_space_member
    ADD COLUMN IF NOT EXISTS user_name VARCHAR(128) NULL DEFAULT NULL
        COMMENT '成员用户名快照';

-- Membership removal now uses physical DELETE. Remove rows left by the former
-- INACTIVE soft-delete behavior so the unique member key cannot block re-adding
-- a user after this migration.
DELETE FROM ac_space_member WHERE status = 'INACTIVE';
