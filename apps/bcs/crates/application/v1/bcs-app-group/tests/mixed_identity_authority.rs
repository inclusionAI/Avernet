//! Mixed-identity Group CRUD cutover (PR #2568 review F4, spec §12.1/§12.4):
//! every V1 Group use case selects the effective Principal through the
//! ASYNC live authority (`resolve_authorized_principal`), never the
//! deprecated synchronous `HumanOrOwnedBot` check that trusted the
//! Gateway-signed `owner_id` claim.
//!
//! The claim is HISTORICAL evidence: a stale claim names the creator at
//! signing time, while the CURRENT owner/manager fact lives in the
//! authority edges. After a transfer the former creator must lose the
//! acting-Bot lane (subject only to a live manager edge), and the CURRENT
//! owner must never be rejected for a claim that predates the transfer.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

#[path = "common/mod.rs"]
mod common;

use std::sync::Arc;

use bcs_app_group::{GroupServiceConfig, GroupServiceImpl};
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_friend::FriendCore;
use bcs_group::{GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedBotIdentity, AuthenticatedCaller,
    AuthenticatedUserIdentity, BotFinalDelivery, ChatConfiguration, CollaborationConfiguration,
    CreateCollaborationGroup, CreateGroup, CreateGroupSpec, CreateParticipant, DeleteGroup,
    GetGroup, GroupDetail, GroupService, GroupVisibility, UpdateGroup,
};
use bcs_service_api::application::v1::{GroupPatch};
use bcs_service_api::{
    ActorStatus, BotCapabilities, BotRepoPort, FriendCoreService, GroupCoreService,
    ParticipantRole, SystemMessageService,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

const STAFF_A: &str = "staff-a";
const STAFF_B: &str = "staff-b";
const BOT_P: &str = "bot-p";

struct MixedIdentityFixture {
    service: GroupServiceImpl,
    bot_repo: Arc<MemoryBotRepo>,
    _temp: tempfile::TempDir,
}

impl MixedIdentityFixture {
    async fn new() -> Self {
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));

        let temp = tempfile::tempdir().expect("temp bot dir");
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
        let bots = Arc::new(BotCore::with_repo(bot_repo.clone()));
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
            .with_authority(common::repo_authority_hook(&bot_repo)),
        );
        let service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends.clone(),
            relation.clone(),
            sessions.clone(),
            management,
            common::repo_authority_hook(&bot_repo),
            GroupServiceConfig {
                relation_env: "dev".to_string(),
            },
        );
        Self {
            service,
            bot_repo,
            _temp: temp,
        }
    }

    /// Register one PUBLIC bot whose HISTORICAL creator is `creator`, then
    /// move the CURRENT ownership to `current_owner` (the transfer shape:
    /// `created_by` keeps recording the creation source, the live owner
    /// edge answers the authority questions).
    async fn public_bot_transferred(&self, bot_id: &str, creator: &str, current_owner: &str) {
        self.bot_repo
            .register_with_owner_and_token(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".into(),
                    ..Default::default()
                },
                creator,
                &format!("token-{bot_id}"),
            )
            .await
            .expect("register seeded bot");
        self.ensure_human_actor(creator).await;
        self.ensure_human_actor(current_owner).await;
        self.bot_repo
            .seed_authority_owned(bot_id, current_owner)
            .await
            .expect("seed the CURRENT owner edge");
    }

    async fn ensure_human_actor(&self, staff_no: &str) {
        self.bot_repo
            .ensure_human_actor(staff_no, staff_no)
            .await
            .expect("seed human actor");
    }
}

/// Gateway-authenticated caller carrying BOTH identities: the real User and
/// the acting Bot with a `signed_owner` claim that may be stale.
fn mixed_caller(staff_no: &str, bot_uuid: &str, signed_owner: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: staff_no.into(),
            username: staff_no.into(),
            display_name: None,
            full_name: None,
        }),
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: signed_owner.into(),
            app_id: 7,
            agent_code: format!("agent-{bot_uuid}"),
        }),
        app: None,
        access_key: None,
    }
}

fn bot_originated_public_group(caller: AuthenticatedCaller, driver: &str) -> CreateGroup {
    CreateGroup {
        caller,
        group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
            name: Some("Mixed Identity".into()),
            context: None,
            opening_message: None,
            visibility: GroupVisibility::Public,
            driver_bot_uuid: driver.into(),
            participants: Vec::new(),
            collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                delivery_policy: bcs_service_api::application::v1::GroupDeliveryPolicy {
                    bot_final_delivery: BotFinalDelivery::SendToDriver,
                },
            }),
            originator: Some(driver.to_string()),
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
async fn mixed_identity_create_current_owner_with_stale_claim_is_allowed() {
    let fixture = MixedIdentityFixture::new().await;
    // Public bot-p was CREATED by staff-a and is now OWNED by staff-b; the
    // signed claim still names the former creator.
    fixture
        .public_bot_transferred(BOT_P, STAFF_A, STAFF_B)
        .await;

    let created = fixture
        .service
        .create(bot_originated_public_group(
            mixed_caller(STAFF_B, BOT_P, STAFF_A),
            BOT_P,
        ))
        .await
        .expect("the CURRENT owner acts as the Bot despite the stale signed claim");
    assert_eq!(group_id_of(&created), group_id_of(&created));
}

