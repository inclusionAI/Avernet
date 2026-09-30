//! SQLite-backed conformance for the SQL Group store's eventful mutation
//! primitives, participant CRUD, and count/list projections.
//!
//! The MySQL-only evidence lives in the ignored full-chain test; these tests
//! pin the same Store behavior on the SQLite dialect (non-ignored, runs in
//! every CI coverage run): every eventful mutation arm, optimistic-version
//! guards, participant add/update/remove round trips, and the filtered
//! count/list/find projections. Each post-mutation assertion uses a fresh
//! Store over the same database so reads are cold SQL reads, never cache.

use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_db_api::DbPlugin;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{
    GroupMessage, GroupMessageType, MessageRole, MessageViewScope, Participant, ParticipantMode,
    ParticipantRole,
};
use bcs_group_store::{GroupBuilder, MySqlGroupStore};
use bcs_service_api::port::NewEvent;
use bcs_service_api::port::repo::{
    AppendEventRecord, CommitGroupEventfulMutation, GroupEventfulMutation, GroupRepoPort,
};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EventActor, EventActorType, EventScope, EventSubject,
};
use bcs_service_api::{
    DefaultDelivery, Group, GroupKind, GroupMutableFieldsPatch, GroupStatus,
    HumanMentionNotifyMode, RoutingMode, RoutingPolicy, ServiceError, ServiceSpec,
};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

const ENV: &str = "eventful-contract";

fn sqlite_store(db: &Arc<dyn DbPlugin>, env: &str) -> MySqlGroupStore {
    MySqlGroupStore::sqlite(Arc::clone(db), env.to_string())
}

async fn migrated_db() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    db
}

/// Seed a Normal group with one bot driver plus one extra participant.
async fn seeded_group(db: &Arc<dyn DbPlugin>, id: &str, extra: Option<Participant>) -> Group {
    let store = sqlite_store(db, ENV);
    let mut group = GroupBuilder::new("driver").id(id).build();
    group
        .participants
        .push(Participant::bot("driver", ParticipantRole::Driver));
    if let Some(participant) = extra {
        group.participants.push(participant);
    }
    store.upsert(group.clone()).await.expect("seed group");
    group
}

/// Commit one eventful mutation from a fresh Store (writers do not share
/// caches, matching production multi-instance behavior).
async fn commit(
    db: &Arc<dyn DbPlugin>,
    group_id: &str,
    expected_version: i32,
    mutation: GroupEventfulMutation,
) -> Group {
    sqlite_store(db, ENV)
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: group_id.to_string(),
            expected_version,
            mutated_at_ms: 1_787_028_000_000,
            mutation,
            event: None,
        })
        .await
        .expect("eventful mutation commits")
}

fn participant_of<'a>(group: &'a Group, bot_uuid: &str) -> &'a Participant {
    group
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == bot_uuid)
        .unwrap_or_else(|| panic!("participant {bot_uuid} missing"))
}

