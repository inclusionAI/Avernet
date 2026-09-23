-- Per-Skill Team Space editor-request policy. Existing and new bindings default
-- to manual approval until the Skill owner explicitly opts in.
ALTER TABLE ac_skill_space_binding
    ADD COLUMN auto_approve_editor_requests TINYINT(1) NOT NULL DEFAULT 0;
