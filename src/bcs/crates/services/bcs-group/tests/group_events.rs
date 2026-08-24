#![allow(clippy::expect_used)]

use std::sync::{Arc, Mutex};

use bcs_event_store::MemoryEventStore;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::core::{GroupMutationCommand, GroupMutationKind};
use bcs_service_api::port::repo::{AppendEventRecord, GroupRepoPort};
use bcs_service_api::port::{EventRecordError, EventRecordFactoryPort, NewEvent};
use bcs_service_api::types::{EventActor, EventActorType};
use bcs_service_api::{
    Group, GroupCoreService, GroupMutableFieldsPatch, Participant, ParticipantMode, ParticipantRole,
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
        tags: Vec::new(),
        mode: Some(ParticipantMode::Present),
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
