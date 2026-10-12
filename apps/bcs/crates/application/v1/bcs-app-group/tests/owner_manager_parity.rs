//! Owner/manager parity for the V1 Group facade (plan Task 10, spec §8).
//!
//! Every asserted variable comes from a REAL facade result: a Human caller
//! who owns `bot-x` and manages `bot-y` — both protected, no friendship
//! between them — sponsors their joint collaboration without any Bot↔Bot
//! friend edge, public Groups still accept public Bots only, a
//! Bot-originated same input is rejected, a Human who merely manages a
//! plain worker does not gain group-management parity, and add-member
//! passes the group-management authorization FIRST and only then
//! evaluates the target's sponsorship.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use bcs_app_group::{GroupServiceConfig, GroupServiceImpl};
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_friend::FriendCore;
use bcs_group::{GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::v1::{
    AddGroupParticipant, ApplicationError, AuthenticatedBotIdentity, AuthenticatedCaller,
    AuthenticatedUserIdentity, BotFinalDelivery, ChatConfiguration,
    CollaborationConfiguration, CreateCollaborationGroup, CreateGroup, CreateGroupSpec,
    CreateParticipant, GetGroup, GroupDetail, GroupKindFilter, GroupPatch, GroupService,
    GroupSummary, GroupVisibility, ListGroups, MembershipFilter, UpdateGroup,
};
use bcs_service_api::{
    ActorStatus, BotCapabilities, BotRepoPort, FriendCoreService, Group, GroupCoreService,
    Participant, ParticipantRole, SystemMessageService,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

#[path = "common/mod.rs"]
mod common;

const STAFF_A: &str = "staff-a";
const STAFF_B: &str = "staff-b";
const BOT_X: &str = "bot-x";
const BOT_Y: &str = "bot-y";
const BOT_Z: &str = "bot-z";
const BOT_W: &str = "bot-w";

struct ParityFixture {
    service: GroupServiceImpl,
    groups: Arc<GroupCore>,
    group_repo: Arc<MemoryGroupRepo>,
    bot_repo: Arc<MemoryBotRepo>,
    friends: Arc<FriendCore>,
    /// Keeps the tempdir backing `bot_repo` alive for the whole fixture.
    _temp: tempfile::TempDir,
}

impl ParityFixture {
    async fn new() -> Self {
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));

        let temp = tempfile::tempdir().expect("temp bot dir");
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
        let bots = Arc::new(BotCore::with_repo(bot_repo.clone()));
        let authority = common::repo_authority_hook(&bot_repo);
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MemorySessionRepo::new()),
            group_repo.clone(),
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
            .for_v1_openapi()
            .with_authority(authority.clone()),
        );
        let service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends.clone(),
            relation.clone(),
            sessions.clone(),
            management,
            authority,
            GroupServiceConfig {
                relation_env: "dev".to_string(),
            },
        );
        Self {
            service,
            groups,
            group_repo,
            bot_repo,
            friends,
            _temp: temp,
        }
    }

    /// Register a protected Bot and initialize it as OWNED by `owner`
    /// (Task-5 harness) so facade decisions resolve through the LIVE hook.
    async fn owned_protected_bot(&self, bot_id: &str, owner: &str) {
        self.bot_repo
            .register_with_owner_and_token(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "protected".into(),
                    ..Default::default()
                },
                owner,
                &format!("token-{bot_id}"),
            )
            .await
            .expect("register seeded bot");
        self.bot_repo
            .seed_authority_owned(bot_id, owner)
            .await
            .expect("seed owner edge");
    }

    /// Register a protected Bot owned by `owner`, plus a formal
    /// `direct/manual` MANAGER edge for `manager`.
    async fn managed_protected_bot(&self, bot_id: &str, owner: &str, manager: &str) {
        self.owned_protected_bot(bot_id, owner).await;
        self.bot_repo
            .seed_authority_manager_source(bot_id, manager, "direct", "manual")
            .await
            .expect("seed manager edge");
    }

    /// Sorted union of every friend edge observable between the fixture
    /// bots (spec §8.3.2: sponsorship must not create friend edges).
    async fn friend_snapshot(&self) -> Vec<String> {
        let mut snapshot = Vec::new();
        for bot in [BOT_X, BOT_Y, BOT_W, BOT_Z] {
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

fn human_caller(staff_no: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
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

fn bot_caller(bot_uuid: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: bot_uuid.into(),
            app_id: 7,
            agent_code: format!("agent-{bot_uuid}"),
        }),
        app: None,
        access_key: None,
    }
}

