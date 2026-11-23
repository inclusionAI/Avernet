//! Committed Group notifications must not depend on a second database read.
#![allow(clippy::expect_used)]

use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group::{GroupCore, MySqlGroupStore};
use bcs_service_api::core::{GroupMutationCommand, GroupMutationKind};
use bcs_service_api::port::CatalogChangePort;
use bcs_service_api::port::repo::{
    CommitGroupEventfulMutation, GroupEventfulMutation, GroupRepoPort,
};
use bcs_service_api::types::catalog_labels::CatalogLabels;
use bcs_service_api::types::{EventActor, EventActorType};
use bcs_service_api::{
    Group, GroupCoreService, GroupMutableFieldsPatch, GroupStrategy, Participant, ParticipantRole,
};
use serde_json::json;

const ENV: &str = "catalog-commit";
const GROUP: &str = "committed-group";

/// Real SQLite transactions, with faults armed at their actual commit boundary.
struct CommitProbe {
    inner: Arc<LocalSqliteDbPlugin>,
    armed: AtomicBool,
    fail_in_transaction: AtomicBool,
    committed: AtomicBool,
    failed_reads: AtomicUsize,
    pre_commit_group_reads: AtomicUsize,
    transactions: AtomicUsize,
    successful_transactions: AtomicUsize,
}

impl CommitProbe {
    fn arm(&self, fail_in_transaction: bool) {
        self.fail_in_transaction
            .store(fail_in_transaction, Ordering::SeqCst);
        self.committed.store(false, Ordering::SeqCst);
        self.failed_reads.store(0, Ordering::SeqCst);
        self.pre_commit_group_reads.store(0, Ordering::SeqCst);
        self.transactions.store(0, Ordering::SeqCst);
        self.successful_transactions.store(0, Ordering::SeqCst);
        self.armed.store(true, Ordering::SeqCst);
    }
}

#[async_trait]
impl DbPlugin for CommitProbe {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if self.armed.load(Ordering::SeqCst)
            && (statement.sql().contains("FROM bcs_groups")
                || statement.sql().contains("FROM bcs_group_participants"))
        {
            if self.committed.load(Ordering::SeqCst) {
                self.failed_reads.fetch_add(1, Ordering::SeqCst);
                return Err(DbError::Backend(
                    "injected post-commit Group read failure".into(),
                ));
            }
            self.pre_commit_group_reads.fetch_add(1, Ordering::SeqCst);
        }
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        mut steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        if self.armed.load(Ordering::SeqCst) {
            self.transactions.fetch_add(1, Ordering::SeqCst);
            if self.fail_in_transaction.load(Ordering::SeqCst) {
                // Fail after the real Group/template/label steps have executed:
                // SQLite must roll all of them back, not fake a pre-write error.
                steps.push(DbTransactionStep::Execute(DbStatement::new(
                    "UPDATE catalog_commit_nonexistent_table SET value = 1",
                )));
            }
        }
        let results = self.inner.transaction(steps).await?;
        if self.armed.load(Ordering::SeqCst) {
            self.successful_transactions.fetch_add(1, Ordering::SeqCst);
            self.committed.store(true, Ordering::SeqCst);
        }
        Ok(results)
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

struct Changes {
    db: Arc<CommitProbe>,
    ids: Mutex<Vec<String>>,
}

impl CatalogChangePort for Changes {
    fn group_changed(&self, id: &str) {
        assert!(
            self.db.committed.load(Ordering::SeqCst),
            "notify only after COMMIT"
        );
        self.ids
            .lock()
            .expect("notification lock")
            .push(id.to_string());
    }

