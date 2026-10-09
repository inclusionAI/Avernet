-- Execute only if ce_steps exists and ext_json is absent.
-- Check with: SHOW COLUMNS FROM ce_steps LIKE 'ext_json';
ALTER TABLE ce_steps ADD COLUMN ext_json TEXT;
