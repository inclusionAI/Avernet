#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_bot::{BotServiceConfig, BotServiceImpl};
use bcs_bot::{BotControlPlaneCore, BotCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_friend::FriendCore;
use bcs_service_api::application::{ConnectService, RequestDirection, RequestsPage};
use bcs_service_api::application::v1::{
    ApplicationError, Bot, BotCandidatePurpose, BotCandidateSearchMode, BotDescriptorPatch,
    BotKind, BotPatch, BotReachability, BotService, BotStatus, BotVisibility,
    FriendCheckInStrategy, GetBot, ListBotCandidates, ListMyBots, QueryBots,
    SearchBotCandidates, UpdateBot, UserVisibility,
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateSearchCoreResult, BotCandidateSearchCoreService,
    BotCandidateSearchHit, BotCandidateSearchMode as CoreCandidateSearchMode,
    BotCandidateSearchQuery, BotCapabilities, BotControlPlaneCoreService,
    BotControlPlaneDescriptor, BotControlPlaneRecord, BotControlPlaneView, BotRegistryCoreService,
    BotRepoPort, FriendCoreService, LegacyBotCandidateSearchCoreResult, ProviderBotBinding,
    ProviderBotBindingRepoPort, ProviderRecord, ProviderRepoPort, RegisteredBot, ServiceError,
    ServiceResult, Skill,
};
use bcs_domain::edge_permission::{FriendListEntry, PermissionRequest, RequestStatus};
use bcs_test_support::{NoopBotRegistryCoreService, NoopConnectService, NoopFriendCoreService};

struct Fixture {
    service: BotServiceImpl,
    repo: Arc<MemoryBotRepo>,
    friends: Arc<FriendCore>,
    providers: Arc<MemoryProviderStore>,
    _temp: tempfile::TempDir,
}

impl Fixture {
    fn new() -> Self {
        let temp = tempfile::tempdir().expect("temp dir");
        let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
        let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
        let friends = Arc::new(FriendCore::memory());
        let providers = Arc::new(MemoryProviderStore::new());
        let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(
            BotControlPlaneCore::new(repo.clone(), providers.clone(), providers.clone()),
        );
        let env = bcs_config::resolve_env_str();
        let service = BotServiceImpl::new(
            control_plane,
            registry,
            friends.clone(),
            Arc::new(NoopConnectService),
            Arc::new(RecordingCandidateSearch::empty()),
            BotServiceConfig { env: env.clone() },
        )
        .with_bot_authority_hook(bcs_test_support::authority_hook_for(repo.clone()));
        Self {
            service,
            repo,
            friends,
            providers,
            _temp: temp,
        }
    }

    async fn add_bot(&self, bot_id: &str, owner: &str, visibility: &str, status: ActorStatus) {
        self.repo
            .register_with_owner_and_token(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    summary: Some(format!("summary-{bot_id}")),
                    domains: vec!["planning".to_string()],
                    skills: vec![Skill::with_description("plan", "Make a plan")],
                    scopes: vec!["workspace".to_string()],
                    visibility: visibility.to_string(),
                    agent_code: Some(format!("agent-{bot_id}")),
                    ..Default::default()
                },
                owner,
                &format!("token-{bot_id}"),
            )
            .await
            .expect("register bot");
        if !owner.is_empty() {
            bcs_test_support::grant_owner_edge(&self.repo, bot_id, owner)
                .await;
        }
        self.repo
            .update_actor_status(bot_id, status)
            .await
            .expect("update actor status");
    }

    /// A bot the Human CONTROLS under the authority model:
    /// the preset Human registry entry plus the governance
    /// initialize (owner edge + version 1) on top of
    /// `add_bot`'s created_by registration.
    async fn add_controlled_bot(
        &self,
        bot_id: &str,
        owner: &str,
        visibility: &str,
        status: ActorStatus,
    ) {
        self.repo
            .ensure_human_actor(owner, owner)
            .await
            .expect("ensure fixture Human actor row");
        self.add_bot(bot_id, owner, visibility, status).await;
        self.repo
            .initialize_ownership(
                bot_id,
                owner,
                bcs_service_api::types::bot_authority::AuthorityMutation {
                    mutation_id: format!("fixture-{bot_id}"),
                    actor: bcs_service_api::types::bot_authority::AuthorityActor::
                        Human {
                            user_id: owner.to_string(),
                        },
                },
            )
            .await
            .expect("initialize ownership");
    }
}