    fn agent_changed(&self, _: &str) {
        panic!("Group test must not notify an Agent");
    }
}

struct Fixture {
    db: Arc<CommitProbe>,
    repo: Arc<MySqlGroupStore>,
    core: GroupCore,
    changes: Arc<Changes>,
}

async fn fixture() -> Fixture {
    let inner = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite"));
    bcs_db_migrations::run_sqlite_migrations(inner.as_ref())
        .await
        .expect("migrate SQLite");
    let db = Arc::new(CommitProbe {
        inner,
        armed: AtomicBool::new(false),
        fail_in_transaction: AtomicBool::new(false),
        committed: AtomicBool::new(false),
        failed_reads: AtomicUsize::new(0),
        pre_commit_group_reads: AtomicUsize::new(0),
        transactions: AtomicUsize::new(0),
        successful_transactions: AtomicUsize::new(0),
    });
    let repo = Arc::new(MySqlGroupStore::sqlite(db.clone(), ENV.to_string()));
    let mut manager = Participant::bot("manager", ParticipantRole::Manager);
    manager.bot_name = Some("cache-only display name".into());
    let mut group = Group::new(GROUP, "manager", vec![manager]);
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.visibility = "public".into();
    group.label = Some("Before".into());
    group.context = Some("Before context".into());
    // Upsert caches the input, but SQL owns creation time and participant names.
    group.created_at = 123_456;
    repo.upsert_with_catalog_labels(group, Some(labels("trial")))
        .await
        .expect("seed Group and labels");
    repo.try_get(GROUP)
        .await
        .expect("warm Group cache")
        .expect("Group");
    let changes = Arc::new(Changes {
        db: db.clone(),
        ids: Mutex::new(Vec::new()),
    });
    let core = GroupCore::with_repo(repo.clone()).with_catalog_changes(Some(changes.clone()));
    Fixture {
        db,
        repo,
        core,
        changes,
    }
}

fn labels(value: &str) -> CatalogLabels {
    serde_json::from_value(json!({"lifecycle": [value]})).expect("normalized labels")
}

fn command(mutation: GroupMutationKind) -> GroupMutationCommand {
    GroupMutationCommand {
        group_id: GROUP.into(),
        actor: EventActor {
            actor_type: EventActorType::Human,
            id: "human_owner".into(),
            display_name: None,
        },
        correlation_id: None,
        trace_id: None,
        mutation,
    }
}

fn patch(combined: bool) -> GroupMutableFieldsPatch {
    GroupMutableFieldsPatch {
        catalog_labels: Some(labels("running")),
        label: combined.then(|| "After".into()),
        context: combined.then(|| "After context".into()),
        ..GroupMutableFieldsPatch::default()
    }
}

fn canonical_group(mut group: Group) -> serde_json::Value {
    group
        .participants
        .sort_by(|a, b| a.bot_uuid.cmp(&b.bot_uuid));
    serde_json::to_value(group).expect("serialize Group")
}

#[tokio::test]
async fn committed_labels_notify_without_post_commit_group_reads() {
    for combined in [false, true] {
        let f = fixture().await;
        f.db.arm(false);
        let updated = f
            .core
            .mutate(command(GroupMutationKind::PatchMutableFields(patch(
                combined,
            ))))
            .await
            .expect("committed mutation must not require another read");
        assert_eq!(f.db.transactions.load(Ordering::SeqCst), 1);
        assert_eq!(f.db.pre_commit_group_reads.load(Ordering::SeqCst), 2);
        assert_eq!(f.db.successful_transactions.load(Ordering::SeqCst), 1);
        assert_eq!(f.db.failed_reads.load(Ordering::SeqCst), 0);
        assert_eq!(
            *f.changes.ids.lock().expect("notifications"),
            vec![GROUP.to_string()]
        );
        assert_eq!(updated.version, 2);
        assert_eq!(
            updated.label.as_deref(),
            Some(if combined { "After" } else { "Before" })
        );
        assert_eq!(updated.updated_at % 1000, 0);

        // A new, undecorated store reads the actual committed DB and bypasses
        // both the fault and any cached input/candidate.
        let reader = MySqlGroupStore::sqlite(f.db.inner.clone(), ENV.to_string());
        let stored = reader
            .try_get(GROUP)
            .await
            .expect("read committed Group")
            .expect("Group");
        assert_eq!(canonical_group(updated.clone()), canonical_group(stored));
        let snapshot = reader
            .read_card_snapshot(GROUP)
            .await
            .expect("Card snapshot")
            .expect("Group");
        assert_eq!(snapshot.catalog_labels, labels("running"));
        let card = snapshot.card_template.expect("template");
        assert_eq!(card["name"], if combined { "After" } else { "Before" });
        assert_eq!(
            card["description"],
            if combined {
                "After context"
            } else {
                "Before context"
            }
        );

        // Prove the failure is armed AND the mutation invalidated its cache.
        // An old implementation consumes this fault before it can notify.
        assert!(f.repo.try_get(GROUP).await.is_err());
        assert_eq!(f.db.failed_reads.load(Ordering::SeqCst), 1);
        f.db.armed.store(false, Ordering::SeqCst);
        let reloaded = f
            .repo
            .try_get(GROUP)
            .await
            .expect("recovery read")
            .expect("Group");
        assert_eq!(canonical_group(updated), canonical_group(reloaded));
    }
}

#[tokio::test]
async fn transaction_failure_rolls_back_group_template_labels_and_never_notifies() {
    let f = fixture().await;
    let reader = MySqlGroupStore::sqlite(f.db.inner.clone(), ENV.to_string());
    let before = reader
        .try_get(GROUP)
        .await
        .expect("before Group")
        .expect("Group");
    let before_card = reader
        .read_card_snapshot(GROUP)
        .await
        .expect("before Card")
        .expect("Group");
    f.db.arm(true);
    let result = f
        .core
        .mutate(command(GroupMutationKind::PatchMutableFields(patch(true))))
        .await;
    assert!(result.is_err(), "DB write failures must propagate");
    assert_eq!(f.db.transactions.load(Ordering::SeqCst), 1);
    assert_eq!(f.db.successful_transactions.load(Ordering::SeqCst), 0);
    assert!(!f.db.committed.load(Ordering::SeqCst));
    assert_eq!(f.db.failed_reads.load(Ordering::SeqCst), 0);
    assert!(f.changes.ids.lock().expect("notifications").is_empty());
    // Use another store so even a stale success-shaped cache cannot mask rollback.
    let after_reader = MySqlGroupStore::sqlite(f.db.inner.clone(), ENV.to_string());
    let after = after_reader
        .try_get(GROUP)
        .await
        .expect("after Group")
        .expect("Group");
    assert_eq!(canonical_group(after), canonical_group(before));
    let after_card = after_reader
        .read_card_snapshot(GROUP)
        .await
        .expect("after Card")
        .expect("Group");
    assert_eq!(after_card.catalog_labels, before_card.catalog_labels);
    assert_eq!(after_card.card_template, before_card.card_template);
}

#[tokio::test]
async fn candidate_preserves_sql_timestamp_and_participant_response_projection() {
    let f = fixture().await;
    let mut participant = Participant::bot("added", ParticipantRole::Worker);
    participant.bot_name = Some("not persisted by membership INSERT".into());
    participant.kind = None;
    participant.mode = None;
    participant.tags = vec!["not inserted by this existing SQL path".into()];
    let mutations = [
        GroupEventfulMutation::PatchMutableFields(patch(true)),
        GroupEventfulMutation::AddParticipant {
            participant,
            actor_is_public: true,
        },
        GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
            context: Some("materialize default routing".into()),
            default_bot_final_delivery: Some(Default::default()),
            ..GroupMutableFieldsPatch::default()
        }),
        GroupEventfulMutation::UpdateParticipantMode {
            actor_id: "added".into(),
            mode: bcs_service_api::ParticipantMode::Muted,
        },
        GroupEventfulMutation::AddParticipant {
            participant: Participant::human("human_observer", ParticipantRole::Observer),
            actor_is_public: false,
        },
        GroupEventfulMutation::UpdateParticipantMessageViewScope {
            actor_id: "human_observer".into(),
            message_view_scope: bcs_service_api::types::MessageViewScope::Participant,
            mode: Some(bcs_service_api::ParticipantMode::Present),
        },
        GroupEventfulMutation::UpdateParticipantMessageViewScope {
            actor_id: "human_observer".into(),
            message_view_scope: bcs_service_api::types::MessageViewScope::Full,
            mode: None,
        },
        GroupEventfulMutation::UpdateRoutingPolicy(bcs_service_api::RoutingPolicy {
            mode: bcs_service_api::RoutingMode::Mention,
            ..Default::default()
        }),
        GroupEventfulMutation::RemoveParticipant {
            actor_id: "added".into(),
        },
        GroupEventfulMutation::UpdateStatus(bcs_service_api::GroupStatus::Closed),
    ];
    let mut version = 1;
    for mutation in mutations {
        f.db.arm(false);
        let updated = f
            .repo
            .commit_eventful_mutation(CommitGroupEventfulMutation {
                group_id: GROUP.into(),
                expected_version: version,
                mutated_at_ms: 1_700_000_000_987,
                mutation,
                event: None,
            })
            .await
            .expect("commit candidate");
        version += 1;
        assert_eq!(updated.version, version);
        assert_eq!(updated.updated_at, 1_700_000_000_000);
        assert_eq!(f.db.failed_reads.load(Ordering::SeqCst), 0);
        let reader = MySqlGroupStore::sqlite(f.db.inner.clone(), ENV.to_string());
        let stored = reader
            .try_get(GROUP)
            .await
            .expect("SQL projection")
            .expect("Group");
        assert_eq!(canonical_group(updated), canonical_group(stored));
        f.db.armed.store(false, Ordering::SeqCst);
    }
}