fn chat_group(caller: &str, visibility: GroupVisibility) -> CreateGroup {
    chat_group_with_originator(human_caller(caller), Some(format!("human_{caller}")), visibility)
}

fn chat_group_with_originator(
    caller: AuthenticatedCaller,
    originator: Option<String>,
    visibility: GroupVisibility,
) -> CreateGroup {
    CreateGroup {
        caller,
        group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
            name: Some("Parity".into()),
            context: None,
            opening_message: None,
            visibility,
            driver_bot_uuid: BOT_X.into(),
            participants: vec![CreateParticipant {
                actor_id: BOT_Y.into(),
                role: ParticipantRole::Consultant,
                tags: Vec::new(),
                message_view_scope: None,
            }],
            collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                delivery_policy: bcs_service_api::application::v1::GroupDeliveryPolicy {
                    bot_final_delivery: BotFinalDelivery::SendToDriver,
                },
            }),
            originator,
        }),
    }
}

fn group_id_of(detail: &GroupDetail) -> String {
    match detail {
        GroupDetail::Collaboration(it) => it.group_id.clone(),
        other => panic!("expected collaboration detail, got {other:?}"),
    }
}

#[tokio::test]
async fn human_sponsors_private_group_over_owned_and_managed_bots() {
    let fixture = ParityFixture::new().await;
    fixture.owned_protected_bot(BOT_X, STAFF_A).await;
    fixture
        .managed_protected_bot(BOT_Y, "other-owner", STAFF_A)
        .await;
    let command = chat_group(STAFF_A, GroupVisibility::Private);

    let friend_edges_before = fixture.friend_snapshot().await;
    let human_private_group_result = fixture.service.create(command).await;
    assert!(human_private_group_result.is_ok());
    let friend_edges_after = fixture.friend_snapshot().await;
    assert_eq!(friend_edges_after, friend_edges_before);
}

#[tokio::test]
async fn facade_mutations_carry_the_required_operation_context_to_the_store() {
    let fixture = ParityFixture::new().await;
    fixture.owned_protected_bot(BOT_X, STAFF_A).await;
    fixture
        .managed_protected_bot(BOT_Y, "other-owner", STAFF_A)
        .await;
    let detail = fixture
        .service
        .create(chat_group(STAFF_A, GroupVisibility::Private))
        .await
        .expect("human-sponsored private creation");
    let group_id = group_id_of(&detail);
    // Baseline: the pre-existing audit rows (from setup writes through the
    // management lane) aside, this test asserts the FACADE mutation lane.
    let audits_before = fixture
        .group_repo
        .group_action_audit_records()
        .await
        .expect("read audits");

    fixture
        .service
        .update(UpdateGroup {
            caller: human_caller(STAFF_A),
            group_id: group_id.clone(),
            patch: GroupPatch {
                name: Some("Renamed".into()),
                ..Default::default()
            },
        })
        .await
        .expect("the owner updates their group");

    let audits = fixture
        .group_repo
        .group_action_audit_records()
        .await
        .expect("read audits");
    let fresh: Vec<_> = audits
        .iter()
        .filter(|record| {
            !audits_before
                .iter()
                .any(|before| before.audit_id == record.audit_id)
                && record.step_key == "update/group/applied"
        })
        .collect();
    assert_eq!(
        fresh.len(),
        1,
        "the facade mutation produced exactly one applied audit row: {audits:#?}"
    );
    assert_eq!(fresh[0].operator.operator_kind(), "human");
    assert_eq!(fresh[0].operator.operator_user_id(), Some(STAFF_A));
    assert_eq!(fresh[0].operator.effective_actor_id(), format!("human_{STAFF_A}"));
    assert_eq!(fresh[0].resource_id, group_id);
}

