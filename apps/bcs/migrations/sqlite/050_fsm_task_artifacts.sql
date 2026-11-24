-- Per-task immutable final-product archive (plan T4).
-- One row per (scope, group, Tid). `state` is the archive's whole lifecycle:
-- 'preparing' (pin registered before the first object write; recovery-only,
-- never public success), 'committed' (one finishing transaction wrote the
-- final record, every ordered part and all immutable blob references, or
-- nothing) and 'expired' (reclaim tombstone: stays readable after proof and
-- result deletion so the Tid can never be re-executed as absent).
-- Archive success never confers public eligibility; wire disclosure is gated
-- by the saved projection check (budget version, digest, measured size).
-- Hash keys bind index width; full identities are re-checked on every read.
CREATE TABLE IF NOT EXISTS bcs_fsm_task_archives (
    archive_hash TEXT NOT NULL PRIMARY KEY,
    scope_tenant TEXT NOT NULL,
    scope_principal TEXT NOT NULL,
    group_id TEXT NOT NULL,
    sid TEXT NOT NULL,
    tid TEXT NOT NULL,
    round_generation INTEGER NOT NULL,
    state TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    result_digest TEXT NOT NULL,
    result_key TEXT NOT NULL,
    commit_id TEXT,
    archive_json TEXT,
    budget_version INTEGER,
    projection_digest TEXT,
    measured_bytes INTEGER NOT NULL DEFAULT 0,
    projection_fits INTEGER,
    proof_json TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fsm_task_archives_listing
    ON bcs_fsm_task_archives (scope_tenant, scope_principal, group_id, state, tid);
-- Ordered immutable product references of one archived task. Unique key is
-- exactly (tid, artifact_id); `content_key` is the content-addressed key of
-- the part's immutable blob (deduped across tasks sharing equal bytes); the
-- bytes themselves live only in bcs_dist_immutable_objects.
CREATE TABLE IF NOT EXISTS bcs_fsm_task_artifacts (
    tid TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    archive_hash TEXT NOT NULL,
    part_order INTEGER NOT NULL,
    content_key TEXT NOT NULL,
    PRIMARY KEY (tid, artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_fsm_task_artifacts_archive
    ON bcs_fsm_task_artifacts (archive_hash);
