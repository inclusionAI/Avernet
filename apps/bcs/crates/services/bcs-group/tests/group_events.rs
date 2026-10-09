#![allow(clippy::expect_used)]

use std::sync::{Arc, Mutex};

use bcs_event_store::MemoryEventStore;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::core::{GroupMutationCommand, GroupMutationKind};
use bcs_service_api::port::repo::{AppendEventRecord, GroupRepoPort};
use bcs_service_api::port::{EventRecordError, EventRecordFactoryPort, NewEvent};
use bcs_service_api::types::{EventActor, EventActorType};
use bcs_service_api::{
    DefaultDelivery, Group, GroupCoreService, GroupMutableFieldsPatch, HumanMentionNotifyMode,
    Participant, ParticipantMode, ParticipantRole, RoutingMode, RoutingPolicy,
};

#[derive(Default)]
struct RecordingEventFactory {
    events: Mutex<Vec<NewEvent>>,
}

impl EventRecordFactoryPort for RecordingEventFactory {
    fn prepare(&self, event: NewEvent) -> Result<Option<AppendEventRecord>, EventRecordError> {
        let mut events = self.events.lock().expect("event factory lock");
        let event_id = format!("evt-group-mutation-{}", events.len() + 1);
        let mut event = event;
        event.event_id = event_id;
        events.push(event.clone());
        Ok(Some(AppendEventRecord {
            event,
            recorded_at: "2026-08-19T01:00:00.000Z".to_string(),
            retention_until_ms: 2_000_000_000_000,
            env: "test".to_string(),
        }))
    }
}

fn actor() -> EventActor {
    EventActor {
        actor_type: EventActorType::Human,
        id: "human_owner".to_string(),
        display_name: Some("Owner".to_string()),
    }
}

fn command(group_id: &str, mutation: GroupMutationKind) -> GroupMutationCommand {
    GroupMutationCommand {
        group_id: group_id.to_string(),
        actor: actor(),
        correlation_id: Some("request-1".to_string()),
        trace_id: None,
        mutation,
    }
}

