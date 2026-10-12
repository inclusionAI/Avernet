//! Human sponsorship at the Group management layer (plan Task 10,
//! spec §8.3).
//!
//! Every asserted variable comes from a REAL `GroupManagementService`
//! result: a verified Human origins a group driving their owned Bot with a
//! MANAGED (not owned, not befriended) Bot as participant; a Bot originator
//! never accepts the Human sponsorship credential; sponsorship never
//! creates friend edges; add-member passes the group-management
//! authorization BEFORE target sponsorship; qualification is re-verified
//! LIVE against the authority hook, never trusted as a passthrough string;
//! and the Workbench chat-send authorization obtains owner parity for
//! managed Bots.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_friend::FriendCore;
use bcs_group::{GroupConfig, GroupManagement, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::v1::BotAuthorityHook;
use bcs_service_api::workbench_use_cases::{
    WorkbenchChatAuthorizationCommand, WorkbenchSessionService, WorkbenchUseCaseError,
};
use bcs_service_api::{
    ActorStatus, BotCapabilities, BotRepoPort, FriendCoreService, Group, GroupAddMemberCommand,
    GroupCreateCommand, GroupCoreService, GroupCreateParticipantCommand, GroupManagementService,
    HumanSponsorship, Participant, ParticipantRole, SessionManagementService,
    SystemMessageService,
};
use bcs_test_support::NoopSystemMessageService;

const STAFF_A: &str = "staff-a";
const STAFF_B: &str = "staff-b";
const BOT_X: &str = "bot-x"; // owned by staff-a
const BOT_Y: &str = "bot-y"; // managed by staff-a, owned by staff-b

/// Recording authority double: each can_manage question is recorded and
/// answered from the configured verdict table, so sponsorship decisions
/// provably resolve through the hook and can be flipped LIVE.
#[derive(Default)]
struct RecordingAuthorityHook {
    questions: Mutex<Vec<(String, String)>>,
    verdicts: Mutex<BTreeMap<(String, String), bool>>,
    default: bool,
}

impl RecordingAuthorityHook {
    fn new(default: bool) -> Self {
        Self {
            default,
            ..Self::default()
        }
    }

    fn grant(&self, user_id: &str, bot_id: &str) {
        self.verdicts
            .lock()
            .unwrap()
            .insert((user_id.to_string(), bot_id.to_string()), true);
    }

    fn revoke(&self, user_id: &str, bot_id: &str) {
        self.verdicts
            .lock()
            .unwrap()
            .insert((user_id.to_string(), bot_id.to_string()), false);
    }

    fn asked_about(&self, user_id: &str, bot_id: &str) -> bool {
        self.questions
            .lock()
            .unwrap()
            .iter()
            .any(|(user, bot)| user == user_id && bot == bot_id)
    }
}

#[async_trait]
impl BotAuthorityHook for RecordingAuthorityHook {
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> bcs_service_api::ServiceResult<bool> {
        self.questions
            .lock()
            .unwrap()
            .push((user_id.to_string(), bot_id.to_string()));
        Ok(self
            .verdicts
            .lock()
            .unwrap()
            .get(&(user_id.to_string(), bot_id.to_string()))
            .copied()
            .unwrap_or(self.default))
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> bcs_service_api::ServiceResult<()> {
        Err(bcs_service_api::ServiceError::Forbidden(format!(
            "user '{user_id}' is not the owner of bot '{bot_id}'"
        )))
    }
}

struct Fixture {
    management: GroupManagement,
    groups: Arc<bcs_group::GroupCore>,
    bot_repo: Arc<MemoryBotRepo>,
    friends: Arc<FriendCore>,
    hook: Arc<RecordingAuthorityHook>,
    /// Keeps the tempdir backing `bot_repo` alive for the whole fixture.
    _temp: tempfile::TempDir,
}

impl Fixture {
    async fn new() -> Self {
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(bcs_group::GroupCore::with_repo(group_repo.clone()));
        let temp = tempfile::tempdir().expect("temp bot dir");
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
        let registry: Arc<dyn bcs_service_api::BotRegistryCoreService> =
            Arc::new(BotCore::with_repo(bot_repo.clone()));
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let hook = Arc::new(RecordingAuthorityHook::new(false));
        let sessions: Arc<dyn SessionManagementService> = Arc::new(
            bcs_session::SessionManagementServiceImpl::new(
                Arc::new(bcs_session_store::MemorySessionRepo::new()),
                group_repo.clone(),
            ),
        );
        let system_message: Arc<dyn SystemMessageService> = Arc::new(NoopSystemMessageService);
        let management = GroupManagement::new(
            groups.clone(),
            registry,
            friends.clone(),
            relation,
            GroupConfig::default(),
            sessions,
            system_message,
        )
        .for_v1_openapi()
        .with_authority(hook.clone());
        Self {
            management,
            groups,
            bot_repo,
            friends,
            hook,
            _temp: temp,
        }
    }

    async fn register_protected_bot(&self, bot_id: &str, created_by: &str) {
        self.bot_repo
            .register_with_owner_and_token(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "protected".into(),
                    ..Default::default()
                },
                created_by,
                &format!("token-{bot_id}"),
            )
            .await
            .expect("register bot");
    }

    /// The canonical scenario: staff-a owns bot-x and MANAGES bot-y;
    /// bot-y is owned by staff-b and shares no friendship with bot-x.
    async fn seed_owned_and_managed(&self) {
        self.register_protected_bot(BOT_X, STAFF_A).await;
        self.register_protected_bot(BOT_Y, STAFF_B).await;
        // The driver AND the participant both resolve their Human
        // sponsorship through the live hook (recording double stands in for
        // the seeded authority edges): staff-a owns x and manages y.
        self.hook.grant(STAFF_A, BOT_X);
        self.hook.grant(STAFF_A, BOT_Y);
    }

    fn human_group_command(&self, visibility: &str) -> GroupCreateCommand {
        GroupCreateCommand {
            create_initial_session: false,
            group_id: Some("sponsored-group".to_string()),
            caller_actor_id: Some(format!("human_{STAFF_A}")),
            driver_bot_id: BOT_X.to_string(),
            label: None,
            topic: None,
            context: None,
            opening_message: None,
            routing_policy: None,
            participants: vec![GroupCreateParticipantCommand {
                bot_id: BOT_Y.to_string(),
                role: Some("consultant".to_string()),
                tags: Vec::new(),
                message_view_scope: None,
            }],
            member_bot_ids: Vec::new(),
            group_kind: None,
            service_spec: None,
            group_strategy: None,
            originator: Some(format!("human_{STAFF_A}")),
            visibility: Some(visibility.to_string()),
            provisioning: false,
            human_sponsorship: Some(HumanSponsorship {
                user_id: STAFF_A.to_string(),
            }),
        }
    }

    async fn friend_snapshot(&self) -> Vec<String> {
        let mut snapshot = Vec::new();
        for bot in [BOT_X, BOT_Y, "bot-w"] {
            let mut friends = self.friends.list_friends(bot).await;
            friends.sort();
            for friend in friends {
                snapshot.push(format!("{bot}↔{friend}"));
            }
        }
        snapshot.sort();
        snapshot
    }
}

#[tokio::test]
async fn human_sponsorship_creates_private_group_without_friendship() {
    let fixture = Fixture::new().await;
    fixture.seed_owned_and_managed().await;

    let friend_edges_before = fixture.friend_snapshot().await;
    let result = fixture
        .management
        .create_group(fixture.human_group_command("private"))
        .await;
    let human_private_group_result = result.map(|_| ());
    assert!(human_private_group_result.is_ok());
    let friend_edges_after = fixture.friend_snapshot().await;
    assert_eq!(friend_edges_after, friend_edges_before);
    assert!(
        fixture.hook.asked_about(STAFF_A, BOT_Y),
        "the sponsorship must be re-verified live through the authority hook"
    );
}

#[tokio::test]
async fn human_sponsorship_cannot_create_a_public_group_out_of_protected_bots() {
    let fixture = Fixture::new().await;
    fixture.seed_owned_and_managed().await;

    let human_public_group_result = fixture
        .management
        .create_group(fixture.human_group_command("public"))
        .await;
    assert!(human_public_group_result.is_err());
}

#[tokio::test]
async fn bot_originator_never_accepts_a_human_sponsorship_credential() {
    let fixture = Fixture::new().await;
    fixture.seed_owned_and_managed().await;

    let mut command = fixture.human_group_command("private");
    command.originator = Some(BOT_X.to_string());
    command.driver_bot_id = BOT_X.to_string();
    command.human_sponsorship = Some(HumanSponsorship {
        user_id: STAFF_A.to_string(),
    });

    let bot_originated_same_input_result = fixture.management.create_group(command).await;
    assert!(bot_originated_same_input_result.is_err());

    // And without the credential, the SAME bot-originated input is still
    // rejected through the ordinary friendship lane — for a different
    // reason (public/friendship), never inherited Human sponsorship.
    let mut command = fixture.human_group_command("private");
    command.originator = Some(BOT_X.to_string());
    command.driver_bot_id = BOT_X.to_string();
    command.human_sponsorship = None;
    let bot_originated_without_credential = fixture.management.create_group(command).await;
    assert!(bot_originated_without_credential.is_err());
}

#[tokio::test]
async fn sponsorship_qualification_is_reread_live_not_cumulative_strings() {
    let fixture = Fixture::new().await;
    fixture.seed_owned_and_managed().await;

    fixture
        .management
        .create_group(fixture.human_group_command("private"))
        .await
        .expect("live manager grant admits the sponsored creation");

    // Revoke the manager edge: the SAME command must now fail — the
    // credential carries no authority of its own.
    fixture.hook.revoke(STAFF_A, BOT_Y);
    let revoked = fixture
        .management
        .create_group(fixture.human_group_command("private"))
        .await
        .expect_err("revoked live qualification must reject the same input");
    let message = revoked.to_string();
    assert!(
        message.to_lowercase().contains("sponsor"),
        "the rejection must name the sponsorship stage, got {message}"
    );
}

#[tokio::test]
async fn add_member_checks_group_management_authorization_before_sponsorship() {
    let fixture = Fixture::new().await;
    fixture.seed_owned_and_managed().await;
    fixture
        .management
        .create_group(fixture.human_group_command("private"))
        .await
        .expect("sponsored private creation");

    // Positive: the coordinator (driver caller + verified human context)
    // can add MANAGED bot-w — friendship is NOT required.
    fixture.hook.grant(STAFF_A, "bot-w");
    fixture
        .register_protected_bot("bot-w", STAFF_B)
        .await;
    fixture
        .management
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some(BOT_X.to_string()),
            human_actor_id: Some(format!("human_{STAFF_A}")),
            group_id: "sponsored-group".to_string(),
            bot_id: "bot-w".to_string(),
            message_view_scope: None,
            human_sponsorship: Some(HumanSponsorship {
                user_id: STAFF_A.to_string(),
            }),
        })
        .await
        .expect("coordinator + valid sponsorship adds the managed bot");

    // Unsponsored target: authorization holds, sponsor(target) rejects.
    fixture
        .register_protected_bot("bot-unrelated", STAFF_B)
        .await;
    let err = fixture
        .management
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some(BOT_X.to_string()),
            human_actor_id: Some(format!("human_{STAFF_A}")),
            group_id: "sponsored-group".to_string(),
            bot_id: "bot-unrelated".to_string(),
            message_view_scope: None,
            human_sponsorship: Some(HumanSponsorship {
                user_id: STAFF_A.to_string(),
            }),
        })
        .await
        .expect_err("unsponsored target must be rejected");
    match &err {
        bcs_service_api::GroupUseCaseError::Forbidden(message) => {
            assert!(
                message.to_lowercase().contains("sponsor"),
                "the failing stage must be the target sponsorship, got {message}"
            );
        }
        other => panic!("expected sponsorship Forbidden, got {other:?}"),
    }

    // Authorization-first pin: a caller with NO group-management rights
    // whose sponsorship WOULD hold still fails at the coordinator gate —
    // the group-management authorization runs BEFORE target sponsorship.
    let err = fixture
        .management
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("human_staff-d".to_string()),
            human_actor_id: Some("human_staff-d".to_string()),
            group_id: "sponsored-group".to_string(),
            bot_id: BOT_Y.to_string(),
            message_view_scope: None,
            human_sponsorship: Some(HumanSponsorship {
                user_id: STAFF_A.to_string(),
            }),
        })
        .await
        .expect_err("the group-management gate must fire before sponsorship");
    match &err {
        bcs_service_api::GroupUseCaseError::Forbidden(message) => {
            assert!(
                !message.to_lowercase().contains("sponsor"),
                "authorization failures must not masquerade as sponsorship failures: {message}"
            );
        }
        other => panic!("expected authorization Forbidden, got {other:?}"),
    }

    // Hidden refusal: authorized caller, sponsorship that would hold, the
    // hidden target is still refused.
    fixture
        .register_protected_bot("bot-hidden", STAFF_B)
        .await;
    fixture.hook.grant(STAFF_A, "bot-hidden");
    fixture
        .bot_repo
        .update_actor_status("bot-hidden", ActorStatus::Hidden)
        .await
        .expect("hide the bot");
    let err = fixture
        .management
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some(BOT_X.to_string()),
            human_actor_id: Some(format!("human_{STAFF_A}")),
            group_id: "sponsored-group".to_string(),
            bot_id: "bot-hidden".to_string(),
            message_view_scope: None,
            human_sponsorship: Some(HumanSponsorship {
                user_id: STAFF_A.to_string(),
            }),
        })
        .await
        .expect_err("hidden target must be refused even when sponsored");
    match &err {
        bcs_service_api::GroupUseCaseError::Forbidden(message) => {
            assert!(
                message.contains("hidden"),
                "the hidden target refusal names the hidden state, got {message}"
            );
        }
        other => panic!("expected hidden Forbidden, got {other:?}"),
    }
}

