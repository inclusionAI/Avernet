ALTER TABLE bcs_bots
    ADD COLUMN IF NOT EXISTS task_claim_mode tinyint(4) NOT NULL DEFAULT '0' COMMENT '任务领取开关(0=关,1=开)',
    ADD COLUMN IF NOT EXISTS task_dream_mode tinyint(4) NOT NULL DEFAULT '0' COMMENT '任务 Dream 开关(0=关,1=开)';