-- Migration v17: Add structured columns to validation_templates for baseline metadata.
-- Target: SQLite (engine.db)
-- These columns extract frequently-queried fields from the JSON `content` blob into
-- dedicated columns, enabling indexed lookups and SQL-level filtering.
-- The `content` TEXT column remains the source of truth; these columns are
-- denormalized copies populated by the application layer.

ALTER TABLE validation_templates ADD COLUMN category VARCHAR(64);
ALTER TABLE validation_templates ADD COLUMN grading_type VARCHAR(64);
ALTER TABLE validation_templates ADD COLUMN timeout_seconds INTEGER;
ALTER TABLE validation_templates ADD COLUMN grading_weights_json TEXT;

CREATE INDEX IF NOT EXISTS idx_validation_templates_category ON validation_templates (category);
CREATE INDEX IF NOT EXISTS idx_validation_templates_grading_type ON validation_templates (grading_type);