/// A canonical Event record with the requested environment. Scope is bound to
/// `group_id` so Store-level Event validation (not scope checks) is isolated
/// per test.
fn event_record(env: &str, group_id: &str, event_id: &str) -> AppendEventRecord {
    AppendEventRecord {
        event: NewEvent {
            event_id: event_id.to_string(),
            event_type: "group.updated".to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "eventful-contract-test".to_string(),
            producer_key: format!("{event_id}:v1"),
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
                id: "human_owner".to_string(),
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

// --- Eventful mutation arms -------------------------------------------------

#[tokio::test]
async fn update_status_eventful_mutation_persists_and_bumps_version() {
    let db = migrated_db().await;
    let seeded = seeded_group(&db, "ev-status", None).await;

    let committed = commit(
        &db,
        "ev-status",
        seeded.version,
        GroupEventfulMutation::UpdateStatus(GroupStatus::Completed),
    )
    .await;
    assert_eq!(committed.status, GroupStatus::Completed);
    assert_eq!(committed.version, seeded.version + 1);

    let cold = sqlite_store(&db, ENV)
        .try_get("ev-status")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(cold.status, GroupStatus::Completed);
    assert_eq!(cold.version, seeded.version + 1);
}

#[tokio::test]
async fn remove_participant_eventful_mutation_deletes_membership_and_bumps_version() {
    let db = migrated_db().await;
    let seeded = seeded_group(
        &db,
        "ev-remove",
        Some(Participant::bot("consultant-bot", ParticipantRole::Consultant)),
    )
    .await;
    assert_eq!(seeded.participants.len(), 2);

    let committed = commit(
        &db,
        "ev-remove",
        seeded.version,
        GroupEventfulMutation::RemoveParticipant {
            actor_id: "consultant-bot".to_string(),
        },
    )
    .await;
    assert_eq!(committed.version, seeded.version + 1);
    assert!(committed
        .participants
        .iter()
        .all(|participant| participant.bot_uuid != "consultant-bot"));

    let cold = sqlite_store(&db, ENV)
        .try_get("ev-remove")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(cold.participants.len(), 1);
    assert_eq!(cold.participants[0].bot_uuid, "driver");
}

#[tokio::test]
async fn update_participant_mode_eventful_mutation_persists_mode() {
    let db = migrated_db().await;
    let seeded = seeded_group(&db, "ev-mode", None).await;

    let committed = commit(
        &db,
        "ev-mode",
        seeded.version,
        GroupEventfulMutation::UpdateParticipantMode {
            actor_id: "driver".to_string(),
            mode: ParticipantMode::Muted,
        },
    )
    .await;
    assert_eq!(participant_of(&committed, "driver").effective_mode(), ParticipantMode::Muted);

    let cold = sqlite_store(&db, ENV)
        .try_get("ev-mode")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(
        participant_of(&cold, "driver").effective_mode(),
        ParticipantMode::Muted,
        "mode must survive a cold read from SQL"
    );
}

#[tokio::test]
async fn update_participant_message_view_scope_eventful_persists_scope_and_optional_mode() {
    let db = migrated_db().await;
    let seeded = seeded_group(
        &db,
        "ev-scope",
        Some(Participant::human("human-1", ParticipantRole::Consultant)),
    )
    .await;
    assert_eq!(seeded.version, 1);

    // First commit: scope + explicit mode both land.
    let committed = commit(
        &db,
        "ev-scope",
        seeded.version,
        GroupEventfulMutation::UpdateParticipantMessageViewScope {
            actor_id: "human-1".to_string(),
            message_view_scope: MessageViewScope::Participant,
            mode: Some(ParticipantMode::Present),
        },
    )
    .await;
    let updated = participant_of(&committed, "human-1");
    assert_eq!(updated.message_view_scope, MessageViewScope::Participant);
    assert_eq!(updated.effective_mode(), ParticipantMode::Present);
    assert_eq!(committed.version, seeded.version + 1);

    // Second commit: the no-mode variant must leave the stored mode intact
    // while still narrowing the scope again (Full → Participant already done,
    // so widen back to Full and drop the explicit mode).
    let committed = commit(
        &db,
        "ev-scope",
        committed.version,
        GroupEventfulMutation::UpdateParticipantMessageViewScope {
            actor_id: "human-1".to_string(),
            message_view_scope: MessageViewScope::Full,
            mode: None,
        },
    )
    .await;
    let updated = participant_of(&committed, "human-1");
    assert_eq!(updated.message_view_scope, MessageViewScope::Full);
    assert_eq!(
        updated.effective_mode(),
        ParticipantMode::Present,
        "no-mode variant must not overwrite the stored mode"
    );

    let cold = sqlite_store(&db, ENV)
        .try_get("ev-scope")
        .await
        .expect("cold read")
        .expect("group");
    let reloaded = participant_of(&cold, "human-1");
    assert_eq!(reloaded.message_view_scope, MessageViewScope::Full);
    assert_eq!(reloaded.effective_mode(), ParticipantMode::Present);
}

#[tokio::test]
async fn update_routing_policy_eventful_mutation_persists_policy() {
    let db = migrated_db().await;
    let seeded = seeded_group(&db, "ev-routing", None).await;

    let policy = RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: Default::default(),
    };
    let committed = commit(
        &db,
        "ev-routing",
        seeded.version,
        GroupEventfulMutation::UpdateRoutingPolicy(policy),
    )
    .await;
    assert_eq!(committed.version, seeded.version + 1);

    let cold = sqlite_store(&db, ENV)
        .try_get("ev-routing")
        .await
        .expect("cold read")
        .expect("group");
    let loaded = cold.routing_policy.expect("routing policy persisted");
    assert_eq!(loaded.mode, RoutingMode::Structured);
    assert_eq!(
        loaded.default_bot_final_delivery,
        DefaultDelivery::SendToDriver
    );
}

#[tokio::test]
async fn update_service_spec_eventful_mutation_installs_then_clears() {
    let db = migrated_db().await;
    let seeded = seeded_group(&db, "ev-spec", None).await;

    let spec = ServiceSpec {
        callback_config: None,
        timeout_seconds: Some(30),
        max_concurrency: Some(3),
    };
    let committed = commit(
        &db,
        "ev-spec",
        seeded.version,
        GroupEventfulMutation::UpdateServiceSpec(Some(spec.clone())),
    )
    .await;
    let installed = committed.service_spec.as_ref().expect("spec installed");
    assert_eq!(installed.timeout_seconds, spec.timeout_seconds);
    assert_eq!(installed.max_concurrency, spec.max_concurrency);
    assert_eq!(committed.version, seeded.version + 1);

    let cleared = commit(
        &db,
        "ev-spec",
        committed.version,
        GroupEventfulMutation::UpdateServiceSpec(None),
    )
    .await;
    assert!(cleared.service_spec.is_none());
    assert_eq!(cleared.version, committed.version + 1);

    let cold = sqlite_store(&db, ENV)
        .try_get("ev-spec")
        .await
        .expect("cold read")
        .expect("group");
    assert!(cold.service_spec.is_none());
}

#[tokio::test]
async fn add_participant_eventful_mutation_refuses_non_public_bot_on_public_group() {
    let db = migrated_db().await;
    let store = sqlite_store(&db, ENV);
    let mut public = GroupBuilder::new("driver").id("ev-add-guard").build();
    public.visibility = "public".to_string();
    public
        .participants
        .push(Participant::bot("driver", ParticipantRole::Driver));
    store.upsert(public).await.expect("seed public group");

    let mut visitor = Participant::bot("hidden-bot", ParticipantRole::Consultant);
    visitor.bot_name = Some("Hidden Bot".to_string());
    let rejected = store
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "ev-add-guard".to_string(),
            expected_version: 1,
            mutated_at_ms: 1_787_028_000_000,
            mutation: GroupEventfulMutation::AddParticipant {
                participant: visitor,
                actor_is_public: false,
            },
            event: None,
        })
        .await
        .expect_err("non-public bot must be refused on a public Group");
    assert!(
        matches!(rejected, ServiceError::ExistNonPublicBots { ref bots }
            if bots.iter().any(|(id, name)| id == "hidden-bot" && name.as_deref() == Some("Hidden Bot"))),
        "{rejected}"
    );
}

