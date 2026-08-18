#![allow(deprecated)]

use std::collections::BTreeMap;

use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateVisibility, BotCapabilities, BotDynamicStatus,
    RegisteredBot, ServiceResult, WorkerProfile as RootWorkerProfile,
    WorkerProfileService as RootWorkerProfileService,
    WorkerRecommendCommand as RootWorkerRecommendCommand,
    WorkerRecommendResult as RootWorkerRecommendResult,
    WorkerRecommendation as RootWorkerRecommendation,
    actors::{
        WorkerProfile as ActorsWorkerProfile, WorkerProfileService as ActorsWorkerProfileService,
        WorkerRecommendCommand as ActorsWorkerRecommendCommand,
        WorkerRecommendResult as ActorsWorkerRecommendResult,
        WorkerRecommendation as ActorsWorkerRecommendation,
    },
    application::actor_directory::{
        WorkerProfile as ApplicationWorkerProfile,
        WorkerProfileService as ApplicationWorkerProfileService,
        WorkerRecommendCommand as ApplicationWorkerRecommendCommand,
        WorkerRecommendResult as ApplicationWorkerRecommendResult,
        WorkerRecommendation as ApplicationWorkerRecommendation,
    },
    core::{
        BotCandidateSearchCoreResult, BotCandidateSearchCoreService, BotCandidateSearchHit,
        BotCandidateSearchMode, BotCandidateSearchQuery, LegacyBotCandidateSearchCoreResult,
        WorkerProfile, WorkerProfileCoreService, WorkerRecommendCommand, WorkerRecommendResult,
        WorkerRecommendation,
    },
};

fn candidate_query() -> BotCandidateSearchQuery {
    BotCandidateSearchQuery {
        query: "database expert".to_string(),
        acting_actor_id: "bot-1".to_string(),
        visibility: BotCandidateVisibility::Collaboration,
        limit: 20,
    }
}

fn candidate_hit() -> BotCandidateSearchHit {
    BotCandidateSearchHit {
        bot: RegisteredBot {
            bot_uuid: "bot-2".to_string(),
            capabilities: BotCapabilities::default(),
            dynamic_status: BotDynamicStatus::default(),
            env: Some("pre".to_string()),
            created_by: Some("staff-1".to_string()),
            actor_kind: ActorKind::Bot,
            status: ActorStatus::Online,
        },
        is_friend: true,
        tags: BTreeMap::from([("domain".to_string(), serde_json::json!("database"))]),
        score: Some(0.8),
        short_profile: Some("database specialist".to_string()),
    }
}

struct FakeCandidateSearchCoreService;

#[async_trait]
impl BotCandidateSearchCoreService for FakeCandidateSearchCoreService {
    async fn search_candidates(
        &self,
        query: BotCandidateSearchQuery,
    ) -> BotCandidateSearchCoreResult {
        assert_eq!(query.query, "database expert");
        BotCandidateSearchCoreResult {
            hits: vec![candidate_hit()],
            mode: BotCandidateSearchMode::Semantic,
        }
    }

    async fn search_candidates_for_legacy(
        &self,
        query: BotCandidateSearchQuery,
    ) -> LegacyBotCandidateSearchCoreResult {
        LegacyBotCandidateSearchCoreResult {
            result: self.search_candidates(query).await,
            recommend_response: Some(serde_json::json!({"provider": "opaque"})),
        }
    }
}

struct FakeWorkerProfileCoreService;

#[async_trait]
impl WorkerProfileCoreService for FakeWorkerProfileCoreService {
    async fn recommend_workers(
        &self,
        command: WorkerRecommendCommand,
    ) -> ServiceResult<WorkerRecommendResult> {
        let WorkerRecommendCommand {
            query,
            top_k,
            min_score,
        } = command;
        assert_eq!(query, "database expert");
        assert_eq!(top_k, 20);
        assert_eq!(min_score, 0.1);
        Ok(WorkerRecommendResult {
            recommendations: vec![WorkerRecommendation {
                worker_id: "bot-2".to_string(),
                score: 0.8,
                short_profile: Some("database specialist".to_string()),
            }],
            raw_response: serde_json::json!({"provider": "opaque"}),
        })
    }

    async fn batch_query_worker_profiles(
        &self,
        worker_ids: &[String],
    ) -> ServiceResult<Vec<WorkerProfile>> {
        assert_eq!(worker_ids, ["bot-2"]);
        Ok(vec![WorkerProfile {
            worker_id: "bot-2".to_string(),
            tags: BTreeMap::from([("domain".to_string(), serde_json::json!("database"))]),
        }])
    }
}

