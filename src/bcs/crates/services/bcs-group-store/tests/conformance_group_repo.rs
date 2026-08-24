use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue, db_get_column};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{AixUiOpeningMessage, AixUiOpeningMessageType, AixUiOpeningTab, OpeningMessage};
use bcs_event_store::{DbEventStore, MemoryEventStore};
use bcs_group_store::{GroupBuilder, MemoryGroupRepo, MySqlGroupStore};
use bcs_service_api::port::NewEvent;
use bcs_service_api::port::repo::{
    AppendEventRecord, ClaimFanoutTargets, CommitGroupEventfulMutation,
    CreateEventSubscriptionRecord, EventRepoPort, EventSubscriptionRecord,
    EventSubscriptionRevisionRecord, FinalizeGroupProvisioning, GroupEventfulMutation,
    GroupRepoPort,
};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EventActor, EventActorType, EventPayloadMode, EventScope,
    EventSubject, EventSubscriptionScope, EventSubscriptionScopeType, EventSubscriptionStatus,
};
use bcs_service_api::{
    DefaultDelivery, GroupKind, GroupMutableFieldsPatch, GroupStatus, GroupStrategy, Participant,
    ParticipantRole, RoutingMode, RoutingPolicy, ServiceError,
};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

const PROVISIONING_ENV: &str = "contract";
const PROVISIONING_GROUP: &str = "group-provisioning";
const PROVISIONING_SUBSCRIPTION: &str = "sub-provisioning";
const PROVISIONING_EVENT: &str = "evt-group-provisioning";

#[tokio::test]
async fn memory_group_repo_passes_group_repo_contract() {
    let repo = MemoryGroupRepo::new();

    bcs_test_support::contract::repo::group_repo_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn visibility_guard_rejects_a_protected_bot_without_changing_group_version() {
    let repo = MemoryGroupRepo::new();
    let protected = Participant::bot("protected", ParticipantRole::Consultant);

    let mut public = GroupBuilder::new("driver").id("public-group").build();
    public.visibility = "public".to_string();
    let original_version = public.version;
    repo.upsert(public).await.expect("seed public group");
    let rejected = repo
        .add_participant_with_visibility_guard("public-group", protected, false)
        .await;
    assert!(matches!(
        rejected,
        Err(ServiceError::ExistNonPublicBots { .. })
    ));
    assert_eq!(
        repo.get("public-group")
            .await
            .expect("public group exists")
            .version,
        original_version
    );
}

#[tokio::test]
async fn memory_group_metrics_snapshot_port_contract() {
    let repo = MemoryGroupRepo::new();
    let mut normal = GroupBuilder::new("driver").id("metrics-normal").build();
    normal.group_strategy = GroupStrategy::ManagerWorker;
    normal.service_mode = Some("master_slave".to_string());
    let mut dm = GroupBuilder::new("driver").id("metrics-dm").build();
    dm.group_kind = GroupKind::Dm;
    dm.group_strategy = GroupStrategy::StateMachine;
    dm.status = GroupStatus::Completed;
    dm.service_mode = Some("user-provided-mode".to_string());

    repo.upsert(normal).await.expect("insert normal group");
    repo.upsert(dm).await.expect("insert dm group");

    bcs_test_support::contract::port::group_metrics_snapshot_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn mysql_group_store_sqlite_smoke_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let repo = MySqlGroupStore::new(db, "contract".to_string());

    assert!(repo.get("bcs-contract-missing-group").await.is_none());
}

#[tokio::test]
async fn sqlite_group_opening_message_round_trips_and_can_be_cleared() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let repo = MySqlGroupStore::sqlite(db, "contract".to_string());
    let mut group = GroupBuilder::new("driver").id("opening-group").build();
    group.group_strategy = GroupStrategy::StateMachine;
    group.opening_message = Some(OpeningMessage::Text("Run {{bcs.run_id}}".to_string()));
    repo.upsert(group).await.expect("persist group");

    assert_eq!(
        repo.try_get("opening-group")
            .await
            .expect("load group")
            .expect("group")
            .opening_message,
        Some(OpeningMessage::Text("Run {{bcs.run_id}}".to_string()))
    );

    let structured = OpeningMessage::AixUi(AixUiOpeningMessage {
        message_type: AixUiOpeningMessageType::Panel,
        component: "releasePanel.RunOverview".to_string(),
        params: Some(BTreeMap::from([(
            "runId".to_string(),
            serde_json::Value::String("{{bcs.run_id}}".to_string()),
        )])),
        tab: Some(AixUiOpeningTab {
            id: Some("run-{{bcs.run_id}}".to_string()),
            title: None,
            closable: Some(true),
        }),
    });
    repo.patch_mutable_fields(
        "opening-group",
        GroupMutableFieldsPatch {
            opening_message: Some(Some(structured.clone())),
            ..Default::default()
        },
    )
    .await
    .expect("replace opening message with structured AixUI");
    assert_eq!(
        repo.try_get("opening-group")
            .await
            .expect("reload structured group")
            .expect("group")
            .opening_message,
        Some(structured)
    );

    repo.patch_mutable_fields(
        "opening-group",
        GroupMutableFieldsPatch {
            opening_message: Some(None),
            ..Default::default()
        },
    )
    .await
    .expect("clear opening message");
    assert_eq!(
        repo.try_get("opening-group")
            .await
            .expect("reload group")
            .expect("group")
            .opening_message,
        None
    );
}