// --- Eventful guards --------------------------------------------------------

#[tokio::test]
async fn eventful_guards_reject_missing_group_and_empty_or_stale_patches() {
    let db = migrated_db().await;
    let seeded = seeded_group(&db, "ev-guards", None).await;
    let store = sqlite_store(&db, ENV);

    // Unknown Group: even a no-op-looking mutation must be GroupNotFound.
    let missing = store
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "ev-missing".to_string(),
            expected_version: 1,
            mutated_at_ms: 1,
            mutation: GroupEventfulMutation::UpdateStatus(GroupStatus::Closed),
            event: None,
        })
        .await
        .expect_err("missing group");
    assert!(matches!(missing, ServiceError::GroupNotFound(ref id) if *id == "ev-missing"), "{missing}");

    // Stale expected_version must conflict, never bypass optimistic
    // versioning.
    let stale = store
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "ev-guards".to_string(),
            expected_version: seeded.version.checked_add(99).expect("version"),
            mutated_at_ms: 1,
            mutation: GroupEventfulMutation::UpdateStatus(GroupStatus::Closed),
            event: None,
        })
        .await
        .expect_err("stale expected_version");
    assert!(matches!(stale, ServiceError::Conflict(_)), "{stale}");

    // A patch that changes no field is rejected instead of silently bumping
    // the version.
    let empty = store
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "ev-guards".to_string(),
            expected_version: seeded.version,
            mutated_at_ms: 1,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch::default()),
            event: None,
        })
        .await
        .expect_err("empty patch");
    assert!(matches!(empty, ServiceError::Conflict(_)), "{empty}");

    // Nothing above may have mutated the Group.
    let cold = sqlite_store(&db, ENV)
        .try_get("ev-guards")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(cold.version, seeded.version);
    assert_eq!(cold.status, GroupStatus::Active);
}

