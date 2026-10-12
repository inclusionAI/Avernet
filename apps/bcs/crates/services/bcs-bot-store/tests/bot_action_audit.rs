//! Bot control-plane patch + same-commit ordinary-business audit
//! (plan Task 9, spec §12.5).
//!
//! Every asserted variable comes from a real store query — the SQLite twin
//! is probed through `bcs_bot_action_audits` rows directly, the memory twin
//! through its published audit records. Failure injection replaces the
//! marked audit INSERT step with a failing statement inside the SAME
//! transaction, so the rollback asserted here is the real store rollback,
//! not a mocked boolean.
//!
//! Coverage pinned by the brief:
//! - Human operator with effective actor: `operator_user_id` filled;
//! - Bot-only operator: `operator_user_id` NULL (genuinely no Human);
//! - audit INSERT failure rolls the business UPDATE back;
//! - an identical replay of the same (env, operation_id, step_key) is an
//!   idempotent no-op on the audit slot while re-applying the business
//!   UPDATE; different content under the same slot is a conflict;
//! - the memory twin publishes state + audit in one critical section and
//!   discards the staged mutation on the armed write failure.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use support::*;

#[path = "common/ownership.rs"]
mod support;

use bcs_service_api::port::repo::BotControlPlaneRepoPort;
use bcs_service_api::types::BotControlPlanePatch;
use bcs_service_api::ServiceError;

fn human_operation(user: &str, bot_id: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: uuid::Uuid::new_v4().to_string(),
        actor: BotOperationActor::Human {
            user_id: user.to_string(),
            effective_actor_id: bot_id.to_string(),
        },
    }
}

fn bot_operation(bot_id: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: uuid::Uuid::new_v4().to_string(),
        actor: BotOperationActor::Bot {
            bot_id: bot_id.to_string(),
        },
    }
}

fn name_patch(name: &str) -> BotControlPlanePatch {
    BotControlPlanePatch {
        name: Some(name.to_string()),
        ..Default::default()
    }
}

async fn audit_rows(db: &dyn DbPlugin) -> Vec<DbRow> {
    db.query(DbStatement::new(
        "SELECT audit_id, env, operation_id, step_key, operator_kind, operator_id, \
         operator_user_id, effective_actor_id, resource_kind, resource_id, action, phase \
         FROM bcs_bot_action_audits ORDER BY id",
    ))
    .await
    .expect("query bot action audits")
}

fn row_str(row: &DbRow, column: &str) -> String {
    row.get_string(column)
        .expect("audit column decodes")
        .unwrap_or_default()
}

// ---------------------------------------------------------------------------
// SQLite twin
// ---------------------------------------------------------------------------

/// The control-plane projection selects the full mutable column set, so
/// the shared lifecycle schema gains the task/internal-attribute columns
/// for this suite only.
async fn audit_suite_sqlite() -> Arc<dyn DbPlugin> {
    let db = sqlite().await;
    for statement in [
        "ALTER TABLE bcs_bots ADD COLUMN gmt_create TEXT NOT NULL DEFAULT \'2020-01-01 00:00:00\'",
        "ALTER TABLE bcs_bots ADD COLUMN gmt_modified TEXT NOT NULL DEFAULT \'2020-01-01 00:00:00\'",
        "ALTER TABLE bcs_bots ADD COLUMN task_claim_mode INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE bcs_bots ADD COLUMN task_dream_mode INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE bcs_bots ADD COLUMN user_visibility TEXT DEFAULT 'protected'",
        "ALTER TABLE bcs_bots ADD COLUMN friend_ext TEXT",
        "ALTER TABLE bcs_bots ADD COLUMN friend_check_in_strategy TEXT DEFAULT 'APPROVAL'",
    ] {
        db.execute(DbStatement::new(statement))
            .await
            .expect("extend the bots schema for control-plane patches");
    }
    db
}

