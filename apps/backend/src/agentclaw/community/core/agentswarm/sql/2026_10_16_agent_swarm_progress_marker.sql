-- agentswarm ac_agent_swarm_job.progress_marker — progress-aware reclaim
-- breaker (D-D). Applied ascending by file name after 2026_10_08_agent_swarm.sql.
-- Additive, defaulted, backward-readable (D38): old replicas keep inserting
-- the old row shape and leave the column at its default. The sentinel -1 is
-- the unobserved baseline — a new replica's first routed-shard-count
-- observation becomes the baseline, never counts as progress — so
-- mixed-version fleets coexist safely. Mirrors repository/job/model.py and
-- the simulator agentclawdb-structure.sql.

ALTER TABLE `ac_agent_swarm_job`
    ADD COLUMN `progress_marker` bigint(20) NOT NULL DEFAULT -1
        COMMENT 'routed-shard count observed at last claim/reclaim; baseline sentinel -1 resets reclaims on growth (D40 progress-aware breaker)'
        AFTER `reclaims`;