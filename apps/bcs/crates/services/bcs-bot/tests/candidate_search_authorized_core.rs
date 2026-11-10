//! Authorized OpenAPI V1 candidate-search Core behavior.
//!
//! Recall and authorization are separate boundaries. The control-plane stub
//! emulates the authoritative viewer-dimension/edge filtering the real store
//! applies before pagination and records the exact queries the Core sends.

use std::collections::HashSet;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot::{BotCandidateSearchCore, BotCore, EmptyWorkerProfileCoreService};
use bcs_service_api::core::WorkerProfileCoreService;
use bcs_service_api::{
    ActorKind, ActorStatus, AuthorizedCandidateSearchQuery, BotCandidateReadQuery,
    BotCandidateSearchCoreService, BotCandidateSearchMode, BotCandidateVisibility, BotCapabilities,
    BotControlPlaneCandidate, BotControlPlaneCoreService, BotControlPlaneDescriptor,
    BotControlPlaneOwnedQuery, BotControlPlanePatch, BotControlPlaneRecord, BotControlPlaneView,
    BotRegistryCoreService, BotSearchCandidateQuery, FriendCheckInStrategy, FriendCoreService,
    ServiceError, ServiceResult, UserVisibility, WorkerProfile, WorkerRecommendCommand,
    WorkerRecommendResult, WorkerRecommendation, WorkerRecommendationScope,
};

#[derive(Clone)]
enum RecommendOutcome {
    Success(WorkerRecommendResult),
    Failure(String),
}

struct RecordingWorkerProfiles {
    recommend_outcome: RecommendOutcome,
    profiles: Vec<WorkerProfile>,
    recommend_calls: AtomicUsize,
    commands: Mutex<Vec<WorkerRecommendCommand>>,
    profile_worker_ids: Mutex<Vec<Vec<String>>>,
}

