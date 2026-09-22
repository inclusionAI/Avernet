ALTER TABLE bcs_groups
    ADD COLUMN human_mention_notify_mode VARCHAR(32) NOT NULL DEFAULT 'all';