#[tokio::test]
async fn eventful_event_checks_reject_env_mismatch_and_deletion_events() {
    let db = migrated_db().await;
    let seeded = seeded_group(&db, "ev-event-checks", None).await;
    let store = sqlite_store(&db, ENV);

    // The Event env must match the Store env.
    let cross_env = event_record("other-env", "ev-event-checks", "evt-cross-env");
    let rejected = store
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "ev-event-checks".to_string(),
            expected_version: seeded.version,
            mutated_at_ms: 1_787_028_000_000,
            mutation: GroupEventfulMutation::UpdateStatus(GroupStatus::Completed),
            event: Some(cross_env),
        })
        .await
        .expect_err("cross-env Event must be rejected");
    assert!(matches!(rejected, ServiceError::InvalidOperation { .. }), "{rejected}");

    // Deletion is not part of the public Event Catalog: a Delete mutation can
    // only run with `event: None`.
    let deletion_event = event_record(ENV, "ev-event-checks", "evt-delete");
    let rejected = store
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "ev-event-checks".to_string(),
            expected_version: seeded.version,
            mutated_at_ms: 1_787_028_000_000,
            mutation: GroupEventfulMutation::Delete,
            event: Some(deletion_event),
        })
        .await
        .expect_err("Delete plus an Event must be rejected");
    assert!(matches!(rejected, ServiceError::InvalidOperation { .. }), "{rejected}");

    // Both rejections are non-mutating.
    let cold = sqlite_store(&db, ENV)
        .try_get("ev-event-checks")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(cold.version, seeded.version);
}

// --- Participant CRUD -------------------------------------------------------

