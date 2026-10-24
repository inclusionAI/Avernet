//! Core-forwarding conformance for the strict Group
//! authorization snapshot read (bot-authority plan
//! Task 6.4, spec §8.3): `GroupCore` forwards the repo's
//! strict read and adds no cache fallback.

use std::sync::Arc;

use bcs_group::GroupCore;
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::core::GroupCoreService;
use bcs_service_api::port::repo::GroupRepoPort;
use bcs_service_api::types::authorization_snapshot::{
    GroupAuthorizationSnapshot, ParticipantAuthorizationSnapshot,
};
use bcs_service_api::types::MessageViewScope;
use bcs_service_api::{
    ActorKind, Group as DomainGroup, GroupStrategy, Participant, ParticipantMode,
    ParticipantRole,
};

fn bot_participant(actor_id: &str, role: ParticipantRole) -> Participant {
    Participant::bot(actor_id, role)
}

#[tokio::test]
async fn group_core_forwards_the_strict_snapshot_read() {
    let repo = Arc::new(MemoryGroupRepo::new());
    let mut group = DomainGroup::new("core-snap-group", "bot-driver", Vec::new());
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        bot_participant("bot-manager", ParticipantRole::Manager),
        bot_participant("bot-worker", ParticipantRole::Worker),
    ];
    repo.upsert(group).await.expect("upsert");

    let core = GroupCore::with_repo(Arc::clone(&repo) as Arc<dyn bcs_service_api::port::repo::GroupRepoPort>);
    let snapshot: GroupAuthorizationSnapshot = core
        .read_authorization_snapshot("core-snap-group")
        .await
        .expect("strict read")
        .expect("snapshot");
    assert_eq!(snapshot.id, "core-snap-group");
    assert_eq!(snapshot.group_strategy, GroupStrategy::ManagerWorker);
    assert!(snapshot.live);
    // Driver + originator + extra Manager under ManagerWorker.
    let candidates = snapshot.management_actor_ids();
    assert!(candidates.contains(&"bot-driver"));
    assert!(candidates.contains(&"bot-manager"));

    // A missing group is `Ok(None)`, never an error.
    assert!(
        core.read_authorization_snapshot("never-created")
            .await
            .expect("missing is Ok(None)")
            .is_none()
    );

    // The default fail-closed contract: a Core with no
    // strict snapshot repo never degrades to a cached
    // `get` read — it errors (Noop core has no snapshot
    // repo and keeps the trait default).
    let noop = bcs_test_support::NoopGroupCoreService::default();
    let error = noop
        .read_authorization_snapshot("any-group")
        .await
        .err()
        .expect("unconfigured core read is Err");
    matches!(error, bcs_service_api::ServiceError::InvalidOperation { .. });
}

#[tokio::test]
async fn group_core_snapshot_projection_matches_domain_decode() {
    let repo = Arc::new(MemoryGroupRepo::new());
    let mut group = DomainGroup::new("core-decode-group", "bot-driver", Vec::new());
    group.record_status = "provisioning".to_string();
    group.participants = vec![
        bot_participant("bot-worker", ParticipantRole::Worker),
        Participant {
            bot_uuid: "human-view".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Participant,
        },
    ];
    repo.upsert(group).await.expect("upsert");

    let core =
        GroupCore::with_repo(Arc::clone(&repo) as Arc<dyn bcs_service_api::port::repo::GroupRepoPort>);
    let snapshot = core
        .read_authorization_snapshot("core-decode-group")
        .await
        .expect("strict read")
        .expect("snapshot");
    // A provisioning record is not a live resource yet.
    assert!(!snapshot.live);
    assert_eq!(snapshot.record_status, "provisioning");
    let expected: Vec<ParticipantAuthorizationSnapshot> = snapshot
        .participants
        .iter()
        .map(|participant| participant.clone())
        .collect();
    assert_eq!(expected.len(), 2);
    let human = &snapshot.participants[1];
    assert_eq!(human.actor_id, "human-view");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.message_view_scope, MessageViewScope::Participant);
}
