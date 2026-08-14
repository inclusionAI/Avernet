//! Core service contract harnesses.

use std::collections::BTreeMap;

use bcs_service_api::core::{
    WorkerProfile, WorkerProfileCoreService, WorkerRecommendCommand, WorkerRecommendResult,
};
use bcs_service_api::{
    BotCandidateSearchCoreService, BotCandidateSearchMode, BotCandidateSearchQuery,
    BotCandidateVisibility, BotRegistryCoreService, FriendCoreService, FriendRequestCoreService,
    FriendRequestDirection, FusionCoreService, GroupCoreService, OrganizationCoreService,
    ProposalCoreService, RelationCoreService, RoutingCoreService, ServiceError,
    SystemMessageDispatcherService, SystemMessageProducerService,
};

pub struct BotCandidateSearchHitExpectation {
    pub bot_id: String,
    pub is_friend: bool,
    pub tags: BTreeMap<String, serde_json::Value>,
    pub score: Option<f64>,
    pub short_profile: Option<String>,
}

pub struct BotCandidateSearchContractScenario {
    pub query: BotCandidateSearchQuery,
    pub expected_mode: BotCandidateSearchMode,
    pub expected_hits: Vec<BotCandidateSearchHitExpectation>,
    pub expected_legacy_recommend_response: Option<serde_json::Value>,
}

pub async fn bot_candidate_search_core_service_contract_tests<
    T: BotCandidateSearchCoreService + ?Sized,
>(
    svc: &T,
    semantic: BotCandidateSearchContractScenario,
    fallback: BotCandidateSearchContractScenario,
) {
    let empty_query = BotCandidateSearchQuery {
        query: "   ".to_string(),
        acting_actor_id: "contract-actor".to_string(),
        visibility: BotCandidateVisibility::Discovery,
        limit: 20,
    };

    let result = svc.search_candidates(empty_query.clone()).await;
    assert_eq!(result.mode, BotCandidateSearchMode::EmptyQuery);
    assert!(result.hits.is_empty());

    let legacy = svc.search_candidates_for_legacy(empty_query).await;
    assert_eq!(legacy.result.mode, BotCandidateSearchMode::EmptyQuery);
    assert!(legacy.result.hits.is_empty());
    assert!(legacy.recommend_response.is_none());

    assert_candidate_search_scenario(svc, semantic).await;
    assert_candidate_search_scenario(svc, fallback).await;
}

async fn assert_candidate_search_scenario<T: BotCandidateSearchCoreService + ?Sized>(
    svc: &T,
    scenario: BotCandidateSearchContractScenario,
) {
    let normal = svc.search_candidates(scenario.query.clone()).await;
    assert_candidate_search_result(&normal, &scenario);

    let legacy = svc
        .search_candidates_for_legacy(scenario.query.clone())
        .await;
    assert_candidate_search_result(&legacy.result, &scenario);
    assert_eq!(
        legacy.recommend_response,
        scenario.expected_legacy_recommend_response
    );
}

fn assert_candidate_search_result(
    actual: &bcs_service_api::BotCandidateSearchCoreResult,
    scenario: &BotCandidateSearchContractScenario,
) {
    assert_eq!(actual.mode, scenario.expected_mode);
    assert_eq!(actual.hits.len(), scenario.expected_hits.len());
    for (actual, expected) in actual.hits.iter().zip(&scenario.expected_hits) {
        assert_eq!(actual.bot.bot_uuid, expected.bot_id);
        assert_eq!(actual.is_friend, expected.is_friend);
        assert_eq!(actual.tags, expected.tags);
        assert_eq!(actual.score, expected.score);
        assert_eq!(actual.short_profile, expected.short_profile);
    }
}

