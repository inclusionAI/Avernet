//! Group eventful mutation + same-commit ordinary-business audit
//! (plan Task 10, spec §12.5).
//!
//! Every asserted variable comes from a real store query — the SQLite twin
//! is probed through `bcs_bot_action_audits` rows directly, the memory twin
//! through its published audit records. Failure injection replaces the
//! marked audit INSERT step with a failing statement inside the SAME
//! transaction, so the asserted rollback is the real store rollback, and
//! no partial-success residue survives.
//!
//! Pinned by the brief:
//! - a Human operation with a real effective actor stores both identities;
//! - a Bot-only operation records `operator_user_id` NULL (no Human);
//! - an audit INSERT failure rolls the business write AND its Event back;
//! - an identical replay of the same `(env, operation_id, step_key)` is an
//!   idempotent completion; different content under the slot is a conflict;
//! - the memory twin publishes state(+audit) together or not at all, on
//!   mutations and on workspace writes alike.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::Mutex as StdMutex;

use async_trait::async_trait;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{MessageViewScope, ParticipantRole};
use bcs_service_api::port::repo::{CommitGroupEventfulMutation, GroupEventfulMutation, GroupRepoPort};
use bcs_service_api::types::bot_operation::{BotOperationActor, BotOperationContext};
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind,
    GroupMutableFieldsPatch, HumanMentionNotifyMode, Participant,
};
use bcs_service_api::Workspace;
use bcs_service_api::{GroupStrategy, ServiceError};

use bcs_event_store::MemoryEventStore;
use bcs_group_store::{GroupBuilder, MemoryGroupRepo, MySqlGroupStore};
use bcs_service_api::port::NewEvent;
use bcs_service_api::port::repo::{
    AppendEventRecord, CreateEventSubscriptionRecord, EventRepoPort, EventSubscriptionRecord,
    EventSubscriptionRevisionRecord, FinalizeGroupProvisioning,
};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EventActor, EventActorType, EventPayloadMode, EventScope,
    EventSubject, EventSubscriptionScope, EventSubscriptionScopeType, EventSubscriptionStatus,
};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

const AUDIT_ENV: &str = "contract";

fn human_operation(user: &str, group_id: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: format!("op-{group_id}"),
        actor: BotOperationActor::Human {
            user_id: user.to_string(),
            effective_actor_id: format!("human_{user}"),
        },
    }
}