#[tokio::test]
async fn candidate_search_contract_exposes_normalized_and_legacy_entries() {
    let service = FakeCandidateSearchCoreService;
    fn accepts_service(_: &dyn BotCandidateSearchCoreService) {}
    accepts_service(&service);

    let BotCandidateSearchCoreResult { hits, mode } =
        service.search_candidates(candidate_query()).await;
    assert_eq!(mode, BotCandidateSearchMode::Semantic);
    let [hit]: [BotCandidateSearchHit; 1] = hits.try_into().unwrap();
    let BotCandidateSearchHit {
        bot,
        is_friend,
        tags,
        score,
        short_profile,
    } = hit;
    assert_eq!(bot.bot_uuid, "bot-2");
    assert!(is_friend);
    assert_eq!(tags["domain"], "database");
    assert_eq!(score, Some(0.8));
    assert_eq!(short_profile.as_deref(), Some("database specialist"));

    let LegacyBotCandidateSearchCoreResult {
        result,
        recommend_response,
    } = service
        .search_candidates_for_legacy(candidate_query())
        .await;
    assert_eq!(result.mode, BotCandidateSearchMode::Semantic);
    assert_eq!(recommend_response.unwrap()["provider"], "opaque");

    let BotCandidateSearchQuery {
        query,
        acting_actor_id,
        visibility,
        limit,
    } = candidate_query();
    assert_eq!(query, "database expert");
    assert_eq!(acting_actor_id, "bot-1");
    assert_eq!(visibility, BotCandidateVisibility::Collaboration);
    assert_eq!(limit, 20);
}

#[tokio::test]
async fn worker_profile_contract_exposes_all_provider_fields() {
    let service = FakeWorkerProfileCoreService;
    fn accepts_service(_: &dyn WorkerProfileCoreService) {}
    accepts_service(&service);
    let result = service
        .recommend_workers(WorkerRecommendCommand {
            query: "database expert".to_string(),
            top_k: 20,
            min_score: 0.1,
        })
        .await
        .unwrap();
    let WorkerRecommendResult {
        recommendations,
        raw_response,
    } = result;
    let [recommendation]: [WorkerRecommendation; 1] = recommendations.try_into().unwrap();
    let WorkerRecommendation {
        worker_id,
        score,
        short_profile,
    } = recommendation;
    assert_eq!(worker_id, "bot-2");
    assert_eq!(score, 0.8);
    assert_eq!(short_profile.as_deref(), Some("database specialist"));
    assert_eq!(raw_response["provider"], "opaque");

    let profiles = service
        .batch_query_worker_profiles(&["bot-2".to_string()])
        .await
        .unwrap();
    let [profile]: [WorkerProfile; 1] = profiles.try_into().unwrap();
    let WorkerProfile { worker_id, tags } = profile;
    assert_eq!(worker_id, "bot-2");
    assert_eq!(tags["domain"], "database");
}

#[test]
fn candidate_search_modes_are_core_variants_without_wire_assumptions() {
    let modes = [
        BotCandidateSearchMode::EmptyQuery,
        BotCandidateSearchMode::Semantic,
        BotCandidateSearchMode::NameFallback,
    ];
    assert_eq!(modes.len(), 3);
}

#[test]
fn legacy_worker_profile_import_paths_remain_source_compatible() {
    fn accepts_application_service(_: &dyn ApplicationWorkerProfileService) {}
    fn accepts_actors_service(_: &dyn ActorsWorkerProfileService) {}
    fn accepts_root_service(_: &dyn RootWorkerProfileService) {}
    fn accepts_application_types(
        _: ApplicationWorkerRecommendCommand,
        _: ApplicationWorkerRecommendation,
        _: ApplicationWorkerRecommendResult,
        _: ApplicationWorkerProfile,
    ) {
    }
    fn accepts_actors_types(
        _: ActorsWorkerRecommendCommand,
        _: ActorsWorkerRecommendation,
        _: ActorsWorkerRecommendResult,
        _: ActorsWorkerProfile,
    ) {
    }
    fn accepts_root_types(
        _: RootWorkerRecommendCommand,
        _: RootWorkerRecommendation,
        _: RootWorkerRecommendResult,
        _: RootWorkerProfile,
    ) {
    }

    let service = FakeWorkerProfileCoreService;
    accepts_application_service(&service);
    accepts_actors_service(&service);
    accepts_root_service(&service);

    let command = WorkerRecommendCommand {
        query: "query".to_string(),
        top_k: 1,
        min_score: 0.0,
    };
    let recommendation = WorkerRecommendation {
        worker_id: "bot-2".to_string(),
        score: 0.0,
        short_profile: None,
    };
    let result = WorkerRecommendResult {
        recommendations: vec![recommendation.clone()],
        raw_response: serde_json::Value::Null,
    };
    let profile = WorkerProfile {
        worker_id: "bot-2".to_string(),
        tags: BTreeMap::new(),
    };
    accepts_application_types(
        command.clone(),
        recommendation.clone(),
        result.clone(),
        profile.clone(),
    );
    accepts_actors_types(command, recommendation, result, profile);
    accepts_root_types(
        WorkerRecommendCommand {
            query: "query".to_string(),
            top_k: 1,
            min_score: 0.0,
        },
        WorkerRecommendation {
            worker_id: "bot-2".to_string(),
            score: 0.0,
            short_profile: None,
        },
        WorkerRecommendResult {
            recommendations: Vec::new(),
            raw_response: serde_json::Value::Null,
        },
        WorkerProfile {
            worker_id: "bot-2".to_string(),
            tags: BTreeMap::new(),
        },
    );

    let application_profile = ApplicationWorkerProfile::default();
    assert!(application_profile.worker_id.is_empty());
    assert!(application_profile.tags.is_empty());

    let actors_profile = ActorsWorkerProfile::default();
    assert!(actors_profile.worker_id.is_empty());
    assert!(actors_profile.tags.is_empty());

    let root_profile = RootWorkerProfile::default();
    assert!(root_profile.worker_id.is_empty());
    assert!(root_profile.tags.is_empty());
}