#[tokio::test]
async fn sqlite_delivery_patches_persist_canonical_routing_policy_json() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let repo = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let mut group = GroupBuilder::new("driver")
        .id("routing-policy-group")
        .build();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Mention,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: HashMap::from([("driver".to_string(), vec!["observer".to_string()])]),
    });
    repo.upsert(group).await.expect("persist group");

    let concurrent_policy = RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: HashMap::from([("driver".to_string(), vec!["new-observer".to_string()])]),
    };
    let concurrent_policy_json =
        serde_json::to_string(&concurrent_policy).expect("serialize concurrent routing policy");
    db.execute(DbStatement::with_params(
        "UPDATE bcs_groups SET routing_policy_json = ? WHERE env = ? AND group_id = ?",
        vec![
            DbValue::from(concurrent_policy_json),
            DbValue::from(PROVISIONING_ENV),
            DbValue::from("routing-policy-group"),
        ],
    ))
    .await
    .expect("simulate a concurrent routing update behind the repository cache");

    repo.patch_mutable_fields(
        "routing-policy-group",
        GroupMutableFieldsPatch {
            default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
            ..Default::default()
        },
    )
    .await
    .expect("apply direct delivery patch");
    let directly_updated = repo
        .try_get("routing-policy-group")
        .await
        .expect("load directly updated group")
        .expect("group");
    let direct_policy = directly_updated.routing_policy.expect("routing policy");
    assert_eq!(direct_policy.mode, RoutingMode::Structured);
    assert_eq!(
        direct_policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );
    assert_eq!(
        direct_policy.sender_routes.get("driver"),
        Some(&vec!["new-observer".to_string()])
    );

    let transactionally_updated = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "routing-policy-group".to_string(),
            expected_version: directly_updated.version,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                default_bot_final_delivery: Some(DefaultDelivery::SendToDriver),
                ..Default::default()
            }),
            event: None,
        })
        .await
        .expect("apply transactional delivery patch");
    let repeatedly_updated = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "routing-policy-group".to_string(),
            expected_version: transactionally_updated.version,
            mutated_at_ms: 1_787_028_200_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
                ..Default::default()
            }),
            event: None,
        })
        .await
        .expect("apply repeated transactional delivery patch");
    let repeated_policy = repeatedly_updated.routing_policy.expect("routing policy");
    assert_eq!(repeated_policy.mode, RoutingMode::Structured);
    assert_eq!(
        repeated_policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );
    assert_eq!(
        repeated_policy.sender_routes.get("driver"),
        Some(&vec!["new-observer".to_string()])
    );

    let rows = db
        .query(DbStatement::with_params(
            "SELECT routing_policy_json FROM bcs_groups WHERE env = ? AND group_id = ?",
            vec![
                DbValue::from(PROVISIONING_ENV),
                DbValue::from("routing-policy-group"),
            ],
        ))
        .await
        .expect("query stored routing policy");
    let stored_json =
        db_get_column::<String>(&rows[0], "routing_policy_json").expect("routing_policy_json");
    assert!(
        serde_json::from_str::<serde_json::Value>(&stored_json)
            .expect("valid JSON")
            .is_object()
    );
    let stored_policy =
        serde_json::from_str::<RoutingPolicy>(&stored_json).expect("routing policy object");
    assert_eq!(stored_policy.mode, RoutingMode::Structured);
    assert_eq!(
        stored_policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );
    assert_eq!(
        stored_policy.sender_routes.get("driver"),
        Some(&vec!["new-observer".to_string()])
    );
}

