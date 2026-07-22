use async_trait::async_trait;

use crate::types::{
    CollaborationDefinition, CollaborationDefinitionRef, GroupRuntimeBinding,
    ResolvedParticipantBinding, RuntimeParticipantBinding, ServiceResult,
    StateMachineDeliveryCorrelation, StateMachineNodeRun,
    StateMachineRun, StateMachineRunStatus,
};
use std::collections::BTreeMap;

#[derive(Debug, Clone)]
pub struct CollaborationDefinitionRecord {
    pub definition: CollaborationDefinition,
    pub source_format: Option<String>,
    pub yaml_text: Option<String>,
    pub content_hash: Option<String>,
}

#[async_trait]
pub trait StateMachineDefinitionRepoPort: Send + Sync {
    async fn upsert(&self, definition: CollaborationDefinition) -> ServiceResult<()>;
    async fn upsert_with_source_yaml(
        &self,
        definition: CollaborationDefinition,
        source_yaml: String,
    ) -> ServiceResult<()> {
        let _ = source_yaml;
        self.upsert(definition).await
    }
    async fn get(&self, id: &str, version: i32) -> ServiceResult<Option<CollaborationDefinition>>;
    async fn get_record(
        &self,
        id: &str,
        version: i32,
    ) -> ServiceResult<Option<CollaborationDefinitionRecord>> {
        Ok(self.get(id, version).await?.map(|definition| CollaborationDefinitionRecord {
            definition,
            source_format: None,
            yaml_text: None,
            content_hash: None,
        }))
    }
    async fn save_run_snapshot(
        &self,
        run: &StateMachineRun,
        group_version: i32,
        definition: &CollaborationDefinition,
        resolved_participant_bindings: Option<&BTreeMap<String, ResolvedParticipantBinding>>,
    ) -> ServiceResult<()>;
    async fn get_run_snapshot(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<CollaborationDefinition>>;
}

#[async_trait]
pub trait GroupRuntimeBindingRepoPort: Send + Sync {
    async fn upsert(&self, binding: GroupRuntimeBinding) -> ServiceResult<()>;
    async fn get(&self, group_id: &str) -> ServiceResult<Option<GroupRuntimeBinding>>;
    async fn bind_default_definition(
        &self,
        group_id: &str,
        group_version: i32,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<()>;
    async fn bind_default_definition_if_current(
        &self,
        group_id: &str,
        group_version: i32,
        expected_definition: Option<CollaborationDefinitionRef>,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<bool> {
        let current = self.get(group_id).await?;
        let current_definition = current.and_then(|binding| binding.default_definition);
        if current_definition != expected_definition {
            return Ok(false);
        }
        self.bind_default_definition(
            group_id,
            group_version,
            definition,
            participant_bindings,
            auto_start_on_service_invocation,
        )
        .await?;
        Ok(true)
    }
}

#[async_trait]
pub trait StateMachineRunRepoPort: Send + Sync {
    async fn create_run(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<()>;

    async fn get_run(&self, run_id: &str) -> ServiceResult<Option<StateMachineRun>>;
    async fn get_run_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>>;
    async fn list_node_runs(&self, run_id: &str) -> ServiceResult<Vec<StateMachineNodeRun>>;
    async fn get_node_run(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> ServiceResult<Option<StateMachineNodeRun>>;

    async fn mark_node_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<()>;

    async fn mark_node_running_if_run_active(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<bool> {
        self.mark_node_running(run_id, node_id, attempt, delivery_request_id, started_at)
            .await?;
        Ok(true)
    }

    async fn mark_node_judging(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
    ) -> ServiceResult<bool>;

    async fn list_claimable_judging_node_runs(
        &self,
        now_ms: u64,
        limit: usize,
    ) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let _ = (now_ms, limit);
        Ok(Vec::new())
    }

    async fn claim_judging_node(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        claim_token: &str,
        now_ms: u64,
        lease_until_ms: u64,
    ) -> ServiceResult<bool>;

    async fn renew_judging_node_claim(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        claim_token: &str,
        lease_until_ms: u64,
    ) -> ServiceResult<bool>;

    async fn get_judging_node_decision(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
    ) -> ServiceResult<Option<String>>;

    async fn persist_judging_node_decision(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        claim_token: &str,
        decision_json: String,
        now_ms: u64,
    ) -> ServiceResult<bool>;

    async fn complete_judging_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        claim_token: &str,
        completed_at: u64,
    ) -> ServiceResult<bool>;

    async fn fail_judging_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        claim_token: &str,
        error: String,
        completed_at: u64,
    ) -> ServiceResult<bool>;

    async fn finish_judging_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        claim_token: &str,
    ) -> ServiceResult<bool>;

    async fn complete_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
        completed_at: u64,
    ) -> ServiceResult<bool>;

    async fn fail_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        error: String,
        completed_at: u64,
    ) -> ServiceResult<bool>;

    async fn schedule_node_retry(
        &self,
        run_id: &str,
        node_id: &str,
        failed_attempt: i32,
        next_attempt: i32,
    ) -> ServiceResult<bool>;

    async fn skip_node(
        &self,
        run_id: &str,
        node_id: &str,
        skipped_at: u64,
    ) -> ServiceResult<bool>;

    async fn update_run_status(
        &self,
        run_id: &str,
        status: StateMachineRunStatus,
        output: Option<String>,
        error: Option<String>,
        updated_at: u64,
        completed_at: Option<u64>,
    ) -> ServiceResult<bool>;

    async fn upsert_delivery_correlation(
        &self,
        correlation: StateMachineDeliveryCorrelation,
    ) -> ServiceResult<()>;

    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> ServiceResult<()>;

    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<StateMachineDeliveryCorrelation>>;

    async fn list_expired_active_node_runs(
        &self,
        now_ms: u64,
        timeout_grace_ms: u64,
        limit: usize,
    ) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let _ = (now_ms, timeout_grace_ms, limit);
        Ok(Vec::new())
    }
}

#[async_trait]
pub trait CollaborationEventRepoPort: Send + Sync {
    async fn append_event(
        &self,
        state_machine_run_id: &str,
        node_id: Option<&str>,
        attempt: Option<i32>,
        event_type: &str,
        payload: serde_json::Value,
        created_at: u64,
    ) -> ServiceResult<()>;

    async fn list_events_by_run_and_type(
        &self,
        state_machine_run_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>>;

    async fn list_events_by_run_node_and_type(
        &self,
        state_machine_run_id: &str,
        node_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>>;
}

#[derive(Debug, Clone)]
pub struct CollaborationEventRecord {
    pub state_machine_run_id: String,
    pub node_id: Option<String>,
    pub attempt: Option<i32>,
    pub event_type: String,
    pub payload: serde_json::Value,
    pub created_at: u64,
}
