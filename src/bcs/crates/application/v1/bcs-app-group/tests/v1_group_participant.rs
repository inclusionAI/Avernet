//! V1 Group participant use-case tests for `bcs_app_group::GroupServiceImpl`.
//!
//! The harness mirrors `tests/v1_group_service.rs`: it wires
//! `GroupServiceImpl` with the in-memory real services (`GroupCore`,
//! `BotCore`, `FriendCore`, `RelationCore`, `SessionManagementServiceImpl`,
//! `GroupManagement`) and seeds a Chat group managed by a Human originator.

use std::sync::Arc;

use bcs_bot::BotCore;
use bcs_friend::FriendCore;
use bcs_group::{GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::v1::{
    AddGroupParticipant, ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity,
    DeleteGroupParticipant, GroupService, ParticipantRole, UpdateGroupParticipant,
};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupCoreService, GroupStrategy, Participant,
    ParticipantMode, SystemMessageService,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

use bcs_app_group::{GroupServiceConfig, GroupServiceImpl};

const GROUP_ID: &str = "group-1";

struct Fixture {
    service: GroupServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
}

impl Fixture {
    async fn new() -> Self {
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let bots = Arc::new(BotCore::memory());
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MemorySessionRepo::new()),
            group_repo,
        ));
        let system_message: Arc<dyn SystemMessageService> = Arc::new(NoopSystemMessageService);
        let management = Arc::new(
            GroupManagement::new(
                groups.clone(),
                bots.clone(),
                friends.clone(),
                relation.clone(),
                GroupConfig::default(),
                sessions.clone(),
                system_message,
            )
            .for_v1_openapi(),
        );
        let service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends,
            relation.clone(),
            sessions,
            management,
            GroupServiceConfig {
                relation_env: "dev".to_string(),
            },
        );
        Self {
            service,
            groups,
            bots,
        }
    }

    async fn add_public_bot(&self, bot_uuid: &str) {
        let capabilities = BotCapabilities {
            name: Some(bot_uuid.to_string()),
            visibility: "public".into(),
            ..Default::default()
        };
        self.bots
            .register(bot_uuid.to_string(), capabilities)
            .await
            .expect("register bot");
    }
}

