//! Session-file store + same-commit ordinary-business audit (plan Task 11,
//! spec §12.5).
//!
//! Every asserted variable comes from a REAL probe: the SQLite twin is
//! interrogated through `bcs_bot_action_audits` SQL directly, the memory
//! twin through its published audit records. Failure injection replaces the
//! marked audit INSERT step with a failing statement inside the SAME
//! transaction, so the asserted rollback is the real store rollback.
//!
//! Pinned by the Task-11 brief:
//! - prepare (INSERT) commits `create/session_file/applied` in ONE
//!   transaction and an audit failure rolls the metadata INSERT back;
//! - status changes with no change write NO `applied` row (both dialects,
//!   no affected_rows reliance);
//! - the final DELETE commits `delete/session_file/completed` in ONE
//!   transaction; a metadata/audit failure RETAINS the row (no false
//!   completion) while the earlier `admitted` row stays for inspection;
//! - `record_operation_phase` is the ONLY standalone lane: identical
//!   same-slot replays are idempotent, different content is a Conflict.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;
use std::sync::Mutex as StdMutex;

use async_trait::async_trait;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::{NewSessionFileParams, SessionFileRepoPort};
use bcs_service_api::types::bot_operation::{BotOperationActor, BotOperationContext};
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind, ServiceError,
    stable_step_key,
};
use bcs_domain::{ActorKind, ActorRef, FileStatus};

use bcs_session_file_store::{MemorySessionFileRepo, MySqlSessionFileStore};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

const AUDIT_ENV: &str = "contract";

fn human_operation(operation_id: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: operation_id.to_string(),
        actor: BotOperationActor::Human {
            user_id: "a".to_string(),
            effective_actor_id: "bot-x".to_string(),
        },
    }
}

async fn sqlite() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    db
}

async fn sqlite_store(db: Arc<dyn DbPlugin>) -> MySqlSessionFileStore {
    MySqlSessionFileStore::sqlite(db, AUDIT_ENV.to_string())
}

async fn audit_rows_for(db: &dyn DbPlugin, operation_id: &str, step_key: &str) -> Vec<DbRow> {
    db.query(DbStatement::with_params(
        "SELECT audit_id, env, operation_id, step_key, operator_kind, operator_id, \
         operator_user_id, effective_actor_id, resource_kind, resource_id, action, phase \
         FROM bcs_bot_action_audits \
         WHERE env = ? AND operation_id = ? AND step_key = ?",
        vec![
            DbValue::from(AUDIT_ENV),
            DbValue::from(operation_id),
            DbValue::from(step_key),
        ],
    ))
    .await
    .expect("query file audits")
}

async fn all_audit_rows(db: &dyn DbPlugin) -> Vec<DbRow> {
    db.query(DbStatement::new(
        "SELECT audit_id, operation_id, step_key, operator_kind, operator_id, \
         operator_user_id, effective_actor_id, resource_kind, resource_id, action, phase \
         FROM bcs_bot_action_audits ORDER BY id",
    ))
    .await
    .expect("query all file audits")
}

fn row_str(row: &DbRow, column: &str) -> String {
    row.get_string(column)
        .expect("audit column decodes")
        .unwrap_or_default()
}

/// Test-only DbPlugin wrapper that turns the FIRST transaction step whose
/// SQL contains the armed marker into a failing statement.
struct InjectedStepDb {
    db: Arc<dyn DbPlugin>,
    armed: StdMutex<Option<&'static str>>,
}

impl InjectedStepDb {
    fn new(db: Arc<dyn DbPlugin>) -> Arc<Self> {
        Arc::new(Self {
            db,
            armed: StdMutex::new(None),
        })
    }

    fn arm(&self, marker: &'static str) {
        *self.armed.lock().unwrap() = Some(marker);
    }
}

#[async_trait]
impl DbPlugin for InjectedStepDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.db.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.db.execute(statement).await
    }

    async fn transaction(
        &self,
        mut steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        let marker = self.armed.lock().unwrap().take();
        if let Some(marker) = marker {
            for step in steps.iter_mut() {
                let sql = match step {
                    DbTransactionStep::Query(inner) => inner.sql(),
                    DbTransactionStep::Execute(inner) => inner.sql(),
                    DbTransactionStep::ExecuteChecked { statement: inner, .. } => inner.sql(),
                };
                if sql.contains(marker) {
                    *step = DbTransactionStep::ExecuteChecked {
                        statement: DbStatement::new(
                            "SELECT 1 FROM bcs_file_audit_failure_injection",
                        ),
                        expected_affected_rows: 1,
                    };
                    break;
                }
            }
        }
        self.db.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.db.health_check().await
    }
}

