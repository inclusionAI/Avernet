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
    EventSubscriptionRevisionRecord, EventFanoutTargetPurpose, FinalizeGroupProvisioning,
    GroupEventfulMutation, GroupRepoPort,
};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EventActor, EventActorType, EventPayloadMode, EventScope,
    EventSubject, EventSubscriptionScope, EventSubscriptionScopeType, EventSubscriptionStatus,
};
use bcs_service_api::{
    DefaultDelivery, GroupKind, GroupMutableFieldsPatch, GroupStatus, GroupStrategy,
    HumanMentionNotifyMode, Participant, ParticipantRole, RoutingMode, RoutingPolicy,
    ServiceError,
};

use bcs_db_migrations as bootstrap_migrations;


#[path = "conformance_group_repo/configuration.rs"]
mod configuration;

#[path = "conformance_group_repo/atomic_mutations.rs"]
mod atomic_mutations;

const PROVISIONING_ENV: &str = "contract";
const PROVISIONING_GROUP: &str = "group-provisioning";
const PROVISIONING_SUBSCRIPTION: &str = "sub-provisioning";
const PROVISIONING_EVENT: &str = "evt-group-provisioning";
const PROVISIONING_PREWINDOW_CAUSE_EVENT: &str = "evt-provisioning-prewindow-cause";
const PROVISIONING_WINDOW_EFFECT_EVENT: &str = "evt-provisioning-window-effect";
const PROVISIONING_RUN_CREATED_EVENT: &str = "evt-provisioning-run-created";
const PROVISIONING_RUN_STARTED_EVENT: &str = "evt-provisioning-run-started";

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
            event_filters: vec!["group.created".to_string(), "state_machine.*".to_string()],
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

fn provisioning_runtime_event(
    event_id: &str,
    event_type: &str,
    causation_event_id: Option<&str>,
) -> AppendEventRecord {
    AppendEventRecord {
        event: NewEvent {
            event_id: event_id.to_string(),
            event_type: event_type.to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "state-machine-runtime".to_string(),
            producer_key: event_id.to_string(),
            occurred_at: "2026-08-18T04:39:59.500Z".to_string(),
            subject: EventSubject {
                subject_type: "state_machine.run".to_string(),
                id: "run-provisioning".to_string(),
            },
            scope: EventScope {
                group_id: Some(PROVISIONING_GROUP.to_string()),
                session_id: Some("session-provisioning".to_string()),
                run_id: Some("run-provisioning".to_string()),
                ..EventScope::default()
            },
            stream_key: "state-machine-run:run-provisioning".to_string(),
            actor: Some(actor()),
            correlation_id: None,
            causation_event_id: causation_event_id.map(str::to_string),
            trace_id: None,
            data: BTreeMap::new(),
        },
        recorded_at: "2026-08-18T04:39:59.500Z".to_string(),
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