#[tokio::test]
async fn sqlite_patch_commits_the_applied_audit_with_both_human_identities() {
    let db = audit_suite_sqlite().await;
    let repo = persistent(db.clone());
    repo.register_with_owner_and_token(
        "audit-bot".to_string(),
        caps("audit-bot"),
        "creator-user",
        "token-audit-bot",
    )
    .await
    .expect("register audit bot");

    let env = bcs_config::resolve_env_str();
    let updated = repo
        .patch_control_plane(
            "audit-bot",
            &env,
            name_patch("Audited Name"),
            human_operation("staff-1", "audit-bot"),
        )
        .await
        .expect("human patch succeeds")
        .expect("record survives the patch");

    // Business change visible...
    assert_eq!(updated.name, "Audited Name");
    let stored = repo
        .get_control_plane("audit-bot", &env)
        .await
        .expect("read back")
        .expect("row exists");
    assert_eq!(stored.name, "Audited Name");

    // ...and the applied audit row carries BOTH identities, with the
    // slot built from the controlled vocabulary only.
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(rows.len(), 1);
    let audit = &rows[0];
    assert_eq!(row_str(audit, "operator_kind"), "human");
    assert_eq!(row_str(audit, "operator_id"), "staff-1");
    assert_eq!(row_str(audit, "operator_user_id"), "staff-1");
    assert_eq!(row_str(audit, "effective_actor_id"), "audit-bot");
    assert_eq!(row_str(audit, "resource_kind"), "bot");
    assert_eq!(row_str(audit, "resource_id"), "audit-bot");
    assert_eq!(row_str(audit, "action"), "update");
    assert_eq!(row_str(audit, "phase"), "applied");
    assert_eq!(row_str(audit, "step_key"), "update/bot/applied");
    assert_eq!(row_str(audit, "env"), env);

    // Bot-only operator: operator_user_id is NULL and that is legal.
    repo.patch_control_plane(
            "audit-bot",
            &env,
            name_patch("Bot Self Name"),
            bot_operation("audit-bot"),
        )
        .await
        .expect("bot-only patch succeeds")
        .expect("record survives");
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(rows.len(), 2);
    let bot_audit = &rows[1];
    assert_eq!(row_str(bot_audit, "operator_kind"), "bot");
    assert_eq!(row_str(bot_audit, "operator_id"), "audit-bot");
    assert!(
        bot_audit
            .get_string("operator_user_id")
            .expect("decode operator_user_id")
            .is_none(),
        "Bot-only operation has NO operator user id — no Human was involved"
    );
    assert_eq!(row_str(bot_audit, "effective_actor_id"), "audit-bot");
}

#[tokio::test]
async fn sqlite_audit_insert_failure_rolls_the_patch_back() {
    let db = audit_suite_sqlite().await;
    let injected = InjectedStepDb::new(db.clone());
    let repo = persistent(injected.clone());
    repo.register_with_owner_and_token(
        "rollback-bot".to_string(),
        caps("rollback-bot"),
        "creator-user",
        "token-rollback-bot",
    )
    .await
    .expect("register rollback bot");
    let env = bcs_config::resolve_env_str();

    injected.arm("INTO bcs_bot_action_audits");
    let error = repo
        .patch_control_plane(
            "rollback-bot",
            &env,
            name_patch("Must Not Persist"),
            human_operation("staff-1", "rollback-bot"),
        )
        .await
        .expect_err("the audit INSERT failure must fail the whole patch");
    assert!(
        matches!(error, ServiceError::InternalError(_)),
        "the rolled-back attempt surfaces the storage failure, got: {error:?}"
    );

    // Real-rollback assertions: neither the business change nor the audit
    // row survived.
    let stored = repo
        .get_control_plane("rollback-bot", &env)
        .await
        .expect("read back")
        .expect("row exists");
    assert_ne!(stored.name, "Must Not Persist");
    let rows = audit_rows(db.as_ref()).await;
    assert!(
        rows.is_empty(),
        "no audit row may survive a rolled-back transaction"
    );

    // After disarm, the SAME logical patch applies and audits normally.
    injected.disarm();
    repo.patch_control_plane(
            "rollback-bot",
            &env,
            name_patch("Must Not Persist"),
            human_operation("staff-1", "rollback-bot"),
        )
        .await
        .expect("retry after disarm succeeds")
        .expect("record survives");
    let stored = repo
        .get_control_plane("rollback-bot", &env)
        .await
        .expect("read back")
        .expect("row exists");
    assert_eq!(stored.name, "Must Not Persist");
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(rows.len(), 1);
}