fn human_params(file_id: &str, operation: &BotOperationContext) -> NewSessionFileParams {
    NewSessionFileParams {
        file_id: file_id.to_string(),
        session_id: "session-a".to_string(),
        file_name: format!("f-{file_id}.txt"),
        mime_type: "text/plain".to_string(),
        size: 10,
        owner: ActorRef {
            actor_kind: ActorKind::Bot,
            actor_id: "bot-x".to_string(),
        },
        storage_backend: "local".to_string(),
        object_handle: r#"{"expires_at":9999}"#.to_string(),
        expires_at: 9999,
        operation: operation.clone(),
    }
}

const CREATE_KEY: &str = "create/session_file/applied";
const UPDATE_KEY: &str = "update/session_file/applied";
const DELETE_COMPLETED_KEY: &str = "delete/session_file/completed";
const DELETE_ADMITTED_KEY: &str = "delete/session_file/admitted";

fn phase_record(
    operation: &BotOperationContext,
    file_id: &str,
    action: BotActionKind,
    phase: BotActionAuditPhase,
) -> BotActionAuditRecord {
    let _ = &operation;
    BotActionAuditRecord::new(
        format!(
            "probe-{}-{file_id}-{action:?}-{phase:?}",
            operation.operation_id
        ),
        AUDIT_ENV,
        &operation.operation_id,
        operation.actor.clone(),
        BotActionResourceKind::SessionFile,
        file_id,
        action,
        phase,
        None,
    )
}

#[tokio::test]
async fn sqlite_insert_commits_its_applied_audit_in_the_same_transaction() {
    let db = sqlite().await;
    let repo = sqlite_store(db.clone()).await;
    let operation = human_operation("file-operation-prepare");
    let row = repo
        .insert(human_params("file-1", &operation))
        .await
        .expect("insert file metadata");

    let rows = audit_rows_for(db.as_ref(), "file-operation-prepare", CREATE_KEY).await;
    assert_eq!(rows.len(), 1, "exactly one create applied row");
    let audit = &rows[0];
    assert_eq!(row_str(audit, "operator_kind"), "human");
    assert_eq!(row_str(audit, "operator_id"), "a");
    assert_eq!(row_str(audit, "effective_actor_id"), "bot-x");
    assert_eq!(row_str(audit, "resource_id"), row.file_id);
    assert_eq!(row_str(audit, "action"), "create");
    assert_eq!(row_str(audit, "phase"), "applied");
    assert_eq!(row_str(audit, "step_key"), CREATE_KEY);
    assert_eq!(row_str(audit, "env"), AUDIT_ENV);
}

#[tokio::test]
async fn sqlite_insert_audit_failure_rolls_metadata_back_with_zero_residue() {
    let db = sqlite().await;
    let injected = InjectedStepDb::new(db.clone());
    let repo = MySqlSessionFileStore::sqlite(injected.clone(), AUDIT_ENV.to_string());
    let operation = human_operation("file-operation-rollback");

    injected.arm("INTO bcs_bot_action_audits");
    let error = repo
        .insert(human_params("file-1", &operation))
        .await
        .expect_err("the audit INSERT failure must fail the insert");
    assert!(
        matches!(error, ServiceError::InternalError(_)),
        "got: {error:?}"
    );
    let after = repo
        .get("session-a", "file-1")
        .await
        .expect("probe metadata");
    assert!(
        after.is_none(),
        "the metadata INSERT rolled back with the audit row"
    );
    assert!(all_audit_rows(db.as_ref()).await.is_empty());
}

#[tokio::test]
async fn sqlite_status_change_commits_applied_and_no_change_writes_nothing() {
    let db = sqlite().await;
    let repo = sqlite_store(db.clone()).await;
    let operation = human_operation("file-operation-ready");
    repo.insert(human_params("file-1", &operation))
        .await
        .expect("seed metadata");

    let completion = human_operation("file-operation-ready-complete");
    repo.update_object_handle_and_status(
        "session-a",
        "file-1",
        r#"{"handle":"final","expires_at":1}"#,
        FileStatus::Ready,
        42,
        &completion,
    )
    .await
    .expect("status change")
    .expect("row still exists");
    let rows = audit_rows_for(db.as_ref(), "file-operation-ready-complete", UPDATE_KEY).await;
    assert_eq!(rows.len(), 1, "the real change writes exactly one applied row");
    assert_eq!(row_str(&rows[0], "action"), "update");
    assert_eq!(row_str(&rows[0], "operator_user_id"), "a");

    // No-change update (same triple) writes NO audit row for a fresh
    // operation — on EITHER dialect, without reading affected_rows.
    let noop = human_operation("file-operation-ready-noop");
    let updated = repo
        .update_object_handle_and_status(
            "session-a",
            "file-1",
            r#"{"handle":"final","expires_at":1}"#,
            FileStatus::Ready,
            42,
            &noop,
        )
        .await
        .expect("no-op update stays Ok")
        .expect("row present");
    assert_eq!(updated.status, FileStatus::Ready);
    let rows = audit_rows_for(db.as_ref(), "file-operation-ready-noop", UPDATE_KEY).await;
    assert!(
        rows.is_empty(),
        "an idempotent no-change update must not fabricate an applied row"
    );
}

