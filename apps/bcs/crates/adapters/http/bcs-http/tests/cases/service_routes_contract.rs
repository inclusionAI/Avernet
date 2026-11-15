use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_bot::BotCore;
use bcs_group::GroupStore;
use bcs_http::{
    router::build_router,
    state::HttpAppState,
};
use bcs_http::service_key::{ApiKeyEntry, ApiKeyRegistry, caller_principal_for, sha256_hex};
use bcs_service_api::{
    BotRegistryCoreService, CancelStateMachineRunCommand, CollaborationDefinition,
    CollaborationRuntimeError, CollaborationRuntimeService, ConfigureGroupRuntimeCommand,
    ConfigureGroupRuntimeOutcome, CreateOrReactivateCommand, CreateOrReactivateOutcome, Group,
    GroupCoreService, GroupStrategy, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome,
    NewSessionParams, Participant, ParticipantMode, ParticipantRole, Session,
    SessionKind, SessionManagementService, SessionHistoryResult, SessionStatus,
    SessionUseCaseError, StartStateMachineRunCommand, StartStateMachineRunOutcome,
    StateMachineDeliveryCorrelation, StateMachineRun,
    StateMachineRunStatus, StateMachineRunView,
};
use bcs_services_container::Services;
use serde_json::json;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;




#[derive(Default)]
struct RecordingSessionManagement {
    commands: Mutex<Vec<CreateOrReactivateCommand>>,
}

#[async_trait::async_trait]
impl SessionManagementService for RecordingSessionManagement {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        let session = session_from_params("group-1:abcdef12", &cmd.group_id, &cmd.params);
        self.commands.lock().await.push(cmd);
        Ok(CreateOrReactivateOutcome {
            session,
            created: true,
        })
    }

    async fn get(&self, _session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn belongs_to_group(
        &self,
        _session_id: &str,
        _group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}

fn session_from_params(id: &str, group_id: &str, params: &NewSessionParams) -> Session {
    Session {
        id: id.to_string(),
        group_id: group_id.to_string(),
        session_title: params.session_title.clone(),
        env: None,
        status: SessionStatus::Running,
        session_kind: params.session_kind,
        participants: params.participants.clone(),
        group_version: params.group_version,
        caller_id: params.caller_id.clone(),
        input: params.input.clone(),
        output: None,
        error_message: None,
        activation_count: 1,
        caller_principal: params.caller_principal.clone(),
        created_by: params.created_by.clone(),
        message_visibility_version: params.message_visibility_version,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: params.meta.clone(),
        current_msg_seq: 0,
        participant_join_seq: None,
        collected_at: None,
    }
}





#[path = "service_routes_contract/invocation.rs"]
mod invocation;
#[path = "service_routes_contract/access.rs"]
mod access;
#[path = "service_routes_contract/state_machine.rs"]
mod state_machine;
