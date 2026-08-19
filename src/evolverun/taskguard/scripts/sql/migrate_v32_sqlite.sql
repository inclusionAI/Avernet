-- ============================================================================
-- Migration v32: Add title column to workflow_specs (SQLite)
-- Purpose:
--   Avoid loading TEXT spec_json in list views. The title column stores
--   the workflow title extracted from spec_json, so list APIs can query
--   only workflow_id, pack_id, title, gmt_modified instead of the full
--   spec_json payload (20KB+ per row).
--
--   Backfill is handled by clawweb on startup via
--   WorkflowSpecRepository.backfillTitles() which parses JSON and YAML
--   content to extract titles.
-- ============================================================================

ALTER TABLE workflow_specs ADD COLUMN title VARCHAR(255);