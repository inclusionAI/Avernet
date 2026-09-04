//! Session invite-token authorization tests for `InviteServiceImpl`.
//!
//! `create_session_invite_token` is gated on session membership only: any
//! participant of the session (any role, Bot or Human) may mint a token.
//! Group-level roles (driver, originator, manager) are intentionally NOT
//! required. Group invite tokens keep the stricter
//! `authorize_group_invite` contract (see `invite_integration` in the
//! bootstrap crate for the HTTP-level group path).

use std::sync::Arc;

use bcs_bot::BotCore;
use bcs_group::application::invite::InviteServiceImpl;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::application::invite::{
    CreateInviteTokenCommand, InviteService, InviteUseCaseError,
};
use bcs_service_api::application::session::{CreateOrReactivateCommand, SessionManagementService};
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupCoreService, GroupStrategy, Participant,
    ParticipantRole, SessionKind,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

const SECRET: &[u8] = b"test-invite-secret-32-bytes-long!!";

struct Fixture {
    service: InviteServiceImpl,
    groups: Arc<GroupCore>,
    sessions: Arc<SessionManagementServiceImpl>,
    bots: Arc<BotCore>,
}

impl Fixture {
    async fn new() -> Self {
        let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        // The session invite path consults the bot registry to grant Human
        // callers whose owned Bot participates in the session.
        let bots = Arc::new(BotCore::memory());
        let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            group_repo.clone(),
        ));
        let service = InviteServiceImpl {
            registry: bots.clone(),
            group: groups.clone(),
            session: sessions.clone(),
            system_message: Arc::new(NoopSystemMessageService),
            token_secret: SECRET.to_vec(),
            default_ttl_seconds: 3600,
            base_url: None,
            group_link_url: None,
            session_link_url: None,
        };
        Self {
            service,
            groups,
            sessions,
            bots,
        }
    }

    async fn add_owned_bot(&self, bot_uuid: &str, owner_staff_no: &str) {
        self.bots
            .register(
                bot_uuid.to_string(),
                BotCapabilities {
                    name: Some(bot_uuid.to_string()),
                    visibility: "public".into(),
                    ..Default::default()
                },
            )
            .await
            .expect("register bot");
        self.bots
            .save_created_by(bot_uuid, owner_staff_no, true)
            .await
            .expect("assign test Bot owner");
    }

    async fn store_group(&self, group_id: &str, driver: &str) {
        let mut group = Group::new(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
        );
        group.originator = Some("human_staff-owner".to_string());
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::Chat;
        self.groups.upsert(group).await.expect("store group");
    }

    async fn create_session_with_participants(
        &self,
        group_id: &str,
        driver: &str,
        participants: Vec<Participant>,
    ) -> String {
        let group = self
            .groups
            .get(group_id)
            .await
            .expect("group exists for session");
        let params = NewSessionParams {
            session_kind: SessionKind::Chat,
            participants,
            group_version: Some(group.version),
            caller_id: Some(driver.to_string()),
            caller_principal: Some(driver.to_string()),
            input: None,
            created_by: Some(driver.to_string()),
            session_title: Some(format!("{group_id}-session")),
            id: None,
            meta: None,
        };
        let outcome = self
            .sessions
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id: group_id.to_string(),
                session_id: None,
                params,
            })
            .await
            .expect("create session");
        outcome.session.id
    }

    fn cmd(&self, session_id: &str, caller_actor_id: Option<&str>) -> CreateInviteTokenCommand {
        CreateInviteTokenCommand {
            caller_actor_id: caller_actor_id.map(str::to_string),
            caller_staff_no: None,
            target_id: session_id.to_string(),
            ttl_seconds: None,
        }
    }
}

#[tokio::test]
async fn session_invite_token_allows_human_participant() {
    // A Human Consultant participant — not the group driver, originator, or a
    // bot owner — may mint a session invite token. Both caller identification
    // forms (direct actor id and staff_no) resolve to the same membership.
    let fx = Fixture::new().await;
    fx.store_group("grp-1", "bot-a").await;
    let session_id = fx
        .create_session_with_participants(
            "grp-1",
            "bot-a",
            vec![
                Participant::bot("bot-a", ParticipantRole::Driver),
                Participant::human("human_staff-9", ParticipantRole::Consultant),
            ],
        )
        .await;

    let result = fx
        .service
        .create_session_invite_token(fx.cmd(&session_id, Some("human_staff-9")))
        .await
        .expect("human participant may mint");
    assert!(result.invite_token.len() > 0);
    assert!(result.join_url.contains("/sessions/join/"));

    // Same human identified via staff_no instead of the actor id.
    let via_staff = CreateInviteTokenCommand {
        caller_actor_id: None,
        caller_staff_no: Some("staff-9".to_string()),
        target_id: session_id.clone(),
        ttl_seconds: None,
    };
    let result = fx
        .service
        .create_session_invite_token(via_staff)
        .await
        .expect("human participant identified by staff_no may mint");
    assert!(result.invite_token.len() > 0);
}

