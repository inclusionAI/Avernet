//! Task 9 mine union-projection suite (spec §7): the `GET /bots/mine`
//! contract over the owner ∪ manager authority union.
//!
//! RED-first (plan Task 9): every case below drives the real application
//! façade over the real memory store and the real strict authority chain,
//! asserts the WIRE shape (`data.items[].access_relation` required, Bot
//! fields flattened — never a nested `bot` object, labels ∈ {owner,manager}),
//! and proves the mine path consults the controllable union read — NOT the
//! legacy `list_by_creator` (which stays untouched, creator-literal, and
//! is asserted as such in `legacy_creator_query_stays_creation_literal`).
//!
//! The gray fixture disables the legacy query by panic: any mine path that
//! "queries the old list first, then stitches" would abort the whole suite
//! — an independent controllable read keeps every case green.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use async_trait::async_trait;
use bcs_app_bot::{BotAuthorityHookImpl, BotServiceConfig, BotServiceImpl};
use bcs_bot::{BotControlPlaneCore, BotCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_service_api::application::v1::{BotKind, BotReachability, BotService, ListMyBots};
use bcs_service_api::port::repo::BotControlPlaneRepoPort;
use bcs_service_api::types::BotAccessRelation;
use bcs_service_api::{
    ActorStatus, BotControlPlaneCoreService, BotControlPlaneOwnedQuery, BotRegistryCoreService,
    BotRepoPort, ServiceResult,
};

struct Fixture {
    service: BotServiceImpl,
    repo: Arc<MemoryBotRepo>,
    _temp: tempfile::TempDir,
}

impl Fixture {
    async fn new() -> Self {
        let temp = tempfile::tempdir().expect("temp dir");
        let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
        let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
        let providers = Arc::new(MemoryProviderStore::new());
        let core: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
            repo.clone(),
            providers.clone(),
            providers,
        ));
        let authority = Arc::new(BotAuthorityHookImpl::new(Arc::new(
            bcs_edge_permission::authority::BotAuthorityCoreServiceImpl::new(repo.clone()),
        )));
        let service = BotServiceImpl::new(
            // The gray fixture: every control-plane read delegates to the
            // real core, but the LEGACY creator query panics — the mine
            // path must be reachable without ever consulting it.
            Arc::new(LegacyBanningCore::new(core)),
            registry,
            Arc::new(bcs_friend::FriendCore::memory()),
            Arc::new(bcs_test_support::NoopConnectService),
            Arc::new(EmptyCandidateSearch),
            authority,
            BotServiceConfig {
                env: bcs_config::resolve_env_str(),
            },
        );
        Self {
            service,
            repo,
            _temp: temp,
        }
    }

    async fn add_bot(&self, bot_id: &str, created_by: &str, visibility: &str, status: ActorStatus) {
        self.repo
            .register_with_owner_and_token(
                bot_id.to_string(),
                bcs_service_api::BotCapabilities {
                    name: Some(bot_id.to_string()),
                    summary: Some(format!("summary-{bot_id}")),
                    visibility: visibility.to_string(),
                    ..Default::default()
                },
                created_by,
                &format!("token-{bot_id}"),
            )
            .await
            .expect("register bot");
        self.repo
            .update_actor_status(bot_id, status)
            .await
            .expect("update actor status");
    }

    /// Initialize a bot's ownership with `owner` and pin its `created_at`
    /// for deterministic contract ordering.
    async fn seed_owner(&self, bot_id: &str, owner: &str, created_at: u64) {
        self.repo
            .seed_authority_owned(bot_id, owner)
            .await
            .expect("seed owner edge");
        self.repo
            .set_control_plane_created_at(bot_id, created_at)
            .await;
    }

    async fn seed_manager(&self, bot_id: &str, user: &str, kind: &str, id: &str) {
        self.repo
            .seed_authority_manager_source(bot_id, user, kind, id)
            .await
            .expect("seed manager edge");
    }

    fn env(&self) -> String {
        bcs_config::resolve_env_str()
    }

    async fn mine(&self, staff_no: &str) -> Result<
        bcs_service_api::application::v1::Page<
            bcs_service_api::application::v1::MyBot,
        >,
        bcs_service_api::application::v1::ApplicationError,
    > {
        self.mine_query(staff_no, None, None, None, None, 0, 20).await
    }

    #[allow(clippy::too_many_arguments)]
    async fn mine_query(
        &self,
        staff_no: &str,
        kind: Option<BotKind>,
        name: Option<String>,
        status: Option<bcs_service_api::application::v1::BotStatus>,
        reachability: Option<BotReachability>,
        offset: u64,
        limit: u64,
    ) -> Result<
        bcs_service_api::application::v1::Page<
            bcs_service_api::application::v1::MyBot,
        >,
        bcs_service_api::application::v1::ApplicationError,
    > {
        self.service
            .list_mine(ListMyBots {
                caller: human_caller(staff_no),
                kind,
                name,
                status,
                reachability,
                offset,
                limit,
            })
            .await
    }

    /// The raw control-plane tag of one page item.
    fn relation_tags(
        page: &bcs_service_api::application::v1::Page<
            bcs_service_api::application::v1::MyBot,
        >,
    ) -> Vec<&'static str> {
        page.items
            .iter()
            .map(|item| match item.access_relation {
                BotAccessRelation::Owner => "owner",
                BotAccessRelation::Manager => "manager",
            })
            .collect()
    }
}