#[tokio::test]
async fn participant_add_update_remove_round_trips_through_sql() {
    let db = migrated_db().await;
    let store = sqlite_store(&db, ENV);
    seeded_group(&db, "crud-participants", None).await;

    // Missing Group is refused before any SQL runs.
    let missing = store
        .add_participant(
            "crud-none",
            Participant::bot("bot-b", ParticipantRole::Consultant),
        )
        .await
        .expect_err("missing group");
    assert!(matches!(missing, ServiceError::GroupNotFound(ref id) if *id == "crud-none"), "{missing}");

    // Add with tags + role persists every participant column.
    let mut visitor = Participant::bot("bot-b", ParticipantRole::Consultant);
    visitor.tags = vec!["tenant-a".to_string()];
    store
        .add_participant("crud-participants", visitor)
        .await
        .expect("add participant");

    // Re-adding the same participant is an idempotent no-op, leaving exactly
    // one membership row behind.
    store
        .add_participant(
            "crud-participants",
            Participant::bot("bot-b", ParticipantRole::Consultant),
        )
        .await
        .expect("duplicate add is idempotent");

    let loaded = sqlite_store(&db, ENV)
        .try_get("crud-participants")
        .await
        .expect("cold read")
        .expect("group");
    let membership = participant_of(&loaded, "bot-b");
    assert_eq!(membership.role, ParticipantRole::Consultant);
    assert_eq!(membership.tags, vec!["tenant-a".to_string()]);
    assert_eq!(
        loaded
            .participants
            .iter()
            .filter(|participant| participant.bot_uuid == "bot-b")
            .count(),
        1,
        "duplicate add must not create a second membership"
    );

    // Mode change persists; an unchanged request is an early no-op Ok.
    store
        .update_participant_mode("crud-participants", "bot-b", ParticipantMode::Muted)
        .await
        .expect("mute participant");
    store
        .update_participant_mode("crud-participants", "bot-b", ParticipantMode::Muted)
        .await
        .expect("no-op mode update is Ok");
    // Unknown participant and unknown Group surfaces typed errors.
    let unknown_bot = store
        .update_participant_mode("crud-participants", "ghost", ParticipantMode::Auto)
        .await
        .expect_err("unknown participant");
    assert!(matches!(unknown_bot, ServiceError::BotNotFound(ref id) if *id == "ghost"), "{unknown_bot}");
    let unknown_group = store
        .update_participant_mode("crud-none", "bot-b", ParticipantMode::Auto)
        .await
        .expect_err("unknown group");
    assert!(matches!(unknown_group, ServiceError::GroupNotFound(ref id) if *id == "crud-none"), "{unknown_group}");
    let cold = sqlite_store(&db, ENV)
        .try_get("crud-participants")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(
        participant_of(&cold, "bot-b").effective_mode(),
        ParticipantMode::Muted
    );

    // Removal persists, then a second removal reports the membership gone.
    store
        .remove_participant("crud-participants", "bot-b")
        .await
        .expect("remove participant");
    let again = store
        .remove_participant("crud-participants", "bot-b")
        .await
        .expect_err("second removal");
    assert!(
        matches!(again, ServiceError::ParticipantNotFound(ref id) if *id == "bot-b"),
        "{again}"
    );
    let missing_on_remove = store
        .remove_participant("crud-none", "bot-b")
        .await
        .expect_err("removal on unknown group");
    assert!(
        matches!(missing_on_remove, ServiceError::GroupNotFound(ref id) if *id == "crud-none"),
        "{missing_on_remove}"
    );
    let cold = sqlite_store(&db, ENV)
        .try_get("crud-participants")
        .await
        .expect("cold read")
        .expect("group");
    assert!(cold
        .participants
        .iter()
        .all(|participant| participant.bot_uuid != "bot-b"));
}

#[tokio::test]
async fn message_view_scope_updates_enforce_bot_full_and_persist_human_changes() {
    let db = migrated_db().await;
    let store = sqlite_store(&db, ENV);
    seeded_group(
        &db,
        "crud-scope",
        Some(Participant::human("human-1", ParticipantRole::Consultant)),
    )
    .await;

    // Bots must always keep the full view: narrowing is rejected in-memory.
    let narrowed_bot = store
        .update_participant_message_view_scope(
            "crud-scope",
            "driver",
            MessageViewScope::Participant,
        )
        .await
        .expect_err("bot scope must stay full");
    assert!(matches!(narrowed_bot, ServiceError::InvalidOperation { .. }), "{narrowed_bot}");

    // A no-op human update (Full → Full) is an early Ok.
    store
        .update_participant_message_view_scope("crud-scope", "human-1", MessageViewScope::Full)
        .await
        .expect("no-op scope update");

    // Narrowing a Human participant persists.
    store
        .update_participant_message_view_scope(
            "crud-scope",
            "human-1",
            MessageViewScope::Participant,
        )
        .await
        .expect("narrow human scope");
    // Unknown actor / unknown group errors on the same primitive.
    let unknown = store
        .update_participant_message_view_scope("crud-scope", "ghost", MessageViewScope::Participant)
        .await
        .expect_err("unknown participant");
    assert!(matches!(unknown, ServiceError::ParticipantNotFound(ref id) if *id == "ghost"), "{unknown}");
    let missing = store
        .update_participant_message_view_scope("crud-none", "human-1", MessageViewScope::Full)
        .await
        .expect_err("unknown group");
    assert!(matches!(missing, ServiceError::GroupNotFound(ref id) if *id == "crud-none"), "{missing}");

    let cold = sqlite_store(&db, ENV)
        .try_get("crud-scope")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(
        participant_of(&cold, "human-1").message_view_scope,
        MessageViewScope::Participant
    );
    assert_eq!(
        participant_of(&cold, "driver").message_view_scope,
        MessageViewScope::Full,
        "the rejected bot narrowing must not have been persisted"
    );
}

// --- Mutable-field updates via direct primitives ------------------------------