fn human_caller(staff_no: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: "tenant-a".into(),
        user: Some(AuthenticatedUserIdentity {
            id: staff_no.into(),
            username: staff_no.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn normal_group(
    group_id: &str,
    driver: &str,
    participants: Vec<Participant>,
    strategy: GroupStrategy,
    updated_at: u64,
) -> Group {
    let mut group = Group::new(group_id, driver, participants);
    group.originator = Some(driver.to_string());
    group.label = Some(group_id.to_string());
    group.group_strategy = strategy;
    group.updated_at = updated_at;
    group
}

/// Build a fixture with a Chat group `GROUP_ID` whose Human originator manages
/// the Group. A second Human is a plain participant for self-service tests.
async fn seed() -> Fixture {
    let fixture = Fixture::new().await;
    for bot in ["bot-driver", "bot-a", "bot-b"] {
        fixture.add_public_bot(bot).await;
    }
    for staff_no in ["staff-manager", "staff-member"] {
        fixture
            .bots
            .ensure_human_actor(staff_no, staff_no)
            .await
            .expect("register Human actor");
    }
    let mut group = normal_group(
        GROUP_ID,
        "bot-driver",
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            Participant::bot("bot-a", ParticipantRole::Consultant),
            Participant::human("human_staff-manager", ParticipantRole::Observer),
            Participant::human("human_staff-member", ParticipantRole::Observer),
        ],
        GroupStrategy::Chat,
        1,
    );
    group.originator = Some("human_staff-manager".into());
    fixture
        .groups
        .upsert(group)
        .await
        .expect("store group");
    fixture
}

#[tokio::test]
async fn human_manager_can_add_bot_participant() {
    let fixture = seed().await;
    let caller = human_caller("staff-manager");
    let added = fixture
        .service
        .add_participant(AddGroupParticipant {
            caller,
            group_id: GROUP_ID.into(),
            actor_id: "bot-b".into(),
            role: ParticipantRole::Consultant,
        })
        .await
        .expect("driver can add");
    assert_eq!(added.actor_id, "bot-b");
    assert_eq!(added.role, ParticipantRole::Consultant);
}

#[tokio::test]
async fn non_manager_cannot_add_participant() {
    let fixture = seed().await;
    let caller = human_caller("staff-member");
    let err = fixture
        .service
        .add_participant(AddGroupParticipant {
            caller,
            group_id: GROUP_ID.into(),
            actor_id: "bot-b".into(),
            role: ParticipantRole::Consultant,
        })
        .await
        .expect_err("plain participant forbidden");
    assert!(matches!(err, ApplicationError::Forbidden(_)));
}

#[tokio::test]
async fn update_participant_mode_returns_participant() {
    let fixture = seed().await;
    let caller = human_caller("staff-manager");
    let updated = fixture
        .service
        .update_participant(UpdateGroupParticipant {
            caller,
            group_id: GROUP_ID.into(),
            actor_id: "bot-a".into(),
            mode: ParticipantMode::Muted,
        })
        .await
        .expect("update ok");
    assert_eq!(updated.actor_id, "bot-a");
    assert_eq!(updated.mode, ParticipantMode::Muted);
}

#[tokio::test]
async fn delete_participant_is_idempotent_for_bot() {
    let fixture = seed().await;

    let first = fixture
        .service
        .delete_participant(DeleteGroupParticipant {
            caller: human_caller("staff-manager"),
            group_id: GROUP_ID.into(),
            actor_id: "bot-a".into(),
        })
        .await
        .expect("first delete ok");
    assert!(first.deleted);

    // Re-deleting the same already-removed actor must be idempotent: the V1
    // contract treats a missing participant as success (`deleted: false`),
    // not a 404.
    let second = fixture
        .service
        .delete_participant(DeleteGroupParticipant {
            caller: human_caller("staff-manager"),
            group_id: GROUP_ID.into(),
            actor_id: "bot-a".into(),
        })
        .await
        .expect("second delete is idempotent");
    assert!(!second.deleted);
}

#[tokio::test]
async fn participant_can_update_own_mode() {
    let fixture = seed().await;
    // Design §8.7: a plain (non-manager) participant may update its own mode
    // via self-service. The V1 facade must not gate self-update behind
    // `can_manage_group`; the legacy `update_participant_mode` still allows the
    // caller-as-actor path and enforces `mode.is_valid_for(actor_kind)`.
    let updated = fixture
        .service
        .update_participant(UpdateGroupParticipant {
            caller: human_caller("staff-member"),
            group_id: GROUP_ID.into(),
            actor_id: "human_staff-member".into(),
            mode: ParticipantMode::Absent,
        })
        .await
        .expect("plain participant can update own mode");
    assert_eq!(updated.actor_id, "human_staff-member");
    assert_eq!(updated.mode, ParticipantMode::Absent);
}

#[tokio::test]
async fn participant_can_leave_via_delete() {
    let fixture = seed().await;
    // Design §8.7: a plain (non-manager) participant may leave by deleting
    // itself via self-service. The V1 facade must not gate self-leave behind
    // `can_manage_group`; the legacy `remove_member` permits self-removal for
    // non-driver/non-originator actors and still rejects driver removal.
    let result = fixture
        .service
        .delete_participant(DeleteGroupParticipant {
            caller: human_caller("staff-member"),
            group_id: GROUP_ID.into(),
            actor_id: "human_staff-member".into(),
        })
        .await
        .expect("plain participant can leave via delete");
    assert!(result.deleted);

    // The participant is no longer a member of the group.
    let group = fixture
        .groups
        .try_get(GROUP_ID)
        .await
        .expect("group present")
        .expect("group found");
    assert!(
        !group
            .participants
            .iter()
            .any(|p| p.bot_uuid == "human_staff-member"),
        "the Human participant should have left the group"
    );
}

#[tokio::test]
async fn participant_cannot_update_others() {
    let fixture = seed().await;
    // Self-service is only for self: a plain participant may NOT PATCH a
    // different participant (here the driver). The V1 facade must reject with
    // 403 before reaching the legacy layer.
    let err = fixture
        .service
        .update_participant(UpdateGroupParticipant {
            caller: human_caller("staff-member"),
            group_id: GROUP_ID.into(),
            actor_id: "bot-driver".into(),
            mode: ParticipantMode::Muted,
        })
        .await
        .expect_err("plain participant cannot update another participant");
    assert!(
        matches!(err, ApplicationError::Forbidden(_)),
        "expected forbidden, got {err:?}"
    );
}
