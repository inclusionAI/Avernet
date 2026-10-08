ALTER TABLE ac_session_resource
  ADD COLUMN binding_id BIGINT NULL
    COMMENT 'target Bot device binding ID for Session File materialization routing';