#[tokio::test]
async fn human_public_group_rejects_protected_sponsored_bots() {
    let fixture = ParityFixture::new().await;
    fixture.owned_protected_bot(BOT_X, STAFF_A).await;
    fixture
        .managed_protected_bot(BOT_Y, "other-owner", STAFF_A)
        .await;

    let human_public_group_result = fixture
        .service
        .create(chat_group(STAFF_A, GroupVisibility::Public))
        .await;
    assert!(human_public_group_result.is_err());
}

#[tokio::test]
async fn bot_originated_same_input_is_rejected_without_inheriting_sponsorship() {
    let fixture = ParityFixture::new().await;
    fixture.owned_protected_bot(BOT_X, STAFF_A).await;
    fixture
        .managed_protected_bot(BOT_Y, "other-owner", STAFF_A)
        .await;

    // Same payload, BOT caller: no originator is carried (defaults to the
    // bot principal), the facade attaches no Human sponsorship credential,
    // and the Bot lane keeps its public/friendship rules, so the protected
    // non-friend target is rejected.
    let command = chat_group_with_originator(
        bot_caller(BOT_X),
        None,
        GroupVisibility::Private,
    );
    let bot_originated_same_input_result = fixture.service.create(command).await;
    assert!(bot_originated_same_input_result.is_err());

    // The equality anchor: the exact same payload under the Human caller
    // succeeds, so the difference is the caller lane, not the input.
    let human_same_input_result = fixture
        .service
        .create(chat_group(STAFF_A, GroupVisibility::Private))
        .await;
    assert!(human_same_input_result.is_ok());
}

#[tokio::test]
async fn worker_manager_does_not_gain_group_management_parity() {
    let fixture = ParityFixture::new().await;
    // Human B owns the manager/driver; Human A merely OWNS bot-w, a plain
    // worker participant — controlling the worker does not elevate A.
    fixture.owned_protected_bot(BOT_Z, STAFF_B).await;
    fixture.owned_protected_bot(BOT_W, STAFF_A).await;

    let mut group = Group::new(
        "worker-manager-parity",
        BOT_Z,
        vec![
            Participant::bot(BOT_Z, ParticipantRole::Manager),
            Participant::bot(BOT_W, ParticipantRole::Worker),
        ],
    );
    group.originator = Some(format!("human_{STAFF_B}"));
    fixture.groups.upsert(group).await.expect("store group");

    let worker_manager_update_group_result = fixture
        .service
        .update(UpdateGroup {
            caller: human_caller(STAFF_A),
            group_id: "worker-manager-parity".into(),
            patch: GroupPatch {
                name: Some("Must Not Apply".into()),
                ..Default::default()
            },
        })
        .await;
    assert!(worker_manager_update_group_result.is_err());
}

