use std::sync::Arc;

use bcs_bot::BotCore;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::port::repo::{GroupRepoPort, SessionRepoPort};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotRegistryCoreService, CreateSessionLaunch, Group,
    GroupCoreService, GroupStrategy, Participant, ParticipantMode, ParticipantRole, SessionCaller,
    SessionKind, SessionLaunchError, SessionLaunchRequest, SessionLaunchService,
};
use bcs_session::{SessionLaunchApplication, SessionManagementServiceImpl};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{NoopCollaborationRuntimeService, NoopSystemMessageService};

struct Fixture {
    service: SessionLaunchApplication,
    bots: Arc<BotCore>,
    groups: Arc<GroupCore>,
}

impl Fixture {
    fn new() -> Self {
        let bots = Arc::new(BotCore::memory());
        let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(session_repo, group_repo));
        let service = SessionLaunchApplication::new(
            bots.clone(),
            groups.clone(),
            sessions,
            Arc::new(NoopCollaborationRuntimeService),
            Arc::new(NoopSystemMessageService),
        );
        Self {
            service,
            bots,
            groups,
        }
    }

    async fn add_bot(&self, bot_id: &str, owner: &str) {
        self.bots
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".into(),
                    ..Default::default()
                },
            )
            .await
            .expect("register bot");
        self.bots
            .save_created_by(bot_id, owner, true)
            .await
            .expect("store owner");
    }

    async fn add_group(&self, group: Group) {
        self.groups.upsert(group).await.expect("store group");
    }
}

fn human(owner_id: &str) -> SessionCaller {
    SessionCaller::Human {
        actor_id: format!("human_{owner_id}"),
        owner_id: owner_id.to_string(),
        display_name: Some(owner_id.to_string()),
    }
}

fn request(
    caller: SessionCaller,
    group_id: &str,
    requested_creator: Option<&str>,
) -> SessionLaunchRequest {
    SessionLaunchRequest {
        caller,
        group_id: group_id.to_string(),
        requested_creator: requested_creator.map(str::to_string),
        title: None,
        kind: Some(SessionKind::Chat),
        input: None,
        meta: None,
        public_creator_role: None,
        context_delivery: None,
    }
}

#[tokio::test]
async fn human_creates_as_owned_bot() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture.add_bot("worker", "alice").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::bot("worker", ParticipantRole::Consultant),
            ],
        ))
        .await;

    let outcome = fixture
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", Some("worker")),
        })
        .await
        .expect("owned Bot may create");

    assert_eq!(outcome.session.created_by.as_deref(), Some("worker"));
    assert_eq!(
        outcome.session.caller_principal.as_deref(),
        Some("human_alice")
    );
}

#[tokio::test]
async fn human_cannot_create_as_unowned_bot() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture.add_bot("worker", "bob").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::bot("worker", ParticipantRole::Consultant),
            ],
        ))
        .await;

    let result = fixture
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", Some("worker")),
        })
        .await;

    assert!(matches!(result, Err(SessionLaunchError::Forbidden(_))));
}

#[tokio::test]
async fn bot_creates_only_as_itself() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture.add_bot("worker", "alice").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::bot("worker", ParticipantRole::Consultant),
            ],
        ))
        .await;

    let result = fixture
        .service
        .create(CreateSessionLaunch {
            request: request(
                SessionCaller::Bot {
                    bot_uuid: "driver".into(),
                },
                "group-1",
                Some("worker"),
            ),
        })
        .await;

    assert!(matches!(result, Err(SessionLaunchError::Forbidden(_))));
}

#[tokio::test]
async fn public_non_member_is_added_with_requested_role() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    let mut group = Group::new(
        "group-public",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.visibility = "public".into();
    fixture.add_group(group).await;

    let mut launch = request(human("bob"), "group-public", None);
    launch.public_creator_role = Some(ParticipantRole::Observer);
    let outcome = fixture
        .service
        .create(CreateSessionLaunch { request: launch })
        .await
        .expect("public non-member may create");

    let participant = outcome
        .session
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_bob")
        .expect("Human inserted");
    assert_eq!(participant.actor_kind, ActorKind::Human);
    assert_eq!(participant.role, ParticipantRole::Observer);
    assert_eq!(participant.mode, Some(ParticipantMode::Present));
}

#[tokio::test]
async fn inferred_private_human_creator_is_not_auto_added() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
        ))
        .await;

    let outcome = fixture
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", None),
        })
        .await
        .expect("Human owner may create");

    assert_eq!(outcome.session.created_by.as_deref(), Some("human_alice"));
    assert!(
        outcome
            .session
            .participants
            .iter()
            .all(|participant| participant.bot_uuid != "human_alice")
    );
}

#[tokio::test]
async fn state_machine_human_is_added_as_present_observer() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    let mut group = Group::new(
        "group-sm",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    fixture.add_group(group).await;

    let outcome = fixture
        .service
        .create(CreateSessionLaunch {
            request: SessionLaunchRequest {
                kind: Some(SessionKind::ServiceInvocation),
                ..request(human("alice"), "group-sm", Some("driver"))
            },
        })
        .await
        .expect("StateMachine session created");

    let participant = outcome
        .session
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("authenticated Human inserted");
    assert_eq!(participant.role, ParticipantRole::Observer);
    assert_eq!(participant.mode, Some(ParticipantMode::Present));
}