impl RecordingWorkerProfiles {
    fn new(recommend_outcome: RecommendOutcome, profiles: Vec<WorkerProfile>) -> Self {
        Self {
            recommend_outcome,
            profiles,
            recommend_calls: AtomicUsize::new(0),
            commands: Mutex::new(Vec::new()),
            profile_worker_ids: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl WorkerProfileCoreService for RecordingWorkerProfiles {
    async fn recommend_workers(
        &self,
        command: WorkerRecommendCommand,
    ) -> ServiceResult<WorkerRecommendResult> {
        self.recommend_calls.fetch_add(1, Ordering::SeqCst);
        self.commands.lock().unwrap().push(command);
        match &self.recommend_outcome {
            RecommendOutcome::Success(result) => Ok(result.clone()),
            RecommendOutcome::Failure(message) => Err(ServiceError::InternalError(message.clone())),
        }
    }

    async fn batch_query_worker_profiles(
        &self,
        worker_ids: &[String],
    ) -> ServiceResult<Vec<WorkerProfile>> {
        self.profile_worker_ids
            .lock()
            .unwrap()
            .push(worker_ids.to_vec());
        Ok(self.profiles.clone())
    }
}

struct StaticFriends {
    friend_ids: HashSet<String>,
}

impl StaticFriends {
    fn new(friend_ids: &[&str]) -> Self {
        Self {
            friend_ids: friend_ids.iter().map(|id| (*id).to_string()).collect(),
        }
    }
}

#[async_trait]
impl FriendCoreService for StaticFriends {
    async fn list_friends(&self, _bot_id: &str) -> Vec<String> {
        self.friend_ids.iter().cloned().collect()
    }

    async fn are_friends(&self, _bot_a: &str, bot_b: &str) -> bool {
        self.friend_ids.contains(bot_b)
    }

    async fn are_all_friends(&self, _bot_id: &str, others: &[String]) -> ServiceResult<()> {
        let missing: Vec<String> = others
            .iter()
            .filter(|id| !self.friend_ids.contains(id.as_str()))
            .cloned()
            .collect();
        if missing.is_empty() {
            Ok(())
        } else {
            Err(ServiceError::NotFriends(missing))
        }
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }
}

fn semantic_result(
    recommendations: Vec<WorkerRecommendation>,
    raw: serde_json::Value,
) -> RecommendOutcome {
    RecommendOutcome::Success(WorkerRecommendResult {
        recommendations,
        raw_response: raw,
    })
}

fn recommendation(
    worker_id: &str,
    score: f64,
    short_profile: Option<&str>,
) -> WorkerRecommendation {
    WorkerRecommendation {
        worker_id: worker_id.to_string(),
        score,
        short_profile: short_profile.map(str::to_string),
    }
}

async fn register_bot(registry: &BotCore, id: &str, name: &str, visibility: &str) {
    registry
        .register(
            id.to_string(),
            BotCapabilities {
                name: Some(name.to_string()),
                visibility: visibility.to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .expect("register bot");
}

fn core(
    registry: Arc<BotCore>,
    friends: Arc<StaticFriends>,
    profiles: Arc<RecordingWorkerProfiles>,
    min_score: f64,
) -> BotCandidateSearchCore {
    BotCandidateSearchCore::new(registry, friends, profiles, min_score)
}

/// Control-plane stub that emulates the authoritative viewer-dimension
/// filtering the real store applies before pagination. It records the exact
/// queries the Core sends so the tests can assert the recall/authorization
/// boundary without a real database.
struct FakeControlPlane {
    records: Vec<BotControlPlaneRecord>,
    failure: Option<String>,
    queries: Mutex<Vec<BotSearchCandidateQuery>>,
}

impl FakeControlPlane {
    fn new(records: Vec<BotControlPlaneRecord>) -> Self {
        Self {
            records,
            failure: None,
            queries: Mutex::new(Vec::new()),
        }
    }

    fn failing(message: &str) -> Self {
        Self {
            records: Vec::new(),
            failure: Some(message.to_string()),
            queries: Mutex::new(Vec::new()),
        }
    }

    fn queries(&self) -> Vec<BotSearchCandidateQuery> {
        self.queries.lock().unwrap().clone()
    }
}

fn visible_to_viewer(record: &BotControlPlaneRecord, query: &BotSearchCandidateQuery) -> bool {
    let is_friend = query.friend_ids.contains(&record.bot_id);
    match query.viewer_kind.unwrap_or(ActorKind::Bot) {
        ActorKind::Human => match query.visibility {
            BotCandidateVisibility::Discovery => matches!(
                record.user_visibility,
                UserVisibility::Public | UserVisibility::Protected
            ),
            BotCandidateVisibility::Collaboration => {
                record.user_visibility == UserVisibility::Public || is_friend
            }
        },
        ActorKind::Bot => match query.visibility {
            BotCandidateVisibility::Discovery => {
                matches!(record.visibility.as_str(), "public" | "protected")
            }
            BotCandidateVisibility::Collaboration => {
                record.visibility == "public" || is_friend
            }
        },
    }
}

#[async_trait]
impl BotControlPlaneCoreService for FakeControlPlane {
    async fn get_record(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        Err(ServiceError::InternalError(
            "fake control plane record read is not configured".into(),
        ))
    }

    async fn get(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "fake control plane read is not configured".into(),
        ))
    }

    async fn get_by_ids(
        &self,
        _bot_ids: &[String],
        _env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "fake control plane batch read is not configured".into(),
        ))
    }

    async fn list_candidates(
        &self,
        _query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        Err(ServiceError::InternalError(
            "fake control plane list is not configured".into(),
        ))
    }

    async fn search_candidate_records(
        &self,
        query: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<bcs_service_api::BotCandidateReadRecord>, u64)> {
        self.queries.lock().unwrap().push(query.clone());
        if let Some(message) = &self.failure {
            return Err(ServiceError::InternalError(message.clone()));
        }
        let needle = query
            .name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_lowercase);
        let mut records: Vec<BotControlPlaneRecord> = self
            .records
            .iter()
            .filter(|record| {
                record.env == query.env
                    && record.kind == ActorKind::Bot
                    && record.bot_id != query.acting_bot_id
            })
            .filter(|record| {
                query
                    .bot_uuids
                    .as_ref()
                    .is_none_or(|ids| ids.contains(&record.bot_id))
            })
            .filter(|record| {
                needle.as_ref().is_none_or(|needle| {
                    if query.name_only {
                        record.name.to_lowercase().contains(needle)
                    } else {
                        record.name.to_lowercase().contains(needle)
                            || record.bot_id.to_lowercase().contains(needle)
                    }
                })
            })
            .filter(|record| visible_to_viewer(record, &query))
            .cloned()
            .collect();
        records.sort_by(|left, right| {
            right
                .created_at
                .cmp(&left.created_at)
                .then_with(|| left.bot_id.cmp(&right.bot_id))
        });
        let total = records.len() as u64;
        let page = records
            .into_iter()
            .skip(query.offset as usize)
            .take(query.limit as usize)
            .map(|bot| bcs_service_api::BotCandidateReadRecord {
                is_friend: query.friend_ids.contains(&bot.bot_id),
                bot,
            })
            .collect();
        Ok((page, total))
    }

