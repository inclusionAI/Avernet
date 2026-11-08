//! Spec §10.2a on the legacy `/bots/my` entry: inside the
//! declared migration window the controllable union falls back
//! to a byte-exact stored-creator arm for Bots the lazy sweep
//! has not initialized yet; outside the window the page carries
//! only the current edge state (plus the Human self row).
#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use bcs_bot::{Bot, BotControlPlaneCore, BotCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_service_api::application::MyBotsCommand;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, OwnershipInitialization,
};
use bcs_service_api::types::BotAccessRelation;
use bcs_service_api::{BotCapabilities, BotControlPlaneCoreService, BotQueryService, BotRegistryCoreService};

async fn fixture() -> (
    tempfile::TempDir,
    Arc<MemoryBotRepo>,
    Arc<dyn BotRegistryCoreService>,
    Arc<dyn BotControlPlaneCoreService>,
) {
    let temp = tempfile::tempdir().expect("temp dir");
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let providers = Arc::new(MemoryProviderStore::new());
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(bot_repo.clone()));
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        bot_repo.clone(),
        providers.clone(),
        providers.clone(),
    ));
    (temp, bot_repo, registry, control_plane)
}

fn make_service(
    registry: &Arc<dyn BotRegistryCoreService>,
    control_plane: &Arc<dyn BotControlPlaneCoreService>,
    legacy_window: bool,
) -> Bot {
    Bot::new(registry.clone())
        .with_control_plane(control_plane.clone())
        .with_legacy_created_by_fallback(legacy_window)
}

async fn mine(service: &Bot) -> Vec<String> {
    let mut ids: Vec<String> = service
        .list_my_bots(MyBotsCommand {
            staff_no: "alice".to_string(),
            offset: 0,
            limit: 20,
            active_only: false,
        })
        .await
        .expect("my bots")
        .items
        .iter()
        .map(|view| view.bot_uuid.clone())
        .collect();
    ids.sort();
    ids
}

#[tokio::test]
async fn bots_my_falls_back_to_legacy_creator_inside_the_window() {
    let (_temp, bot_repo, registry, control_plane) = fixture().await;
    // The already-initialized Bot (edge minted) is always present.
    bot_repo
        .create_registration_if_absent_with_ownership(
            "bot-initialized".to_string(),
            BotCapabilities {
                name: Some("Initialized".to_string()),
                ..Default::default()
            },
            "alice",
            "initialized-token",
            OwnershipInitialization {
                owner_user_id: "alice".to_string(),
                mutation: AuthorityMutation {
                    mutation_id: "window-init".to_string(),
                    actor: AuthorityActor::Human {
                        user_id: "alice".to_string(),
                    },
                },
            },
        )
        .await
        .expect("initialized composite");
    // The legacy Bot (stored creator only, version 0, no edges)
    // is the one the original report missed from `/bots/my`.
    registry
        .register_with_owner_and_token(
            "bot-legacy".to_string(),
            BotCapabilities {
                name: Some("Legacy".to_string()),
                ..Default::default()
            },
            "alice",
            "legacy-token",
        )
        .await
        .expect("legacy register");

    let strict = mine(&make_service(&registry, &control_plane, false)).await;
    assert_eq!(
        strict,
        vec!["bot-initialized".to_string(), "human_alice".to_string()],
        "outside the window only the current edge state shows"
    );

    let windowed = mine(&make_service(&registry, &control_plane, true)).await;
    assert_eq!(
        windowed,
        vec![
            "bot-initialized".to_string(),
            "bot-legacy".to_string(),
            "human_alice".to_string(),
        ],
        "inside the window the byte-exact stored-creator Bot joins"
    );

    // Once initialized (any path — sweep, bcs-admin, or a
    // trusted new registration), the Bot never double-lists
    // through the legacy arm.
    bot_repo
        .initialize_ownership(
            "bot-legacy",
            "alice",
            AuthorityMutation {
                mutation_id: "legacy-init".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "alice".to_string(),
                },
            },
        )
        .await
        .expect("initialize legacy");
    let after = mine(&make_service(&registry, &control_plane, true)).await;
    assert_eq!(
        after,
        vec![
            "bot-initialized".to_string(),
            "bot-legacy".to_string(),
            "human_alice".to_string(),
        ],
        "initialized Bot stays exactly once via the edge arm"
    );

    // Role-read parity inside the window: the page's legacy
    // entry already reads as Owner through the shim (spec
    // §10.2a), never as manager or owner-of-default-Projection.
    assert_eq!(
        repo_role(&bot_repo, "human_alice", "bot-legacy").await,
        Some(BotAccessRelation::Owner),
        "the summary marker behind the fallback row stays Owner"
    );
}

async fn repo_role(
    repo: &Arc<MemoryBotRepo>,
    user: &str,
    bot: &str,
) -> Option<BotAccessRelation> {
    BotAuthorityRepoPort::role(repo.as_ref(), user, bot)
        .await
        .expect("role read")
}
