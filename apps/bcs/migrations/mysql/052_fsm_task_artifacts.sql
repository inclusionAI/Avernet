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
    archive_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_tenant VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    scope_principal VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    group_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    sid VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    tid VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    round_generation BIGINT UNSIGNED NOT NULL,
    state VARCHAR(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    request_digest VARCHAR(71) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    result_digest VARCHAR(71) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    result_key VARCHAR(1024) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    commit_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    archive_json LONGTEXT,
    budget_version BIGINT UNSIGNED,
    projection_digest VARCHAR(71) CHARACTER SET ascii COLLATE ascii_bin,
    measured_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
    projection_fits TINYINT,
    proof_json LONGTEXT,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY (archive_hash),
    KEY idx_fsm_task_archives_listing
        (scope_tenant, scope_principal, group_id, state, tid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
-- Ordered immutable product references of one archived task. Unique key is
-- exactly (tid, artifact_id); `content_key` is the content-addressed key of
-- the part's immutable blob (deduped across tasks sharing equal bytes); the
-- bytes themselves live only in bcs_dist_immutable_objects.
CREATE TABLE IF NOT EXISTS bcs_fsm_task_artifacts (
    tid VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    artifact_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    archive_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    part_order BIGINT UNSIGNED NOT NULL,
    content_key VARCHAR(1024) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    PRIMARY KEY (tid, artifact_id),
    KEY idx_fsm_task_artifacts_archive (archive_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