pub async fn worker_profile_core_service_contract_tests<T: WorkerProfileCoreService + ?Sized>(
    svc: &T,
    command: WorkerRecommendCommand,
    expected_recommendations: &[(&str, f64, Option<&str>)],
    expected_raw_response: &serde_json::Value,
    profile_worker_ids: &[String],
    expected_profiles: &[WorkerProfile],
) -> WorkerRecommendResult {
    let result = svc
        .recommend_workers(command)
        .await
        .expect("worker recommendation succeeds through the Core contract");

    assert_eq!(result.recommendations.len(), expected_recommendations.len());
    for (actual, expected) in result.recommendations.iter().zip(expected_recommendations) {
        assert_eq!(actual.worker_id, expected.0);
        assert_eq!(actual.score, expected.1);
        assert_eq!(actual.short_profile.as_deref(), expected.2);
    }
    assert_eq!(&result.raw_response, expected_raw_response);

    let profiles = svc
        .batch_query_worker_profiles(profile_worker_ids)
        .await
        .expect("worker profile batch query succeeds through the Core contract");
    assert_eq!(profiles.len(), expected_profiles.len());
    for (actual, expected) in profiles.iter().zip(expected_profiles) {
        assert_eq!(actual.worker_id, expected.worker_id);
        assert_eq!(actual.tags, expected.tags);
    }

    result
}

pub async fn bot_registry_core_service_contract_tests<T: BotRegistryCoreService + ?Sized>(
    _svc: &T,
) {
}

pub async fn friend_core_service_contract_tests<T: FriendCoreService + ?Sized>(svc: &T) {
    svc.add_friendship("core-alice", "core-bob")
        .await
        .expect("add friendship");
    svc.add_friendship("core-bob", "core-alice")
        .await
        .expect("add friendship idempotent reverse");

    assert!(svc.are_friends("core-alice", "core-bob").await);
    assert!(svc.are_friends("core-bob", "core-alice").await);
    assert_eq!(
        svc.list_friends("core-alice").await,
        vec!["core-bob".to_string()]
    );
    assert!(
        svc.are_all_friends("core-alice", &["core-bob".to_string()])
            .await
            .is_ok()
    );
    assert!(matches!(
        svc.are_all_friends("core-alice", &["core-missing".to_string()])
            .await,
        Err(ServiceError::NotFriends(_))
    ));
    assert_eq!(
        svc.remove_all_friendships("core-alice")
            .await
            .expect("remove friendships"),
        1
    );
    assert!(!svc.are_friends("core-alice", "core-bob").await);
}

pub async fn friend_request_core_service_contract_tests<T: FriendRequestCoreService + ?Sized>(
    svc: &T,
) {
    assert!(matches!(
        svc.get_request("core-missing-request").await,
        Err(ServiceError::FriendRequestNotFound(_))
    ));
    assert!(
        svc.list_requests("core-alice", FriendRequestDirection::All, None)
            .await
            .is_empty()
    );
}

pub async fn fusion_core_service_contract_tests<T: FusionCoreService + ?Sized>(_svc: &T) {}

pub async fn group_core_service_contract_tests<T: GroupCoreService + ?Sized>(_svc: &T) {}

pub async fn organization_core_service_contract_tests<T: OrganizationCoreService + ?Sized>(
    svc: &T,
    managing_provider_id: &str,
    organization_code: &str,
) {
    let created = svc
        .create(
            managing_provider_id,
            organization_code,
            "Organization Contract",
            Some("created by the core contract"),
        )
        .await
        .expect("create organization");
    assert_eq!(created.code, organization_code);
    assert_eq!(created.managing_provider_id, managing_provider_id);

    let fetched = svc
        .get_for_manager(managing_provider_id, organization_code)
        .await
        .expect("get organization");
    assert_eq!(fetched.name, "Organization Contract");
    assert!(svc
        .list_for_manager(managing_provider_id, false)
        .await
        .expect("list organizations")
        .iter()
        .any(|organization| organization.code == organization_code));
    assert!(matches!(
        svc.create(managing_provider_id, organization_code, "Duplicate", None)
            .await,
        Err(ServiceError::Conflict(_))
    ));
    assert!(matches!(
        svc.get_for_manager("contract-other-manager", organization_code)
            .await,
        Err(ServiceError::Forbidden(_))
    ));
}

pub async fn proposal_core_service_contract_tests<T: ProposalCoreService + ?Sized>(_svc: &T) {}

pub async fn relation_core_service_contract_tests<T: RelationCoreService + ?Sized>(_svc: &T) {}

pub async fn routing_core_service_contract_tests<T: RoutingCoreService + ?Sized>(_svc: &T) {}

pub async fn system_message_producer_service_contract_tests<
    T: SystemMessageProducerService + ?Sized,
>(
    svc: &T,
) {
    let _ = svc.kind();
}

pub async fn system_message_dispatcher_service_contract_tests<
    T: SystemMessageDispatcherService + ?Sized,
>(
    _svc: &T,
) {
}