#[tokio::test]
async fn label_status_visibility_and_service_spec_updates_round_trip() {
    let db = migrated_db().await;
    let store = sqlite_store(&db, ENV);
    let seeded = seeded_group(&db, "crud-fields", None).await;

    // Every primitive refuses unknown Groups before touching SQL.
    let error = store
        .update_label("crud-none", Some("x".to_string()))
        .await
        .expect_err("label");
    assert!(matches!(error, ServiceError::GroupNotFound(ref g) if *g == "crud-none"), "{error}");
    let error = store
        .update_status("crud-none", GroupStatus::Closed)
        .await
        .expect_err("status");
    assert!(matches!(error, ServiceError::GroupNotFound(ref g) if *g == "crud-none"), "{error}");
    let error = store
        .update_visibility("crud-none", "private")
        .await
        .expect_err("visibility");
    assert!(matches!(error, ServiceError::GroupNotFound(ref g) if *g == "crud-none"), "{error}");
    let error = store
        .update_service_spec("crud-none", Some(ServiceSpec { callback_config: None, timeout_seconds: Some(1), max_concurrency: None }))
        .await
        .expect_err("service_spec");
    assert!(matches!(error, ServiceError::GroupNotFound(ref g) if *g == "crud-none"), "{error}");

    store
        .update_label("crud-fields", Some("renamed".to_string()))
        .await
        .expect("set label");
    store
        .update_status("crud-fields", GroupStatus::Completed)
        .await
        .expect("set status");
    store
        .update_visibility("crud-fields", "private")
        .await
        .expect("set visibility");
    let spec = ServiceSpec {
        callback_config: None,
        timeout_seconds: Some(15),
        max_concurrency: Some(2),
    };
    store
        .update_service_spec("crud-fields", Some(spec))
        .await
        .expect("set service spec");
    store
        .update_service_spec("crud-fields", None)
        .await
        .expect("clear service spec");

    // Patches of the remaining mutable arms (context, visibility) also land.
    store
        .patch_mutable_fields(
            "crud-fields",
            GroupMutableFieldsPatch {
                context: Some("fresh context".to_string()),
                visibility: Some("public".to_string()),
                human_mention_notify_mode: Some(HumanMentionNotifyMode::DriverBotOnly),
                ..Default::default()
            },
        )
        .await
        .expect("patch context/visibility");

    let cold = sqlite_store(&db, ENV)
        .try_get("crud-fields")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(cold.label.as_deref(), Some("renamed"));
    assert_eq!(cold.status, GroupStatus::Completed);
    assert_eq!(cold.visibility, "public", "the later patch wins");
    assert!(cold.service_spec.is_none());
    assert_eq!(cold.context.as_deref(), Some("fresh context"));
    assert_eq!(cold.human_mention_notify_mode, HumanMentionNotifyMode::DriverBotOnly);
    // Only the field-level primitives ran; the eventful version counter is
    // untouched by patch_mutable_fields.
    assert_eq!(cold.version, seeded.version);
}

// --- Count / list / find projections ------------------------------------------