fn bot_operation(bot_id: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: format!("op-{bot_id}"),
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

async fn audit_rows(db: &dyn DbPlugin) -> Vec<DbRow> {
    db.query(DbStatement::new(
        "SELECT audit_id, env, operation_id, step_key, operator_kind, operator_id, \
         operator_user_id, effective_actor_id, resource_kind, resource_id, action, phase \
         FROM bcs_bot_action_audits ORDER BY id",
    ))
    .await
    .expect("query group action audits")
}

fn row_str(row: &DbRow, column: &str) -> String {
    row.get_string(column)
        .expect("audit column decodes")
        .unwrap_or_default()
}

/// Test-only DbPlugin wrapper that turns the FIRST transaction step whose
/// SQL contains the armed marker into a failing statement — the classic
/// same-transaction failure-injection harness (mirrors the bot-store
/// audit suite).
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

    fn disarm(&self) {
        *self.armed.lock().unwrap() = None;
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
                        statement: DbStatement::new("SELECT 1 FROM bcs_group_audit_failure_injection"),
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

async fn sqlite_store(db: Arc<dyn DbPlugin>) -> MySqlGroupStore {
    MySqlGroupStore::sqlite(db, AUDIT_ENV.to_string())
}

async fn seed_group(repo: &MySqlGroupStore, group_id: &str) -> i32 {
    let mut group = GroupBuilder::new("driver").id(group_id).build();
    group.participants.push(Participant {
        bot_uuid: "consultant".to_string(),
        bot_name: None,
        kind: None,
        role: ParticipantRole::Consultant,
        actor_kind: bcs_service_api::ActorKind::Bot,
        mode: None,
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Full,
    });
    group.group_strategy = GroupStrategy::Chat;
    repo.upsert(group).await.expect("seed group");
    repo.try_get(group_id)
        .await
        .expect("read seeded group")
        .expect("group exists")
        .version
}

#[tokio::test]
async fn sqlite_patch_commits_the_applied_audit_with_both_human_identities() {
    let db = sqlite().await;
    let repo = sqlite_store(db.clone()).await;
    let version = seed_group(&repo, "audit-group").await;

    let updated = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "audit-group".to_string(),
            expected_version: version,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..Default::default()
            }),
            event: None,
            operation: human_operation("staff-1", "audit-group"),
        })
        .await
        .expect("patch with an audit context commits");

    // Business change visible...
    assert_eq!(updated.version, version + 1);

    // ...and the applied audit row carries BOTH identities, with the slot
    // built from the controlled vocabulary only.
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(rows.len(), 1);
    let audit = &rows[0];
    assert_eq!(row_str(audit, "operator_kind"), "human");
    assert_eq!(row_str(audit, "operator_id"), "staff-1");
    assert_eq!(row_str(audit, "operator_user_id"), "staff-1");
    assert_eq!(row_str(audit, "effective_actor_id"), "human_staff-1");
    assert_eq!(row_str(audit, "resource_kind"), "group");
    assert_eq!(row_str(audit, "resource_id"), "audit-group");
    assert_eq!(row_str(audit, "action"), "update");
    assert_eq!(row_str(audit, "phase"), "applied");
    assert_eq!(row_str(audit, "step_key"), "update/group/applied");
    assert_eq!(row_str(audit, "env"), AUDIT_ENV);

    // Bot-only operator: operator_user_id is NULL and that is legal.
    let version = updated.version;
    repo.commit_eventful_mutation(CommitGroupEventfulMutation {
        group_id: "audit-group".to_string(),
        expected_version: version,
        mutated_at_ms: 1_787_028_200_000,
        mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
            label: Some("Bot Only".to_string()),
            ..Default::default()
        }),
        event: None,
        operation: bot_operation("driver"),
    })
    .await
    .expect("bot-only patch commits");
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(rows.len(), 2);
    let bot_audit = &rows[1];
    assert_eq!(row_str(bot_audit, "operator_kind"), "bot");
    assert_eq!(row_str(bot_audit, "operator_id"), "driver");
    assert!(
        bot_audit
            .get_string("operator_user_id")
            .expect("decode operator_user_id")
            .is_none(),
        "Bot-only operation has NO operator user id — no Human was involved"
    );
    assert_eq!(row_str(bot_audit, "effective_actor_id"), "driver");
}

#[tokio::test]
async fn sqlite_audit_insert_failure_rolls_back_state_and_event_with_no_residue() {
    let db = sqlite().await;
    let injected = InjectedStepDb::new(db.clone());
    // The store must read through the SAME wrapper so the injected failure
    // hits the mutation's own transaction.
    let repo = MySqlGroupStore::sqlite(injected.clone(), AUDIT_ENV.to_string());
    let version = seed_group(&repo, "rollback-group").await;

    injected.arm("INTO bcs_bot_action_audits");
    let error = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "rollback-group".to_string(),
            expected_version: version,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                label: Some("Must Not Persist".to_string()),
                ..Default::default()
            }),
            event: None,
            operation: human_operation("staff-1", "rollback-group"),
        })
        .await
        .expect_err("the audit INSERT failure must fail the whole mutation");
    assert!(
        matches!(error, ServiceError::InternalError(_)),
        "the rolled-back attempt surfaces the storage failure, got: {error:?}"
    );

    // Real-rollback assertions: neither the business change nor the audit
    // row survived.
    let stored = repo
        .try_get("rollback-group")
        .await
        .expect("read back")
        .expect("group survives");
    assert_eq!(stored.version, version);
    assert!(
        stored.label.as_deref().is_none_or(|l| l != "Must Not Persist"),
        "the rolled-back patch must not be partially applied"
    );
    let rows = audit_rows(db.as_ref()).await;
    assert!(
        rows.is_empty(),
        "no audit row may survive a rolled-back transaction (no partial-success residue)"
    );

    // After disarm, the SAME logical patch applies and audits normally.
    injected.disarm();
    repo.commit_eventful_mutation(CommitGroupEventfulMutation {
        group_id: "rollback-group".to_string(),
        expected_version: version,
        mutated_at_ms: 1_787_028_200_000,
        mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
            label: Some("Must Not Persist".to_string()),
            ..Default::default()
        }),
        event: None,
        operation: human_operation("staff-1", "rollback-group"),
    })
    .await
    .expect("retry after disarm succeeds");
    let stored = repo
        .try_get("rollback-group")
        .await
        .expect("read back")
        .expect("group exists");
    assert_eq!(stored.label.as_deref(), Some("Must Not Persist"));
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(rows.len(), 1);
}

