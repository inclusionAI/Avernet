//! bcs-group consumer-cutover tests (rows 14/15/16 of the
//! mounted-entry table; bot-authority plan Task 6.2/6.3,
//! spec §3.3 and §8.1).
//!
//! The Human→Bot control legs of the legacy Group
//! management service run through a REAL authority
//! chain (the lazy-seed hook over the authority
//! Core): the coordinator legs (row 15), the
//! friendless Group-create sponsorship (row 16, §8.1)
//! and the "a same-named manager edge is NOT a Group
//! management role" boundary (row 14) are asserted at
//! the real entries.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_group::{GroupConfig, GroupManagement, GroupStore};
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::{
    BotCapabilities, GroupCreateCommand, GroupCreateParticipantCommand,
    GroupManagementService, GroupRemoveMemberCommand, GroupUseCaseError,
    GroupUpdateVisibilityCommand, ParticipantRole,
};
use bcs_session_store::MemorySessionRepo;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::ServiceError;
use bcs_test_support::{grant_manager_edge, grant_owner_edge, NoopSystemMessageService};

struct Fixture {
    group: Arc<GroupStore>,
    registry: Arc<BotCore>,
    bots: Arc<MemoryBotRepo>,
    relation: Arc<bcs_relation::RelationCore>,
    authority: Arc<dyn BotAuthorityPort>,
}

impl Fixture {
    async fn new() -> Self {
        let bots = Arc::new(MemoryBotRepo::new());
        Self {
            group: Arc::new(GroupStore::new()),
            registry: Arc::new(BotCore::with_repo(bots.clone())),
            relation: Arc::new(bcs_relation::RelationCore::memory()),
            authority: bcs_test_support::authority_hook_for(bots.clone()),
            bots,
        }
    }

    /// Register one fixture Bot and grant its
    /// CURRENT owner edge (the control fact).
    async fn add_bot(&self, bot_id: &str, visibility: &str, owner: &str) {
        BotRepoPort::register(
            &*self.bots,
            bot_id.to_string(),
            BotCapabilities {
                name: Some(bot_id.into()),
                visibility: visibility.into(),
                ..Default::default()
            },
        )
        .await
        .expect("register fixture bot");
        grant_owner_edge(&self.bots, bot_id, owner).await;
    }

    async fn service(&self) -> GroupManagement {
        GroupManagement::new(
            self.group.clone(),
            self.registry.clone(),
            Arc::new(bcs_friend::FriendCore::memory()),
            self.relation.clone(),
            GroupConfig::default(),
            Arc::new(bcs_session::SessionManagementServiceImpl::new(
                Arc::new(MemorySessionRepo::new()) as _,
                Arc::new(bcs_group::MemoryGroupRepo::new()),
            )),
            Arc::new(NoopSystemMessageService),
        )
        .with_bot_authority(self.authority.clone())
    }
}

fn create_cmd(
    caller: Option<&str>,
    driver_bot_id: &str,
    participants: Vec<GroupCreateParticipantCommand>,
) -> GroupCreateCommand {
    GroupCreateCommand {
        human_mention_notify_mode: Default::default(),
        create_initial_session: true,
        group_id: Some("group-under-test".to_string()),
        caller_actor_id: caller.map(str::to_string),
        driver_bot_id: driver_bot_id.to_string(),
        originator: None,
        label: None,
        topic: Some("topic".to_string()),
        context: Some("context".to_string()),
        opening_message: None,
        routing_policy: None,
        member_bot_ids: Vec::new(),
        participants,
        group_kind: None,
        service_spec: None,
        group_strategy: None,
        visibility: None,
        provisioning: false,
    }
}

fn participant(bot_id: &str, role: Option<&str>) -> GroupCreateParticipantCommand {
    GroupCreateParticipantCommand {
        bot_id: bot_id.to_string(),
        role: role.map(str::to_string),
        tags: Vec::new(),
        message_view_scope: None,
    }
}

// ── Row 15: the remove-member owner leg ─────────────────