#[tokio::test]
async fn sqlite_group_opening_message_rejects_invalid_persisted_json() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let repo = MySqlGroupStore::sqlite(db.clone(), "contract".to_string());
    let group = GroupBuilder::new("driver")
        .id("invalid-opening-group")
        .build();
    repo.upsert(group).await.expect("persist group");
    db.execute(DbStatement::with_params(
        "UPDATE bcs_groups SET opening_message_json = ? WHERE group_id = ? AND env = ?",
        vec![
            DbValue::from("{invalid-json"),
            DbValue::from("invalid-opening-group"),
            DbValue::from("contract"),
        ],
    ))
    .await
    .expect("corrupt persisted opening message");

    let reloaded = MySqlGroupStore::sqlite(db, "contract".to_string());
    let error = reloaded
        .try_get("invalid-opening-group")
        .await
        .expect_err("invalid persisted JSON must not fall back to the default opening message");
    assert!(
        error
            .to_string()
            .contains("deserialize opening_message_json")
    );

    let listed = reloaded.list().await;
    assert!(
        listed.is_empty(),
        "an infallible legacy list must fail the whole read instead of returning a Group with the default opening message"
    );
}

#[tokio::test]
async fn mysql_group_metrics_snapshot_port_sqlite_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_groups ( \
             group_id TEXT PRIMARY KEY, \
             env TEXT NOT NULL, \
             status TEXT NOT NULL, \
             group_kind TEXT, \
             group_strategy TEXT, \
             service_mode TEXT \
         )",
    ))
    .await
    .expect("create groups table");
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_groups (group_id, env, status, group_kind, group_strategy, service_mode) \
         VALUES (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?)",
        vec![
            DbValue::from("metrics-normal"),
            DbValue::from("contract"),
            DbValue::from("active"),
            DbValue::from("normal"),
            DbValue::from("manager_worker"),
            DbValue::from("master_slave"),
            DbValue::from("metrics-dm"),
            DbValue::from("contract"),
            DbValue::from("completed"),
            DbValue::from("dm"),
            DbValue::from("state_machine"),
            DbValue::from("user-provided-mode"),
            DbValue::from("metrics-other-env"),
            DbValue::from("other"),
            DbValue::from("active"),
            DbValue::from("normal"),
            DbValue::from("chat"),
            DbValue::from("master_slave"),
        ],
    ))
    .await
    .expect("seed groups");

    let repo = MySqlGroupStore::new(db, "contract".to_string());
    bcs_test_support::contract::port::group_metrics_snapshot_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn sqlite_group_provisioning_finalization_commits_group_subscription_event_and_target() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let groups = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let events = DbEventStore::sqlite(db.clone());
    seed_provisioning_group(&groups).await;
    events
        .create_subscription(pending_subscription())
        .await
        .expect("create pending Subscription");

    groups
        .finalize_provisioning(finalization(event_record(None)))
        .await
        .expect("commit provisioning finalization");

    assert_eq!(
        groups
            .get(PROVISIONING_GROUP)
            .await
            .expect("Group")
            .record_status,
        "active"
    );
    let (subscription, revision) = events
        .get_subscription(PROVISIONING_SUBSCRIPTION, PROVISIONING_ENV)
        .await
        .expect("load Subscription")
        .expect("Subscription");
    assert_eq!(subscription.status, EventSubscriptionStatus::Active);
    assert_eq!(revision.activated_at_ms, 1_787_028_000_000);
    let event = events
        .get_event(PROVISIONING_EVENT, PROVISIONING_ENV)
        .await
        .expect("load Event")
        .expect("Event");
    assert_eq!(event.envelope.stream.sequence, 1);
    let rows = db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS target_count FROM bcs_event_fanout_targets WHERE env = ? AND event_id = ?",
            vec![
                DbValue::from(PROVISIONING_ENV),
                DbValue::from(PROVISIONING_EVENT),
            ],
        ))
        .await
        .expect("query target snapshot");
    assert_eq!(db_get_column::<i64>(&rows[0], "target_count").unwrap(), 1);
}

