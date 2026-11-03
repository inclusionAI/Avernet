-- DEFERRED / REVIEW ONLY: NOT executed. Not part of the approved table creation.
-- User confirmed on 2026-10-26 that these existing-table index changes have not
-- been executed and will be considered separately later. Do not auto-apply.
-- First inspect the target database's existing indexes and query plans, then
-- obtain separate deployment approval. Fresh local schemas retain model indexes;
-- that does not prove existing deployments contain them or meet performance needs.
-- No publication columns, status semantics or lifecycle changes are introduced.
ALTER TABLE ac_bot_publish
 ADD INDEX idx_publish_current_source(env,source_bot_pk,version,id),
 ADD INDEX idx_publish_current_default(env,publish_bot_id(512),owner_id,version,id);