    async fn list_by_creator(
        &self,
        _query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "fake control plane creator list is not configured".into(),
        ))
    }

    async fn patch(
        &self,
        _bot_id: &str,
        _env: &str,
        _patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "fake control plane patch is not configured".into(),
        ))
    }
}

fn control_plane_record(
    bot_id: &str,
    name: &str,
    visibility: &str,
    user_visibility: UserVisibility,
    created_at: u64,
) -> BotControlPlaneRecord {
    BotControlPlaneRecord {
        bot_id: bot_id.to_string(),
        kind: ActorKind::Bot,
        name: name.to_string(),
        visibility: visibility.to_string(),
        status: ActorStatus::Online,
        env: bcs_config::resolve_env_str(),
        created_by: Some("staff-owner".to_string()),
        descriptor: BotControlPlaneDescriptor {
            summary: String::new(),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
        },
        agent_code: None,
        task_claim_mode: false,
        task_dream_mode: false,
        created_at,
        updated_at: created_at,
        user_visibility,
        friend_ext: Default::default(),
        friend_check_in_strategy: FriendCheckInStrategy::Open,
    }
}

fn authorized_query(
    query: &str,
    viewer_kind: ActorKind,
    visibility: BotCandidateVisibility,
    friend_ids: &[&str],
    limit: usize,
) -> AuthorizedCandidateSearchQuery {
    AuthorizedCandidateSearchQuery {
        query: query.to_string(),
        acting_actor_id: "acting".to_string(),
        env: bcs_config::resolve_env_str(),
        viewer_kind,
        visibility,
        friend_ids: friend_ids.iter().map(|id| (*id).to_string()).collect(),
        limit,
    }
}

fn core_with_control_plane(
    registry: Arc<BotCore>,
    friends: Arc<StaticFriends>,
    profiles: Arc<dyn WorkerProfileCoreService>,
    min_score: f64,
    control_plane: Arc<FakeControlPlane>,
) -> BotCandidateSearchCore {
    BotCandidateSearchCore::new(registry, friends, profiles, min_score)
        .with_control_plane(control_plane)
}

fn high_score_recommendations(ids: &[&str]) -> RecommendOutcome {
    semantic_result(
        ids.iter()
            .enumerate()
            .map(|(index, id)| {
                recommendation(id, 1.0 - index as f64 / 100.0, Some("semantic profile"))
            })
            .collect(),
        serde_json::json!({"trace_id": "authorized"}),
    )
}

