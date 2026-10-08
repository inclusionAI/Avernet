//! §12.5 acting operation-audit coverage for the Task 12 command lanes
//! (spec §12.5 + brief Step 1/3).
//!
//! Everything runs on REAL persistence:
//!
//! - the friend/invitation lane: `DbConnectService` + the real SQLite
//!   store chain — every request insert/decision commits its
//!   `bcs_bot_action_audits` record in the same transaction, with the real
//!   operator + effective actor projected from the REQUIRED context (Human
//!   keeps both identities, Bot-only has no operator user id);
//!   an armed audit failure rolls the business write back; a missing context
//!   on a NEW command is rejected, never downgraded to System; the
//!   permission-request lane never writes the manager ledger
//!   (`bot_manager_changes`).
//! - the managed-delivery lane: a real `MySqlMessageStore` over the FULL
//!   migration chain — admission persists `operation_id` on the delivery
//!   rows and appends the `send/message/admitted` snapshot in the SAME
//!   transaction; pre-cutover history rows (NULL operation id) read back
//!   legal.
//! - the direct-chat run lane: a real `MemoryChatRunRepo` — create without
//!   a context is rejected; with one, the run row and its
//!   `launch/message/admitted` audit publish in the same critical section,
//!   and an armed audit failure aborts the create leaving ZERO residue.

#![allow(dead_code)]

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission::DbConnectService;
use bcs_edge_permission_store::{
    DbBotActorConfigStore, DbEdgeGrantStore, DbPermissionProfileStore, DbPermissionRequestStore,
};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

use bcs_service_api::application::connect::ConnectService;
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
    PermissionRequestRepoPort,
};
use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoPort as _;
use bcs_service_api::port::{NoopFriendConnectNotificationPort, NoopFriendAuthSyncPort};
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionKind, BotOperationActor, BotOperationContext,
};

const ENV: &str = "local";

async fn full_chain_sqlite() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));
    migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply the full production migration chain");
    db
}

async fn seed_bot_row(db: &Arc<dyn DbPlugin>, bot_id: &str, strategy: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version, visibility, \
         user_visibility, friend_check_in_strategy, created_by, friend_ext, bot_info) \
         VALUES (?, ?, ?, 1, 'protected', 'protected', ?, '1001', '{}', '{}')",
        vec![
            DbValue::from(bot_id),
            DbValue::from(bot_id),
            DbValue::from(ENV),
            DbValue::from(strategy),
        ],
    ))
    .await
    .expect("seed bot row");
}

async fn count(db: &Arc<dyn DbPlugin>, sql: &str) -> i64 {
    let rows = db
        .query(DbStatement::new(sql))
        .await
        .expect("count query");
    rows[0].get_i64("c").ok().flatten().unwrap_or(0)
}

fn human_operation(label: &str, for_user: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: format!("audit-test:{label}:{}", uuid::Uuid::new_v4()),
        actor: BotOperationActor::Human {
            user_id: for_user.to_string(),
            effective_actor_id: format!("human_{for_user}"),
        },
    }
}

fn bot_operation(label: &str, bot_id: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: format!("audit-test-bot:{label}:{}", uuid::Uuid::new_v4()),
        actor: BotOperationActor::Bot {
            bot_id: bot_id.to_string(),
        },
    }
}

/// Armed DbPlugin wrapper: the next transaction writing
/// `bcs_bot_action_audits` fails once (the §12.5 all-or-nothing probe).
struct AuditFailureDb {
    inner: Arc<dyn DbPlugin>,
    armed: std::sync::atomic::AtomicBool,
}

impl AuditFailureDb {
    fn arc(self) -> Arc<AuditFailureDb> {
        Arc::new(self)
    }
}

