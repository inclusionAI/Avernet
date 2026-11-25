//! Bot authority cutover (bot-authority plan
//! Task 6, carry B): the visibility READ gate
//! (`get_bot` / `get_visibility`) consults the
//! CURRENT live management edge — a manager of a
//! controlled Bot may read its visibility info, and
//! the pure legacy read fallback (`created_by: None
//! → allow`, or a `created_by` row without a live
//! edge) no longer silently authorizes. A failing
//! authority read propagates as an internal error.
//!
//! Matrix at the real application entry: A(owner)
//! / M(manager) / X(unrelated), plus the
//! A→B handover (the old owner is fallback-free
//! denied).

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use bcs_bot::Bot;
use bcs_bot_store::MemoryBotRepo;
use bcs_domain::BotCapabilities;
use bcs_service_api::BotQueryService;
use bcs_service_api::{
    ActorStatus, BotDetailCommand, BotRegistryCoreService,
    BotUseCaseError, ServiceError,
};
use bcs_test_support::LazySeedAuthorityHook;
use tempfile::TempDir;

struct Fixture {
    registry: Arc<BotCore>,
    _bot_repo: Arc<MemoryBotRepo>,
    authority: Arc<LazySeedAuthorityHook>,
    service: Bot,
}

use bcs_bot::BotCore;

fn fixture() -> Fixture {
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(
        TempDir::new()
            .expect("temp dir")
            .path()
            .to_path_buf(),
    ));
    let registry = Arc::new(BotCore::with_repo(bot_repo.clone()));
    let authority = bcs_test_support::lazy_authority_chain().hook;
    let service = Bot::new(registry.clone() as Arc<dyn BotRegistryCoreService>)
        .with_bot_authority(authority.clone());
    Fixture {
        registry,
        _bot_repo: bot_repo,
        authority,
        service,
    }
}

impl Fixture {
    async fn register_bot(&self, bot_id: &str, visibility: &str) {
        self.registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: visibility.to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .expect("register fixture bot");
    }
}

fn human(staff: &str) -> String {
    format!("human_{staff}")
}

#[tokio::test]
async fn visibility_read_follows_the_current_control_matrix() {
    let fixture = fixture();
    fixture.register_bot("bot-1", "protected").await;
    // A(owner) and M(manager): both read the
    // visibility info through their CURRENT edge
    // (the legacy created_by never enters the
    // judgment).
    fixture.authority.seed_owner("bot-1", "alice");
    fixture.authority.seed_manager("bot-1", "morgan");

    let owner = fixture
        .service
        .get_visibility(bcs_service_api::BotVisibilityQueryCommand {
            caller_actor_id: Some(human("alice")),
            bot_id: "bot-1".to_string(),
        })
        .await
        .expect("the CURRENT owner reads visibility");
    assert_eq!(owner.visibility, "protected");

    let manager = fixture
        .service
        .get_visibility(bcs_service_api::BotVisibilityQueryCommand {
            caller_actor_id: Some(human("morgan")),
            bot_id: "bot-1".to_string(),
        })
        .await
        .expect("a CURRENT manager reads visibility");
    assert_eq!(manager.visibility, "protected");

    let denied = fixture
        .service
        .get_visibility(bcs_service_api::BotVisibilityQueryCommand {
            caller_actor_id: Some(human("unrelated")),
            bot_id: "bot-1".to_string(),
        })
        .await
        .expect_err("an unrelated Human is denied");
    assert!(matches!(denied, BotUseCaseError::Forbidden(_)));

    // Detail read shares the gate.
    let denied = fixture
        .service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some(human("unrelated")),
            bot_id: "bot-1".to_string(),
        })
        .await
        .expect_err("an unrelated Human is denied");
    assert!(matches!(denied, BotUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn legacy_read_labels_never_silently_authorize() {
    let fixture = fixture();
    fixture.register_bot("bot-legacy", "protected").await;
    // No authority edge was EVER granted: the
    // "uncontrolled legacy Bot" read fallback grants
    // nothing.
    let denied = fixture
        .service
        .get_visibility(bcs_service_api::BotVisibilityQueryCommand {
            caller_actor_id: Some(human("alice")),
            bot_id: "bot-legacy".to_string(),
        })
        .await
        .expect_err("the legacy read fallback is gone");
    assert!(matches!(denied, BotUseCaseError::Forbidden(_)));

    // The Handover: after the owner edge moved to
    // bob (the lazy seed replaces it, spec §3.2.1),
    // alice no longer reads; bob does.
    fixture.authority.seed_owner("bot-legacy", "alice");
    fixture.authority.seed_owner("bot-legacy", "bob");
    fixture
        .registry
        .update_actor_status("bot-legacy", ActorStatus::Online)
        .await
        .expect("activate");
    let denied = fixture
        .service
        .get_visibility(bcs_service_api::BotVisibilityQueryCommand {
            caller_actor_id: Some(human("bob")),
            bot_id: "bot-legacy".to_string(),
        })
        .await
        .expect("the CURRENT owner reads");
    assert_eq!(denied.visibility, "protected");
}

#[tokio::test]
async fn failing_authority_read_is_internal_never_a_deny() {
    let fixture = fixture();
    fixture.register_bot("bot-1", "protected").await;
    let failing = Bot::new(
        fixture.registry.clone() as Arc<dyn BotRegistryCoreService>
    )
    .with_bot_authority(bcs_test_support::failing_authority_hook());

    let error = failing
        .get_visibility(bcs_service_api::BotVisibilityQueryCommand {
            caller_actor_id: Some(human("alice")),
            bot_id: "bot-1".to_string(),
        })
        .await
        .expect_err("a failing authority read propagates");
    assert!(
        matches!(
            error,
            BotUseCaseError::Service(ServiceError::InternalError(_))
        ),
        "got {error:?}"
    );
}