#[tokio::test]
async fn session_invite_token_allows_bot_consultant_participant() {
    // A Bot Consultant session participant — not the group driver nor
    // originator — may mint; previously only driver/originator/owner could.
    let fx = Fixture::new().await;
    fx.store_group("grp-1", "bot-a").await;
    let session_id = fx
        .create_session_with_participants(
            "grp-1",
            "bot-a",
            vec![
                Participant::bot("bot-a", ParticipantRole::Driver),
                Participant::bot("bot-b", ParticipantRole::Consultant),
            ],
        )
        .await;

    let result = fx
        .service
        .create_session_invite_token(fx.cmd(&session_id, Some("bot-b")))
        .await
        .expect("bot consultant participant may mint");
    assert!(result.invite_token.len() > 0);
}

#[tokio::test]
async fn session_invite_token_rejects_non_participant() {
    // bot-b participates in the parent GROUP but not in the session — session
    // membership is the only grant, and group membership does not substitute.
    let fx = Fixture::new().await;
    let mut group = Group::new(
        "grp-1",
        "bot-a",
        vec![
            Participant::bot("bot-a", ParticipantRole::Driver),
            Participant::bot("bot-b", ParticipantRole::Consultant),
        ],
    );
    group.originator = Some("human_staff-owner".to_string());
    group.label = Some("grp-1".to_string());
    group.group_strategy = GroupStrategy::Chat;
    fx.groups.upsert(group).await.expect("store group");

    let session_id = fx
        .create_session_with_participants(
            "grp-1",
            "bot-a",
            vec![Participant::bot("bot-a", ParticipantRole::Driver)],
        )
        .await;

    let error = fx
        .service
        .create_session_invite_token(fx.cmd(&session_id, Some("bot-b")))
        .await
        .expect_err("non-participant is forbidden");

    assert!(
        matches!(error, InviteUseCaseError::Forbidden(_)),
        "expected Forbidden, got {error:?}",
    );
}

#[tokio::test]
async fn session_invite_token_allows_human_owner_of_participant_bot() {
    // A Human caller who is not itself a session participant may still mint a
    // session invite token when one of the Human's owned Bots participates in
    // the session.
    let fx = Fixture::new().await;
    fx.add_owned_bot("bot-a", "staff-owner").await;
    fx.add_owned_bot("bot-b", "staff-9").await;
    fx.store_group("grp-1", "bot-a").await;
    let session_id = fx
        .create_session_with_participants(
            "grp-1",
            "bot-a",
            vec![
                Participant::bot("bot-a", ParticipantRole::Driver),
                Participant::bot("bot-b", ParticipantRole::Consultant),
            ],
        )
        .await;

    let cmd = CreateInviteTokenCommand {
        caller_actor_id: None,
        caller_staff_no: Some("staff-9".to_string()),
        target_id: session_id.clone(),
        ttl_seconds: None,
    };
    let result = fx
        .service
        .create_session_invite_token(cmd)
        .await
        .expect("owner of a participant bot may mint");
    assert!(result.invite_token.len() > 0);
    assert!(result.join_url.contains("/sessions/join/"));
}

#[tokio::test]
async fn session_invite_token_rejects_human_without_participant_bot() {
    // A Human caller whose owned Bots are NOT session participants is still
    // forbidden: ownership alone (without session membership of the owned
    // Bot) does not grant minting.
    let fx = Fixture::new().await;
    fx.add_owned_bot("bot-a", "staff-owner").await;
    fx.add_owned_bot("bot-c", "staff-9").await;
    fx.store_group("grp-1", "bot-a").await;
    let session_id = fx
        .create_session_with_participants(
            "grp-1",
            "bot-a",
            vec![Participant::bot("bot-a", ParticipantRole::Driver)],
        )
        .await;

    let cmd = CreateInviteTokenCommand {
        caller_actor_id: None,
        caller_staff_no: Some("staff-9".to_string()),
        target_id: session_id.clone(),
        ttl_seconds: None,
    };
    let error = fx
        .service
        .create_session_invite_token(cmd)
        .await
        .expect_err("human without a participant bot is forbidden");

    assert!(
        matches!(error, InviteUseCaseError::Forbidden(_)),
        "expected Forbidden, got {error:?}",
    );
}