struct RecordingCandidateSearch {
    queries: Mutex<Vec<BotCandidateSearchQuery>>,
    result: BotCandidateSearchCoreResult,
}

struct RecordingConnectService {
    friends: Mutex<Vec<FriendListEntry>>,
}

impl RecordingConnectService {
    fn new(friends: Vec<FriendListEntry>) -> Self {
        Self {
            friends: Mutex::new(friends),
        }
    }
}

#[async_trait]
impl ConnectService for RecordingConnectService {
    async fn create_connect(
        &self,
        _: &str,
        _: &str,
        _: Option<String>,
        _: Option<bcs_service_api::RequestAuthHeaders>,
    ) -> ServiceResult<bcs_service_api::application::ConnectResult> {
        unreachable!("not used")
    }

    async fn approve(
        &self,
        _: &str,
        _: &str,
        _: Option<bcs_service_api::RequestAuthHeaders>,
    ) -> ServiceResult<Vec<u64>> {
        unreachable!("not used")
    }

    async fn reject(
        &self,
        _: &str,
        _: &str,
        _: Option<String>,
    ) -> ServiceResult<()> {
        unreachable!("not used")
    }

    async fn cancel(&self, _: &str) -> ServiceResult<()> {
        unreachable!("not used")
    }

    async fn get_request(
        &self,
        _: &str,
    ) -> ServiceResult<PermissionRequest> {
        unreachable!("not used")
    }

    async fn revoke_friend(
        &self,
        _: &str,
        _: &str,
        _: Option<bcs_service_api::RequestAuthHeaders>,
    ) -> ServiceResult<Vec<u64>> {
        unreachable!("not used")
    }

    async fn list_friends_paginated(
        &self,
        _: &str,
        _: bcs_service_api::application::connect::FriendListQuery,
    ) -> ServiceResult<bcs_service_api::application::connect::FriendEntriesPage> {
        unreachable!("legacy caller must retain the unpaginated query")
    }

    async fn list_friends(
        &self,
        _: &str,
    ) -> ServiceResult<Vec<FriendListEntry>> {
        Ok(self.friends.lock().expect("friends lock").clone())
    }

    async fn list_requests(
        &self,
        _: &str,
        _: RequestDirection,
        _: Option<RequestStatus>,
        _page: u32,
        _page_size: u32,
    ) -> ServiceResult<RequestsPage> {
        unreachable!("not used")
    }
}

struct RecordingBotControlPlane {
    record: BotControlPlaneRecord,
    queries: Mutex<Vec<bcs_service_api::BotCandidateReadQuery>>,
}