#[tokio::test]
async fn sqlite_identical_replay_completes_idempotently_while_a_conflicting_slot_rejects() {
    let db = sqlite().await;
    let repo = sqlite_store(db.clone()).await;
    let version = seed_group(&repo, "replay-group").await;
    let operation = human_operation("staff-1", "replay-group");

    let first = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "replay-group".to_string(),
            expected_version: version,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                label: Some("Replayed".to_string()),
                ..Default::default()
            }),
            event: None,
            operation: operation.clone(),
        })
        .await
        .expect("first attempt succeeds");
    assert_eq!(first.version, version + 1);
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(rows.len(), 1);
    let first_audit_id = row_str(&rows[0], "audit_id");

    // Byte-identical replay of the SAME operation (same slot, same content):
    // the first audit row keeps its identity and the mutation completes
    // idempotently against the committed state.
    let replay = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "replay-group".to_string(),
            expected_version: version,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                label: Some("Replayed".to_string()),
                ..Default::default()
            }),
            event: None,
            operation: operation.clone(),
        })
        .await;
    match replay {
        Ok(replayed) => assert_eq!(replayed.version, version + 1),
        Err(error) => panic!("identical replay is an idempotent completion: {error:?}"),
    }
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(rows.len(), 1, "the first row is kept, not duplicated");
    assert_eq!(row_str(&rows[0], "audit_id"), first_audit_id);

    // A DIFFERENT operator under the SAME (env, operation_id, step_key)
    // slot is a conflict — never an overwrite.
    let conflicting = BotOperationContext {
        operation_id: operation.operation_id.clone(),
        actor: BotOperationActor::Human {
            user_id: "staff-2".to_string(),
            effective_actor_id: "human_staff-2".to_string(),
        },
    };
    let version = repo
        .try_get("replay-group")
        .await
        .expect("read")
        .expect("group")
        .version;
    let error = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "replay-group".to_string(),
            expected_version: version,
            mutated_at_ms: 1_787_028_300_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                label: Some("Forged".to_string()),
                ..Default::default()
            }),
            event: None,
            operation: conflicting,
        })
        .await
        .expect_err("different content in the same slot is a conflict");
    assert!(
        matches!(error, ServiceError::Conflict(_)),
        "got: {error:?}"
    );
    let rows = audit_rows(db.as_ref()).await;
    assert_eq!(
        row_str(&rows[0], "operator_id"),
        "staff-1",
        "the conflicting write must not overwrite the committed row"
    );
}