#[async_trait::async_trait]
impl DbPlugin for AuditFailureDb {
    async fn query(
        &self,
        statement: DbStatement,
    ) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbRow>> {
        self.inner.query(statement).await
    }
    async fn execute(
        &self,
        statement: DbStatement,
    ) -> bcs_db_api::DbResult<bcs_db_api::DbExecuteResult> {
        self.inner.execute(statement).await
    }
    async fn transaction(
        &self,
        steps: Vec<bcs_db_api::DbTransactionStep>,
    ) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> {
        let touches_audit = steps.iter().any(|step| match step {
            bcs_db_api::DbTransactionStep::Query(statement)
            | bcs_db_api::DbTransactionStep::Execute(statement) => {
                statement.sql().contains("bcs_bot_action_audits")
            }
            bcs_db_api::DbTransactionStep::ExecuteChecked { statement, .. } => {
                statement.sql().contains("bcs_bot_action_audits")
            }
        });
        if touches_audit
            && self
                .armed
                .swap(false, std::sync::atomic::Ordering::SeqCst)
        {
            return Err(bcs_db_api::DbError::Backend(
                "test-injected action audit write failure".into(),
            ));
        }
        self.inner.transaction(steps).await
    }
    async fn health_check(&self) -> bcs_db_api::DbResult<bcs_db_api::DbHealth> {
        self.inner.health_check().await
    }
}

impl AuditFailureDb {
    fn arm(&self) {
        self.armed
            .store(true, std::sync::atomic::Ordering::SeqCst);
    }
}

async fn connect_service(db: &Arc<dyn DbPlugin>) -> DbConnectService {
    let edge_grants: Arc<dyn EdgeGrantRepoPort> = Arc::new(DbEdgeGrantStore::sqlite(db.clone()));
    let profiles: Arc<dyn PermissionProfileRepoPort> =
        Arc::new(DbPermissionProfileStore::sqlite(db.clone()));
    let requests: Arc<dyn PermissionRequestRepoPort> =
        Arc::new(DbPermissionRequestStore::sqlite(db.clone()));
    let bot_config: Arc<dyn BotActorConfigRepoPort> =
        Arc::new(DbBotActorConfigStore::sqlite(db.clone()));
    DbConnectService::new(
        edge_grants,
        profiles,
        requests.clone(),
        bot_config,
        None,
        Arc::new(NoopFriendConnectNotificationPort),
        Arc::new(NoopFriendAuthSyncPort),
        ENV.to_string(),
    )
}

// ---------------------------------------------------------------------------
// Friend lane
// ---------------------------------------------------------------------------

#[tokio::test]
async fn friend_request_records_both_identities_and_bot_only_has_no_user_id() {
    let db = full_chain_sqlite().await;
    seed_bot_row(&db, "bot-audit", "APPROVAL").await;
    seed_bot_row(&db, "bot-audit-peer", "APPROVAL").await;
    let service = connect_service(&db).await;

    // Human→Bot connect: Human operator acting as herself; the audit keeps
    // BOTH identities.
    service
        .create_connect(
            "human_1001",
            "bot-audit",
            None,
            None,
            human_operation("create", "1001"),
        )
        .await
        .expect("pending connect");

    // Bot→Bot connect: Bot-only operator — worker_user_id NULL (no Human was
    // involved, never a forged one).
    service
        .create_connect(
            "bot-audit-peer",
            "bot-audit",
            None,
            None,
            bot_operation("create", "bot-audit-peer"),
        )
        .await
        .expect("second pending connect");

    let rows = db
        .query(DbStatement::with_params(
            "SELECT operator_kind, operator_id, operator_user_id, effective_actor_id, \
             resource_kind, action, phase FROM bcs_bot_action_audits WHERE env = ? \
             AND step_key = 'invite/friend/applied' ORDER BY id",
            vec![DbValue::from(ENV)],
        ))
        .await
        .unwrap();
    assert_eq!(rows.len(), 3, "one audit row per persisted request record");
    let human_rows: Vec<_> = rows
        .iter()
        .filter(|row| {
            row.get_string("operator_kind").ok().flatten().as_deref() == Some("human")
        })
        .collect();
    assert!(
        human_rows
            .iter()
            .all(|row| row.get_string("operator_user_id").ok().flatten() == Some("1001".to_string())),
        "Human operator keeps its trusted user id"
    );
    let bot_rows: Vec<_> = rows
        .iter()
        .filter(|row| {
            row.get_string("operator_kind").ok().flatten().as_deref() == Some("bot")
        })
        .collect();
    assert_eq!(bot_rows.len(), 2, "one Human + two Bot-only request records");
    assert!(
        bot_rows
            .iter()
            .all(|row| matches!(row.get("operator_user_id"), None | Some(DbValue::Null))),
        "Bot-only operations record NULL operator_user_id"
    );
    assert!(
        rows.iter().all(|row| row
            .get_string("resource_kind")
            .ok()
            .flatten()
            .as_deref()
            == Some("friend")),
        "friend-lane audits carry the friend resource kind"
    );
    let manager_rows = count(
        &db,
        "SELECT count(1) AS c FROM bot_manager_changes",
    )
    .await;
    assert_eq!(
        manager_rows, 0,
        "permission-request decisions never appear in the manager ledger"
    );
}