#[tokio::test]
async fn default_human_view_stays_own_membership_and_explicit_perspectives_open() {
    let fixture = ParityFixture::new().await;
    fixture.owned_protected_bot(BOT_X, STAFF_A).await;
    fixture
        .managed_protected_bot(BOT_Y, "other-owner", STAFF_A)
        .await;
    let detail = fixture
        .service
        .create(chat_group(STAFF_A, GroupVisibility::Private))
        .await
        .expect("human-sponsored private creation");
    let group_id = group_id_of(&detail);

    let contains_group = |page: &bcs_service_api::application::v1::Page<GroupSummary>| {
        page.items.iter().any(|item| match item {
            GroupSummary::Normal(it) => it.group_id == group_id,
            _ => false,
        })
    };

    // Default (no view_bot_id): the caller remains their own Human actor —
    // not a participant — so the sponsored group must NOT be implicitly
    // aggregated into their list (spec §8.1/§8.3.6, view_bot_id contract
    // unchanged).
    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: human_caller(STAFF_A),
            view_bot_id: None,
            offset: 0,
            limit: 100,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list groups");
    assert!(
        !contains_group(&page),
        "default Human view must not aggregate controllable-Bot groups"
    );

    // The explicit owned-Bot perspective includes it (spec §8.1: using a
    // managed perspective needs no friendship).
    let owned_view = fixture
        .service
        .list_groups(ListGroups {
            caller: human_caller(STAFF_A),
            view_bot_id: Some(BOT_X.into()),
            offset: 0,
            limit: 100,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list groups under the owned perspective");
    assert!(contains_group(&owned_view));

    // The explicit MANAGED-Bot perspective includes it too — manager (not
    // only owner) parity.
    let managed_view = fixture
        .service
        .list_groups(ListGroups {
            caller: human_caller(STAFF_A),
            view_bot_id: Some(BOT_Y.into()),
            offset: 0,
            limit: 100,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list groups under the managed perspective");
    assert!(contains_group(&managed_view));

    // Detail read follows the same live authority: the caller owns a
    // participant Bot (spec §8.2 Group/Session detail row).
    fixture
        .service
        .get(GetGroup {
            caller: human_caller(STAFF_A),
            group_id: group_id.clone(),
        })
        .await
        .expect("detail read via the owner/manager of a participant");
}

#[tokio::test]
async fn add_member_requires_group_management_authorization_before_sponsorship() {
    let fixture = ParityFixture::new().await;
    fixture.owned_protected_bot(BOT_X, STAFF_A).await;
    fixture
        .managed_protected_bot(BOT_Y, "other-owner", STAFF_A)
        .await;
    // bot-w: protected, owned by staff-b — a valid sponsorship target for
    // NOBODY here.
    fixture.owned_protected_bot(BOT_W, STAFF_B).await;
    let detail = fixture
        .service
        .create(chat_group(STAFF_A, GroupVisibility::Private))
        .await
        .expect("human-sponsored private creation");
    let group_id = group_id_of(&detail);

    // Authorized caller, unsponsored target: the sponsor(target) stage is
    // what rejects.
    let err = fixture
        .service
        .add_participant(AddGroupParticipant {
            caller: human_caller(STAFF_A),
            group_id: group_id.clone(),
            actor_id: BOT_W.into(),
            message_view_scope: None,
        })
        .await
        .expect_err("unsponsored target must be rejected");
    assert!(matches!(err, ApplicationError::Forbidden(_)), "got {err:?}");

    // Authorization-first pin: on ANOTHER Human's group, a caller whose
    // sponsorship for the target WOULD hold still fails at the
    // group-management gate — the error names the manage/coordinator gate,
    // never the sponsorship stage.
    let foreign_group_id = "foreign-manager-group".to_string();
    fixture
        .bot_repo
        .register_with_owner_and_token(
            BOT_Z.to_string(),
            BotCapabilities {
                name: Some(BOT_Z.to_string()),
                visibility: "protected".into(),
                ..Default::default()
            },
            STAFF_B,
            "token-z",
        )
        .await
        .expect("register z");
    fixture
        .bot_repo
        .seed_authority_owned(BOT_Z, STAFF_B)
        .await
        .expect("seed z owner");
    let mut foreign = Group::new(
        &foreign_group_id,
        BOT_Z,
        vec![Participant::bot(BOT_Z, ParticipantRole::Driver)],
    );
    foreign.originator = Some(format!("human_{STAFF_B}"));
    fixture.groups.upsert(foreign).await.expect("store group");

    let err = fixture
        .service
        .add_participant(AddGroupParticipant {
            caller: human_caller(STAFF_A),
            group_id: foreign_group_id,
            actor_id: BOT_Y.into(),
            message_view_scope: None,
        })
        .await
        .expect_err("the group-management gate must fire before sponsorship");
    match &err {
        ApplicationError::Forbidden(message) => {
            assert!(
                !message.to_lowercase().contains("sponsor"),
                "the failing stage is the group-management gate, got {message}"
            );
        }
        other => panic!("expected Forbidden from the manage gate, got {other:?}"),
    }

    // Hidden refusal: authorized caller with a sponsorship that WOULD pass,
    // the hidden target is still refused.
    fixture
        .bot_repo
        .register_with_owner_and_token(
            "bot-hidden".to_string(),
            BotCapabilities {
                name: Some("bot-hidden".to_string()),
                visibility: "protected".into(),
                ..Default::default()
            },
            STAFF_A,
            "token-bot-hidden",
        )
        .await
        .expect("register hidden bot");
    fixture
        .bot_repo
        .seed_authority_owned("bot-hidden", STAFF_A)
        .await
        .expect("seed hidden bot owner edge");
    fixture
        .bot_repo
        .update_actor_status("bot-hidden", ActorStatus::Hidden)
        .await
        .expect("hide the bot");
    let err = fixture
        .service
        .add_participant(AddGroupParticipant {
            caller: human_caller(STAFF_A),
            group_id,
            actor_id: "bot-hidden".into(),
            message_view_scope: None,
        })
        .await
        .expect_err("hidden target must be refused even when sponsored");
    assert!(matches!(err, ApplicationError::Forbidden(_)), "got {err:?}");
}

#[tokio::test]
async fn sponsorship_decisions_resolve_live_through_the_authority_hook() {
    // The recording double proves the facade actually ASKS the hook and
    // re-verifies CURRENT qualification: flipping the verdict flips the
    // result, so no passthrough string satisfies the check.
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
    let temp = tempfile::tempdir().expect("temp bot dir");
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let bots = Arc::new(BotCore::with_repo(bot_repo.clone()));
    let hook = Arc::new(common::RecordingAuthorityHook::new(false));
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
        .for_v1_openapi()
        .with_authority(hook.clone()),
    );
    let service = GroupServiceImpl::new(
        groups.clone(),
        bots.clone(),
        friends.clone(),
        relation,
        sessions,
        management,
        hook.clone(),
        GroupServiceConfig {
            relation_env: "dev".to_string(),
        },
    );
    for (bot_id, token) in [(BOT_X, "token-x"), (BOT_Y, "token-y")] {
        bot_repo
            .register_with_owner_and_token(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "protected".into(),
                    ..Default::default()
                },
                "someone",
                token,
            )
            .await
            .expect("register bot");
    }

    // The hook denies everything initially → creation is rejected and the
    // hook WAS consulted for the sponsorship pair.
    let denied = service
        .create(chat_group(STAFF_A, GroupVisibility::Private))
        .await;
    assert!(denied.is_err());
    assert!(
        hook.asked_about(STAFF_A, BOT_Y),
        "the facade must actually consult the authority hook for the sponsorship"
    );

    // A live grant for the driver AND the participant opens the identical
    // input (spec §8.3.1: the driver and every Bot participant are checked).
    hook.set(STAFF_A, BOT_X, true);
    hook.set(STAFF_A, BOT_Y, true);
    service
        .create(chat_group(STAFF_A, GroupVisibility::Private))
        .await
        .expect("live sponsorship grant opens the same input");

    // A live revoke closes the next identical input: qualification is
    // re-verified per request, never cached or trusted as a string.
    hook.set(STAFF_A, BOT_Y, false);
    let revoked = service
        .create(chat_group(STAFF_A, GroupVisibility::Private))
        .await;
    assert!(revoked.is_err());
    drop(temp);
}