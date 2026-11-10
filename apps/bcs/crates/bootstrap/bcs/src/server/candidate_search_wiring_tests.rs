use super::*;
use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};
use std::sync::Mutex;

#[test]
fn bindings_share_one_core_between_legacy_and_v1() {
    let bindings = build_candidate_search_bindings(
        &BcsConfig::default(),
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        None,
        None,
    );

    assert!(Arc::ptr_eq(&bindings.legacy, &bindings.openapi_v1));
}

#[tokio::test]
async fn composition_root_wires_the_authorized_entry_to_the_control_plane() {
    struct RecordingControlPlane {
        queries: Mutex<Vec<bcs_service_api::BotSearchCandidateQuery>>,
    }

    #[async_trait::async_trait]
    impl bcs_service_api::BotControlPlaneCoreService for RecordingControlPlane {
        async fn get_record(
            &self,
            _bot_id: &str,
            _env: &str,
        ) -> bcs_service_api::ServiceResult<
            Option<bcs_service_api::BotControlPlaneRecord>,
        > {
            Err(bcs_service_api::ServiceError::InternalError(
                "record read is not configured".into(),
            ))
        }

        async fn get(
            &self,
            _bot_id: &str,
            _env: &str,
        ) -> bcs_service_api::ServiceResult<
            Option<bcs_service_api::BotControlPlaneView>,
        > {
            Err(bcs_service_api::ServiceError::InternalError(
                "control plane read is not configured".into(),
            ))
        }

        async fn get_by_ids(
            &self,
            _bot_ids: &[String],
            _env: &str,
        ) -> bcs_service_api::ServiceResult<
            Vec<bcs_service_api::BotControlPlaneView>,
        > {
            Err(bcs_service_api::ServiceError::InternalError(
                "control plane batch read is not configured".into(),
            ))
        }

        async fn list_candidates(
            &self,
            _query: bcs_service_api::BotCandidateReadQuery,
        ) -> bcs_service_api::ServiceResult<(
            Vec<bcs_service_api::BotControlPlaneCandidate>,
            u64,
        )> {
            Err(bcs_service_api::ServiceError::InternalError(
                "control plane list is not configured".into(),
            ))
        }

        async fn search_candidates(
            &self,
            query: bcs_service_api::BotSearchCandidateQuery,
        ) -> bcs_service_api::ServiceResult<(
            Vec<bcs_service_api::BotControlPlaneCandidate>,
            u64,
        )> {
            self.queries.lock().unwrap().push(query);
            Ok((Vec::new(), 0))
        }

        async fn list_by_creator(
            &self,
            _query: bcs_service_api::BotControlPlaneOwnedQuery,
        ) -> bcs_service_api::ServiceResult<
            Vec<bcs_service_api::BotControlPlaneView>,
        > {
            Err(bcs_service_api::ServiceError::InternalError(
                "control plane creator list is not configured".into(),
            ))
        }

        async fn patch(
            &self,
            _bot_id: &str,
            _env: &str,
            _patch: bcs_service_api::BotControlPlanePatch,
        ) -> bcs_service_api::ServiceResult<
            Option<bcs_service_api::BotControlPlaneView>,
        > {
            Err(bcs_service_api::ServiceError::InternalError(
                "control plane patch is not configured".into(),
            ))
        }
    }

    let control_plane = Arc::new(RecordingControlPlane {
        queries: Mutex::new(Vec::new()),
    });
    let bindings = build_candidate_search_bindings(
        &BcsConfig::default(),
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        None,
        Some(control_plane.clone() as Arc<dyn bcs_service_api::BotControlPlaneCoreService>),
    );

    let result = bindings
        .openapi_v1
        .search_authorized_candidates(bcs_service_api::AuthorizedCandidateSearchQuery {
            query: "planning".to_string(),
            acting_actor_id: "acting".to_string(),
            env: bcs_config::resolve_env_str(),
            viewer_kind: bcs_service_api::ActorKind::Human,
            visibility: bcs_service_api::BotCandidateVisibility::Discovery,
            friend_ids: Default::default(),
            limit: 20,
        })
        .await
        .expect("authorized search is wired");

    assert_eq!(
        result.mode,
        bcs_service_api::BotCandidateSearchMode::NameFallback
    );
    let queries = control_plane.queries.lock().unwrap();
    assert_eq!(queries.len(), 1);
    assert!(queries[0].name_only);
    assert_eq!(queries[0].viewer_kind, Some(bcs_service_api::ActorKind::Human));
}
