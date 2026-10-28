//! Consumer identity-matrix cutover tests for the
//! bcs-app-bot family (rows 1/2 of the mounted-entry
//! table; bot-authority plan Task 6.2/6.3, spec §3.3).
//!
//! `authorize_candidate_perspective` (row 1) and the
//! descriptor patch (row 2) run at the REAL entries
//! with a REAL authority chain; the transfer and
//! withdrawal scenarios are driven through the
//! authority store.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use bcs_app_bot::{BotServiceConfig, BotServiceImpl};
use bcs_bot::{BotControlPlaneCore, BotCore};
use bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_friend::FriendCore;
use bcs_service_api::application::v1::{
    AuthenticatedCaller, AuthenticatedUserIdentity, Bot,
    BotCandidatePurpose, BotPatch, BotService, GetBot, ListBotCandidates,
    ListMyBots, QueryBots, UpdateBot,
};
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::{
    ActorStatus, BotCapabilities, BotControlPlaneCoreService,
};
use bcs_test_support::{
    authority_hook_for, grant_manager_edge, grant_owner_edge,
    revoke_authority_edge,
};

struct Fixture {
    service: BotServiceImpl,
    repo: Arc<MemoryBotRepo>,
    registry: Arc<BotCore>,
    // Holds the per-test persistence root for
    // as long as the fixture lives.
    _temp: tempfile::TempDir,
}

impl Fixture {

    async fn new() -> Self {
        let env = bcs_config::resolve_env_str();
        // The fixture repo persists to a PER-TEST
        // temp dir (same convention as
        // `v1_bot_service.rs` / `authority_mine.rs`);
        // `MemoryBotRepo::new()` would root
        // `bot.json`-shaped storage at the crate
        // directory.
        let temp = tempfile::tempdir().expect("temp dir");
        let repo =
            Arc::new(MemoryBotRepo::with_base_dir(
                temp.path().to_path_buf(),
            ));
        let registry =
            Arc::new(BotCore::with_repo(repo.clone()));
        let providers = Arc::new(MemoryProviderStore::new());
        let control_plane: Arc<dyn BotControlPlaneCoreService> =
            Arc::new(BotControlPlaneCore::new(
                repo.clone(),
                providers.clone(),
                providers,
            ));
        let authority = authority_hook_for(repo.clone());
        let service = BotServiceImpl::new(
            control_plane,
            registry.clone(),
            Arc::new(FriendCore::memory()),
            Arc::new(bcs_test_support::NoopConnectService),
            RecordingCandidateSearch::empty(),
            BotServiceConfig { env },
        )
        .with_bot_authority_hook(authority.clone());
        Self {
            service,
            registry,
            repo,
            _temp: temp,
        }
    }

    async fn add_bot(
        &self,
        bot_id: &str,
        _owner: &str,
        status: ActorStatus,
    ) {
        BotRepoPort::register(&*self.repo,
            bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    ..Default::default()
                },
            )
            .await
            .expect("register fixture bot");
        self.repo
            .update_actor_status(bot_id, status)
            .await
            .expect("offline");
    }
}

/// A recording no-op candidate search: the
/// authorization happens BEFORE the core search.
struct RecordingCandidateSearch {
    queries: std::sync::Mutex<Vec<String>>,
}

impl RecordingCandidateSearch {
    fn empty() -> Arc<Self> {
        Arc::new(Self {
            queries: std::sync::Mutex::new(Vec::new()),
        })
    }
}

use bcs_service_api::BotCandidateSearchCoreService;

#[async_trait::async_trait]
impl BotCandidateSearchCoreService
    for RecordingCandidateSearch
{
    async fn search_candidates(
        &self,
        query: bcs_service_api::BotCandidateSearchQuery,
    ) -> bcs_service_api::BotCandidateSearchCoreResult {
        self.queries
            .lock()
            .expect("queries")
            .push(query.acting_actor_id);
        bcs_service_api::BotCandidateSearchCoreResult {
            hits: Vec::new(),
            mode: bcs_service_api::BotCandidateSearchMode::NameFallback,
        }
    }

    async fn search_candidates_for_legacy(
        &self,
        _query: bcs_service_api::BotCandidateSearchQuery,
    ) -> bcs_service_api::LegacyBotCandidateSearchCoreResult {
        bcs_service_api::LegacyBotCandidateSearchCoreResult {
            result: bcs_service_api::BotCandidateSearchCoreResult {
                hits: Vec::new(),
                mode: bcs_service_api::BotCandidateSearchMode::NameFallback,
            },
            recommend_response: None,
        }
    }
}

