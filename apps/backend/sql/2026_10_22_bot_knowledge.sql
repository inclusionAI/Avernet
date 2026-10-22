-- REVIEW ONLY: not executed. Only current selection; no ac_space changes.
CREATE TABLE ac_bot_knowledge_binding (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
 env VARCHAR(20) NOT NULL,
 bot_pk BIGINT NOT NULL,
 space_id BIGINT NOT NULL,
 kb_id VARCHAR(128) NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT FALSE,
 revision BIGINT NOT NULL,
 updated_by VARCHAR(128) NOT NULL,
 gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 UNIQUE KEY uk_bot_knowledge_binding(avernet_tenant,env,bot_pk,kb_id)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
