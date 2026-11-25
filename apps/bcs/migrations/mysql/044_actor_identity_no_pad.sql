-- Upgrade installations that already recorded the original PAD SPACE 043.
-- Repeat the corrected NO PAD identity ALTERs: safe on fresh corrected-043
-- installations and partially applied original-043 upgrades. Do not rewrite the
-- retained 043 migration record. Requires MySQL 8+ utf8mb4_0900_bin support.
-- Stop writers for the full migration and retain these collations on rollback.

ALTER TABLE `bcs_bots`
    MODIFY COLUMN `bot_uuid` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,
    MODIFY COLUMN `created_by` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin DEFAULT NULL;

ALTER TABLE `edge_grants`
    MODIFY COLUMN `from_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL COMMENT '授权方 actor id（A）：human_<工号> 或 bot uuid',
    MODIFY COLUMN `to_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL COMMENT '被授权目标（B）：bot uuid';

ALTER TABLE `permission_profiles`
    MODIFY COLUMN `bot_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL COMMENT '所属 bot uuid（被授权方的默认权限）';

ALTER TABLE `permission_requests`
    MODIFY COLUMN `from_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL COMMENT '发起方 actor id',
    MODIFY COLUMN `to_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL COMMENT '目标 actor id';

ALTER TABLE `bcs_actor_relations`
    MODIFY COLUMN `from_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,
    MODIFY COLUMN `to_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_friendships`
    MODIFY COLUMN `left_bot` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,
    MODIFY COLUMN `right_bot` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_friend_requests`
    MODIFY COLUMN `from_bot` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,
    MODIFY COLUMN `to_bot` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_provider_bot_bindings`
    MODIFY COLUMN `bot_uuid` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_organization_members`
    MODIFY COLUMN `bot_uuid` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_group_participants`
    MODIFY COLUMN `bot_uuid` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_session_participants`
    MODIFY COLUMN `bot_uuid` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_groups`
    MODIFY COLUMN `dm_pair_key` varchar(513) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin DEFAULT NULL;

ALTER TABLE `bcs_messages`
    MODIFY COLUMN `sender_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,
    MODIFY COLUMN `owner_bot_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin DEFAULT NULL COMMENT '消息历史所属视角 Bot ID';

ALTER TABLE `bcs_session_speaking_leases`
    MODIFY COLUMN `holder_actor_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL DEFAULT '';

ALTER TABLE `bcs_collaboration_drafts`
    MODIFY COLUMN `owner_bot_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_state_machine_node_runs`
    MODIFY COLUMN `assignee_bot_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_state_machine_runs`
    MODIFY COLUMN `created_by` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin DEFAULT NULL;

ALTER TABLE `bcs_user_identities`
    MODIFY COLUMN `user_id` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_invite_codes`
    MODIFY COLUMN `bound_user_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin DEFAULT NULL,
    MODIFY COLUMN `created_by` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin DEFAULT NULL;

ALTER TABLE `bcs_channel_bindings`
    MODIFY COLUMN `target_json` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_channel_im_participants`
    MODIFY COLUMN `im_user_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL;

ALTER TABLE `bcs_channel_conversations`
    MODIFY COLUMN `im_user_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL DEFAULT '';

ALTER TABLE `bcs_human_input_requests`
    MODIFY COLUMN `reply_scope_key` varchar(768) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,
    MODIFY COLUMN `active_slot_key` varchar(768) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin DEFAULT NULL;