#[tokio::test]
async fn sqlite_group_provisioning_finalization_rolls_back_every_component_on_event_failure() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let groups = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let events = DbEventStore::sqlite(db);
    seed_provisioning_group(&groups).await;
    events
        .create_subscription(pending_subscription())
        .await
        .expect("create pending Subscription");

    let error = groups
        .finalize_provisioning(finalization(event_record(Some("missing-cause"))))
        .await
        .expect_err("missing causation Event must roll back the transaction");
    assert!(error.to_string().contains("finalization failed"));
    assert_eq!(
        groups
            .get(PROVISIONING_GROUP)
            .await
            .expect("Group")
            .record_status,
        "provisioning"
    );
    let (subscription, _) = events
        .get_subscription(PROVISIONING_SUBSCRIPTION, PROVISIONING_ENV)
        .await
        .expect("load Subscription")
        .expect("Subscription");
    assert_eq!(subscription.status, EventSubscriptionStatus::Pending);
    assert!(
        events
            .get_event(PROVISIONING_EVENT, PROVISIONING_ENV)
            .await
            .expect("load Event")
            .is_none()
    );
}

#[tokio::test]
async fn sqlite_group_member_add_commits_version_event_and_target_atomically() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let groups = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let events = DbEventStore::sqlite(db.clone());
    seed_active_group(&groups).await;
    events
        .create_subscription(active_subscription("group.participant.added"))
        .await
        .expect("create active Subscription");

    let updated = groups
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: PROVISIONING_GROUP.to_string(),
            expected_version: 1,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::AddParticipant {
                participant: Participant::bot("observer", ParticipantRole::Observer),
                actor_is_public: true,
            },
            event: Some(mutation_event(
                "evt-group-member-added",
                "group.participant.added",
                None,
            )),
        })
        .await
        .expect("commit Group update");

    assert!(
        updated
            .participants
            .iter()
            .any(|p| p.bot_uuid == "observer")
    );
    assert_eq!(updated.version, 2);
    assert!(
        events
            .get_event("evt-group-member-added", PROVISIONING_ENV)
            .await
            .expect("load Event")
            .is_some()
    );
    assert_eq!(target_count(db.as_ref(), "evt-group-member-added").await, 1);
}

#[tokio::test]
async fn sqlite_group_member_add_rolls_back_when_event_append_fails() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let groups = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let events = DbEventStore::sqlite(db);
    seed_active_group(&groups).await;

    groups
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: PROVISIONING_GROUP.to_string(),
            expected_version: 1,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::AddParticipant {
                participant: Participant::bot("observer", ParticipantRole::Observer),
                actor_is_public: true,
            },
            event: Some(mutation_event(
                "evt-group-member-add-failed",
                "group.participant.added",
                Some("missing-cause"),
            )),
        })
        .await
        .expect_err("missing causation Event must fail the unit of work");

    let unchanged = groups.get(PROVISIONING_GROUP).await.expect("Group remains");
    assert!(
        !unchanged
            .participants
            .iter()
            .any(|p| p.bot_uuid == "observer")
    );
    assert_eq!(unchanged.version, 1);
    assert!(
        events
            .get_event("evt-group-member-add-failed", PROVISIONING_ENV)
            .await
            .expect("load Event")
            .is_none()
    );
}

#[tokio::test]
async fn memory_group_member_add_rolls_back_when_event_append_fails() {
    let events = Arc::new(MemoryEventStore::new());
    let groups = MemoryGroupRepo::new().with_event_store(events.clone(), PROVISIONING_ENV);
    let mut group = GroupBuilder::new("driver").id(PROVISIONING_GROUP).build();
    group.label = Some("Active".to_string());
    groups.upsert(group).await.expect("seed active Group");

    groups
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: PROVISIONING_GROUP.to_string(),
            expected_version: 1,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::AddParticipant {
                participant: Participant::bot("observer", ParticipantRole::Observer),
                actor_is_public: true,
            },
            event: Some(mutation_event(
                "evt-memory-member-add-failed",
                "group.participant.added",
                Some("missing-cause"),
            )),
        })
        .await
        .expect_err("missing causation Event must fail the unit of work");

    let unchanged = groups.get(PROVISIONING_GROUP).await.expect("Group remains");
    assert!(
        !unchanged
            .participants
            .iter()
            .any(|p| p.bot_uuid == "observer")
    );
    assert_eq!(unchanged.version, 1);
}

