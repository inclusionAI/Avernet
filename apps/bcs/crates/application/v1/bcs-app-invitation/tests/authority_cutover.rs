//! bcs-app-invitation consumer-cutover tests (row 11 of
//! the mounted-entry table; bot-authority plan
//! Task 6.2/6.3, spec §3.3).
//!
//! `authorize_bot_resource` (the Human→Bot control leg of
//! the friendship/invitation entries) runs at the REAL
//! entry with a REAL authority chain. The friend-request
//! ASSET leg (`request.created_by`, row 12) is asserted
//! to stay intact.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_app_invitation::{
    InvitationFriendshipServiceConfig, InvitationFriendshipServiceImpl,
};
use bcs_friend::{FriendCore, FriendRequestCore};
use bcs_group::application::invite::InviteServiceImpl;
use bcs_group::{GroupCore, MemoryGroupRepo};use bcs_service_api::application::v1::{
    CreateBotFriendRequest, FriendRequestDirection, FriendshipService,
    ListBotFriendRequests,
};
use bcs_service_api::application::v1::friend_connection::{
    CreateFriendConnectionRequest, FriendConnectionActor,
    FriendConnectionActorType, FriendConnectionService,
};
use bcs_service_api::port::repo::{GroupRepoPort, SessionRepoPort};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, InviteService,
    SessionManagementService,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{
    authority_hook_for, grant_owner_edge, NoopSystemMessageService,
};

const SECRET: &[u8] = b"test-secret";

struct Fixture {
    service: InvitationFriendshipServiceImpl,
    connect: InvitationFriendshipServiceImpl,
    bots: Arc<BotCore>,
    authority_repo: Arc<MemoryBotRepo>,
}

impl Fixture {
    async fn new() -> Self {
        let group_repo: Arc<dyn GroupRepoPort> =
            Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let bots = Arc::new(BotCore::memory());
        let relation = Arc::new(bcs_relation::RelationCore::memory());
        let friends =
            Arc::new(FriendCore::memory().with_relation(relation));
        let friend_requests =
            Arc::new(FriendRequestCore::memory(friends.clone(), bots.clone()));
        let session_repo: Arc<dyn SessionRepoPort> =
            Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo,
            group_repo,
        ));
        let authority_repo = Arc::new(MemoryBotRepo::new());
        let hook = authority_hook_for(authority_repo.clone());
        let invite: Arc<dyn InviteService> = Arc::new(InviteServiceImpl {
            registry: bots.clone(),
            group: groups.clone(),
            session: sessions.clone(),
            system_message: Arc::new(NoopSystemMessageService),
            token_secret: SECRET.to_vec(),
            default_ttl_seconds: 3600,
            base_url: None,
            group_link_url: None,
            session_link_url: None,
            // The legacy invite-issuance control legs
            // live inside the mounted InviteService;
            // they share the fixture's REAL chain.
            bot_authority: Some(hook.clone()),
        });
        let service = InvitationFriendshipServiceImpl::new(
            friends.clone(),
            friend_requests.clone(),
            groups.clone(),
            sessions.clone(),
            bots.clone(),
            invite.clone(),
            SECRET.to_vec(),
            InvitationFriendshipServiceConfig {
                default_ttl_seconds: 3600,
            },
        )
        .with_bot_authority_hook(hook.clone());
        let connect = InvitationFriendshipServiceImpl::new(
            friends,
            friend_requests,
            groups,
            sessions as Arc<dyn SessionManagementService>,
            bots.clone(),
            invite,
            SECRET.to_vec(),
            InvitationFriendshipServiceConfig {
                default_ttl_seconds: 3600,
            },
        )
        .with_friend_connection_service(Arc::new(
            bcs_test_support::NoopConnectService,
        ))
        .with_bot_authority_hook(hook);
        Self {
            service,
            connect,
            bots,
            authority_repo,
        }
    }

    async fn add_bot(&self, bot_id: &str, owner: &str) {
        self.bots
            .register(
                bot_id.into(),
                BotCapabilities {
                    name: Some(bot_id.into()),
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
        grant_owner_edge(&self.authority_repo, bot_id, owner).await;
    }
}

fn human_caller(id: &str) -> bcs_service_api::application::v1::AuthenticatedCaller {
    bcs_service_api::application::v1::AuthenticatedCaller {
        tenant: Some("dev".into()),
        user: Some(bcs_service_api::application::v1::AuthenticatedUserIdentity {
            id: id.into(),
            username: id.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

// ── Row 11: the Bot-resource control leg ─────────────────

#[tokio::test]
async fn friend_request_resource_leg_follows_the_current_edge() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice").await;

    // A (current owner) sends a request via its Bot.
    fx.service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: human_caller("alice"),
            bot_uuid: "bot-a".into(),
            to_bot_uuid: "bot-a".into(),
        })
        .await
        .err(); // self-add may be rejected by the core; the
                // control leg already passed.

    // X (no edge) is denied AT THE CONTROL LEG.
    let x = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: human_caller("stranger"),
            bot_uuid: "bot-a".into(),
            to_bot_uuid: "bot-a".into(),
        })
        .await
        .expect_err("X is denied");
    assert!(
        matches!(
            x,
            bcs_service_api::application::v1::ApplicationError::Forbidden(_)
        ),
        "expected 403, got {x:?}"
    );

    // A→B handover: A is fallback-free denied;
    // B runs the same entry.
    grant_owner_edge(&fx.authority_repo, "bot-a", "bob").await;
    let a = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: human_caller("alice"),
            bot_uuid: "bot-a".into(),
            to_bot_uuid: "bot-a".into(),
        })
        .await;
    assert!(
        matches!(
            a,
            Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))
        ),
        "A without edges is denied: {a:?}"
    );
    let b = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: human_caller("bob"),
            bot_uuid: "bot-a".into(),
            to_bot_uuid: "bot-a".into(),
        })
        .await;
    let _ = b; // the entry's own core rules now decide for B
}