async fn fixture() -> (GroupCore, Arc<MemoryGroupRepo>, Arc<RecordingEventFactory>) {
    let event_store = Arc::new(MemoryEventStore::new());
    let groups = Arc::new(MemoryGroupRepo::new().with_event_store(event_store, "test"));
    let factory = Arc::new(RecordingEventFactory::default());
    let core = GroupCore::with_repo(groups.clone()).with_event_record_factory(factory.clone());
    let mut group = Group::new(
        "group-1",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.label = Some("Before".to_string());
    groups.upsert(group).await.expect("seed Group");
    (core, groups, factory)
}

#[tokio::test]
async fn group_patch_is_not_part_of_the_public_event_catalog() {
    let (core, groups, factory) = fixture().await;
    let patch = GroupMutableFieldsPatch {
        label: Some("After".to_string()),
        ..GroupMutableFieldsPatch::default()
    };

    let updated = core
        .mutate(command(
            "group-1",
            GroupMutationKind::PatchMutableFields(patch.clone()),
        ))
        .await
        .expect("commit Group update");
    let unchanged = core
        .mutate(command(
            "group-1",
            GroupMutationKind::PatchMutableFields(patch),
        ))
        .await
        .expect("idempotent Group update");

    assert_eq!(updated.version, 2);
    assert_eq!(unchanged.version, 2);
    assert_eq!(
        groups.get("group-1").await.expect("stored Group").version,
        2
    );
    let events = factory.events.lock().expect("event factory lock");
    assert!(events.is_empty());
}

#[tokio::test]
async fn participant_add_and_remove_are_the_only_public_membership_events() {
    let (core, _, factory) = fixture().await;
    let participant = Participant {
        bot_uuid: "human_member".to_string(),
        bot_name: Some("Member".to_string()),
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: bcs_service_api::ActorKind::Human,
        mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
    };

    core.mutate(command(
        "group-1",
        GroupMutationKind::AddParticipant {
            participant,
            actor_is_public: true,
        },
    ))
    .await
    .expect("add participant");
    core.mutate(command(
        "group-1",
        GroupMutationKind::UpdateParticipantMode {
            actor_id: "human_member".to_string(),
            mode: ParticipantMode::Absent,
        },
    ))
    .await
    .expect("update participant");
    core.mutate(command(
        "group-1",
        GroupMutationKind::RemoveParticipant {
            actor_id: "human_member".to_string(),
            reason: "member_removed".to_string(),
        },
    ))
    .await
    .expect("remove participant");

    let events = factory.events.lock().expect("event factory lock");
    assert_eq!(
        events
            .iter()
            .map(|event| event.event_type.as_str())
            .collect::<Vec<_>>(),
        ["group.participant.added", "group.participant.removed"]
    );
    assert_eq!(events[0].data["group_version"], 2);
    assert_eq!(events[1].data["previous_role"], "observer");
    assert_eq!(events[1].data["group_version"], 4);
}

#[tokio::test]
async fn notify_mode_change_commits_and_equal_value_remains_a_noop() {
    let (core, groups, factory) = fixture().await;
    let patch = GroupMutableFieldsPatch {
        human_mention_notify_mode: Some(HumanMentionNotifyMode::DriverBotOnly),
        ..GroupMutableFieldsPatch::default()
    };

    let updated = core
        .mutate(command(
            "group-1",
            GroupMutationKind::PatchMutableFields(patch.clone()),
        ))
        .await
        .expect("commit notify mode change");
    assert_eq!(updated.version, 2);
    assert_eq!(
        updated.human_mention_notify_mode,
        HumanMentionNotifyMode::DriverBotOnly
    );
    assert_eq!(
        groups
            .get("group-1")
            .await
            .expect("stored Group")
            .human_mention_notify_mode,
        HumanMentionNotifyMode::DriverBotOnly
    );
    assert!(
        factory
            .events
            .lock()
            .expect("event factory lock")
            .is_empty()
    );

    let unchanged = core
        .mutate(command(
            "group-1",
            GroupMutationKind::PatchMutableFields(patch),
        ))
        .await
        .expect("idempotent notify mode update");
    assert_eq!(unchanged.version, 2);
    assert_eq!(
        unchanged.human_mention_notify_mode,
        HumanMentionNotifyMode::DriverBotOnly
    );
    assert!(
        factory
            .events
            .lock()
            .expect("event factory lock")
            .is_empty()
    );
}

// Step 2: a `None` notify-mode patch goes through the same eventful mutation
// path as other `GroupMutableFieldsPatch` fields: the returned Group, the
// stored Group, the version bump, and the absence of a public event all
// match the contract that already holds for label patches.
#[tokio::test]
async fn notify_mode_none_change_commits_like_other_mutable_fields() {
    let (core, groups, factory) = fixture().await;

    let label_patch = GroupMutationKind::PatchMutableFields(GroupMutableFieldsPatch {
        label: Some("After".to_string()),
        ..GroupMutableFieldsPatch::default()
    });
    let label_updated = core
        .mutate(command("group-1", label_patch))
        .await
        .expect("commit label patch");
    assert_eq!(label_updated.version, 2);
    assert_eq!(label_updated.label.as_deref(), Some("After"));

    // A label patch and a notify-mode patch must behave identically: both
    // bump the version, persist to the store, and emit no public event.
    let notify_patch = GroupMutationKind::PatchMutableFields(GroupMutableFieldsPatch {
        human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
        ..GroupMutableFieldsPatch::default()
    });
    let notify_updated = core
        .mutate(command("group-1", notify_patch))
        .await
        .expect("commit notify-mode patch");
    assert_eq!(notify_updated.version, 3);
    assert_eq!(
        notify_updated.human_mention_notify_mode,
        HumanMentionNotifyMode::None
    );
    assert_eq!(
        groups
            .get("group-1")
            .await
            .expect("stored Group")
            .human_mention_notify_mode,
        HumanMentionNotifyMode::None
    );
    assert_eq!(
        groups.get("group-1").await.expect("stored Group").version,
        3
    );
    assert!(
        factory
            .events
            .lock()
            .expect("event factory lock")
            .is_empty(),
        "PatchMutableFields never emits public events, regardless of which field changes"
    );
}

// Step 3: a notify-mode patch must not reset unrelated mutable fields. The
// Group is seeded with a non-default label, context, visibility, and routing
// policy; patching only `human_mention_notify_mode` leaves everything else
// byte-identical.
#[tokio::test]
async fn notify_mode_patch_leaves_other_mutable_fields_unchanged() {
    let event_store = Arc::new(MemoryEventStore::new());
    let groups = Arc::new(MemoryGroupRepo::new().with_event_store(event_store, "test"));
    let factory = Arc::new(RecordingEventFactory::default());
    let core = GroupCore::with_repo(groups.clone()).with_event_record_factory(factory.clone());

    let mut group = Group::new(
        "isolation-group",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.label = Some("ops".to_string());
    group.context = Some("incident-room".to_string());
    group.visibility = "public".to_string();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: std::collections::HashMap::from([(
            "driver".to_string(),
            vec!["observer".to_string()],
        )]),
    });
    let original_version = group.version;
    groups.upsert(group).await.expect("seed Group");

    let updated = core
        .mutate(command(
            "isolation-group",
            GroupMutationKind::PatchMutableFields(GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..GroupMutableFieldsPatch::default()
            }),
        ))
        .await
        .expect("commit notify-mode-only patch");
    assert_eq!(updated.version, original_version + 1);
    assert_eq!(updated.human_mention_notify_mode, HumanMentionNotifyMode::None);
    assert_eq!(updated.label.as_deref(), Some("ops"));
    assert_eq!(updated.context.as_deref(), Some("incident-room"));
    assert_eq!(updated.visibility, "public");
    let policy = updated.routing_policy.expect("routing policy preserved");
    assert_eq!(policy.mode, RoutingMode::Structured);
    assert_eq!(
        policy.default_bot_final_delivery,
        DefaultDelivery::SendToDriver
    );
    assert_eq!(
        policy.sender_routes.get("driver"),
        Some(&vec!["observer".to_string()])
    );

    let stored = groups
        .get("isolation-group")
        .await
        .expect("stored Group");
    assert_eq!(stored.version, original_version + 1);
    assert_eq!(stored.human_mention_notify_mode, HumanMentionNotifyMode::None);
    assert_eq!(stored.label.as_deref(), Some("ops"));
    assert_eq!(stored.context.as_deref(), Some("incident-room"));
    assert_eq!(stored.visibility, "public");
    let stored_policy = stored.routing_policy.expect("stored routing policy");
    assert_eq!(stored_policy.mode, RoutingMode::Structured);
    assert_eq!(
        stored_policy.default_bot_final_delivery,
        DefaultDelivery::SendToDriver
    );
    assert_eq!(
        stored_policy.sender_routes.get("driver"),
        Some(&vec!["observer".to_string()])
    );

    assert!(
        factory
            .events
            .lock()
            .expect("event factory lock")
            .is_empty()
    );
}

