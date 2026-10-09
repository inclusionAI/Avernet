-- Apply before deploying Backend; NULL preserves legacy instance selection.
ALTER TABLE ac_session_resource ADD COLUMN device_uuid VARCHAR(128) NULL;
