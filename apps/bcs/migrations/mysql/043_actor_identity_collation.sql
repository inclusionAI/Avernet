-- Actor identity comparison is case/accent-sensitive AND NO PAD (MySQL 8+).
-- Legacy utf8mb4_0900_ai_ci allows trailing-space-distinct unique keys;
-- utf8mb4_bin is PAD SPACE and would reject or alias those retained identities.
-- Preserve values, widths, defaults, comments and keys. Stop ALL writers during
-- these auto-committing ALTERs; do not downgrade collation on application rollback.
-- Scoped correction of PR #3708 review F2: original 043 is archived verbatim in
-- migrations/legacy/mysql; bcs-admin recognizes only its exact checksum and 044
-- upgrades already-applied databases without rewriting their history records.
-- Compatible engines must support utf8mb4_0900_bin; never fall back to PAD SPACE.

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
