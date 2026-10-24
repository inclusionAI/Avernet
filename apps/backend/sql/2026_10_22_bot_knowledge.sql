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
 UNIQUE KEY uk_bot_knowledge_binding(avernet_tenant,env,bot_pk,kb_id),
 KEY idx_bk_enabled(avernet_tenant,env,bot_pk,space_id,enabled,kb_id),
 KEY idx_bk_page(avernet_tenant,env,bot_pk,space_id,id)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- Existing publish state is read only for draft editing permission. No new
-- knowledge version or publication state is introduced. Review before applying.
ALTER TABLE ac_bot_publish
 ADD INDEX idx_publish_current_source(env,source_bot_pk,version,id),
 ADD INDEX idx_publish_current_default(env,publish_bot_id(512),owner_id,version,id);