/// The candidate-search core is never used by mine; an empty double only
/// keeps the façade constructor complete.
struct EmptyCandidateSearch;

#[async_trait]
impl bcs_service_api::BotCandidateSearchCoreService for EmptyCandidateSearch {
    async fn search_candidates(
        &self,
        _query: bcs_service_api::BotCandidateSearchQuery,
    ) -> bcs_service_api::BotCandidateSearchCoreResult {
        bcs_service_api::BotCandidateSearchCoreResult {
            hits: Vec::new(),
            mode: bcs_service_api::BotCandidateSearchMode::EmptyQuery,
        }
    }

    async fn search_candidates_for_legacy(
        &self,
        _query: bcs_service_api::BotCandidateSearchQuery,
    ) -> bcs_service_api::LegacyBotCandidateSearchCoreResult {
        panic!("mine never calls the legacy candidate-search entry point")
    }
}

/// The gray fixture: delegate every controllable read, PANIC on the
/// legacy creator query.
struct LegacyBanningCore {
    inner: Arc<dyn BotControlPlaneCoreService>,
}

impl LegacyBanningCore {
    fn new(inner: Arc<dyn BotControlPlaneCoreService>) -> Self {
        Self { inner }
    }
}

#[async_trait]
impl BotControlPlaneCoreService for LegacyBanningCore {
    async fn get_record(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneRecord>> {
        self.inner.get_record(bot_id, env).await
    }

    async fn get(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
        self.inner.get(bot_id, env).await
    }

    async fn get_by_ids(
        &self,
        bot_ids: &[String],
        env: &str,
    ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
        self.inner.get_by_ids(bot_ids, env).await
    }

    async fn list_candidates(
        &self,
        query: bcs_service_api::BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<bcs_service_api::BotControlPlaneCandidate>, u64)> {
        self.inner.list_candidates(query).await
    }

    async fn list_controllable(
        &self,
        query: bcs_service_api::BotControllableQuery,
    ) -> ServiceResult<Vec<bcs_service_api::ControllableBotView>> {
        self.inner.list_controllable(query).await
    }

    async fn list_by_creator(
        &self,
        _query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
        panic!("the mine path must read the controllable union, never the legacy creator query");
    }

    async fn patch(
        &self,
        bot_id: &str,
        env: &str,
        patch: bcs_service_api::BotControlPlanePatch,
        operation: bcs_service_api::types::BotOperationContext,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
        self.inner.patch(bot_id, env, patch, operation).await
    }
}

fn human_caller(staff_no: &str) -> bcs_service_api::application::v1::AuthenticatedCaller {
    bcs_service_api::application::v1::AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
        user: Some(bcs_service_api::application::v1::AuthenticatedUserIdentity {
            id: staff_no.to_string(),
            username: staff_no.to_string(),
            display_name: Some(staff_no.to_string()),
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}
// ---------------------------------------------------------------------------
// Step-1 RED suite: the wire contract over the owner/manager union.
// ---------------------------------------------------------------------------

/// The union base of most cases: deterministic `created_at` pins drive the
/// contract order `created_at DESC, bot_id ASC`.
async fn controllable_union_fixture() -> Fixture {
    let fixture = Fixture::new().await;
    // bot-owned: created by user-b, ownership TRANSFERRED to user-a → the
    // mine label is `owner` even though created_by stays user-b (spec §7.1).
    fixture
        .add_bot("bot-owned", "user-b", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("bot-owned", "user-a", 300).await;
    // bot-managed: created by user-a, ownership moved to user-c; user-a
    // kept a manager edge → the label is `manager`.
    fixture
        .add_bot("bot-managed", "user-a", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("bot-managed", "user-c", 200).await;
    fixture
        .seed_manager("bot-managed", "user-a", "direct", "manual")
        .await;
    // bot-released: created by user-a, transferred away, NO retained role:
    // creation provenance alone shows NOTHING in mine (spec §12.2).
    fixture
        .add_bot("bot-released", "user-a", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("bot-released", "user-c", 150).await;
    fixture
}

/// `<user>`'s mine returns exactly the current owner/manager union plus
/// the Human self row, with REQUIRED, flattened wire labels.
#[tokio::test]
async fn mine_projects_the_wire_contract_with_required_relation_labels() {
    let fixture = controllable_union_fixture().await;
    // Materialize the caller's own Human row first (idempotent), then pin
    // its created_at so deterministic ordering covers it too.
    fixture
        .mine_query("user-a", Some(BotKind::Human), None, None, None, 0, 20)
        .await
        .expect("materialize the human self row");
    fixture
        .repo
        .set_control_plane_created_at("human_user-a", 50)
        .await;

    let page = fixture.mine("user-a").await.expect("list mine");
    assert_eq!(page.total, 3);
    let ids = page
        .items
        .iter()
        .map(|item| item.bot.bot_id().to_string())
        .collect::<Vec<_>>();
    assert_eq!(ids, vec!["bot-owned", "bot-managed", "human_user-a"]);
    assert_eq!(
        Fixture::relation_tags(&page),
        vec!["owner", "manager", "owner"],
        "owner wins over manager; the self Human row is always owner"
    );

    // Direct WIRE assertions: flattened Bot fields — there is never a
    // nested `bot` object — plus the required access_relation label.
    let body = serde_json::json!({ "data": &page });
    assert_eq!(body["data"]["items"][0]["access_relation"], "owner");
    assert_eq!(body["data"]["items"][1]["access_relation"], "manager");
    assert_eq!(body["data"]["items"][2]["access_relation"], "owner");
    assert_eq!(body["data"]["items"][0]["bot_id"], "bot-owned");
    assert_eq!(body["data"]["items"][0]["kind"], "bot");
    assert_eq!(
        body["data"]["items"][0]["created_by"], "user-b",
        "created_by stays the historical creator; the label tracks authority"
    );
    assert_eq!(body["data"]["items"][1]["created_by"], "user-a");
    assert_eq!(body["data"]["items"][2]["kind"], "human");
    assert!(
        body["data"]["items"][0].get("bot").is_none(),
        "mine items flatten the Bot fields; no nested bot object"
    );
    assert!(
        body["data"]["items"]
            .as_array()
            .expect("items array")
            .iter()
            .all(|item| matches!(item["access_relation"].as_str(), Some("owner" | "manager"))),
        "every label ∈ {{owner, manager}} — never missing, never anything else"
    );
    assert_eq!(body["data"]["total"], 3);
    assert_eq!(body["data"]["offset"], 0);
    assert_eq!(body["data"]["limit"], 20);

    // `bot-released` (created by user-a, authority neither owned nor
    // managed anymore) never appears in any page.
    for item in &page.items {
        assert_ne!(item.bot.bot_id(), "bot-released");
    }
}

/// One Bot hit by BOTH the owner edge and three manager sources projects
/// ONE row labeled owner; the three manager sources alone project ONE row
/// labeled manager — the union is deduplicated, not concatenated.
#[tokio::test]
async fn mine_deduplicates_multi_source_bots_with_owner_priority() {
    let fixture = Fixture::new().await;
    fixture
        .add_bot("bot-multi", "user-b", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("bot-multi", "user-a", 200).await;
    fixture
        .seed_manager("bot-multi", "user-a", "direct", "manual")
        .await;
    fixture
        .seed_manager("bot-multi", "user-a", "team", "team-7")
        .await;
    fixture
        .seed_manager(
            "bot-multi",
            "user-a",
            "ownership_transfer",
            "transfer-9",
        )
        .await;
    fixture
        .add_bot("bot-three-managers", "user-b", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("bot-three-managers", "user-c", 100).await;
    fixture
        .seed_manager("bot-three-managers", "user-a", "direct", "manual")
        .await;
    fixture
        .seed_manager("bot-three-managers", "user-a", "team", "team-7")
        .await;
    fixture
        .seed_manager(
            "bot-three-managers",
            "user-a",
            "ownership_transfer",
            "transfer-9",
        )
        .await;

    let page = fixture
        .mine_query("user-a", Some(BotKind::Bot), None, None, None, 0, 20)
        .await
        .expect("list mine multi-source bots");
    assert_eq!(page.total, 2);
    let ids = page
        .items
        .iter()
        .map(|item| item.bot.bot_id().to_string())
        .collect::<Vec<_>>();
    assert_eq!(ids, vec!["bot-multi", "bot-three-managers"]);
    assert_eq!(Fixture::relation_tags(&page), vec!["owner", "manager"]);
}

/// The legacy creator query keeps其 literal meaning (creation provenance
/// only) — it neither adopts the union nor influences it.
#[tokio::test]
async fn legacy_creator_query_stays_creation_literal() {
    let fixture = controllable_union_fixture().await;
    let env = fixture.env();
    let legacy = fixture
        .repo
        .list_control_plane_by_creator(BotControlPlaneOwnedQuery {
            created_by: "user-a".to_string(),
            env: env.clone(),
            kind: None,
            name: None,
            status: None,
        })
        .await
        .expect("legacy creator query");
    let legacy_ids = legacy
        .iter()
        .map(|record| record.bot_id.as_str())
        .collect::<Vec<_>>();
    assert_eq!(
        legacy_ids,
        vec!["bot-managed", "bot-released"],
        "list_by_creator stays creation-literal: NOT the manager union"
    );

    // And the union (mine) shows bot-managed but NOT bot-released: the two
    // contracts stay cleanly separated (spec §7.2).
    let mine = fixture
        .mine_query("user-a", Some(BotKind::Bot), None, None, None, 0, 20)
        .await
        .expect("list mine");
    let mine_ids = mine
        .items
        .iter()
        .map(|item| item.bot.bot_id().to_string())
        .collect::<Vec<_>>();
    assert_eq!(mine_ids, vec!["bot-owned", "bot-managed"]);
}

/// kind / name / status / reachability filters apply uniformly over the
/// union + self row; `kind=human` with reachability stays an empty page.
#[tokio::test]
async fn mine_applies_unified_filters_over_the_union() {
    let fixture = Fixture::new().await;
    fixture
        .add_bot("bot-a-online", "user-b", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("bot-a-online", "user-a", 300).await;
    fixture
        .repo
        .register_streaming_connection("bot-a-online".to_string())
        .await
        .expect("connect bot-a-online");
    fixture
        .add_bot("zzz-hidden", "user-b", "public", ActorStatus::Hidden)
        .await;
    fixture.seed_owner("zzz-hidden", "user-a", 200).await;
    fixture
        .add_bot("manager-bot", "user-c", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("manager-bot", "user-b", 100).await;
    fixture
        .seed_manager("manager-bot", "user-a", "direct", "manual")
        .await;
    fixture
        .mine("user-a")
        .await
        .expect("materialize the human self row");

    // kind=bot keeps only physical members.
    let page = fixture
        .mine_query("user-a", Some(BotKind::Bot), None, None, None, 0, 20)
        .await
        .expect("kind=bot");
    assert_eq!(page.items.iter().map(bot_id_of).collect::<Vec<_>>(), vec![
        "bot-a-online", "zzz-hidden", "manager-bot"
    ]);
    assert_eq!(page.total, 3);

    // kind=human keeps only the self row.
    let page = fixture
        .mine_query("user-a", Some(BotKind::Human), None, None, None, 0, 20)
        .await
        .expect("kind=human");
    assert_eq!(page.items.iter().map(bot_id_of).collect::<Vec<_>>(), vec!["human_user-a"]);
    assert_eq!(page.total, 1);

    // name substring is trimmed and case-insensitive (match the online
    // pair via "A-O").
    let page = fixture
        .mine_query("user-a", Some(BotKind::Bot), Some(" a-o ".to_string()), None, None, 0, 20)
        .await
        .expect("name filter");
    assert_eq!(page.items.iter().map(bot_id_of).collect::<Vec<_>>(), vec!["bot-a-online"]);

    // status=hidden keeps only the hidden member.
    let page = fixture
        .mine_query("user-a", Some(BotKind::Bot), None, Some(bcs_service_api::application::v1::BotStatus::Hidden), None, 0, 20)
        .await
        .expect("status filter");
    assert_eq!(page.items.iter().map(bot_id_of).collect::<Vec<_>>(), vec!["zzz-hidden"]);

    // reachability applies to physical Bots only: only the online,
    // streaming-connected member is reachable.
    let page = fixture
        .mine_query("user-a", None, None, None, Some(BotReachability::Reachable), 0, 20)
        .await
        .expect("reachability=reachable");
    assert_eq!(page.items.iter().map(bot_id_of).collect::<Vec<_>>(), vec!["bot-a-online"]);
    assert_eq!(page.total, 1);
    assert_eq!(page.items[0].access_relation, BotAccessRelation::Owner);

    // kind=human + reachability is an empty page (human rows are never
    // reachable) — but the page metadata is well formed.
    let page = fixture
        .mine_query("user-a", Some(BotKind::Human), None, None, Some(BotReachability::Reachable), 0, 20)
        .await
        .expect("kind=human + reachability");
    assert_eq!(page.total, 0);
    assert!(page.items.is_empty());
}

fn bot_id_of(item: &bcs_service_api::application::v1::MyBot) -> &str {
    item.bot.bot_id()
}

/// Paging slices the SORTED union; total covers the whole candidate set
/// and an off-the-end page is empty without truncating `total`.
#[tokio::test]
async fn mine_pages_the_union_with_full_total() {
    let fixture = Fixture::new().await;
    // Owned and managed rows interleave by pinned created_at.
    fixture
        .add_bot("owned-1", "user-b", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("owned-1", "user-a", 400).await;
    fixture
        .add_bot("managed-1", "user-b", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("managed-1", "user-c", 300).await;
    fixture
        .seed_manager("managed-1", "user-a", "direct", "manual")
        .await;
    fixture
        .add_bot("owned-2", "user-b", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("owned-2", "user-a", 200).await;
    fixture
        .add_bot("managed-2", "user-b", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("managed-2", "user-c", 100).await;
    fixture
        .seed_manager("managed-2", "user-a", "team", "team-7")
        .await;

    let page = fixture
        .mine_query("user-a", Some(BotKind::Bot), None, None, None, 0, 2)
        .await
        .expect("first page");
    assert_eq!(
        page.items.iter().map(bot_id_of).collect::<Vec<_>>(),
        vec!["owned-1", "managed-1"]
    );
    assert_eq!(Fixture::relation_tags(&page), vec!["owner", "manager"]);
    assert_eq!(page.total, 4);

    let page = fixture
        .mine_query("user-a", Some(BotKind::Bot), None, None, None, 2, 2)
        .await
        .expect("second page");
    assert_eq!(
        page.items.iter().map(bot_id_of).collect::<Vec<_>>(),
        vec!["owned-2", "managed-2"]
    );
    assert_eq!(Fixture::relation_tags(&page), vec!["owner", "manager"]);
    assert_eq!(page.total, 4);

    let page = fixture
        .mine_query("user-a", Some(BotKind::Bot), None, None, None, 4, 2)
        .await
        .expect("empty tail page");
    assert!(page.items.is_empty());
    assert_eq!(page.total, 4, "the empty tail page keeps the full total");
    assert_eq!(page.offset, 4);
    assert_eq!(page.limit, 2);
}

/// Mine materializes the caller's Human row idempotently, but pagination
/// validation precedes ANY materialization, and callers without a trusted
/// User are denied.
#[tokio::test]
async fn mine_materializes_the_caller_after_validation_only() {
    let fixture = Fixture::new().await;
    assert!(fixture.repo.get("human_user-x").await.is_none());

    let error = fixture
        .mine_query("user-x", None, None, None, None, 0, 0)
        .await
        .expect_err("invalid pagination");
    assert_eq!(error.code(), "invalid_request");
    assert!(fixture.repo.get("human_user-x").await.is_none());

    // Materialization uses the identity-name fallback chain.
    let mut caller = human_caller("user-x");
    caller
        .user
        .as_mut()
        .expect("human caller")
        .display_name = Some(" Display Name ".to_string());
    let page = fixture
        .service
        .list_mine(ListMyBots {
            caller,
            kind: Some(BotKind::Human),
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("list mine");
    assert_eq!(page.total, 1);
    assert_eq!(bot_id_of(&page.items[0]), "human_user-x");
    assert_eq!(page.items[0].access_relation, BotAccessRelation::Owner);
    let stored = fixture
        .repo
        .get("human_user-x")
        .await
        .expect("materialized human actor");
    assert_eq!(stored.capabilities.name.as_deref(), Some("Display Name"));

    let error = fixture
        .service
        .list_mine(ListMyBots {
            caller: bcs_service_api::application::v1::AuthenticatedCaller {
                tenant: Some("tenant-1".into()),
                user: None,
                bot: Some(bcs_service_api::application::v1::AuthenticatedBotIdentity {
                    bot_uuid: "bot-1".into(),
                    owner_id: "user-a".into(),
                    app_id: 1,
                    agent_code: "agent".into(),
                }),
                app: None,
                access_key: None,
            },
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect_err("bot-only callers cannot use mine");
    assert_eq!(error.code(), "forbidden");

    // Tenantless Humans keep the same mine contract (the tenant was never
    // an authority input).
    let mut tenantless = human_caller("user-x");
    tenantless.tenant = None;
    let page = fixture
        .service
        .list_mine(ListMyBots {
            caller: tenantless,
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("tenantless Human lists mine");
    assert_eq!(page.total, 1);
    assert_eq!(bot_id_of(&page.items[0]), "human_user-x");
}

/// Strict reads stay fail-closed over the union: a role edge whose target
/// never existed (dangling), or a member whose ownership is uninitialized,
/// fails the WHOLE query — a corrupted set never degrades into a shorter
/// successful list.
#[tokio::test]
async fn mine_fails_closed_on_corrupt_or_uninitialized_authority() {
    let fixture = Fixture::new().await;
    fixture
        .add_bot("bot-ok", "user-b", "public", ActorStatus::Online)
        .await;
    fixture.seed_owner("bot-ok", "user-a", 300).await;
    fixture
        .add_bot("bot-uninitialized", "user-b", "public", ActorStatus::Online)
        .await;
    // An approved manager edge of user-a on a bot whose ownership has
    // never been initialized (version 0).
    fixture
        .seed_manager("bot-uninitialized", "user-a", "direct", "manual")
        .await;

    let error = fixture
        .mine_query("user-a", Some(BotKind::Bot), None, None, None, 0, 20)
        .await
        .expect_err("uninitialized authority fails closed");
    assert_eq!(error.code(), "ownership_not_initialized");

    // A dangling edge — a role row whose target bot has no live row —
    // surfaces as the consistency branch (corrupt authority), never as a
    // silently shortened success.
    let fixture = Fixture::new().await;
    fixture
        .seed_manager("bot-ghost", "user-a", "direct", "manual")
        .await;
    let error = fixture
        .mine_query("user-a", Some(BotKind::Bot), None, None, None, 0, 20)
        .await
        .expect_err("dangling role edge fails closed");
    assert_eq!(error.code(), "internal_error");
}

/// The mine of a Human with no relations at all is just the self row.
#[tokio::test]
async fn mine_of_an_unrelated_human_is_only_the_self_row() {
    let fixture = controllable_union_fixture().await;
    let page = fixture.mine("user-z").await.expect("list mine empty");
    assert_eq!(page.total, 1);
    assert_eq!(bot_id_of(&page.items[0]), "human_user-z");
    assert_eq!(page.items[0].access_relation, BotAccessRelation::Owner);
}
