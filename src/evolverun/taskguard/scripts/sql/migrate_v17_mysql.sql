-- Migration v17: Add structured columns to validation_templates for baseline metadata.
-- Target: MySQL / ZDAS (OceanBase)
-- These columns extract frequently-queried fields from the JSON `content` blob into
-- dedicated columns, enabling indexed lookups and SQL-level filtering.
-- The `content` MEDIUMTEXT column remains the source of truth; these columns are
-- denormalized copies populated by the application layer.
-- Compliance: id BIGINT, gmt_create/gmt_modified TIMESTAMP with COMMENT,
--             gmt_modified ON UPDATE CURRENT_TIMESTAMP, indexes inline,
--             indexed columns use VARCHAR(255) not TEXT.

ALTER TABLE validation_templates
  ADD COLUMN category VARCHAR(64) COMMENT '任务类别(如complex/simple, 提取自content JSON)',
  ADD COLUMN grading_type VARCHAR(64) COMMENT '评分类型(如hybrid/automated/llm_judge, 提取自content JSON)',
  ADD COLUMN timeout_seconds INTEGER COMMENT '超时秒数(提取自content JSON)',
  ADD COLUMN grading_weights_json TEXT COMMENT '评分权重JSON(如{"automated":0.4,"llm_judge":0.6}, 提取自content JSON)';

-- Use ALTER TABLE ADD INDEX instead of CREATE INDEX for ODC/OceanBase compatibility
ALTER TABLE validation_templates ADD INDEX idx_validation_templates_category (category);
ALTER TABLE validation_templates ADD INDEX idx_validation_templates_grading_type (grading_type);