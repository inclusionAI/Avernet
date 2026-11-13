ALTER TABLE bcs_group_contexts ADD COLUMN created_from_preset BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS bcs_group_context_item_presets (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    group_id VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    creator VARCHAR(255) NOT NULL,
    modifier VARCHAR(255) NOT NULL,
    gmt_create TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    gmt_modified TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_group_context_item_preset_group_name (group_id, name)
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