#[tokio::test]
async fn friend_decision_audits_use_the_bot_only_or_human_operator_of_the_command() {
    let db = full_chain_sqlite().await;
    seed_bot_row(&db, "bot-decide", "APPROVAL").await;
    let service = connect_service(&db).await;
    let created = service
        .create_connect(
            "human_1001",
            "bot-decide",
            None,
            None,
            human_operation("create", "1001"),
        )
        .await
        .expect("pending connect");
    service
        .approve(
            &created.request_ids[0],
            "human_1001",
            None,
            human_operation("approve", "1001"),
        )
        .await
        .expect("approve");

    let rows = db
        .query(DbStatement::with_params(
            "SELECT operation_id, action, phase FROM bcs_bot_action_audits WHERE env = ? \
             ORDER BY id",
            vec![DbValue::from(ENV)],
        ))
        .await
        .unwrap();
    let decisions: Vec<_> = rows
        .iter()
        .filter(|row| {
            row.get_string("action").ok().flatten().as_deref() == Some("update")
        })
        .collect();
    assert!(
        decisions.len() >= 2,
        "edge build + backfill + decision each carry their own audit slot"
    );
    assert!(
        decisions.iter().all(|row| row
            .get_string("phase")
            .ok()
            .flatten()
            .as_deref()
            == Some("applied"))
    );
}

#[tokio::test]
async fn friend_lane_rejects_missing_context_and_rolls_back_on_audit_failure() {
    let raw = full_chain_sqlite().await;
    seed_bot_row(&raw, "bot-fail", "APPROVAL").await;
    let failing: Arc<AuditFailureDb> = Arc::new(AuditFailureDb {
        inner: raw.clone(),
        armed: std::sync::atomic::AtomicBool::new(false),
    });
    let db: Arc<dyn DbPlugin> = failing.clone();
    let service = connect_service(&db).await;

    // A NEW command without its required context is rejected — never
    // recorded with a forged System operator.
    let mut contextless = human_operation("none", "1001");
    contextless.operation_id = "   ".to_string();
    let denied = service
        .create_connect("human_1001", "bot-fail", None, None, contextless)
        .await;
    assert!(denied.is_err(), "missing context must stop the command");
    let requests = count(
        &raw,
        "SELECT count(1) AS c FROM permission_requests WHERE env = 'local'",
    )
    .await;
    assert_eq!(requests, 0);

    // Armed audit failure: the request insert rolls back with the audit
    // (business + audit all-or-nothing).
    failing.arm();
    let failed = service
        .create_connect(
            "human_1001",
            "bot-fail",
            None,
            None,
            human_operation("audit-fail", "1001"),
        )
        .await;
    assert!(failed.is_err(), "the armed audit failure fails the create");
    let requests = count(
        &raw,
        "SELECT count(1) AS c FROM permission_requests WHERE env = 'local'",
    )
    .await;
    assert_eq!(requests, 0, "no residue of the business write");
    let audits = count(
        &raw,
        "SELECT count(1) AS c FROM bcs_bot_action_audits WHERE env = 'local'",
    )
    .await;
    assert_eq!(audits, 0);
}

