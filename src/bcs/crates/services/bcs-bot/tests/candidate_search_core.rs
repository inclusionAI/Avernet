use std::collections::{BTreeMap, HashSet};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot::{BotCandidateSearchCore, BotCore};
use bcs_service_api::core::WorkerProfileCoreService;
use bcs_service_api::{
    BotCandidateSearchCoreService, BotCandidateSearchMode, BotCandidateSearchQuery,
    BotCandidateVisibility, BotCapabilities, BotRegistryCoreService, FriendCoreService,
    ServiceError, ServiceResult, WorkerProfile, WorkerRecommendCommand, WorkerRecommendResult,
    WorkerRecommendation,
};

#[derive(Clone)]
enum RecommendOutcome {
    Success(WorkerRecommendResult),
    Failure(String),
}

#[derive(Clone)]
enum ProfileOutcome {
    Success(Vec<WorkerProfile>),
    Failure(String),
}

struct RecordingWorkerProfiles {
    recommend_outcome: RecommendOutcome,
    profile_outcome: ProfileOutcome,
    recommend_calls: AtomicUsize,
    profile_calls: AtomicUsize,
    commands: Mutex<Vec<WorkerRecommendCommand>>,
    profile_worker_ids: Mutex<Vec<Vec<String>>>,
}

impl RecordingWorkerProfiles {
    fn new(recommend_outcome: RecommendOutcome, profile_outcome: ProfileOutcome) -> Self {
        Self {
            recommend_outcome,
            profile_outcome,
            recommend_calls: AtomicUsize::new(0),
            profile_calls: AtomicUsize::new(0),
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
        self.profile_calls.fetch_add(1, Ordering::SeqCst);
        self.profile_worker_ids
            .lock()
            .unwrap()
            .push(worker_ids.to_vec());
        match &self.profile_outcome {
            ProfileOutcome::Success(profiles) => Ok(profiles.clone()),
            ProfileOutcome::Failure(message) => Err(ServiceError::InternalError(message.clone())),
        }
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

fn query(query: &str, visibility: BotCandidateVisibility, limit: usize) -> BotCandidateSearchQuery {
    BotCandidateSearchQuery {
        query: query.to_string(),
        acting_actor_id: "acting".to_string(),
        visibility,
        limit,
    }
}

fn core(
    registry: Arc<BotCore>,
    friends: Arc<StaticFriends>,
    profiles: Arc<RecordingWorkerProfiles>,
    min_score: f64,
) -> BotCandidateSearchCore {
    BotCandidateSearchCore::new(registry, friends, profiles, min_score)
}

#[tokio::test]
async fn empty_query_skips_recommendation_and_profile_lookup_for_both_entry_points() {
    let registry = Arc::new(BotCore::memory());
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        semantic_result(
            vec![recommendation("candidate", 0.8, Some("candidate profile"))],
            serde_json::json!({"provider": "raw"}),
        ),
        ProfileOutcome::Success(Vec::new()),
    ));
    let service = core(
        registry,
        Arc::new(StaticFriends::new(&[])),
        profiles.clone(),
        0.2,
    );

    let result = service
        .search_candidates(query("   ", BotCandidateVisibility::Discovery, 10))
        .await;
    let legacy = service
        .search_candidates_for_legacy(query("", BotCandidateVisibility::Discovery, 10))
        .await;

    assert_eq!(result.mode, BotCandidateSearchMode::EmptyQuery);
    assert!(result.hits.is_empty());
    assert_eq!(legacy.result.mode, BotCandidateSearchMode::EmptyQuery);
    assert!(legacy.result.hits.is_empty());
    assert_eq!(legacy.recommend_response, None);
    assert_eq!(profiles.recommend_calls.load(Ordering::SeqCst), 0);
    assert_eq!(profiles.profile_calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn semantic_discovery_preserves_order_filters_actors_and_enriches_hits() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "public", "Public", "public").await;
    register_bot(&registry, "protected", "Protected", "protected").await;
    register_bot(&registry, "private", "Private", "private").await;
    registry
        .ensure_human_actor("42", "Human Candidate")
        .await
        .expect("ensure human actor");

    let profiles = Arc::new(RecordingWorkerProfiles::new(
        semantic_result(
            vec![
                recommendation("protected", 0.0, Some("protected profile")),
                recommendation("acting", 0.99, Some("self profile")),
                recommendation("human_42", 0.98, Some("human profile")),
                recommendation("missing", 0.97, Some("missing profile")),
                recommendation("private", 0.96, Some("private profile")),
                recommendation("public", 0.8, Some("public profile")),
            ],
            serde_json::json!({"trace_id": "semantic"}),
        ),
        ProfileOutcome::Success(vec![
            WorkerProfile {
                worker_id: "protected".to_string(),
                tags: BTreeMap::from([("team".to_string(), serde_json::json!("infra"))]),
            },
            WorkerProfile {
                worker_id: "public".to_string(),
                tags: BTreeMap::from([("team".to_string(), serde_json::json!("open"))]),
            },
        ]),
    ));
    let service = core(
        registry,
        Arc::new(StaticFriends::new(&["public"])),
        profiles.clone(),
        0.35,
    );

    let result = service
        .search_candidates(query(
            "  incident response  ",
            BotCandidateVisibility::Discovery,
            7,
        ))
        .await;

    assert_eq!(result.mode, BotCandidateSearchMode::Semantic);
    assert_eq!(
        result
            .hits
            .iter()
            .map(|hit| hit.bot.bot_uuid.as_str())
            .collect::<Vec<_>>(),
        vec!["protected", "public"]
    );
    assert_eq!(result.hits[0].score, Some(0.0));
    assert_eq!(
        result.hits[0].short_profile.as_deref(),
        Some("protected profile")
    );
    assert_eq!(result.hits[0].tags["team"], serde_json::json!("infra"));
    assert!(!result.hits[0].is_friend);
    assert_eq!(result.hits[1].score, Some(0.8));
    assert_eq!(result.hits[1].tags["team"], serde_json::json!("open"));
    assert!(result.hits[1].is_friend);

    let commands = profiles.commands.lock().unwrap();
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].query, "incident response");
    assert_eq!(commands[0].top_k, 7);
    assert_eq!(commands[0].min_score, 0.35);
    assert_eq!(
        profiles.profile_worker_ids.lock().unwrap().as_slice(),
        &[vec!["protected".to_string(), "public".to_string()]]
    );
}