#[tokio::test]
async fn remove_member_owner_leg_follows_the_current_edge() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot_a", "public", "downer").await;
    fixture.add_bot("bot_b", "public", "alice").await;

    fixture
        .service()
        .await
        .create_group(create_cmd(
            Some("bot_a"),
            "bot_a",
            vec![
                participant("bot_a", Some("driver")),
                participant("bot_b", Some("consultant")),
            ],
        ))
        .await
        .expect("create group");

    // A (the CURRENT owner) removes its own Bot.
    let legacy_service = fixture.service().await;
    legacy_service
        .remove_member(GroupRemoveMemberCommand {
            caller_actor_id: Some("human_alice".into()),
            group_id: "group-under-test".into(),
            bot_id: "bot_b".into(),
        })
        .await
        .expect("owner removes its own bot");

    // Re-create the group for the handover leg.
    legacy_service
        .create_group(create_cmd(
            Some("bot_a"),
            "bot_a",
            vec![
                participant("bot_a", Some("driver")),
                participant("bot_b", Some("consultant")),
            ],
        ))
        .await
        .expect("re-create group");
    // The A→B handover: B owns bot_b now; A's leg
    // is fallback-free denied.
    grant_owner_edge(&fixture.bots, "bot_b", "bob").await;
    let a = legacy_service
        .remove_member(GroupRemoveMemberCommand {
            caller_actor_id: Some("human_alice".into()),
            group_id: "group-under-test".into(),
            bot_id: "bot_b".into(),
        })
        .await;
    assert!(
        matches!(a, Err(GroupUseCaseError::Forbidden(_))),
        "A without any edge is denied: {a:?}"
    );
    legacy_service
        .remove_member(GroupRemoveMemberCommand {
            caller_actor_id: Some("human_bob".into()),
            group_id: "group-under-test".into(),
            bot_id: "bot_b".into(),
        })
        .await
        .expect("B removes its own bot after the handover");
}

#[tokio::test]
async fn manager_edge_does_not_elevate_group_management() {
    let fixture = Fixture::new().await;
    // Alice is the MANAGER of the worker Bot
    // (and nothing else).
    fixture.add_bot("driver", "public", "downer").await;
    fixture.add_bot("worker", "public", "downer").await;
    grant_manager_edge(&fixture.bots, "worker", "alice").await;
    let service = fixture.service().await;

    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("worker", Some("consultant")),
            ],
        ))
        .await
        .expect("create group");

    // Row-14 boundary: controlling (even
    // managing) the plain WORKER Bot does NOT
    // make the Human a Group coordinator.
    let denied = service
        .update_visibility(GroupUpdateVisibilityCommand {
            caller_actor_id: "human_alice".into(),
            group_id: "group-under-test".into(),
            visibility: "private".into(),
        })
        .await;
    assert!(
        denied.is_err(),
        "a worker-Bot manager is not a Group admin: {denied:?}"
    );

    // Controlling the DRIVER opens the
    // coordinator leg: the Group management
    // gating derives from the Group's own rules
    // plus the CURRENT control edge.
    grant_owner_edge(&fixture.bots, "driver", "alice").await;
    service
        .update_visibility(GroupUpdateVisibilityCommand {
            caller_actor_id: "human_alice".into(),
            group_id: "group-under-test".into(),
            visibility: "private".into(),
        })
        .await
        .expect("controlling the driver opens the coordinator leg");
}

#[tokio::test]
async fn create_group_sponsorship_requires_current_edge() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver", "public", "downer").await;
    fixture.add_bot("protected-bot", "protected", "alice").await;
    let service = fixture.service().await;

    // §8.1: the Human introduces its controlled
    // non-hidden Bot into a private Group with
    // no friendship anywhere.
    service
        .create_group(GroupCreateCommand {
            human_mention_notify_mode: Default::default(),
            create_initial_session: true,
            group_id: Some("group-under-test".into()),
            caller_actor_id: Some("human_alice".into()),
            driver_bot_id: "driver".into(),
            originator: Some("human_alice".into()),
            label: None,
            topic: None,
            context: None,
            opening_message: None,
            routing_policy: None,
            member_bot_ids: vec![],
            participants: vec![
                participant("driver", Some("driver")),
                participant("protected-bot", None),
            ],
            group_kind: None,
            service_spec: None,
            group_strategy: None,
            visibility: None,
            provisioning: false,
        })
        .await
        .expect("sponsorship creates the Group");

    // Withdraw via the handover: the sponsorship
    // for Alice dies while B's opens.
    grant_owner_edge(&fixture.bots, "protected-bot", "bob").await;
    let denied = service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("protected-bot", None),
            ],
        ))
        .await;
    assert!(
        matches!(denied, Err(GroupUseCaseError::Forbidden(_))),
        "the sponsorship dies with the edge: {denied:?}"
    );
}