#[tokio::test]
async fn fresh_preflight_version_conflict_invalidates_stale_cache_without_notification() {
    let f = fixture().await;
    // Another store commits while the Core's store still caches version 1.
    let writer = MySqlGroupStore::sqlite(f.db.inner.clone(), ENV.to_string());
    writer
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: GROUP.into(),
            expected_version: 1,
            mutated_at_ms: 1_700_000_000_987,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                context: Some("concurrent writer".into()),
                ..GroupMutableFieldsPatch::default()
            }),
            event: None,
        })
        .await
        .expect("competing commit");
    f.db.arm(false);
    let result = f
        .core
        .mutate(command(GroupMutationKind::PatchMutableFields(patch(false))))
        .await;
    assert!(matches!(
        result,
        Err(bcs_service_api::ServiceError::Conflict(_))
    ));
    assert_eq!(f.db.transactions.load(Ordering::SeqCst), 0);
    assert!(f.changes.ids.lock().expect("notifications").is_empty());
    // The conflict must evict the stale cache, not reject every later retry.
    let updated = f
        .core
        .mutate(command(GroupMutationKind::PatchMutableFields(patch(false))))
        .await
        .expect("retry uses current version");
    assert_eq!(updated.version, 3);
    assert_eq!(updated.context.as_deref(), Some("concurrent writer"));
    assert_eq!(f.db.successful_transactions.load(Ordering::SeqCst), 1);
    assert_eq!(
        *f.changes.ids.lock().expect("notifications"),
        vec![GROUP.to_string()]
    );
    let reader = MySqlGroupStore::sqlite(f.db.inner.clone(), ENV.to_string());
    assert_eq!(
        reader
            .read_card_snapshot(GROUP)
            .await
            .expect("snapshot")
            .expect("Group")
            .catalog_labels,
        labels("running")
    );
}