#[tokio::test]
async fn semantic_collaboration_includes_public_and_non_public_friends_only() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "public", "Public", "public").await;
    register_bot(
        &registry,
        "protected-friend",
        "Protected Friend",
        "protected",
    )
    .await;
    register_bot(&registry, "private-friend", "Private Friend", "private").await;
    register_bot(&registry, "protected-other", "Protected Other", "protected").await;
    register_bot(&registry, "private-other", "Private Other", "private").await;

    let profiles = Arc::new(RecordingWorkerProfiles::new(
        semantic_result(
            vec![
                recommendation("protected-other", 0.9, None),
                recommendation("private-friend", 0.8, None),
                recommendation("public", 0.7, None),
                recommendation("protected-friend", 0.6, None),
                recommendation("private-other", 0.5, None),
            ],
            serde_json::json!({"mode": "collaboration"}),
        ),
        ProfileOutcome::Success(Vec::new()),
    ));
    let service = core(
        registry,
        Arc::new(StaticFriends::new(&["protected-friend", "private-friend"])),
        profiles,
        0.0,
    );

    let result = service
        .search_candidates(query(
            "collaborate",
            BotCandidateVisibility::Collaboration,
            20,
        ))
        .await;

    assert_eq!(result.mode, BotCandidateSearchMode::Semantic);
    assert_eq!(
        result
            .hits
            .iter()
            .map(|hit| (hit.bot.bot_uuid.as_str(), hit.is_friend))
            .collect::<Vec<_>>(),
        vec![
            ("private-friend", true),
            ("public", false),
            ("protected-friend", true),
        ]
    );
}