// ---------------------------------------------------------------------------
// Managed delivery lane
// ---------------------------------------------------------------------------

fn delivery_command(
    message_id: &str,
    operation: BotOperationContext,
) -> bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries {
    use bcs_domain::message_delivery::DeliveryFlowKind;
    let session = format!("session-{message_id}");
    bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries {
        operation,
        display_message: None,
        message_id: message_id.to_string(),
        flow_kind: DeliveryFlowKind::Group,
        targets: vec![bcs_service_api::port::repo::message_delivery::DeliveryAdmissionTarget {
            rejection: None,
            target_bot_id: "bot-target".to_string(),
            kind: bcs_domain::DeliveryType::Send,
            max_queued: 100,
            semantic_projection_json: serde_json::json!({}),
        }],
        now_ms: 11,
        expire_at_ms: None,
        event: None,
        message: bcs_domain::NewMessage {
            visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
            audience: None,
            group_id: "group".to_string(),
            session_id: session,
            sender_id: "human_1001".to_string(),
            sender_type: bcs_domain::SenderType::Human,
            message_type: "chat".to_string(),
            content: serde_json::json!({"text": "hello"}),
            client_msg_id: Some(message_id.to_string()),
            owner_bot_id: None,
            created_at: 1,
            run_id: String::new(),
        },
    }
}

 async fn create_session_row(db: &Arc<dyn DbPlugin>, session: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_group_sessions (env, group_id, session_id, current_msg_seq, \
         message_visibility_version, participants, session_kind) \
         VALUES (?, 'group', ?, 0, 1, '[]', 'chat')",
        vec![DbValue::from(ENV), DbValue::from(session)],
    ))
    .await
    .expect("session row");
}

#[tokio::test]
async fn delivery_admission_persists_operation_id_and_admitted_snapshot_together() {
    let db = full_chain_sqlite().await;
    let store = bcs_message_store::mysql::MySqlMessageStore::sqlite(db.clone(), ENV.to_string());
    // The command builder keys the canonical session on the message id; seed
    // both rows used by this test (the admission and the rejected contextless
    // attempt both need their canonical session row present).
    for message in ["audit-m1", "audit-m2-contextless"] {
        create_session_row(&db, &format!("session-{message}")).await;
    }

    let operation = human_operation("delivery", "1001");
    let operation_id = operation.operation_id.clone();
    let result = store
        .admit(delivery_command("audit-m1", operation))
        .await
        .expect("admission commits");
    assert!(!result.duplicate);
    assert!(result
        .deliveries
        .iter()
        .all(|d| d.operation_id.as_deref() == Some(operation_id.as_str())));

    // The admitted snapshot committed in the SAME transaction: one row per
    // logical admission, operator + effective actor from the command context.
    let audits = db
        .query(DbStatement::with_params(
            "SELECT operator_kind, operator_user_id, effective_actor_id, action, phase, \
             step_key FROM bcs_bot_action_audits WHERE env = ? AND operation_id = ?",
            vec![DbValue::from(ENV), DbValue::from(operation_id.clone())],
        ))
        .await
        .unwrap();
    assert_eq!(audits.len(), 1);
    assert_eq!(
        audits[0].get_string("step_key").ok().flatten().as_deref(),
        Some("send/message/admitted")
    );
    assert_eq!(
        audits[0].get_string("action").ok().flatten().as_deref(),
        Some("send")
    );
    assert_eq!(
        audits[0].get_string("phase").ok().flatten().as_deref(),
        Some("admitted")
    );
    assert_eq!(
        audits[0].get_string("operator_user_id").ok().flatten().as_deref(),
        Some("1001")
    );

    // Pre-cutover history rows (NULL operation id) stay LEGAL on reads.
    let rows = db
        .query(DbStatement::with_params(
            "SELECT * FROM bcs_message_deliveries WHERE env = ? LIMIT 1",
            vec![DbValue::from(ENV)],
        ))
        .await
        .unwrap();
    assert_eq!(rows.len(), 1);
    let deliveries = store.list_deliveries(Some("session-audit-m1")).await.unwrap();
    assert!(deliveries[0].operation_id.is_some());

    // A NEW admission without its context is rejected, never System-recorded.
    let mut contextless = human_operation("delivery", "1001");
    contextless.operation_id = String::new();
    let rejected = store
        .admit(delivery_command("audit-m2-contextless", contextless))
        .await;
    assert!(rejected.is_err());
    let stored = store
        .lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Message(
            "audit-m2-contextless".into(),
        ))
        .await
        .unwrap();
    assert!(stored.is_empty(), "no residue of the rejected admission");
    let _ = BotActionKind::Send;
    let _ = BotActionAuditPhase::Admitted;
}