#[tokio::test]
async fn memory_publishes_state_and_audit_together_or_not_at_all() {
    let repo = Arc::new(MemoryGroupRepo::new());
    let mut group = GroupBuilder::new("driver").id("memory-audit").build();
    group.originator = Some("human_staff-1".into());
    repo.upsert(group.clone()).await.expect("seed group");

    // Published mutation: ONE applied audit row with both identities.
    let updated = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "memory-audit".to_string(),
            expected_version: group.version,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                label: Some("Memory Audited".to_string()),
                ..Default::default()
            }),
            event: None,
            operation: human_operation("staff-1", "memory-audit"),
        })
        .await
        .expect("memory mutation publishes");
    assert_ne!(updated.version, group.version);
    let audits = repo.group_action_audit_records().await.expect("audits");
    assert_eq!(audits.len(), 1);
    assert_eq!(audits[0].step_key, "update/group/applied");
    assert_eq!(audits[0].operator.operator_kind(), "human");
    assert_eq!(audits[0].operator.operator_user_id(), Some("staff-1"));
    assert_eq!(audits[0].operator.effective_actor_id(), "human_staff-1");
    assert_eq!(audits[0].resource_id, "memory-audit");
    assert_eq!(audits[0].action, BotActionKind::Update);
    assert_eq!(audits[0].phase, BotActionAuditPhase::Applied);

    // Armed audit failure: the staged mutation is DISCARDED — the state is
    // untouched and no audit row appears (no partial success).
    repo.arm_action_audit_write_failure();
    let error = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "memory-audit".to_string(),
            expected_version: updated.version,
            mutated_at_ms: 1_787_028_200_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                label: Some("Discarded".to_string()),
                ..Default::default()
            }),
            event: None,
            operation: human_operation("staff-1", "memory-audit"),
        })
        .await
        .expect_err("armed audit failure discards the whole mutation");
    assert!(matches!(error, ServiceError::InternalError(_)));
    let stored = repo.get("memory-audit").await.expect("group exists");
    assert_ne!(stored.label.as_deref(), Some("Discarded"));
    assert_eq!(stored.version, updated.version);
    let audits = repo.group_action_audit_records().await.expect("audits");
    assert_eq!(audits.len(), 1, "the discarded publish adds no audit row");

    // Workspace write: same critical-section rule — workspace + audit
    // publish together (and a failure discards the staged workspace).
    repo.update_workspace(
        "memory-audit",
        Workspace {
            decisions: vec!["ship it".to_string()],
            ..Default::default()
        },
        human_operation("staff-1", "memory-audit"),
    )
    .await
    .expect("workspace publishes with its audit");
    let audits = repo.group_action_audit_records().await.expect("audits");
    assert!(
        audits
            .iter()
            .any(|record: &BotActionAuditRecord| record.step_key == "update/workspace/applied"
                && record.resource_kind == BotActionResourceKind::Workspace),
        "the workspace write carries its own applied audit row"
    );
    let before = audits.len();
    repo.arm_action_audit_write_failure();
    let error = repo
        .update_workspace(
            "memory-audit",
            Workspace {
                decisions: vec!["discarded".to_string()],
                ..Default::default()
            },
            human_operation("staff-1", "memory-audit"),
        )
        .await
        .expect_err("armed audit failure discards the staged workspace");
    assert!(matches!(error, ServiceError::InternalError(_)));
    let stored = repo.get("memory-audit").await.expect("group exists");
    assert_eq!(
        stored.workspace.decisions,
        vec!["ship it".to_string()],
        "the discarded workspace publish restores the previous state"
    );
    let audits = repo.group_action_audit_records().await.expect("audits");
    assert_eq!(audits.len(), before, "the discarded workspace adds no audit row");
}

// ---------------------------------------------------------------------------
// Evented memory lanes: audit failure must roll the EVENT back too
// (spec §12.5: state/Event/audit publish together or not at all). The
// failure is injected with an armed audit-append failure; observability is
// the public event-store surface (`get_event` / `get_subscription`), so no
// private state is peeked.
// ---------------------------------------------------------------------------