#[tokio::test]
async fn mixed_identity_create_former_creator_with_matching_claim_is_denied() {
    let fixture = MixedIdentityFixture::new().await;
    // staff-a CREATED bot-p, but the ownership moved to staff-b WITHOUT a
    // manager edge — the creation-source fact grants no current control.
    fixture
        .public_bot_transferred(BOT_P, STAFF_A, STAFF_B)
        .await;

    let denied = fixture
        .service
        .create(bot_originated_public_group(
            mixed_caller(STAFF_A, BOT_P, STAFF_A),
            BOT_P,
        ))
        .await;
    match denied {
        Err(ApplicationError::Forbidden(message)) => {
            assert!(
                message.contains("may not act as the authenticated Bot"),
                "the denial names the live authority refusal: {message}"
            );
        }
        other => panic!(
            "the former creator with no live role must not act as the Bot: {other:?}"
        ),
    }
}

#[tokio::test]
async fn mixed_identity_update_get_delete_resolve_the_live_owner_not_the_claim() {
    let fixture = MixedIdentityFixture::new().await;
    fixture
        .public_bot_transferred(BOT_P, STAFF_A, STAFF_B)
        .await;

    // The group exists because a plain Bot caller created it.
    let created = fixture
        .service
        .create(bot_originated_public_group(
            mixed_caller(STAFF_B, BOT_P, STAFF_A),
            BOT_P,
        ))
        .await
        .expect("the current owner creates through the mixed identity");
    let group_id = group_id_of(&created);

    // GET: the CURRENT owner reads the group through the stale-claim Bot.
    let fetched = fixture
        .service
        .get(GetGroup {
            caller: mixed_caller(STAFF_B, BOT_P, STAFF_A),
            group_id: group_id.clone(),
        })
        .await;
    assert!(
        fetched.is_ok(),
        "the current owner's mixed identity reads the group: {:?}",
        fetched.err()
    );

    // UPDATE: same resolution, including the persisted rename.
    fixture
        .service
        .update(UpdateGroup {
            caller: mixed_caller(STAFF_B, BOT_P, STAFF_A),
            group_id: group_id.clone(),
            patch: GroupPatch {
                name: Some("Renamed by the live owner".into()),
                ..Default::default()
            },
        })
        .await
        .expect("the current owner's mixed identity updates the group");
    let renamed = fixture
        .service
        .get(GetGroup {
            caller: mixed_caller(STAFF_B, BOT_P, STAFF_A),
            group_id: group_id.clone(),
        })
        .await
        .expect("re-read the renamed group");
    match renamed {
        GroupDetail::Collaboration(it) => {
            assert_eq!(it.name.as_deref(), Some("Renamed by the live owner"));
        }
        other => panic!("expected collaboration detail, got {other:?}"),
    }

    // DELETE: the same resolution authorizes the destructive lane.
    let deleted = fixture
        .service
        .delete(DeleteGroup {
            caller: mixed_caller(STAFF_B, BOT_P, STAFF_A),
            group_id,
            acting_bot_id: None,
        })
        .await
        .expect("the current owner's mixed identity deletes the group");
    assert!(deleted.deleted);
}

#[tokio::test]
async fn mixed_identity_read_of_the_former_creator_without_role_is_denied() {
    let fixture = MixedIdentityFixture::new().await;
    fixture
        .public_bot_transferred(BOT_P, STAFF_A, STAFF_B)
        .await;

    let created = fixture
        .service
        .create(bot_originated_public_group(
            mixed_caller(STAFF_B, BOT_P, STAFF_A),
            BOT_P,
        ))
        .await
        .expect("the current owner creates the group");
    let group_id = group_id_of(&created);

    // The public group remains readable WITHOUT any identity (the public
    // visibility arm), but the former creator's ACTING-BOT lane on the same
    // group must still resolve through the live authority and deny.
    let denied = fixture
        .service
        .update(UpdateGroup {
            caller: mixed_caller(STAFF_A, BOT_P, STAFF_A),
            group_id: group_id.clone(),
            patch: GroupPatch {
                name: Some("Hijack".into()),
                ..Default::default()
            },
        })
        .await;
    match denied {
        Err(ApplicationError::Forbidden(message)) => {
            assert!(
                message.contains("may not act as the authenticated Bot"),
                "the denial names the live authority refusal: {message}"
            );
        }
        other => panic!(
            "the former creator must not update through the acting-Bot lane: {other:?}"
        ),
    }
}