-- Migration: Add missing columns to baas_device_template table
-- Issue: template_id and type columns were missing from the original schema
-- This migration adds them for existing deployments

-- Add template_id column (bigint, global unique, used in paas device id encoding)
ALTER TABLE `baas_device_template`
ADD COLUMN IF NOT EXISTS `template_id` bigint(20) NOT NULL COMMENT '资源模板的唯一编号，会被用作paas设备id的编码中,全局唯一' AFTER `gmt_modified`;

-- Add type column (PaaS platform type: ARCA, SIGMA, etc.)
ALTER TABLE `baas_device_template`
ADD COLUMN IF NOT EXISTS `type` varchar(32) NOT NULL COMMENT '资源Paas类型，例如ARCA或SIGMA等' AFTER `tenant`;

-- Add unique constraint on template_id
-- Note: This will fail if there are duplicate template_id values
ALTER TABLE `baas_device_template`
ADD UNIQUE KEY IF NOT EXISTS `uk_template_id` (`template_id`);

-- If template_id is already populated with duplicates, you may need to:
-- 1. First update existing rows to have unique template_id values
-- 2. Then run this migration
-- Example update (adjust as needed):
-- SET @row_number = 0;
-- UPDATE baas_device_template
-- SET template_id = (@row_number := @row_number + 1)
-- WHERE template_id = 0 OR template_id IS NULL;
