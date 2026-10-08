//! Session store + same-commit ordinary-business audit (plan Task 11,
//! spec §12.5).
//!
//! Every asserted variable comes from a REAL store probe: the SQLite twin is
//! interrogated through `bcs_bot_action_audits` SQL directly, the memory
//! twin through its published audit records. Failure injection replaces the
//! marked audit INSERT step with a failing statement inside the SAME
//! transaction, so the asserted rollback is the real store rollback (no
//! partial-success residue survives).
//!
//! Pinned by the Task-11 brief:
//! - a Human operation with a real effective actor stores BOTH identities in
//!   one real `applied` row (`collect/session/applied`);
//! - a legitimate Bot-only operation records `operator_user_id` NULL;
//! - a same-context internal retry keeps the FIRST row (no duplicate step)
//!   and an already-collected / already-uncollected no-change writes NO new
//!   `applied` row (spec §12.5 幂等无变化不造 applied);
//! - an audit INSERT failure rolls the business write back with zero
//!   residue;
//! - session creation commits its `create/session/applied` row with the
//!   launch operation's dual identity.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;
use std::sync::Mutex as StdMutex;

use async_trait::async_trait;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::NewSessionParams;
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_service_api::types::bot_operation::{BotOperationActor, BotOperationContext};
use bcs_service_api::types::{BotActionAuditPhase, Participant, ParticipantRole};
use bcs_service_api::{ActorKind, ServiceError};

use bcs_session_store::{MemorySessionRepo, MySqlSessionStore};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

const AUDIT_ENV: &str = "contract";

fn human_operation(user: &str, effective: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: format!("collect-operation-{user}-{effective}"),
        actor: BotOperationActor::Human {
            user_id: user.to_string(),
            effective_actor_id: effective.to_string(),
        },
    }
}