fn audit_suite_event(env: &str, group_id: &str, event_id: &str, event_type: &str) -> AppendEventRecord {
    AppendEventRecord {
        event: NewEvent {
            event_id: event_id.to_string(),
            event_type: event_type.to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "group-action-audit-test".to_string(),
            producer_key: format!("{event_type}:{group_id}:{event_id}"),
            occurred_at: "2026-08-19T00:00:00.000Z".to_string(),
            subject: EventSubject {
                subject_type: "group".to_string(),
                id: group_id.to_string(),
            },
            scope: EventScope {
                group_id: Some(group_id.to_string()),
                ..EventScope::default()
            },
            stream_key: format!("group:{group_id}"),
            actor: Some(EventActor {
                actor_type: EventActorType::Human,
                id: "human_staff-1".to_string(),
                display_name: None,
            }),
            correlation_id: None,
            causation_event_id: None,
            trace_id: None,
            data: BTreeMap::new(),
        },
        recorded_at: "2026-08-19T00:00:00.001Z".to_string(),
        retention_until_ms: 2_000_000_000_000,
        env: env.to_string(),
    }
}

#[tokio::test]
async fn memory_evented_mutation_failure_rolls_back_state_event_and_audit() {
    let event_store = Arc::new(MemoryEventStore::new());
    let repo = Arc::new(
        MemoryGroupRepo::new().with_event_store(event_store.clone(), AUDIT_ENV.to_string()),
    );
    let mut group = GroupBuilder::new("driver").id("memory-evented").build();
    group.label = None;
    repo.upsert(group.clone()).await.expect("seed group");

    // Positive control: an event-ful mutation with an audit context commits
    // the business state, the Event, and the applied audit row together.
    let committed = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "memory-evented".to_string(),
            expected_version: group.version,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                label: Some("Committed".to_string()),
                ..Default::default()
            }),
            event: Some(audit_suite_event(
                AUDIT_ENV,
                "memory-evented",
                "evt-audit-1",
                "group.updated",
            )),
            operation: human_operation("staff-1", "memory-evented"),
        })
        .await
        .expect("event-ful mutation with audit commits");
    assert_ne!(committed.version, group.version);
    assert!(
        event_store
            .get_event("evt-audit-1", AUDIT_ENV)
            .await
            .expect("query event")
            .is_some(),
        "the committed Event exists"
    );
    let version_after_commit = committed.version;
    assert_eq!(
        repo.group_action_audit_records().await.expect("audits").len(),
        1
    );

    // Inject the audit failure and replay a second, identical-shaped
    // operation: NO residue may survive — not the state, not the Event, and
    // no partial audit row (spec §12.5).
    repo.arm_action_audit_write_failure();
    let error = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "memory-evented".to_string(),
            expected_version: version_after_commit,
            mutated_at_ms: 1_787_028_200_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                label: Some("Must Not Persist".to_string()),
                ..Default::default()
            }),
            event: Some(audit_suite_event(
                AUDIT_ENV,
                "memory-evented",
                "evt-audit-2",
                "group.updated",
            )),
            operation: BotOperationContext {
                operation_id: "op-memory-evented-2".to_string(),
                actor: BotOperationActor::Human {
                    user_id: "staff-1".to_string(),
                    effective_actor_id: "human_staff-1".to_string(),
                },
            },
        })
        .await
        .expect_err("the armed audit failure must fail the whole mutation");
    assert!(
        matches!(error, ServiceError::InternalError(_)),
        "got: {error:?}"
    );

    let stored = repo
        .get("memory-evented")
        .await
        .expect("group survived");
    assert_eq!(stored.version, version_after_commit);
    assert_ne!(stored.label.as_deref(), Some("Must Not Persist"));
    assert!(
        event_store
            .get_event("evt-audit-2", AUDIT_ENV)
            .await
            .expect("query event")
            .is_none(),
        "the rolled-back Event must not survive the audit failure"
    );
    let audits = repo.group_action_audit_records().await.expect("audits");
    assert_eq!(
        audits.len(),
        1,
        "no audit row may be added by the discarded attempt: {audits:#?}"
    );
}

