use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api as app;
use bcs_service_api::{
    ContextConflict, ContextFusionRequest, ContextFusionResponse, ContextParticipantPerspective,
    FusionCoreService, GroupFusionCommand, GroupFusionService, GroupCoreService, ServiceError,
    ServiceResult,
};

#[derive(Clone)]
pub struct BcsGroupFusion {
    group: Arc<dyn GroupCoreService>,
    fusion: Arc<dyn FusionCoreService>,
}

impl BcsGroupFusion {
    pub fn new(group: Arc<dyn GroupCoreService>, fusion: Arc<dyn FusionCoreService>) -> Self {
        Self { group, fusion }
    }
}

#[async_trait]
impl GroupFusionService for BcsGroupFusion {
    async fn fuse_for_group(
        &self,
        cmd: GroupFusionCommand,
    ) -> ServiceResult<app::FusionResponse> {
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        let mut request = cmd.request;
        if request.participants.is_empty() {
            request.participants = group
                .participant_ids()
                .into_iter()
                .map(str::to_string)
                .collect();
        }
        request.session_id = Some(cmd.group_id);
        let core_request = to_core_fusion_request(request);
        let response = self.fusion.fuse(&core_request).await?;
        Ok(to_app_fusion_response(response))
    }
}

fn to_core_fusion_request(request: app::FusionRequest) -> ContextFusionRequest {
    ContextFusionRequest {
        question: request.question,
        participants: request.participants,
        focus: request.focus,
        session_id: request.session_id,
        fusion_mode: request.fusion_mode,
    }
}

fn to_app_fusion_response(response: ContextFusionResponse) -> app::FusionResponse {
    app::FusionResponse {
        perspectives: response
            .perspectives
            .into_iter()
            .map(to_app_participant_perspective)
            .collect(),
        conflicts: response.conflicts.into_iter().map(to_app_conflict).collect(),
        alignment_points: response.alignment_points,
        recommendation: response.recommendation,
        key_insights: response.key_insights,
        extra: response.extra,
    }
}

fn to_app_participant_perspective(
    perspective: ContextParticipantPerspective,
) -> app::ParticipantPerspective {
    app::ParticipantPerspective {
        bot_uuid: perspective.bot_uuid,
        name: perspective.name,
        emoji: perspective.emoji,
        summary: perspective.summary,
        key_points: perspective.key_points,
        concerns: perspective.concerns,
        role: perspective.role,
        confidence: perspective.confidence,
        status: perspective.status,
        participant_type: perspective.participant_type,
        evidence: perspective.evidence,
    }
}

fn to_app_conflict(conflict: ContextConflict) -> app::Conflict {
    app::Conflict {
        parties: conflict.parties,
        issue: conflict.issue,
        positions: conflict
            .positions
            .into_iter()
            .map(|position| app::ConflictPosition {
                bot_uuid: position.bot_uuid,
                view: position.view,
            })
            .collect(),
        severity: conflict.severity,
    }
}
