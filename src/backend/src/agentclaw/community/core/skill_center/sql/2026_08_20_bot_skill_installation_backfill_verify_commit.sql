-- Source immediately after apply in the SAME database session. This is a
-- deliberately separate explicit operator step: ROLLBACK unless every row is
-- apply_allowed, non-ambiguous, and has zero missing Installations.
SELECT avernet_tenant,
       env,
       legacy_active_local,
       live_exact_bot_candidates,
       ambiguous_live_bot_candidates,
       inserted_installations,
       missing_installations,
       CASE
           WHEN ambiguous_live_bot_candidates = 0 AND missing_installations = 0
           THEN 1 ELSE 0
       END AS commit_allowed
FROM ac_bot_skill_installation_backfill_run_audit
WHERE run_id = @p1_01_installation_backfill_run_id
ORDER BY avernet_tenant, env;

-- Only run this statement after the preceding count is zero.
COMMIT;