#[tokio::test]
async fn authorized_human_search_keeps_authorized_subset_in_provider_order() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "human-only", "Human Only", "private").await;
    register_bot(&registry, "public", "Public", "public").await;
    register_bot(&registry, "bot-only", "Bot Only", "public").await;
    register_bot(&registry, "private-friend", "Private Friend", "private").await;

    let control_plane = Arc::new(FakeControlPlane::new(vec![
        // Creation times are deliberately the reverse of the provider order.
        control_plane_record("human-only", "Human Only", "private", UserVisibility::Public, 1),
        control_plane_record("public", "Public", "public", UserVisibility::Public, 2),
        control_plane_record("bot-only", "Bot Only", "public", UserVisibility::Private, 3),
        control_plane_record(
            "private-friend",
            "Private Friend",
            "private",
            UserVisibility::Private,
            4,
        ),
    ]));
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        high_score_recommendations(&[
            "human-only",
            "public",
            "human-only",
            "bot-only",
            "private-friend",
        ]),
        Vec::new(),
    ));
    let service = core_with_control_plane(
        registry,
        Arc::new(StaticFriends::new(&["public"])),
        profiles.clone(),
        0.25,
        control_plane.clone(),
    );

    let result = service
        .search_authorized_candidates(authorized_query(
            "  planning  ",
            ActorKind::Human,
            BotCandidateVisibility::Discovery,
            &["public"],
            20,
        ))
        .await
        .expect("authorized human search");

    assert_eq!(result.mode, BotCandidateSearchMode::Semantic);
    assert_eq!(
        result
            .hits
            .iter()
            .map(|hit| (hit.bot.bot_uuid.as_str(), hit.is_friend, hit.score))
            .collect::<Vec<_>>(),
        vec![("human-only", false, Some(1.0)), ("public", true, Some(0.99))]
    );
    // Bot-only has user_visibility=private and must never leak to a Human
    // viewer even though its Bot-facing visibility is public.
    assert!(!result.hits.iter().any(|hit| hit.bot.bot_uuid == "bot-only"));
    assert_eq!(
        profiles.profile_worker_ids.lock().unwrap().as_slice(),
        &[vec!["human-only".to_string(), "public".to_string()]]
    );

    let commands = profiles.commands.lock().unwrap();
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].query, "planning");
    assert_eq!(commands[0].top_k, 20);
    assert_eq!(commands[0].min_score, 0.25);
    assert_eq!(commands[0].scope, WorkerRecommendationScope::AllVisibilities);

    let queries = control_plane.queries();
    assert_eq!(queries.len(), 1);
    assert_eq!(queries[0].viewer_kind, Some(ActorKind::Human));
    assert_eq!(queries[0].visibility, BotCandidateVisibility::Discovery);
    assert_eq!(queries[0].bot_uuids.as_ref().map(Vec::len), Some(4));
    assert_eq!(
        queries[0].bot_uuids.as_deref(),
        Some(
            [
                "human-only".to_string(),
                "public".to_string(),
                "bot-only".to_string(),
                "private-friend".to_string(),
            ]
            .as_slice()
        )
    );
    // The validation limit covers the whole deduplicated recall batch, not
    // the smaller display limit.
    assert_eq!(queries[0].limit, 4);
    assert_eq!(queries[0].offset, 0);
    assert!(!queries[0].name_only);
    assert_eq!(queries[0].friend_ids, ["public".to_string()].into_iter().collect());
    assert_eq!(profiles.recommend_calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn authorized_bot_discovery_keeps_legacy_recall_scope() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "public", "Public", "public").await;
    register_bot(&registry, "protected", "Protected", "protected").await;
    register_bot(&registry, "private", "Private", "private").await;

    let control_plane = Arc::new(FakeControlPlane::new(vec![
        control_plane_record("public", "Public", "public", UserVisibility::Private, 1),
        control_plane_record("protected", "Protected", "protected", UserVisibility::Private, 2),
        control_plane_record("private", "Private", "private", UserVisibility::Private, 3),
    ]));
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        high_score_recommendations(&["private", "public", "protected"]),
        Vec::new(),
    ));
    let service = core_with_control_plane(
        registry,
        Arc::new(StaticFriends::new(&[])),
        profiles.clone(),
        0.0,
        control_plane,
    );

    let result = service
        .search_authorized_candidates(authorized_query(
            "discover",
            ActorKind::Bot,
            BotCandidateVisibility::Discovery,
            &[],
            20,
        ))
        .await
        .expect("authorized bot search");

    assert_eq!(result.mode, BotCandidateSearchMode::Semantic);
    assert_eq!(
        result
            .hits
            .iter()
            .map(|hit| hit.bot.bot_uuid.as_str())
            .collect::<Vec<_>>(),
        vec!["public", "protected"]
    );
    assert_eq!(
        profiles.commands.lock().unwrap()[0].scope,
        WorkerRecommendationScope::LegacyDefault
    );
}