#[tokio::test]
async fn workbench_chat_send_follows_manage_authority_for_nonparticipant_humans() {
    let fixture = Fixture::new().await;
    fixture.seed_owned_and_managed().await;

    // staff-b owns bot-y but does NOT manage bot-x; staff-a owns bot-x and
    // manages bot-y. Neither Human is a group participant.
    let mut group = Group::new(
        "workbench-chat-group",
        BOT_X,
        vec![
            Participant::bot(BOT_X, ParticipantRole::Driver),
            Participant::bot(BOT_Y, ParticipantRole::Consultant),
        ],
    );
    group.originator = Some(format!("human_{STAFF_A}"));
    fixture
        .groups
        .upsert(group)
        .await
        .expect("store workbench group");

    // The authorized (managing) Human may access AND send as the managed
    // Bot — explicit X/Y perspective messaging, spec §8.2 parity.
    fixture
        .management
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some(format!("human_{STAFF_A}")),
            group_id: "workbench-chat-group".to_string(),
            from_actor_id: BOT_Y.to_string(),
            session_id: None,
        })
        .await
        .expect("a Human managing the sender Bot may send as it");

    // A Human who neither participates nor manages any participant of the
    // group is refused at the group-access gate (no implicit aggregation
    // of anyone else's controllable Bots).
    let denied = fixture
        .management
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_staff-d".to_string()),
            group_id: "workbench-chat-group".to_string(),
            from_actor_id: BOT_Y.to_string(),
            session_id: None,
        })
        .await
        .expect_err("an uninvolved Human has no default access");
    assert!(
        matches!(denied, WorkbenchUseCaseError::ForbiddenGroupAccess),
        "got {denied:?}"
    );
    assert!(
        fixture.hook.asked_about("staff-d", BOT_Y),
        "the deny too was decided through the live hook, not a string"
    );
}