#[tokio::test]
async fn memory_group_deletion_disables_subscription_and_cancels_pending_target() {
    let events = Arc::new(MemoryEventStore::new());
    let groups = MemoryGroupRepo::new().with_event_store(events.clone(), PROVISIONING_ENV);
    seed_memory_active_group(&groups).await;
    events
        .create_subscription(active_subscription("group.participant.added"))
        .await
        .expect("create active Subscription");
    events
        .append_event(mutation_event(
            "evt-memory-before-group-delete",
            "group.participant.added",
            None,
        ))
        .await
        .expect("append Event with pending target");

    groups
        .commit_eventful_mutation(group_deletion())
        .await
        .expect("delete Group and disable Subscription");

    assert!(groups.get(PROVISIONING_GROUP).await.is_none());
    let (subscription, _) = events
        .get_subscription(PROVISIONING_SUBSCRIPTION, PROVISIONING_ENV)
        .await
        .expect("load Subscription")
        .expect("Subscription");
    assert_eq!(subscription.status, EventSubscriptionStatus::Disabled);
    let targets = events
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "after-group-delete".to_string(),
            now_ms: 1_787_028_200_000,
            lease_until_ms: 1_787_028_210_000,
            limit: 10,
            env: PROVISIONING_ENV.to_string(),
        })
        .await
        .expect("claim targets after Group deletion");
    assert!(targets.is_empty());
}

#[tokio::test]
async fn sqlite_group_deletion_disables_subscription_and_cancels_pending_target_atomically() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let groups = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let events = DbEventStore::sqlite(db.clone());
    seed_active_group(&groups).await;
    events
        .create_subscription(active_subscription("group.participant.added"))
        .await
        .expect("create active Subscription");
    events
        .append_event(mutation_event(
            "evt-sqlite-before-group-delete",
            "group.participant.added",
            None,
        ))
        .await
        .expect("append Event with pending target");

    groups
        .commit_eventful_mutation(group_deletion())
        .await
        .expect("delete Group and disable Subscription");

    assert!(groups.get(PROVISIONING_GROUP).await.is_none());
    let (subscription, _) = events
        .get_subscription(PROVISIONING_SUBSCRIPTION, PROVISIONING_ENV)
        .await
        .expect("load Subscription")
        .expect("Subscription");
    assert_eq!(subscription.status, EventSubscriptionStatus::Disabled);
    let rows = db
        .query(DbStatement::with_params(
            "SELECT status FROM bcs_event_fanout_targets WHERE env = ? AND event_id = ?",
            vec![
                DbValue::from(PROVISIONING_ENV),
                DbValue::from("evt-sqlite-before-group-delete"),
            ],
        ))
        .await
        .expect("query cancelled target");
    assert_eq!(
        db_get_column::<String>(&rows[0], "status").expect("target status"),
        "cancelled"
    );
    let audits = db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS audit_count FROM bcs_event_subscription_audits \
             WHERE env = ? AND subscription_id = ? AND action = 'disabled' \
               AND reason = 'scope_deleted'",
            vec![
                DbValue::from(PROVISIONING_ENV),
                DbValue::from(PROVISIONING_SUBSCRIPTION),
            ],
        ))
        .await
        .expect("query automatic disable audit");
    assert_eq!(
        db_get_column::<i64>(&audits[0], "audit_count").expect("audit count"),
        1
    );
}

#[tokio::test]
#[ignore = "requires a MySQL-compatible backend; LocalSqliteDbPlugin does not support ON DUPLICATE KEY used by MySqlGroupStore::upsert"]
async fn mysql_group_store_full_repo_contract_requires_mysql_backend() {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let repo = MySqlGroupStore::new(db, "contract".to_string());

    bcs_test_support::contract::repo::group_repo_port_contract_tests(&repo).await;
}

async fn seed_provisioning_group(groups: &MySqlGroupStore) {
    let mut group = GroupBuilder::new("driver").id(PROVISIONING_GROUP).build();
    group.label = Some("Provisioning".to_string());
    group.record_status = "provisioning".to_string();
    groups.upsert(group).await.expect("seed provisioning Group");
}

async fn seed_active_group(groups: &MySqlGroupStore) {
    let mut group = GroupBuilder::new("driver").id(PROVISIONING_GROUP).build();
    group.label = Some("Active".to_string());
    groups.upsert(group).await.expect("seed active Group");
}

async fn seed_memory_active_group(groups: &MemoryGroupRepo) {
    let mut group = GroupBuilder::new("driver").id(PROVISIONING_GROUP).build();
    group.label = Some("Active".to_string());
    groups.upsert(group).await.expect("seed active Group");
}

