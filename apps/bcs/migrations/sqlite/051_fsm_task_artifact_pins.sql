-- Preparing-archive blob pins (plan T4 review fix R1).
-- The derived content-addressed blob keys a preparing archive intends to
-- reference. Reclamation must consult these rows before deleting any blob:
-- committed artifact rows cannot protect a blob whose owning archive has not
-- yet run its finishing transaction, and content-addressed dedup means the
-- same bytes are shared across tasks. Rows are registered in the pin's own
-- transaction (before the first object write) and removed in the same
-- finishing transaction that writes the artifact part rows. Hash keys bind
-- index width; the full content key is retained for collision verification.
CREATE TABLE IF NOT EXISTS bcs_fsm_task_artifact_pins (
    archive_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_key TEXT NOT NULL,
    PRIMARY KEY (archive_hash, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_fsm_task_artifact_pins_content
    ON bcs_fsm_task_artifact_pins (content_hash);
