use std::sync::Arc;

use async_trait::async_trait;
use bcs_message_flow::BcsGroupFusion;
use bcs_service_api as app;
use bcs_service_api::{
    ContextBotSummary, ContextFusionRequest, ContextFusionResponse, FusionCoreService, Group,
    GroupFusionCommand, GroupFusionService, GroupCoreService, Participant, ParticipantRole,
    ServiceResult,
};
use tokio::sync::Mutex;

#[path = "../../../test-support/message_flow_contract_support.rs"]
#[allow(dead_code)]
mod support;

#[derive(Default)]
struct RecordingFusion {
    requests: Mutex<Vec<ContextFusionRequest>>,
}

#[async_trait]
impl FusionCoreService for RecordingFusion {
    async fn fuse(&self, request: &ContextFusionRequest) -> ServiceResult<ContextFusionResponse> {
        self.requests.lock().await.push(request.clone());
        Ok(ContextFusionResponse {
            recommendation: Some("merged context".to_string()),
            ..ContextFusionResponse::default()
        })
    }

    fn load_bot_context(&self, bot_id: &str) -> ServiceResult<ContextBotSummary> {
        Ok(ContextBotSummary {
            bot_uuid: bot_id.to_string(),
            name: None,
            emoji: None,
            identity: None,
            soul: None,
            rules: None,
            memory: None,
        })
    }

    fn load_bot_contexts(&self, _bot_ids: &[String]) -> Vec<ContextBotSummary> {
        Vec::new()
    }
}

#[tokio::test]
async fn group_fusion_defaults_participants_and_sets_session_id() {
    let group = Arc::new(support::FakeGroupCoreService::default());
    group
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![
                Participant::bot("driver-bot", ParticipantRole::Driver),
                Participant::bot("target-bot", ParticipantRole::Consultant),
            ],
        ))
        .await
        .unwrap();
    let fusion = Arc::new(RecordingFusion::default());
    let service = BcsGroupFusion::new(group, fusion.clone());

    let response = service
        .fuse_for_group(GroupFusionCommand {
            group_id: "group-1".to_string(),
            request: app::FusionRequest {
                question: "what changed?".to_string(),
                participants: Vec::new(),
                ..app::FusionRequest::default()
            },
        })
        .await
        .unwrap();

    assert_eq!(response.recommendation.as_deref(), Some("merged context"));
    let requests = fusion.requests.lock().await;
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0].session_id.as_deref(), Some("group-1"));
    assert_eq!(
        requests[0].participants,
        vec!["driver-bot".to_string(), "target-bot".to_string()]
    );
}

#[tokio::test]
async fn group_fusion_preserves_explicit_participants() {
    let group = Arc::new(support::FakeGroupCoreService::default());
    group
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![
                Participant::bot("driver-bot", ParticipantRole::Driver),
                Participant::bot("target-bot", ParticipantRole::Consultant),
            ],
        ))
        .await
        .unwrap();
    let fusion = Arc::new(RecordingFusion::default());
    let service = BcsGroupFusion::new(group, fusion.clone());

    service
        .fuse_for_group(GroupFusionCommand {
            group_id: "group-1".to_string(),
            request: app::FusionRequest {
                question: "what changed?".to_string(),
                participants: vec!["target-bot".to_string()],
                ..app::FusionRequest::default()
            },
        })
        .await
        .unwrap();

    let requests = fusion.requests.lock().await;
    assert_eq!(requests[0].session_id.as_deref(), Some("group-1"));
    assert_eq!(requests[0].participants, vec!["target-bot".to_string()]);
}