#[tokio::test]
async fn duplicate_create_conflicts_preserve_everything_and_never_notify() {
    let f = fixture().await;
    let reader = MySqlGroupStore::sqlite(f.db.inner.clone(), ENV.to_string());
    let before = reader.try_get(GROUP).await.unwrap().unwrap();
    let card_before = reader.read_card_snapshot(GROUP).await.unwrap().unwrap();
    let cached_before = f.repo.try_get(GROUP).await.unwrap().unwrap();
    // Both owner-style replay and attempted replacement are create conflicts.
    for replacement_labels in [
        None,
        Some(CatalogLabels::default()),
        Some(labels("running")),
    ] {
        let mut replacement = before.clone();
        if replacement_labels.is_some() {
            replacement.driver_bot = "replacement-driver".into();
            replacement.originator = Some("replacement-originator".into());
            replacement.label = Some("must not replace".into());
            replacement.participants.clear();
        }
        f.db.arm(false);
        let result = f
            .core
            .create_with_catalog_labels(replacement, replacement_labels)
            .await;
        assert!(matches!(
            result,
            Err(bcs_service_api::ServiceError::Conflict(_))
        ));
        assert_eq!(f.db.transactions.load(Ordering::SeqCst), 1);
        assert_eq!(f.db.successful_transactions.load(Ordering::SeqCst), 0);
        assert!(f.changes.ids.lock().unwrap().is_empty());
        let fresh = MySqlGroupStore::sqlite(f.db.inner.clone(), ENV.to_string());
        assert_eq!(
            canonical_group(fresh.try_get(GROUP).await.unwrap().unwrap()),
            canonical_group(before.clone())
        );
        let after = fresh.read_card_snapshot(GROUP).await.unwrap().unwrap();
        assert_eq!(after.catalog_labels, card_before.catalog_labels);
        assert_eq!(after.card_template, card_before.card_template);
        assert_eq!(
            canonical_group(f.repo.try_get(GROUP).await.unwrap().unwrap()),
            canonical_group(cached_before.clone())
        );
    }
}

