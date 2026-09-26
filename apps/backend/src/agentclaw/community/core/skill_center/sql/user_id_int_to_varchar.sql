-- Convert skill-related user_id columns from numeric semantics to string IDs.
-- ac_resource is intentionally excluded; it is handled by a separate owner.

ALTER TABLE `ac_skill_set`
    MODIFY COLUMN `user_id` VARCHAR(128) NULL;

ALTER TABLE `ac_skill`
    MODIFY COLUMN `user_id` VARCHAR(128) NULL;

ALTER TABLE `ac_skill_set_skill`
    MODIFY COLUMN `user_id` VARCHAR(128) NULL;

ALTER TABLE `ac_user_default_skill_set`
    MODIFY COLUMN `user_id` VARCHAR(128) NOT NULL;