#[tokio::test]
async fn sqlite_delete_commits_completed_in_one_tx_and_retains_on_failure() {
    let db = sqlite().await;
    let repo = sqlite_store(db.clone()).await;
    let seed = human_operation("file-operation-delete-seed");
    repo.insert(human_params("file-1", &seed))
        .await
        .expect("seed metadata");
    repo.update_object_handle_and_status(
        "session-a",
        "file-1",
        r#"{"handle":"final","expires_at":1}"#,
        FileStatus::Ready,
        42,
        &human_operation("file-operation-delete-ready"),
    )
    .await
    .expect("make file Ready");

    // External-effect class: `admitted` persists before the final delete.
    let operation = human_operation("file-operation-delete");
    let admitted = phase_record(
        &operation,
        "file-1",
        BotActionKind::Delete,
        BotActionAuditPhase::Admitted,
    );
    repo.record_operation_phase(admitted.clone())
        .await
        .expect("persist the admitted phase");
    let rows = audit_rows_for(db.as_ref(), "file-operation-delete", DELETE_ADMITTED_KEY).await;
    assert_eq!(rows.len(), 1);
    assert_eq!(row_str(&rows[0], "operator_user_id"), "a");

    // Happy path: metadata DELETE + `completed` commit in ONE transaction.
    let deleted = repo
        .delete("session-a", "file-1", &operation)
        .await
        .expect("delete");
    assert!(deleted);
    let rows = audit_rows_for(db.as_ref(), "file-operation-delete", DELETE_COMPLETED_KEY).await;
    assert_eq!(rows.len(), 1, "the completed row commits with the DELETE");
    assert_eq!(row_str(&rows[0], "action"), "delete");
    assert!(repo
        .get("session-a", "file-1")
        .await
        .expect("probe")
        .is_none());

    // Failure class: after re-seeding the row, an injected audit failure in
    // the final delete RETAINS the metadata row (no false completion) while
    // the earlier admitted row stays for inspection and no completed row
    // appears — the error surfaces, the store never claims a rollback of
    // the external object.
    let injected_store = MySqlSessionFileStore::sqlite(InjectedStepDb::new(db.clone()), AUDIT_ENV.to_string());
    injected_store
        .insert(human_params("file-2", &human_operation("file-operation-delete2-seed")))
        .await
        .expect("reseed metadata");
    let operation2 = human_operation("file-operation-delete2");
    let admitted2 = phase_record(
        &operation2,
        "file-2",
        BotActionKind::Delete,
        BotActionAuditPhase::Admitted,
    );
    injected_store
        .record_operation_phase(admitted2)
        .await
        .expect("admitted for the second file");
    // arm via the same wrapper instance: recreate with an armed marker
    // (the wrapper above was constructed fresh; use a dedicated wrapper)
    let injected = InjectedStepDb::new(db.clone());
    let failing_store = MySqlSessionFileStore::sqlite(injected.clone(), AUDIT_ENV.to_string());
    injected.arm("INTO bcs_bot_action_audits");
    let error = failing_store
        .delete("session-a", "file-2", &operation2)
        .await
        .expect_err("the audit failure must surface");
    assert!(matches!(error, ServiceError::InternalError(_)), "{error:?}");
    let retained = failing_store
        .get("session-a", "file-2")
        .await
        .expect("probe retained row");
    assert!(retained.is_some(), "the metadata row stays retained for retry");
    let completed = audit_rows_for(db.as_ref(), "file-operation-delete2", DELETE_COMPLETED_KEY).await;
    assert!(
        completed.is_empty(),
        "no completed row may survive the failed delete"
    );
    let admitted_after = audit_rows_for(db.as_ref(), "file-operation-delete2", DELETE_ADMITTED_KEY).await;
    assert_eq!(
        admitted_after.len(),
        1,
        "the admitted row stays for inspection/recovery"
    );
}

