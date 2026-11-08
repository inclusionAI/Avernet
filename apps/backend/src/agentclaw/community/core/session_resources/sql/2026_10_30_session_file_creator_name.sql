-- Apply before deploying Backend creator-name snapshots; historical rows stay NULL.
ALTER TABLE ac_session_resource
  ADD COLUMN creator_name VARCHAR(255) NULL
    COMMENT 'Trusted upload actor display name snapshot; NULL when unknown';