#[tokio::test]
async fn failed_create_rolls_back_group_members_template_labels_and_notification() {
    let f = fixture().await;
    let mut group = Group::new(
        "failed-create",
        "manager",
        vec![Participant::bot("manager", ParticipantRole::Manager)],
    );
    group.group_strategy = GroupStrategy::ManagerWorker;
    f.db.arm(true);
    let result = f
        .core
        .create_with_catalog_labels(group.clone(), Some(labels("trial")))
        .await;
    assert!(result.is_err());
    assert_eq!(f.db.transactions.load(Ordering::SeqCst), 1);
    assert_eq!(f.db.successful_transactions.load(Ordering::SeqCst), 0);
    assert!(f.changes.ids.lock().unwrap().is_empty());
    let reader = MySqlGroupStore::sqlite(f.db.inner.clone(), ENV.to_string());
    assert!(reader.try_get(&group.id).await.unwrap().is_none());
    for table in [
        "bcs_groups",
        "bcs_group_participants",
        "bcs_group_agent_cards",
    ] {
        let rows =
            f.db.inner
                .query(DbStatement::new(format!(
                    "SELECT group_id FROM {table} WHERE group_id = 'failed-create'"
                )))
                .await
                .unwrap();
        assert!(rows.is_empty(), "rollback must remove rows from {table}");
    }
    assert!(f.repo.try_get(&group.id).await.unwrap().is_none());
    // A real retry must create the identity once, persist labels and notify after commit.
    f.db.arm(false);
    f.core
        .create_with_catalog_labels(group, Some(labels("running")))
        .await
        .unwrap();
    assert_eq!(f.db.successful_transactions.load(Ordering::SeqCst), 1);
    assert_eq!(
        *f.changes.ids.lock().unwrap(),
        vec!["failed-create".to_string()]
    );
    assert_eq!(
        reader
            .read_card_snapshot("failed-create")
            .await
            .unwrap()
            .unwrap()
            .catalog_labels,
        labels("running")
    );
}