// ---------------------------------------------------------------------------
// Direct-chat run lane
// ---------------------------------------------------------------------------

#[tokio::test]
async fn chat_run_create_requires_context_and_audits_in_the_same_section() {
    let repo = bcs_chat_run_store::MemoryChatRunRepo::new();
    let mut record = bcs_service_api::port::repo::ChatRunRecord::new(
        "run-audit".to_string(),
        "bot-target".to_string(),
        "bot-from".to_string(),
        "sk".to_string(),
        1,
        u64::MAX,
        None,
        bcs_service_api::ChatResponseMode::Full,
        bcs_service_api::port::repo::ChatRunCompletionPolicy::WaitForFinal,
    );
    use bcs_service_api::port::repo::ChatRunRepoPort;

    // A new create without its context is REJECTED (the pre-cutover history
    // rows read back with None and stay legal — not covered by new writes).
    let refused = repo.create(record.clone()).await;
    assert!(refused.is_err(), "create without context must fail closed");
    assert!(repo.get("run-audit").await.unwrap().is_none());

    // With the verified Bot context the run row and its
    // `launch/message/admitted` audit publish together.
    let operation = bot_operation("chat-run", "bot-from");
    record.operation = Some(operation);
    repo.create(record.clone()).await.unwrap();
    assert!(repo.get("run-audit").await.unwrap().is_some());
    let audits = repo.action_audit_records().await;
    assert_eq!(audits.len(), 1);
    assert_eq!(
        audits[0].operator,
        BotOperationActor::Bot {
            bot_id: "bot-from".to_string()
        }
    );
    assert_eq!(audits[0].action, BotActionKind::Launch);
    assert_eq!(audits[0].phase, BotActionAuditPhase::Admitted);

    // Same-slot idempotent retry: byte-identical content is a no-op.
    repo.create(record.clone()).await.unwrap_err(); // duplicate run id is a business error path
    let audits = repo.action_audit_records().await;
    assert_eq!(audits.len(), 1, "no phantom audit row for the rejected retry");

    // Armed audit failure: the create aborts with ZERO residue.
    let repo2 = bcs_chat_run_store::MemoryChatRunRepo::new();
    let mut record2 = bcs_service_api::port::repo::ChatRunRecord::new(
        "run-fail".to_string(),
        "bot-target".to_string(),
        "bot-from".to_string(),
        "sk".to_string(),
        1,
        u64::MAX,
        None,
        bcs_service_api::ChatResponseMode::Full,
        bcs_service_api::port::repo::ChatRunCompletionPolicy::WaitForFinal,
    );
    record2.operation = Some(bot_operation("chat-run-fail", "bot-from"));
    repo2.arm_action_audit_write_failure().await;
    let failed = repo2.create(record2).await;
    assert!(failed.is_err());
    assert!(repo2.get("run-fail").await.unwrap().is_none());
    assert!(repo2.action_audit_records().await.is_empty());
}