#[tokio::test]
async fn memory_evented_deletion_failure_rolls_back_group_subscription_and_audit() {
    let event_store = Arc::new(MemoryEventStore::new());
    let repo = Arc::new(
        MemoryGroupRepo::new().with_event_store(event_store.clone(), AUDIT_ENV.to_string()),
    );
    let mut group = GroupBuilder::new("driver").id("memory-delete-audit").build();
    group.record_status = "provisioning".to_string();
    repo.upsert(group).await.expect("seed provisioning group");
    event_store
        .create_subscription(CreateEventSubscriptionRecord {
            subscription: EventSubscriptionRecord {
                subscription_id: "sub-delete-audit".to_string(),
                name: "deletion audit probe".to_string(),
                scope: EventSubscriptionScope {
                    scope_type: EventSubscriptionScopeType::Group,
                    id: "memory-delete-audit".to_string(),
                },
                status: EventSubscriptionStatus::Pending,
                current_revision: 1,
                created_by: EventActor {
                    actor_type: EventActorType::Human,
                    id: "human_staff-1".to_string(),
                    display_name: None,
                },
                created_at_ms: 1_787_027_999_000,
                updated_at_ms: 1_787_027_999_000,
                deleted_at_ms: None,
                env: AUDIT_ENV.to_string(),
            },
            revision: EventSubscriptionRevisionRecord {
                subscription_id: "sub-delete-audit".to_string(),
                revision: 1,
                event_filters: vec!["group.created".to_string()],
                payload_mode: EventPayloadMode::MetadataOnly,
                endpoint_url: "https://events.example.com/delete-audit".to_string(),
                request_timeout_ms: 5_000,
                activated_at_ms: 0,
                retired_at_ms: None,
            },
            scope_limit: 10,
        })
        .await
        .expect("create pending subscription");
    repo.finalize_provisioning(FinalizeGroupProvisioning {
        group_id: "memory-delete-audit".to_string(),
        env: AUDIT_ENV.to_string(),
        subscription_ids: vec!["sub-delete-audit".to_string()],
        events: vec![audit_suite_event(
            AUDIT_ENV,
            "memory-delete-audit",
            "evt-delete-audit-created",
            "group.created",
        )],
        actor: EventActor {
            actor_type: EventActorType::Human,
            id: "human_staff-1".to_string(),
            display_name: None,
        },
        finalized_at_ms: 1_787_028_000_000,
    })
    .await
    .expect("finalize provisioning activates the subscription");

    let (subscription, _) = event_store
        .get_subscription("sub-delete-audit", AUDIT_ENV)
        .await
        .expect("load subscription")
        .expect("subscription exists");
    assert_eq!(subscription.status, EventSubscriptionStatus::Active);

    // Inject the audit failure on the audited deletion: NO partial success
    // may survive — Group restored, subscription reconciliation untouched,
    // zero audit rows (spec §12.5).
    repo.arm_action_audit_write_failure();
    let error = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "memory-delete-audit".to_string(),
            expected_version: 1,
            mutated_at_ms: 1_787_028_200_000,
            mutation: GroupEventfulMutation::Delete,
            event: None,
            operation: human_operation("staff-1", "memory-delete-audit"),
        })
        .await
        .expect_err("the armed audit failure must fail the whole deletion");
    assert!(
        matches!(error, ServiceError::InternalError(_)),
        "got: {error:?}"
    );

    assert!(
        repo.get("memory-delete-audit").await.is_some(),
        "the rolled-back deletion restores the Group"
    );
    let (subscription, _) = event_store
        .get_subscription("sub-delete-audit", AUDIT_ENV)
        .await
        .expect("load subscription after rollback")
        .expect("subscription survives");
    assert_eq!(
        subscription.status,
        EventSubscriptionStatus::Active,
        "the deletion's subscription reconciliation (disable+cancel) must roll back too"
    );
    let audits = repo.group_action_audit_records().await.expect("audits");
    assert!(
        audits.is_empty(),
        "no audit row may survive the discarded deletion: {audits:#?}"
    );
}