fn group_deletion() -> CommitGroupEventfulMutation {
    CommitGroupEventfulMutation {
        group_id: PROVISIONING_GROUP.to_string(),
        expected_version: 1,
        mutated_at_ms: 1_787_028_200_000,
        mutation: GroupEventfulMutation::Delete,
        event: None,
    }
}

fn active_subscription(event_filter: &str) -> CreateEventSubscriptionRecord {
    let mut subscription = pending_subscription();
    subscription.subscription.status = EventSubscriptionStatus::Active;
    subscription.revision.event_filters = vec![event_filter.to_string()];
    subscription.revision.activated_at_ms = 1_787_027_999_000;
    subscription
}

async fn target_count(db: &dyn DbPlugin, event_id: &str) -> i64 {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS target_count FROM bcs_event_fanout_targets \
             WHERE env = ? AND event_id = ?",
            vec![DbValue::from(PROVISIONING_ENV), DbValue::from(event_id)],
        ))
        .await
        .expect("query target count");
    db_get_column(&rows[0], "target_count").expect("target count")
}

fn pending_subscription() -> CreateEventSubscriptionRecord {
    CreateEventSubscriptionRecord {
        subscription: EventSubscriptionRecord {
            subscription_id: PROVISIONING_SUBSCRIPTION.to_string(),
            name: "creation events".to_string(),
            scope: EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: PROVISIONING_GROUP.to_string(),
            },
            status: EventSubscriptionStatus::Pending,
            current_revision: 1,
            created_by: actor(),
            created_at_ms: 1_787_027_999_000,
            updated_at_ms: 1_787_027_999_000,
            deleted_at_ms: None,
            env: PROVISIONING_ENV.to_string(),
        },
        revision: EventSubscriptionRevisionRecord {
            subscription_id: PROVISIONING_SUBSCRIPTION.to_string(),
            revision: 1,
            event_filters: vec!["group.created".to_string()],
            payload_mode: EventPayloadMode::MetadataOnly,
            endpoint_url: "https://events.example.com/group-provisioning".to_string(),
            request_timeout_ms: 5_000,
            activated_at_ms: 0,
            retired_at_ms: None,
        },
        scope_limit: 10,
    }
}

fn finalization(event: AppendEventRecord) -> FinalizeGroupProvisioning {
    FinalizeGroupProvisioning {
        group_id: PROVISIONING_GROUP.to_string(),
        env: PROVISIONING_ENV.to_string(),
        subscription_ids: vec![PROVISIONING_SUBSCRIPTION.to_string()],
        events: vec![event],
        actor: actor(),
        finalized_at_ms: 1_787_028_000_000,
    }
}

fn event_record(causation_event_id: Option<&str>) -> AppendEventRecord {
    AppendEventRecord {
        event: NewEvent {
            event_id: PROVISIONING_EVENT.to_string(),
            event_type: "group.created".to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "group-provisioning-test".to_string(),
            producer_key: "group.created:group-provisioning:v1".to_string(),
            occurred_at: "2026-08-19T00:00:00.000Z".to_string(),
            subject: EventSubject {
                subject_type: "group".to_string(),
                id: PROVISIONING_GROUP.to_string(),
            },
            scope: EventScope {
                group_id: Some(PROVISIONING_GROUP.to_string()),
                ..EventScope::default()
            },
            stream_key: format!("group:{PROVISIONING_GROUP}"),
            actor: Some(actor()),
            correlation_id: None,
            causation_event_id: causation_event_id.map(str::to_string),
            trace_id: None,
            data: BTreeMap::new(),
        },
        recorded_at: "2026-08-19T00:00:00.001Z".to_string(),
        retention_until_ms: 2_000_000_000_000,
        env: PROVISIONING_ENV.to_string(),
    }
}

fn mutation_event(
    event_id: &str,
    event_type: &str,
    causation_event_id: Option<&str>,
) -> AppendEventRecord {
    let mut event = event_record(causation_event_id);
    event.event.event_id = event_id.to_string();
    event.event.event_type = event_type.to_string();
    event.event.producer = "group-mutation-test".to_string();
    event.event.producer_key = format!("{event_type}:{PROVISIONING_GROUP}:v2");
    event
}

fn actor() -> EventActor {
    EventActor {
        actor_type: EventActorType::Human,
        id: "human_owner".to_string(),
        display_name: None,
    }
}
