-- Preparing-archive blob pins (plan T4 review fix R1).
-- The derived content-addressed blob keys a preparing archive intends to
-- reference. Reclamation must consult these rows before deleting any blob:
-- committed artifact rows cannot protect a blob whose owning archive has not
-- yet run its finishing transaction, and content-addressed dedup means the
-- same bytes are shared across tasks. Rows are registered in the pin's own
-- transaction (before the first object write) and removed in the same
-- finishing transaction that writes the artifact part rows. Hash keys bind
-- index width (CHAR(64) ascii) so the utf8mb4 content key stays
-- non-indexed; the full key is retained for collision verification.
CREATE TABLE IF NOT EXISTS bcs_fsm_task_artifact_pins (
    archive_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    content_key VARCHAR(1024) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    PRIMARY KEY (archive_hash, content_hash),
    KEY idx_fsm_task_artifact_pins_content (content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
