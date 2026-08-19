-- ============================================================================
-- Migration v32: Add title column to workflow_specs (MySQL)
-- Purpose:
--   Avoid loading MEDIUMTEXT spec_json in list views. The title column
--   stores the workflow title extracted from spec_json, so list APIs
--   can query only workflow_id, pack_id, title, gmt_modified instead
--   of the full spec_json payload (20KB+ per row).
--
--   After adding the column, backfill existing rows by extracting title
--   from spec_json. The extraction handles both direct JSON format
--   ({"title": "..."}) and wrapper format ({"content": "yaml-string"}).
-- ============================================================================

ALTER TABLE workflow_specs ADD COLUMN title VARCHAR(255) COMMENT '工作流标题，从spec_json提取，避免列表查询加载大字段';

-- Backfill title from spec_json for existing rows
-- Case 1: Direct JSON format with top-level "title" field
UPDATE workflow_specs
SET title = JSON_UNQUOTE(JSON_EXTRACT(spec_json, '$.title'))
WHERE title IS NULL
  AND JSON_VALID(spec_json)
  AND JSON_EXTRACT(spec_json, '$.nodes') IS NOT NULL
  AND JSON_EXTRACT(spec_json, '$.title') IS NOT NULL;

-- Case 2: Wrapper format {"content": "yaml-string"} — title is inside the YAML
-- For these rows, title will be backfilled by clawweb on startup via
-- WorkflowSpecRepository.backfillTitles() which parses the YAML content.
-- This SQL handles only the simple JSON case; the YAML case is done in-app.