#[tokio::test]
async fn legacy_creator_relation_opens_nothing() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver", "public", "downer").await;
    fixture.add_bot("member", "public", "carol").await;
    let service = fixture.service().await;

    // A legacy creator relation edge (历史数据)
    // is inserted WITHOUT any authority edge.
    bcs_service_api::core::actor::RelationCoreService::upsert_edge(
        fixture.relation.as_ref(),
        bcs_service_api::RelationEdge {
            from_id: "human_alice".into(),
            to_id: "member".into(),
            env: "dev".into(),
            kinds: 0,
            allow: 0,
            deny: 0,
            is_creator: true,
        },
    )
    .await
    .expect("insert legacy edge");

    let created = service
        .create_group(create_cmd(
            Some("human_downer"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("member", Some("consultant")),
            ],
        ))
        .await;
    let _ = created.expect("create group");

    let denied = service
        .update_visibility(GroupUpdateVisibilityCommand {
            caller_actor_id: "human_alice".into(),
            group_id: "group-under-test".into(),
            visibility: "private".into(),
        })
        .await;
    assert!(
        matches!(denied, Err(GroupUseCaseError::Forbidden(_))),
        "the legacy creator relation is 死历史: {denied:?}"
    );
}

// ── Review fix (Important#2a): a public
// Group still only accepts public Bots —
// sponsorship must NOT widen that rule
// (spec §8.1).
#[tokio::test]
async fn public_group_rejects_non_public_controlled_bot() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver", "public", "downer").await;
    fixture.add_bot("protected-bot", "protected", "alice").await;
    let service = fixture.service().await.for_v1_openapi();

    // The same controlled-protected shape as the
    // private-Group positive, but into a PUBLIC
    // Group: rejected regardless of the CURRENT
    // control edge.
    let denied = service
        .create_group(GroupCreateCommand {
            human_mention_notify_mode: Default::default(),
            create_initial_session: true,
            group_id: Some("group-public".into()),
            caller_actor_id: Some("human_alice".into()),
            driver_bot_id: "driver".into(),
            originator: Some("human_alice".into()),
            label: None,
            topic: None,
            context: None,
            opening_message: None,
            routing_policy: None,
            member_bot_ids: vec![],
            participants: vec![
                participant("driver", Some("driver")),
                participant("protected-bot", None),
            ],
            group_kind: None,
            service_spec: None,
            group_strategy: None,
            visibility: Some("public".into()),
            provisioning: false,
        })
        .await;
    let public_only = matches!(
        denied,
        Err(GroupUseCaseError::Service(
            ServiceError::ExistNonPublicBots { .. }
        )) | Err(GroupUseCaseError::Forbidden(_))
    );
    assert!(
        public_only,
        "public Groups stay public-only: {denied:?}"
    );

    // The positive keeps working: the same
    // controlled Bot enters a non-public Group.
    service
        .create_group(GroupCreateCommand {
            human_mention_notify_mode: Default::default(),
            create_initial_session: true,
            group_id: Some("group-private".into()),
            caller_actor_id: Some("human_alice".into()),
            driver_bot_id: "driver".into(),
            originator: Some("human_alice".into()),
            label: None,
            topic: None,
            context: None,
            opening_message: None,
            routing_policy: None,
            member_bot_ids: vec![],
            participants: vec![
                participant("driver", Some("driver")),
                participant("protected-bot", None),
            ],
            group_kind: None,
            service_spec: None,
            group_strategy: None,
            visibility: Some("private".into()),
            provisioning: false,
        })
        .await
        .expect("the controlled bot enters the private Group");
}