#[tokio::test]
async fn recommendation_failure_falls_back_to_trimmed_case_insensitive_name_search() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "match", "Incident RESPONDER", "public").await;
    register_bot(&registry, "other", "Other", "public").await;
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        RecommendOutcome::Failure("recommend failed".to_string()),
        ProfileOutcome::Success(Vec::new()),
    ));
    let service = core(registry, Arc::new(StaticFriends::new(&[])), profiles, 0.1);

    let result = service
        .search_candidates(query("  responder ", BotCandidateVisibility::Discovery, 10))
        .await;
    let legacy = service
        .search_candidates_for_legacy(query("  responder ", BotCandidateVisibility::Discovery, 10))
        .await;

    assert_eq!(result.mode, BotCandidateSearchMode::NameFallback);
    assert_eq!(result.hits.len(), 1);
    assert_eq!(result.hits[0].bot.bot_uuid, "match");
    assert_eq!(result.hits[0].score, None);
    assert_eq!(result.hits[0].short_profile, None);
    assert_eq!(legacy.result.mode, BotCandidateSearchMode::NameFallback);
    assert_eq!(legacy.recommend_response, None);
}

#[tokio::test]
async fn empty_recommendations_fall_back_and_preserve_raw_response_for_legacy() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "match", "Search Match", "public").await;
    let raw = serde_json::json!({"recommendations": []});
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        semantic_result(Vec::new(), raw.clone()),
        ProfileOutcome::Success(Vec::new()),
    ));
    let service = core(registry, Arc::new(StaticFriends::new(&[])), profiles, 0.0);

    let legacy = service
        .search_candidates_for_legacy(query("search", BotCandidateVisibility::Discovery, 10))
        .await;

    assert_eq!(legacy.result.mode, BotCandidateSearchMode::NameFallback);
    assert_eq!(legacy.result.hits[0].bot.bot_uuid, "match");
    assert_eq!(legacy.result.hits[0].score, None);
    assert_eq!(legacy.result.hits[0].short_profile, None);
    assert_eq!(legacy.recommend_response, Some(raw));
}

#[tokio::test]
async fn entirely_filtered_recommendations_fall_back_and_preserve_raw_response_for_legacy() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "hidden", "Hidden Search", "private").await;
    register_bot(&registry, "visible", "Visible Search", "public").await;
    let raw = serde_json::json!({"recommendations": [{"worker_id": "hidden"}]});
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        semantic_result(
            vec![
                recommendation("missing", 0.9, None),
                recommendation("hidden", 0.8, None),
            ],
            raw.clone(),
        ),
        ProfileOutcome::Success(Vec::new()),
    ));
    let service = core(registry, Arc::new(StaticFriends::new(&[])), profiles, 0.0);

    let legacy = service
        .search_candidates_for_legacy(query("search", BotCandidateVisibility::Discovery, 10))
        .await;

    assert_eq!(legacy.result.mode, BotCandidateSearchMode::NameFallback);
    assert_eq!(
        legacy
            .result
            .hits
            .iter()
            .map(|hit| hit.bot.bot_uuid.as_str())
            .collect::<Vec<_>>(),
        vec!["visible"]
    );
    assert_eq!(legacy.recommend_response, Some(raw));
}

#[tokio::test]
async fn profile_lookup_failure_keeps_semantic_hits_with_empty_tags() {
    let registry = Arc::new(BotCore::memory());
    register_bot(&registry, "acting", "Acting", "public").await;
    register_bot(&registry, "candidate", "Candidate", "public").await;
    let raw = serde_json::json!({"recommendations": [{"worker_id": "candidate"}]});
    let profiles = Arc::new(RecordingWorkerProfiles::new(
        semantic_result(
            vec![recommendation("candidate", 0.4, Some("semantic profile"))],
            raw.clone(),
        ),
        ProfileOutcome::Failure("profiles failed".to_string()),
    ));
    let service = core(registry, Arc::new(StaticFriends::new(&[])), profiles, 0.0);

    let legacy = service
        .search_candidates_for_legacy(query("candidate", BotCandidateVisibility::Discovery, 10))
        .await;

    assert_eq!(legacy.result.mode, BotCandidateSearchMode::Semantic);
    assert_eq!(legacy.result.hits.len(), 1);
    assert!(legacy.result.hits[0].tags.is_empty());
    assert_eq!(legacy.result.hits[0].score, Some(0.4));
    assert_eq!(
        legacy.result.hits[0].short_profile.as_deref(),
        Some("semantic profile")
    );
    assert_eq!(legacy.recommend_response, Some(raw));
}