#[tokio::test]
async fn authorized_collaboration_uses_all_visibilities_for_both_viewers() {
    for viewer_kind in [ActorKind::Human, ActorKind::Bot] {
        let registry = Arc::new(BotCore::memory());
        register_bot(&registry, "acting", "Acting", "public").await;
        register_bot(&registry, "candidate", "Candidate", "private").await;

        let control_plane = Arc::new(FakeControlPlane::new(vec![control_plane_record(
            "candidate",
            "Candidate",
            "private",
            UserVisibility::Private,
            1,
        )]));
        let profiles = Arc::new(RecordingWorkerProfiles::new(
            high_score_recommendations(&["candidate"]),
            Vec::new(),
        ));
        let service = core_with_control_plane(
            registry,
            Arc::new(StaticFriends::new(&[])),
            profiles.clone(),
            0.0,
            control_plane,
        );

        let result = service
            .search_authorized_candidates(authorized_query(
                "collaborate",
                viewer_kind,
                BotCandidateVisibility::Collaboration,
                &[],
                20,
            ))
            .await
            .expect("authorized collaboration search");

        assert_eq!(
            profiles.commands.lock().unwrap()[0].scope,
            WorkerRecommendationScope::AllVisibilities,
            "collaboration recall must not hide private candidates for {viewer_kind:?}"
        );
        // Recall scope is not authorization: the control plane still filters
        // the private non-friend candidate out.
        assert_eq!(result.mode, BotCandidateSearchMode::NameFallback);
        assert!(result.hits.is_empty());
    }
}

#[tokio::test]
async fn authorized_human_collaboration_rejects_protected_non_friend_and_accepts_friend() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "protected-bot", "Protected Bot", "protected").await;

    let control_plane = Arc::new(FakeControlPlane::new(vec![control_plane_record(
        "protected-bot",
        "Protected Bot",
        "private",
        UserVisibility::Protected,
        1,
    )]));
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        high_score_recommendations(&["protected-bot"]),
        Vec::new(),
    ));
    let service = core_with_control_plane(
        registry,
        Arc::new(StaticFriends::new(&[])),
        profiles.clone(),
        0.0,
        control_plane,
    );

    // `user_visibility=protected` is not public: a non-friend Human viewer must
    // not pass the Human/Collaboration branch.
    let rejected = service
        .search_authorized_candidates(authorized_query(
            "protected bot",
            ActorKind::Human,
            BotCandidateVisibility::Collaboration,
            &[],
            20,
        ))
        .await
        .expect("authorized collaboration search");
    assert_eq!(rejected.mode, BotCandidateSearchMode::NameFallback);
    assert!(rejected.hits.is_empty());

    // The same view with the approved edge passes.
    let accepted = service
        .search_authorized_candidates(authorized_query(
            "protected bot",
            ActorKind::Human,
            BotCandidateVisibility::Collaboration,
            &["protected-bot"],
            20,
        ))
        .await
        .expect("authorized collaboration search with friend");
    assert_eq!(accepted.mode, BotCandidateSearchMode::Semantic);
    assert_eq!(
        accepted
            .hits
            .iter()
            .map(|hit| (hit.bot.bot_uuid.as_str(), hit.is_friend))
            .collect::<Vec<_>>(),
        vec![("protected-bot", true)]
    );
}