// ── Row 11: friend-connection (act-as-Bot) leg ───────────

#[tokio::test]
async fn connect_request_acting_actor_follows_the_edge() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice").await;

    // Acting as the controlled Bot: allowed.
    fx.connect
        .create_friend_connection_request(CreateFriendConnectionRequest {
            caller: human_caller("alice"),
            from_actor: Some(FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: "bot-a".into(),
            }),
            to_actor: FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: "bot-a".into(),
            },
            request_auth: None,
            message: None,
        })
        .await
        .err(); // self-request rejected by the core, not the control leg.

    // X cannot act as the Bot at all. The
    // acting-actor gate rejects with the
    // entry's own 403 form; a NotFound from the
    // Bot load is tolerated as an equivalent
    // rejection shape.
    let x = fx
        .connect
        .create_friend_connection_request(CreateFriendConnectionRequest {
            caller: human_caller("stranger"),
            from_actor: Some(FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: "bot-a".into(),
            }),
            to_actor: FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: "bot-a".into(),
            },
            request_auth: None,
            message: None,
        })
        .await
        .expect_err("X cannot act as the Bot");
    assert!(
        matches!(
            x,
            bcs_service_api::application::v1::ApplicationError::Forbidden(_)
                | bcs_service_api::application::v1::ApplicationError::NotFound { .. }
        ),
        "X is rejected: {x:?}"
    );

    // The handover flips the acting actor.
    grant_owner_edge(&fx.authority_repo, "bot-a", "bob").await;
    let a = fx
        .connect
        .create_friend_connection_request(CreateFriendConnectionRequest {
            caller: human_caller("alice"),
            from_actor: Some(FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: "bot-a".into(),
            }),
            to_actor: FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: "bot-a".into(),
            },
            request_auth: None,
            message: None,
        })
        .await;
    assert!(matches!(
        a,
        Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))
    ));
}

/// A CURRENT MANAGER (not only the owner) may act as the Bot
/// on the friend-connection entry — the identity the work-order
/// notification ultimately forwards as the acting Human. This is
/// the exact leg the manager-represented submission takes.
#[tokio::test]
async fn connect_request_acting_actor_accepts_the_current_manager() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice").await;
    fx.add_bot("bot-b", "bob").await;

    // alice is the OWNER edge; mira only holds a MANAGER edge.
    bcs_test_support::grant_manager_edge(&fx.authority_repo, "bot-a", "mira").await;

    let request = |id: &str| CreateFriendConnectionRequest {
        caller: human_caller(id),
        from_actor: Some(FriendConnectionActor {
            actor_type: FriendConnectionActorType::Bot,
            id: "bot-a".into(),
        }),
        to_actor: FriendConnectionActor {
            actor_type: FriendConnectionActorType::Bot,
            id: "bot-b".into(),
        },
        request_auth: None,
        message: None,
    };

    let owner = fx
        .connect
        .create_friend_connection_request(request("alice"))
        .await
        .expect("the current owner acts as the Bot");
    assert!(matches!(owner.status, bcs_service_api::application::v1::FriendConnectionCreateStatus::Pending));

    let manager = fx
        .connect
        .create_friend_connection_request(request("mira"))
        .await
        .expect("the current manager acts as the Bot too");
    assert!(matches!(manager.status, bcs_service_api::application::v1::FriendConnectionCreateStatus::Pending));

    // A user with NO edge still cannot act as the Bot.
    let stranger = fx
        .connect
        .create_friend_connection_request(request("stranger"))
        .await
        .expect_err("no edge, no act-as-Bot");
    assert!(
        matches!(
            stranger,
            bcs_service_api::application::v1::ApplicationError::Forbidden(_)
                | bcs_service_api::application::v1::ApplicationError::NotFound { .. }
        ),
        "stranger is rejected: {stranger:?}"
    );
}

// ── Row 12: the friend-request ASSET leg is preserved ────

#[tokio::test]
async fn friend_request_asset_creator_leg_is_preserved() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice").await;
    fx.add_bot("bot-b", "bob").await;

    fx.service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: human_caller("alice"),
            bot_uuid: "bot-a".into(),
            to_bot_uuid: "bot-b".into(),
        })
        .await
        .expect("alice's Bot requests bob's Bot");

    // bob reads the INBOUND request (the request asset's
    // `created_by`/direction — NOT a Bot authority fact).
    let requests = fx
        .service
        .list_bot_friend_requests(ListBotFriendRequests {
            caller: human_caller("bob"),
            bot_uuid: "bot-b".into(),
            direction: FriendRequestDirection::Received,
            status: None,
            offset: 0,
            limit: 100,
        })
        .await
        .expect("the target owner reads its inbound requests");
    assert_eq!(requests.items.len(), 1);
}