#[tokio::test]
async fn count_and_filtered_list_projections_match_seeded_rows() {
    let db = migrated_db().await;
    let store = sqlite_store(&db, ENV);

    let mut normal = GroupBuilder::new("driver").id("proj-normal").label("Ops Alpha").build();
    normal
        .participants
        .push(Participant::bot("driver", ParticipantRole::Driver));
    normal
        .participants
        .push(Participant::bot("bot-b", ParticipantRole::Consultant));
    store.upsert(normal).await.expect("seed normal");

    let mut dm = GroupBuilder::new("driver").id("proj-dm").build();
    dm.group_kind = GroupKind::Dm;
    dm.dm_pair_key = Some("proj-dm-pair".to_string());
    dm.visibility = "private".to_string();
    dm.participants
        .push(Participant::bot("driver", ParticipantRole::Driver));
    dm.participants
        .push(Participant::bot("bot-b", ParticipantRole::Consultant));
    store.upsert(dm).await.expect("seed dm");

    // count_by_kind: the unfiltered and per-kind variants both answer from SQL.
    assert_eq!(store.count().await, 2);
    assert_eq!(store.count_by_kind(None).await, 2);
    assert_eq!(store.count_by_kind(Some(GroupKind::Normal)).await, 1);
    assert_eq!(store.count_by_kind(Some(GroupKind::Dm)).await, 1);
    assert_eq!(store.count_by_participant("bot-b").await, 2);
    assert_eq!(store.count_by_participant("ghost").await, 0);

    // list_paginated_by_kind honors the kind filter and pagination.
    assert_eq!(store.list_paginated_by_kind(Some(GroupKind::Dm), 0, 10).await.len(), 1);
    assert_eq!(store.list_paginated_by_kind(None, 0, 1).await.len(), 1);
    assert_eq!(
        store
            .list_paginated_by_kind(Some(GroupKind::Normal), 1, 10)
            .await
            .len(),
        0,
        "pagination past the filtered result set is empty"
    );

    // count_filtered / list_paginated_filtered compose kind, visibility, and
    // case-insensitive label substring filters.
    assert_eq!(store.count_filtered(None, None, None).await, 2);
    assert_eq!(store.count_filtered(Some(GroupKind::Dm), None, None).await, 1);
    assert_eq!(store.count_filtered(None, Some("private"), None).await, 2);
    assert_eq!(store.count_filtered(None, Some("public"), None).await, 0);
    assert_eq!(store.count_filtered(None, None, Some("ops")).await, 1);
    assert_eq!(store.count_filtered(None, None, Some("OPS ALPHA")).await, 1);
    assert_eq!(store.count_filtered(None, Some("private"), Some("ops")).await, 1);
    assert_eq!(store.count_filtered(Some(GroupKind::Dm), None, Some("ops")).await, 0);
    let filtered = store
        .list_paginated_filtered(0, 10, Some(GroupKind::Dm), None, None)
        .await;
    assert_eq!(filtered.len(), 1);
    assert_eq!(filtered[0].id, "proj-dm");

    // find_by_participant family: shared membership across two Groups, with
    // filtered and paginated variants answering the same seeded rows.
    let shared = store.find_by_participant("bot-b").await;
    assert_eq!(shared.len(), 2);
    let try_shared = store.try_find_by_participant("bot-b").await.expect("try find");
    assert_eq!(try_shared.len(), 2);
    assert!(store.find_by_participant("ghost").await.is_empty());
    assert_eq!(store.find_by_participant_paginated("bot-b", 0, 1).await.len(), 1);
    assert_eq!(store.find_by_participant_paginated("bot-b", 1, 1).await.len(), 1);
    assert!(store
        .find_by_participant_paginated("bot-b", 2, 1)
        .await
        .is_empty());
    let only_normal = store
        .find_by_participant_filtered("bot-b", Some(GroupKind::Normal), None)
        .await;
    assert_eq!(only_normal.len(), 1);
    assert_eq!(only_normal[0].id, "proj-normal");
    assert_eq!(
        store
            .find_by_participant_filtered("bot-b", None, Some("alpha"))
            .await
            .len(),
        1
    );
}

#[tokio::test]
async fn message_counters_round_trip() {
    let db = migrated_db().await;
    let store = sqlite_store(&db, ENV);
    seeded_group(&db, "crud-messages", None).await;

    assert_eq!(store.message_count("crud-messages").await.expect("count"), 0);
    store
        .add_message(
            "crud-messages",
            GroupMessage {
                id: "msg-1".to_string(),
                timestamp: 1_787_028_000_000,
                sender: "driver".to_string(),
                content: "hello".to_string(),
                message_type: GroupMessageType::System,
                bot_name: None,
                role: MessageRole::User,
                run_id: String::new(),
                history_meta: None,
                metadata: None,
                attachments: None,
            },
        )
        .await
        .expect("add message is a no-op success on the SQL store");
    store
        .increment_message_count("crud-messages")
        .await
        .expect("increment");
    store
        .increment_message_count("crud-messages")
        .await
        .expect("increment");
    assert_eq!(store.message_count("crud-messages").await.expect("count"), 2);
    store
        .reset_message_count("crud-messages")
        .await
        .expect("reset");
    assert_eq!(store.message_count("crud-messages").await.expect("count"), 0);
}