#[tokio::test]
async fn authorized_semantic_subset_missing_from_registry_returns_empty_semantic_without_fallback() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    // The control plane authorizes "ghost", but the registry contains none of
    // the semantic subset.
    let control_plane = Arc::new(FakeControlPlane::new(vec![control_plane_record(
        "ghost",
        "Ghost",
        "public",
        UserVisibility::Public,
        1,
    )]));
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        high_score_recommendations(&["ghost"]),
        Vec::new(),
    ));
    let service = core_with_control_plane(
        registry,
        Arc::new(StaticFriends::new(&[])),
        profiles.clone(),
        0.0,
        control_plane.clone(),
    );

    let result = service
        .search_authorized_candidates(authorized_query(
            "ghost",
            ActorKind::Human,
            BotCandidateVisibility::Discovery,
            &[],
            20,
        ))
        .await
        .expect("genuinely missing registry ids are skipped, not an error");

    assert_eq!(result.mode, BotCandidateSearchMode::Semantic);
    assert!(result.hits.is_empty());
    let queries = control_plane.queries();
    assert_eq!(queries.len(), 1, "no fallback control-plane query");
    assert!(queries[0].bot_uuids.is_some());
    assert_eq!(profiles.recommend_calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn authorized_search_falls_back_to_viewer_scoped_name_without_provider() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "human-only", "Human Helper", "private").await;
    register_bot(&registry, "bot-only", "Human Hidden", "public").await;

    let control_plane = Arc::new(FakeControlPlane::new(vec![
        control_plane_record(
            "human-only",
            "Human Helper",
            "private",
            UserVisibility::Public,
            1,
        ),
        control_plane_record(
            "bot-only",
            "Human Hidden",
            "public",
            UserVisibility::Private,
            2,
        ),
    ]));
    let service = core_with_control_plane(
        registry,
        Arc::new(StaticFriends::new(&[])),
        Arc::new(EmptyWorkerProfileCoreService),
        0.0,
        control_plane.clone(),
    );

    let result = service
        .search_authorized_candidates(authorized_query(
            "  human  ",
            ActorKind::Human,
            BotCandidateVisibility::Discovery,
            &[],
            50,
        ))
        .await
        .expect("authorized name fallback");

    assert_eq!(result.mode, BotCandidateSearchMode::NameFallback);
    assert_eq!(
        result
            .hits
            .iter()
            .map(|hit| hit.bot.bot_uuid.as_str())
            .collect::<Vec<_>>(),
        vec!["human-only"]
    );
    assert!(result.hits.iter().all(|hit| hit.score.is_none()));
    assert!(result.hits.iter().all(|hit| hit.short_profile.is_none()));

    let queries = control_plane.queries();
    assert_eq!(queries.len(), 1);
    assert_eq!(queries[0].bot_uuids, None);
    assert_eq!(queries[0].name.as_deref(), Some("human"));
    assert!(queries[0].name_only);
    assert_eq!(queries[0].viewer_kind, Some(ActorKind::Human));
    assert_eq!(queries[0].visibility, BotCandidateVisibility::Discovery);
    assert_eq!(queries[0].limit, 20);
}