fn bot_operation(bot_id: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: format!("bot-operation-{bot_id}"),
        actor: BotOperationActor::Bot {
            bot_id: bot_id.to_string(),
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

async fn sqlite_store(db: Arc<dyn DbPlugin>) -> MySqlSessionStore {
    MySqlSessionStore::sqlite(db, AUDIT_ENV.to_string())
}

async fn audit_toolbox(
    db: &dyn DbPlugin,
    operation_id: &str,
    step_key: &str,
) -> Vec<DbRow> {
    db.query(DbStatement::with_params(
        "SELECT audit_id, env, operation_id, step_key, operator_kind, operator_id, \
         operator_user_id, effective_actor_id, resource_kind, resource_id, action, phase \
         FROM bcs_bot_action_audits \
         WHERE env = ? AND operation_id = ? AND step_key = ?",
        vec![
            bcs_db_api::DbValue::from(AUDIT_ENV),
            bcs_db_api::DbValue::from(operation_id),
            bcs_db_api::DbValue::from(step_key),
        ],
    ))
    .await
    .expect("query bcs_bot_action_audits")
}

async fn all_audit_rows(db: &dyn DbPlugin) -> Vec<DbRow> {
    db.query(DbStatement::new(
        "SELECT audit_id, env, operation_id, step_key, operator_kind, operator_id, \
         operator_user_id, effective_actor_id, resource_kind, resource_id, action, phase \
         FROM bcs_bot_action_audits ORDER BY id",
    ))
    .await
    .expect("query all session audits")
}

fn row_str(row: &DbRow, column: &str) -> String {
    row.get_string(column)
        .expect("audit column decodes")
        .unwrap_or_default()
}

/// Test-only DbPlugin wrapper that turns the FIRST transaction step whose
/// SQL contains the armed marker into a failing statement — the same
/// failure-injection harness the group/bot store audit suites use.
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
                            "SELECT 1 FROM bcs_session_audit_failure_injection",
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

fn participant_params(participants: Vec<String>) -> Vec<Participant> {
    participants
        .into_iter()
        .map(|uuid| Participant {
            bot_uuid: uuid,
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: ActorKind::Bot,
            mode: None,
            tags: Vec::new(),
            message_view_scope: bcs_service_api::types::MessageViewScope::Full,
        })
        .collect()
}

fn seed_params(participants: Vec<String>, operation: &BotOperationContext) -> NewSessionParams {
    NewSessionParams {
        participants: participant_params(participants),
        operation: operation.clone(),
        ..Default::default()
    }
}

const COLLECT_STEP_KEY: &str = "collect/session/applied";
const CREATE_STEP_KEY: &str = "create/session/applied";

#[tokio::test]
async fn sqlite_collect_commits_the_applied_audit_with_both_human_identities() {
    let db = sqlite().await;
    let repo = sqlite_store(db.clone()).await;
    let operation = BotOperationContext {
        operation_id: "collect-operation-1".into(),
        actor: BotOperationActor::Human {
            user_id: "a".into(),
            effective_actor_id: "bot-x".into(),
        },
    };
    let session = repo
        .create(
            "session-a-group",
            seed_params(vec!["bot-x".into()], &operation),
        )
        .await
        .expect("seed session");

    // The REAL collect writes its audit row in the same transaction.
    repo.collect(&session.id, "bot-x", &operation)
        .await
        .expect("collect");

    let rows = audit_toolbox(
        db.as_ref(),
        "collect-operation-1",
        COLLECT_STEP_KEY,
    )
    .await;
    assert_eq!(rows.len(), 1, "exactly one applied row for the collect");
    let audit = &rows[0];
    assert_eq!(row_str(audit, "operator_kind"), "human");
    assert_eq!(row_str(audit, "operator_id"), "a");
    assert_eq!(
        audit
            .get_string("operator_user_id")
            .expect("decode operator_user_id")
            .as_deref(),
        Some("a"),
        "the Human operator_user_id survives verbatim"
    );
    assert_eq!(row_str(audit, "effective_actor_id"), "bot-x");
    assert_eq!(row_str(audit, "resource_kind"), "session");
    assert_eq!(row_str(audit, "resource_id"), session.id);
    assert_eq!(row_str(audit, "action"), "collect");
    assert_eq!(row_str(audit, "phase"), "applied");
    assert_eq!(row_str(audit, "step_key"), "collect/session/applied");
    assert_eq!(row_str(audit, "env"), AUDIT_ENV);
}

#[tokio::test]
async fn sqlite_same_context_retry_keeps_one_row_and_no_change_writes_nothing() {
    let db = sqlite().await;
    let repo = sqlite_store(db.clone()).await;
    let operation = BotOperationContext {
        operation_id: "collect-operation-1".into(),
        actor: BotOperationActor::Human {
            user_id: "a".into(),
            effective_actor_id: "bot-x".into(),
        },
    };
    let session = repo
        .create(
            "session-a-group",
            seed_params(vec!["bot-x".into()], &operation),
        )
        .await
        .expect("seed session");

    repo.collect(&session.id, "bot-x", &operation)
        .await
        .expect("first collect");
    // Same-context internal retry: idempotent, and the FIRST committed row
    // is kept — no duplicate step appears in the slot.
    repo.collect(&session.id, "bot-x", &operation)
        .await
        .expect("retry collect");
    let rows = audit_toolbox(db.as_ref(), "collect-operation-1", COLLECT_STEP_KEY).await;
    assert_eq!(rows.len(), 1, "the retry adds no duplicate step");

    // Already collected: a FRESH operation context writes NO new applied
    // row because nothing changed (spec §12.5).
    let repeat = human_operation("a", "bot-x");
    repo.collect(&session.id, "bot-x", &repeat)
        .await
        .expect("no-change collect succeeds");
    let rows = audit_toolbox(db.as_ref(), repeat.operation_id.as_str(), COLLECT_STEP_KEY).await;
    assert!(
        rows.is_empty(),
        "an already-collected collect must not fabricate an applied row"
    );
}

#[tokio::test]
async fn sqlite_bot_only_operation_records_null_operator_user_id() {
    let db = sqlite().await;
    let repo = sqlite_store(db.clone()).await;
    let human_ctx = human_operation("a", "bot-x");
    let session = repo
        .create(
            "session-a-group",
            seed_params(vec!["bot-x".into()], &human_ctx),
        )
        .await
        .expect("seed session");
    repo.collect(&session.id, "bot-x", &human_ctx)
        .await
        .expect("collect under the human's operation");

    // The uncollect flip is a Bot-only operation: `operator_user_id` NULL is
    // a LEGITIMATE row — no Human was involved (spec §12.5).
    let bot_ctx = bot_operation("bot-x");
    repo.uncollect(&session.id, "bot-x", &bot_ctx)
        .await
        .expect("bot-only uncollect");
    let rows = audit_toolbox(db.as_ref(), bot_ctx.operation_id.as_str(), COLLECT_STEP_KEY).await;
    assert_eq!(rows.len(), 1);
    let audit = &rows[0];
    assert_eq!(row_str(audit, "operator_kind"), "bot");
    assert_eq!(row_str(audit, "operator_id"), "bot-x");
    assert!(
        audit
            .get_string("operator_user_id")
            .expect("decode operator_user_id")
            .is_none(),
        "Bot-only operation has NO operator user id"
    );
    assert_eq!(row_str(audit, "effective_actor_id"), "bot-x");

    // A second uncollect (no-change) writes nothing for the retry context.
    let bot_retry = BotOperationContext {
        operation_id: format!("bot-operation-2"),
        actor: BotOperationActor::Bot {
            bot_id: "bot-x".into(),
        },
    };
    repo.uncollect(&session.id, "bot-x", &bot_retry)
        .await
        .expect("idempotent uncollect");
    let rows = all_audit_rows(db.as_ref()).await;
    assert_eq!(
        rows.len(),
        3,
        "expected exactly [create, collect, uncollect] rows: {}",
        rows.len()
    );
}

#[tokio::test]
async fn sqlite_audit_insert_failure_rolls_the_collect_back_with_zero_residue() {
    let db = sqlite().await;
    let injected = InjectedStepDb::new(db.clone());
    // The store MUST read through the same wrapper so the injected failure
    // hits the collect's own transaction.
    let repo = MySqlSessionStore::sqlite(injected.clone(), AUDIT_ENV.to_string());
    let seed_operation = human_operation("a", "bot-x");
    let session = repo
        .create(
            "session-a-group",
            seed_params(vec!["bot-x".into()], &seed_operation),
        )
        .await
        .expect("seed session");
    let before_rows = all_audit_rows(db.as_ref()).await;
    assert_eq!(before_rows.len(), 1);

    injected.arm("INTO bcs_bot_action_audits");
    let operation = human_operation("b", "bot-x");
    let error = repo
        .collect(&session.id, "bot-x", &operation)
        .await
        .expect_err("the audit INSERT failure must fail the whole collect");
    assert!(
        matches!(error, ServiceError::InternalError(_)),
        "the rolled-back attempt surfaces the storage failure: {error:?}"
    );

    // Real-rollback assertions: the favorite flip did NOT survive and the
    // slot carries no row for the failed operation (no partial-success
    // residue).
    let after_rows = all_audit_rows(db.as_ref()).await;
    assert_eq!(after_rows.len(), 1, "no audit row may survive the rollback");
    let probe = audit_toolbox(db.as_ref(), operation.operation_id.as_str(), COLLECT_STEP_KEY).await;
    assert!(probe.is_empty(), "the failed operation wrote nothing");

    // After the arm is spent, the SAME logical collect applies and audits.
    repo.collect(&session.id, "bot-x", &operation)
        .await
        .expect("retry after the injected failure succeeds");
    let rows = audit_toolbox(db.as_ref(), operation.operation_id.as_str(), COLLECT_STEP_KEY).await;
    assert_eq!(rows.len(), 1);
}

#[tokio::test]
async fn sqlite_create_commits_its_applied_audit_row_in_the_same_transaction() {
    let db = sqlite().await;
    let repo = sqlite_store(db.clone()).await;
    let operation = human_operation("a", "bot-launch");
    repo.create(
        "session-a-group",
        seed_params(vec!["bot-driver".into(), "bot-launch".into()], &operation),
    )
    .await
    .expect("create the audited session");

    let rows = audit_toolbox(db.as_ref(), operation.operation_id.as_str(), CREATE_STEP_KEY).await;
    assert_eq!(rows.len(), 1, "creation writes exactly one applied row");
    let audit = &rows[0];
    assert_eq!(row_str(audit, "action"), "create");
    assert_eq!(row_str(audit, "phase"), "applied");
    assert_eq!(row_str(audit, "operator_kind"), "human");
    assert_eq!(row_str(audit, "operator_id"), "a");
    assert_eq!(row_str(audit, "effective_actor_id"), "bot-launch");
}

#[tokio::test]
async fn memory_publishes_collect_state_and_audit_together_or_not_at_all() {
    let repo = Arc::new(MemorySessionRepo::new());
    let operation = human_operation("a", "bot-x");
    let session = repo
        .create(
            "session-a-group",
            seed_params(vec!["bot-x".into()], &operation),
        )
        .await
        .expect("seed memory session");
    repo.collect(&session.id, "bot-x", &operation)
        .await
        .expect("memory collect publishes");

    let audits = repo
        .session_action_audit_records()
        .await
        .expect("memory audits");
    let collect_audits: Vec<_> = audits
        .iter()
        .filter(|record| record.step_key == COLLECT_STEP_KEY)
        .collect();
    assert_eq!(collect_audits.len(), 1);
    assert_eq!(collect_audits[0].operator.operator_user_id(), Some("a"));
    assert_eq!(collect_audits[0].operator.effective_actor_id(), "bot-x");
    assert_eq!(collect_audits[0].phase, BotActionAuditPhase::Applied);

    // Already collected: no fabricated applied row for a fresh context.
    let repeat = human_operation("a", "bot-x");
    repo.collect(&session.id, "bot-x", &repeat)
        .await
        .expect("no-change memory collect");
    let audits = repo
        .session_action_audit_records()
        .await
        .expect("memory audits");
    assert_eq!(
        audits
            .iter()
            .filter(|record| record.step_key == COLLECT_STEP_KEY)
            .count(),
        1,
        "the no-change collect publishes no second applied row"
    );

    // Armed audit failure: the staged collect is DISCARDED — favorite state
    // untouched, no partial-success residue (spec §12.5 all-or-nothing).
    repo.arm_action_audit_write_failure();
    let flip = BotOperationContext {
        operation_id: "uncollect-armed".into(),
        actor: BotOperationActor::Human {
            user_id: "a".into(),
            effective_actor_id: "bot-x".into(),
        },
    };
    let error = repo
        .uncollect(&session.id, "bot-x", &flip)
        .await
        .expect_err("armed audit failure discards the uncollect");
    assert!(matches!(error, ServiceError::InternalError(_)));
    let audits = repo
        .session_action_audit_records()
        .await
        .expect("memory audits");
    assert_eq!(
        audits.len(),
        2,
        "the discarded attempt adds no audit row: {audits:#?}"
    );
    // The collect mark itself is still visible through the read path.
    let sessions = repo
        .list_collected_by_group("session-a-group", "bot-x", None, None, 0, 10)
        .await;
    assert_eq!(sessions.len(), 1, "the collect survived the discarded flip");
}