#[tokio::test]
async fn sqlite_identical_replay_is_idempotent_while_a_conflicting_slot_is_rejected() {
    let db = audit_suite_sqlite().await;
    let repo = persistent(db.clone());
    repo.register_with_owner_and_token(
        "replay-bot".to_string(),
        caps("replay-bot"),
        "creator-user",
        "token-replay-bot",
    )
    .await
    .expect("register replay bot");
    let env = bcs_config::resolve_env_str();
    let operation = human_operation("staff-1", "replay-bot");

    repo.patch_control_plane(
        "replay-bot",
        &env,
        name_patch("Replayed Name"),
        operation.clone(),
    )
    .await
    .expect("first attempt succeeds")
    .expect("record survives");
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(rows.len(), 1);
    let first_audit_id = row_str(&rows[0], "audit_id");

    // Byte-identical replay of the SAME operation context: the first
    // audit row keeps its identity/DB timestamps and the business UPDATE
    // re-applies to the same end state.
    let replayed = repo
        .patch_control_plane("replay-bot", &env, name_patch("Replayed Name"), operation.clone())
        .await
        .expect("identical replay is an idempotent success")
        .expect("record survives");
    assert_eq!(replayed.name, "Replayed Name");
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(rows.len(), 1, "the first row is kept, not duplicated");
    assert_eq!(row_str(&rows[0], "audit_id"), first_audit_id);

    // A DIFFERENT operation under a reused operation id + step: same slot,
    // different content → conflict, never an overwrite.
    let conflicting = BotOperationContext {
        operation_id: operation.operation_id.clone(),
        actor: BotOperationActor::Human {
            user_id: "staff-2".to_string(),
            effective_actor_id: "replay-bot".to_string(),
        },
    };
    let error = repo
        .patch_control_plane("replay-bot", &env, name_patch("Forged Name"), conflicting)
        .await
        .expect_err("different content in the same slot is a conflict");
    assert!(matches!(error, ServiceError::Conflict(_)), "got: {error:?}");
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(
        row_str(&rows[0], "operator_id"),
        "staff-1",
        "the conflicting write must not overwrite the committed row"
    );
}

// ---------------------------------------------------------------------------
// Memory twin
// ---------------------------------------------------------------------------

#[tokio::test]
async fn memory_patch_publishes_state_and_audit_together_or_not_at_all() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    repo.register_with_owner_and_token(
        "memory-audit-bot".to_string(),
        caps("memory-audit-bot"),
        "creator-user",
        "token-memory-audit-bot",
    )
    .await
    .expect("register memory audit bot");
    let env = bcs_config::resolve_env_str();

    // Published patch: ONE applied audit row with both identities.
    let updated = repo
        .patch_control_plane(
            "memory-audit-bot",
            &env,
            name_patch("Memory Audited"),
            human_operation("staff-1", "memory-audit-bot"),
        )
        .await
        .expect("memory patch succeeds")
        .expect("record survives");
    assert_eq!(updated.name, "Memory Audited");
    let audits = repo
        .bot_action_audit_records()
        .await
        .expect("memory audit records");
    assert_eq!(audits.len(), 1);
    assert_eq!(audits[0].step_key, "update/bot/applied");
    assert_eq!(audits[0].operator.operator_user_id(), Some("staff-1"));
    assert_eq!(audits[0].operator.effective_actor_id(), "memory-audit-bot");
    assert_eq!(audits[0].operator.operator_kind(), "human");

    // Armed failure: the staged mutation is DISCARDED — the record is
    // untouched and no audit row appears.
    repo.arm_authority_write_failure();
    let error = repo
        .patch_control_plane(
            "memory-audit-bot",
            &env,
            name_patch("Discarded"),
            human_operation("staff-1", "memory-audit-bot"),
        )
        .await
        .expect_err("armed write failure discards the whole patch");
    assert!(matches!(error, ServiceError::InternalError(_)));
    let stored = repo
        .get_control_plane("memory-audit-bot", &env)
        .await
        .expect("read back")
        .expect("row exists");
    assert_ne!(stored.name, "Discarded");
    let audits = repo
        .bot_action_audit_records()
        .await
        .expect("memory audit records");
    assert_eq!(audits.len(), 1, "the discarded publish adds no audit row");

}