#[tokio::test]
async fn authorized_search_falls_back_when_the_recommendation_service_fails() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "human-only", "Human Helper", "private").await;

    let control_plane = Arc::new(FakeControlPlane::new(vec![control_plane_record(
        "human-only",
        "Human Helper",
        "private",
        UserVisibility::Public,
        1,
    )]));
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        RecommendOutcome::Failure("provider unavailable".to_string()),
        Vec::new(),
    ));
    let service = core_with_control_plane(
        registry,
        Arc::new(StaticFriends::new(&[])),
        profiles.clone(),
        0.0,
        control_plane.clone(),
    );

    let result = service
        .search_authorized_candidates(authorized_query(
            "human",
            ActorKind::Human,
            BotCandidateVisibility::Discovery,
            &[],
            20,
        ))
        .await
        .expect("provider failure falls back to the viewer-scoped name search");

    assert_eq!(result.mode, BotCandidateSearchMode::NameFallback);
    assert_eq!(
        result
            .hits
            .iter()
            .map(|hit| hit.bot.bot_uuid.as_str())
            .collect::<Vec<_>>(),
        vec!["human-only"]
    );
    assert!(result.hits.iter().all(|hit| hit.score.is_none()));

    let queries = control_plane.queries();
    assert_eq!(queries.len(), 1);
    assert_eq!(queries[0].bot_uuids, None);
    assert!(queries[0].name_only);
    assert_eq!(profiles.recommend_calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn authorized_search_falls_back_only_after_recommendations_are_filtered_out() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "human-only", "Human Helper", "private").await;
    register_bot(&registry, "bot-only", "Bot Only", "public").await;

    let control_plane = Arc::new(FakeControlPlane::new(vec![
        control_plane_record(
            "human-only",
            "Human Helper",
            "private",
            UserVisibility::Public,
            1,
        ),
        control_plane_record("bot-only", "Bot Only", "public", UserVisibility::Private, 2),
    ]));
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        high_score_recommendations(&["bot-only"]),
        Vec::new(),
    ));
    let service = core_with_control_plane(
        registry,
        Arc::new(StaticFriends::new(&[])),
        profiles.clone(),
        0.0,
        control_plane.clone(),
    );

    let result = service
        .search_authorized_candidates(authorized_query(
            "human",
            ActorKind::Human,
            BotCandidateVisibility::Discovery,
            &[],
            20,
        ))
        .await
        .expect("authorized fallback after filtering");

    assert_eq!(result.mode, BotCandidateSearchMode::NameFallback);
    assert_eq!(
        result
            .hits
            .iter()
            .map(|hit| hit.bot.bot_uuid.as_str())
            .collect::<Vec<_>>(),
        vec!["human-only"]
    );

    let queries = control_plane.queries();
    assert_eq!(queries.len(), 2);
    // Recall and authorization ran once, then the name fallback; there is no
    // second recommendation page.
    assert!(queries[0].bot_uuids.is_some());
    assert_eq!(queries[1].bot_uuids, None);
    assert!(queries[1].name_only);
    assert_eq!(profiles.recommend_calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn authorized_search_propagates_control_plane_failure_without_fallback() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "candidate", "Candidate", "public").await;
    let control_plane = Arc::new(FakeControlPlane::failing("control plane unavailable"));
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        high_score_recommendations(&["candidate"]),
        Vec::new(),
    ));
    let service = core_with_control_plane(
        registry,
        Arc::new(StaticFriends::new(&[])),
        profiles,
        0.0,
        control_plane.clone(),
    );

    let error = service
        .search_authorized_candidates(authorized_query(
            "candidate",
            ActorKind::Human,
            BotCandidateVisibility::Discovery,
            &[],
            20,
        ))
        .await
        .expect_err("control-plane failure must propagate");

    assert!(matches!(error, ServiceError::InternalError(_)));
    assert_eq!(control_plane.queries().len(), 1);
}

#[tokio::test]
async fn authorized_search_requires_control_plane_wiring() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "candidate", "Candidate", "public").await;
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        high_score_recommendations(&["candidate"]),
        Vec::new(),
    ));
    let service = core(
        registry,
        Arc::new(StaticFriends::new(&[])),
        profiles,
        0.0,
    );

    let error = service
        .search_authorized_candidates(authorized_query(
            "candidate",
            ActorKind::Human,
            BotCandidateVisibility::Discovery,
            &[],
            20,
        ))
        .await
        .expect_err("missing control-plane wiring must fail closed");

    assert!(matches!(error, ServiceError::InternalError(_)));
}