// Step 3 inverse: a normal-field patch must not reset the notify mode. The
// Group is seeded with a non-default notify mode; patching only `label`
// leaves the mode byte-identical.
#[tokio::test]
async fn other_field_patch_leaves_notify_mode_unchanged() {
    let event_store = Arc::new(MemoryEventStore::new());
    let groups = Arc::new(MemoryGroupRepo::new().with_event_store(event_store, "test"));
    let factory = Arc::new(RecordingEventFactory::default());
    let core = GroupCore::with_repo(groups.clone()).with_event_record_factory(factory.clone());

    let mut group = Group::new(
        "inverse-group",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.human_mention_notify_mode = HumanMentionNotifyMode::DriverBotOnly;
    let original_version = group.version;
    groups.upsert(group).await.expect("seed Group");

    let updated = core
        .mutate(command(
            "inverse-group",
            GroupMutationKind::PatchMutableFields(GroupMutableFieldsPatch {
                label: Some("Renamed".to_string()),
                ..GroupMutableFieldsPatch::default()
            }),
        ))
        .await
        .expect("commit label-only patch");
    assert_eq!(updated.version, original_version + 1);
    assert_eq!(updated.label.as_deref(), Some("Renamed"));
    assert_eq!(
        updated.human_mention_notify_mode,
        HumanMentionNotifyMode::DriverBotOnly,
        "label patch must not touch the notify mode"
    );

    let stored = groups
        .get("inverse-group")
        .await
        .expect("stored Group");
    assert_eq!(stored.label.as_deref(), Some("Renamed"));
    assert_eq!(
        stored.human_mention_notify_mode,
        HumanMentionNotifyMode::DriverBotOnly
    );

    assert!(
        factory
            .events
            .lock()
            .expect("event factory lock")
            .is_empty()
    );
}
