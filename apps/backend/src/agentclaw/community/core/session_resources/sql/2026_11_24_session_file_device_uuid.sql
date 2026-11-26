-- Apply before deploying the Session File exact-runtime upload path.
-- NULL retains legacy affinity selection for existing resource records.
ALTER TABLE ac_session_resource ADD COLUMN device_uuid VARCHAR(128) NULL;