fn human_caller(staff_no: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
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

// ── Row 1: the candidate perspective matrix ─────

#[tokio::test]
async fn candidate_perspective_follows_the_current_matrix() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice", ActorStatus::Online).await;
    grant_owner_edge(&fx.repo, "bot-a", "alice").await;
    grant_manager_edge(&fx.repo, "bot-a", "mallory").await;

    // A (owner) / M (manager) / X — the manager may
    // use the Bot's perspective (§8: manager 可使用
    // 对应 Bot 视角).
    for user in ["alice", "mallory"] {
        fx.service
            .list_candidates(ListBotCandidates {
                caller: human_caller(user),
                bot_id: "bot-a".into(),
                purpose: BotCandidatePurpose::Discovery,
                name: None,
                offset: 0,
                limit: 20,
            })
            .await
            .unwrap_or_else(|error| {
                panic!("{user} perspective must pass: {error:?}")
            });
    }
    fx.service
        .list_candidates(ListBotCandidates {
            caller: human_caller("stranger"),
            bot_id: "bot-a".into(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect_err("X is denied");

    // The A→B handover: B takes the owner edge,
    // A keeps the managed whitelist no longer —
    // without ANY edge the perspective entry
    // denies fallback-free.
    grant_owner_edge(&fx.repo, "bot-a", "bob").await;
    revoke_authority_edge(
        &fx.repo,
        "bot-a",
        "alice",
        MemoryAuthorityEdgeKind::Owner,
    )
    .await;
    let a = fx
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("alice"),
            bot_id: "bot-a".into(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await;
    assert!(
        matches!(
            a,
            Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))
        ),
        "A without edges is denied: {a:?}"
    );
    fx.service
        .list_candidates(ListBotCandidates {
            caller: human_caller("bob"),
            bot_id: "bot-a".into(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("B runs the perspective after the handover");

    // M is untouched by A's withdrawal.
    fx.service
        .list_candidates(ListBotCandidates {
            caller: human_caller("mallory"),
            bot_id: "bot-a".into(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("manager perspective stays");
}

// ── Row 2: the descriptor patch follows the CURRENT ROLE ──

#[tokio::test]
async fn descriptor_patch_admits_the_current_owner_or_manager() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice", ActorStatus::Online).await;
    grant_owner_edge(&fx.repo, "bot-a", "alice").await;
    grant_manager_edge(&fx.repo, "bot-a", "mallory").await;

    // The patch is a §8 BUSINESS operation ("Bot
    // 配置 … 使用当前角色"): the CURRENT manager
    // patches exactly like the owner (spec §1
    // goal 1).
    fx.service
        .update(UpdateBot {
            caller: human_caller("mallory"),
            bot_id: "bot-a".into(),
            patch: BotPatch {
                name: Some("manager rename".into()),
                ..Default::default()
            },
        })
        .await
        .expect("the current manager patches");

    // The current owner patches.
    fx.service
        .update(UpdateBot {
            caller: human_caller("alice"),
            bot_id: "bot-a".into(),
            patch: BotPatch {
                name: Some("renamed".into()),
                ..Default::default()
            },
        })
        .await
        .expect("the current owner patches");

    // A→B: the OLD owner loses the patch right even
    // if its Bot token still exists (no current
    // edge at all — spec §3.3); B gains it.
    grant_owner_edge(&fx.repo, "bot-a", "bob").await;
    revoke_authority_edge(
        &fx.repo,
        "bot-a",
        "alice",
        MemoryAuthorityEdgeKind::Owner,
    )
    .await;
    let old_owner = fx
        .service
        .update(UpdateBot {
            caller: human_caller("alice"),
            bot_id: "bot-a".into(),
            patch: BotPatch {
                name: Some("stale".into()),
                ..Default::default()
            },
        })
        .await;
    assert!(
        matches!(
            old_owner,
            Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))
        ),
        "the old owner (no edge) cannot patch: {old_owner:?}"
    );
    fx.service
        .update(UpdateBot {
            caller: human_caller("bob"),
            bot_id: "bot-a".into(),
            patch: BotPatch {
                name: Some("fresh owner".into()),
                ..Default::default()
            },
        })
        .await
        .expect("B patches after the handover");
}

// ── Trash guards: non-perspective entries don't change ──

#[tokio::test]
async fn query_and_get_stay_public_reads() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice", ActorStatus::Online).await;
    let bots = fx
        .service
        .query(QueryBots {
            caller: human_caller("stranger"),
            bot_ids: vec!["bot-a".into()],
        })
        .await
        .expect("query remains a public read");
    let Bot::Physical(physical) = &bots[0] else {
        panic!("expected physical bot");
    };
    assert_eq!(physical.bot_id, "bot-a");
    let bot = fx
        .service
        .get(GetBot {
            caller: human_caller("stranger"),
            bot_id: "bot-a".into(),
        })
        .await
        .expect("get remains a public read");
    assert_eq!(bot.bot_id(), "bot-a");
}

#[tokio::test]
async fn mine_uses_the_controllable_union_not_created_by() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice", ActorStatus::Online).await;
    grant_owner_edge(&fx.repo, "bot-a", "alice").await;
    // The MANAGER of the Bot sees it in its mine
    // list under the manager role (spec §8.0) —
    // never via the historical created_by.
    grant_manager_edge(&fx.repo, "bot-a", "mallory").await;
    let page = fx
        .service
        .list_mine(ListMyBots {
            caller: human_caller("mallory"),
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 50,
        })
        .await
        .expect("manager sees its managed bot");
    assert!(page.total >= 1);
}

// ── Review fix (Critical#1): Human-kind patching ──

#[tokio::test]
async fn human_rows_patch_only_their_own_actor_row() {
    let fx = Fixture::new().await;
    // Seed BOTH Human self rows through the
    // registry (the register path mints
    // `human_{staff}` rows with created_by=self).
    for staff in ["alice", "bob"] {
        self_register_human(&fx, staff).await;
    }

    // CROSS-Human patch: 403 — never a widened gate.
    let cross = fx
        .service
        .update(UpdateBot {
            caller: human_caller("alice"),
            bot_id: "human_bob".into(),
            patch: BotPatch {
                name: Some("hijack".into()),
                ..Default::default()
            },
        })
        .await
        .expect_err("cross-Human patch is denied");
    assert_eq!(cross.code(), "forbidden");

    // SELF patch: the only Human-leg patch.
    let patch = fx
        .service
        .update(UpdateBot {
            caller: human_caller("alice"),
            bot_id: "human_alice".into(),
            patch: BotPatch {
                name: Some("Alice Renamed".into()),
                ..Default::default()
            },
        })
        .await
        .expect("self patch succeeds");
    match &patch {
        Bot::Human(human) => {
            assert_eq!(human.name, "Alice Renamed");
        }
        other => panic!("expected the Human row: {other:?}"),
    }

    // The Human self row still rejects the
    // Human-kind-only fields (input validation
    // unchanged, row 2).
    let invalid = fx
        .service
        .update(UpdateBot {
            caller: human_caller("alice"),
            bot_id: "human_alice".into(),
            patch: BotPatch {
                descriptor: Some(bcs_service_api::application::v1::BotDescriptorPatch {
                    summary: Some("not allowed".into()),
                    ..Default::default()
                }),
                ..Default::default()
            },
        })
        .await
        .expect_err("descriptor is still rejected");
    assert_eq!(invalid.code(), "invalid_bot_kind");
}

async fn self_register_human(fx: &Fixture, staff: &str) {
    // The Human self actor row: `ensure_human_actor`
    // mints `human_{staff}` with
    // `created_by = staff` (the legacy self fact).
    use bcs_service_api::core::registry::BotRegistryCoreService as _;
    fx.registry
        .ensure_human_actor(staff, staff)
        .await
        .expect("register human row");
}