impl RecordingBotControlPlane {
    fn new(record: BotControlPlaneRecord) -> Self {
        Self {
            record,
            queries: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl BotControlPlaneCoreService for RecordingBotControlPlane {
    async fn get_record(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        Ok(Some(self.record.clone()))
    }

    async fn get(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unreachable!("not used")
    }

    async fn get_by_ids(
        &self,
        _bot_ids: &[String],
        _env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unreachable!("not used")
    }

    async fn list_candidates(
        &self,
        query: bcs_service_api::BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<bcs_service_api::BotControlPlaneCandidate>, u64)> {
        self.queries.lock().expect("queries lock").push(query);
        Ok((Vec::new(), 0))
    }

    async fn list_by_creator(
        &self,
        _query: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unreachable!("not used")
    }

    async fn patch(
        &self,
        _bot_id: &str,
        _env: &str,
        _patch: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unreachable!("not used")
    }
}

impl RecordingCandidateSearch {
    fn new(result: BotCandidateSearchCoreResult) -> Self {
        Self {
            queries: Mutex::new(Vec::new()),
            result,
        }
    }

    fn empty() -> Self {
        Self::new(BotCandidateSearchCoreResult {
            hits: Vec::new(),
            mode: CoreCandidateSearchMode::EmptyQuery,
        })
    }
}

#[async_trait]
impl BotCandidateSearchCoreService for RecordingCandidateSearch {
    async fn search_candidates(
        &self,
        query: BotCandidateSearchQuery,
    ) -> BotCandidateSearchCoreResult {
        self.queries
            .lock()
            .expect("candidate search queries lock")
            .push(query);
        self.result.clone()
    }

    async fn search_candidates_for_legacy(
        &self,
        _query: BotCandidateSearchQuery,
    ) -> LegacyBotCandidateSearchCoreResult {
        panic!("OpenAPI V1 must not call the legacy candidate-search entry point")
    }
}

fn search_hit(
    bot_id: &str,
    is_friend: bool,
    score: Option<f64>,
    short_profile: Option<&str>,
) -> BotCandidateSearchHit {
    BotCandidateSearchHit {
        bot: RegisteredBot {
            bot_uuid: bot_id.to_string(),
            capabilities: BotCapabilities {
                name: Some(bot_id.to_string()),
                summary: Some(format!("summary-{bot_id}")),
                skills: vec![Skill::new("plan")],
                domains: vec!["planning".to_string()],
                scopes: vec!["workspace".to_string()],
                visibility: "public".to_string(),
                ..Default::default()
            },
            env: None,
            created_by: None,
            actor_kind: ActorKind::Bot,
            status: ActorStatus::Online,
        },
        is_friend,
        tags: BTreeMap::from([("specialty".to_string(), serde_json::json!("planning"))]),
        score,
        short_profile: short_profile.map(str::to_string),
    }
}

struct AuthorizationProbeCore {
    record: BotControlPlaneRecord,
}

#[async_trait]
impl BotControlPlaneCoreService for AuthorizationProbeCore {
    async fn get_record(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        Ok(Some(self.record.clone()))
    }

    async fn get(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "Provider hydration must happen after authorization".to_string(),
        ))
    }

    async fn get_by_ids(
        &self,
        _bot_ids: &[String],
        _env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unreachable!("not used by authorization-priority test")
    }

    async fn list_candidates(
        &self,
        _query: bcs_service_api::BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<bcs_service_api::BotControlPlaneCandidate>, u64)> {
        unreachable!("not used by authorization-priority test")
    }

    async fn list_by_creator(
        &self,
        _query: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unreachable!("not used by authorization-priority test")
    }

    async fn patch(
        &self,
        _bot_id: &str,
        _env: &str,
        _patch: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unreachable!("not used by authorization-priority test")
    }
}

fn human_caller(staff_no: &str) -> bcs_service_api::application::v1::AuthenticatedCaller {
    bcs_service_api::application::v1::AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
        user: Some(
            bcs_service_api::application::v1::AuthenticatedUserIdentity {
                id: staff_no.to_string(),
                username: staff_no.to_string(),
                display_name: None,
                full_name: None,
            },
        ),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn bot_only_caller(bot_uuid: &str) -> bcs_service_api::application::v1::AuthenticatedCaller {
    bcs_service_api::application::v1::AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
        user: None,
        bot: Some(bcs_service_api::application::v1::AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: "staff-1".into(),
            app_id: 1,
            agent_code: "agent".into(),
        }),
        app: None,
        access_key: None,
    }
}

/// Version-initialized Bots on the fixture's shared authority state.
async fn initialized_count(repo: &MemoryBotRepo) -> usize {
    repo.authority_state()
        .read()
        .await
        .ownership_versions
        .values()
        .filter(|version| **version > 0)
        .count()
}

#[path = "v1_bot_service/commands.rs"]
mod commands;

#[path = "v1_bot_service/candidate_search.rs"]
mod candidate_search;

#[path = "v1_bot_service/candidates_and_projection.rs"]
mod candidates_and_projection;

#[path = "v1_bot_service/mine.rs"]
mod mine;
