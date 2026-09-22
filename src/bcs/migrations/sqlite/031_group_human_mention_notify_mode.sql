ALTER TABLE bcs_groups
    ADD COLUMN human_mention_notify_mode TEXT NOT NULL DEFAULT 'all';