#[tokio::test]
async fn sqlite_record_operation_phase_replays_and_conflicts() {
    let db = sqlite().await;
    let repo = sqlite_store(db.clone()).await;
    let operation = human_operation("file-operation-phase");
    let admitted = phase_record(
        &operation,
        "file-9",
        BotActionKind::Delete,
        BotActionAuditPhase::Admitted,
    );
    repo.record_operation_phase(admitted.clone())
        .await
        .expect("first admitted record");
    // Byte-identical replay of the SAME logical step: idempotent, keeps the
    // first row.
    repo.record_operation_phase(admitted.clone())
        .await
        .expect("identical replay is a no-op");
    let rows = audit_rows_for(db.as_ref(), "file-operation-phase", DELETE_ADMITTED_KEY).await;
    assert_eq!(rows.len(), 1);

    // Different content under the same slot is a Conflict, never an
    // overwrite.
    let other_actor_admitted = BotActionAuditRecord {
        operator: BotOperationActor::Bot {
            bot_id: "bot-other".to_string(),
        },
        ..admitted.clone()
    };
    let error = repo
        .record_operation_phase(other_actor_admitted)
        .await
        .expect_err("different content under the slot is a conflict");
    assert!(matches!(error, ServiceError::Conflict(_)), "{error:?}");
    let rows = audit_rows_for(db.as_ref(), "file-operation-phase", DELETE_ADMITTED_KEY).await;
    assert_eq!(row_str(&rows[0], "operator_id"), "a");
}

#[tokio::test]
async fn memory_publishes_metadata_and_audit_together_or_not_at_all() {
    let repo = Arc::new(MemorySessionFileRepo::new());
    let operation = human_operation("file-operation-memory");
    let row = repo
        .insert(human_params("file-1", &operation))
        .await
        .expect("memory insert");
    let audits = repo
        .session_file_action_audit_records()
        .await
        .expect("memory audits");
    assert_eq!(audits.len(), 1);
    assert_eq!(audits[0].step_key, CREATE_KEY);
    assert_eq!(audits[0].resource_id, row.file_id);
    assert_eq!(audits[0].operator.operator_user_id(), Some("a"));

    // Armed audit failure: the staged NO-GROUP insert is DISCARDED — no
    // metadata row, no audit row (all-or-nothing, spec §12.5).
    repo.arm_action_audit_write_failure();
    let error = repo
        .insert(human_params("file-2", &operation))
        .await
        .expect_err("armed audit failure discards the insert");
    assert!(matches!(error, ServiceError::InternalError(_)));
    assert!(
        repo.get("session-a", "file-2").await.expect("probe").is_none(),
        "the discarded insert leaves no metadata row"
    );
    let audits = repo
        .session_file_action_audit_records()
        .await
        .expect("memory audits");
    assert_eq!(audits.len(), 1, "no audit row survives the discard");

    // Final delete + completed row publish together; the row retains on a
    // staged audit failure.
    let delete_operation = human_operation("file-operation-memory-delete");
    repo.delete("session-a", "file-1", &delete_operation)
        .await
        .expect("memory delete");
    let audits = repo
        .session_file_action_audit_records()
        .await
        .expect("memory audits");
    assert_eq!(audits.len(), 2);
    assert_eq!(audits[1].step_key, DELETE_COMPLETED_KEY);
    assert_eq!(audits[1].phase, BotActionAuditPhase::Completed);
    assert_eq!(audits[1].resource_id, "file-1");

    // Re-seed the row so the armed audit failure hits a REAL delete attempt
    // (a missing-row delete is an idempotent no-op and never stages).
    repo.insert(human_params("file-1", &human_operation("file-operation-memory-reseed")))
        .await
        .expect("reseed metadata for the armed delete");
    // The re-seeded delete is a DISTINCT logical operation: a fresh context
    // that must fail at the staged audit append (its slot is its own).
    let armed_delete_operation = human_operation("file-operation-memory-armed");
    repo.arm_action_audit_write_failure();
    let error = repo
        .delete("session-a", "file-1", &armed_delete_operation)
        .await
        .expect_err("armed audit failure discards the delete");
    assert!(matches!(error, ServiceError::InternalError(_)));
    assert!(
        repo.get("session-a", "file-1").await.expect("probe").is_some(),
        "the metadata row stays retained for retry"
    );
    // No completed audit row may survive the discarded delete; the reseeded
    // insert's applied row IS the only extra row.
    let audits = repo
        .session_file_action_audit_records()
        .await
        .expect("memory audits");
    assert_eq!(audits.len(), 3);
    assert!(
        !audits
            .iter()
            .any(|record| record.step_key == DELETE_COMPLETED_KEY
                && record.resource_id == "file-1"
                && record.operation_id == armed_delete_operation.operation_id),
        "the discarded delete adds no completed row"
    );
    let _ = stable_step_key(
        BotActionKind::Delete,
        BotActionResourceKind::SessionFile,
        BotActionAuditPhase::Admitted,
    );
}