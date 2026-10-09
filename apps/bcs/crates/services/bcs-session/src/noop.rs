//! Noop SessionManagementService for builder defaults / tests.

use async_trait::async_trait;

use bcs_service_api::application::session::{
    CreateOrReactivateCommand, CreateOrReactivateOutcome, SessionManagementService,
    SessionUseCaseError,
};
use bcs_service_api::{
    CreateSessionLaunch, ReactivateSessionLaunch, SessionLaunchError, SessionLaunchOutcome,
    SessionLaunchService,
};
use bcs_service_api::{Participant, ParticipantMode, Session, SessionStatus};
use bcs_service_api::types::MessageViewScope;

#[derive(Default)]
pub struct NoopSessionManagementService;

#[derive(Default)]
pub struct NoopSessionLaunchService;

const NOT_SUPPORTED: &str = "NoopSessionManagementService";

#[async_trait]
impl SessionLaunchService for NoopSessionLaunchService {
    async fn create(
        &self,
        _command: CreateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError> {
        Err(SessionLaunchError::InvalidRequest(
            "NoopSessionLaunchService".into(),
        ))
    }

    async fn reactivate(
        &self,
        _command: ReactivateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError> {
        Err(SessionLaunchError::InvalidRequest(
            "NoopSessionLaunchService".into(),
        ))
    }
}

#[async_trait]
impl SessionManagementService for NoopSessionManagementService {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict(NOT_SUPPORTED.into()))
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

    async fn count_running_service(
        &self,
        _group_id: &str,
    ) -> Result<u64, SessionUseCaseError> {
        Ok(0)
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(())
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
        _operation: &bcs_service_api::types::BotOperationContext,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
        _operation: &bcs_service_api::types::BotOperationContext,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict(NOT_SUPPORTED.into()))
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _operation: &bcs_service_api::types::BotOperationContext,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict(NOT_SUPPORTED.into()))
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
        _operation: &bcs_service_api::types::BotOperationContext,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict(NOT_SUPPORTED.into()))
    }

    async fn update_participant_message_view_scope(
        &self,
        _session_id: &str,
        _actor_id: &str,
        _message_view_scope: MessageViewScope,
        _operation: &bcs_service_api::types::BotOperationContext,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict(NOT_SUPPORTED.into()))
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
        _operation: &bcs_service_api::types::BotOperationContext,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict(NOT_SUPPORTED.into()))
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn delete(
        &self,
        _session_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }

    // ------------------------------------------------------------------
    // Plan Task 18: explicit evolution of every trait method this Noop so
    // far only inherited as a trait default. Noop semantics stay
    // fail-closed: writes / claims / CAS lanes answer errors, and the
    // ONLY empty results are reads over a Noop store that cannot hold
    // rows (the same read posture the Noop's own `get`/`list_by_group`
    // already answer). Any future trait change forces an explicit update
    // here instead of an inherited default silently drifting.
    // ------------------------------------------------------------------

    async fn list_running_service_after(
        &self,
        _after_session_id: Option<&str>,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        // Fail closed: the Noop carries no recovery page — an inherited
        // default must not be the only thing standing between a wiring
        // gap and recovery traffic.
        Err(SessionUseCaseError::Internal(
            bcs_service_api::ServiceError::InvalidOperation {
                message: "Session recovery pagination is not configured (Noop)".into(),
                request_id: None,
            },
        ))
    }

    async fn list_recoverable_callbacks(
        &self,
        _now_ms: u64,
        _after_session_id: Option<&str>,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        // Explicit empty read: the Noop store holds no callbacks to
        // recover (there is no allowance here — nothing was ever leased).
        Ok(Vec::new())
    }

    async fn claim_callback(
        &self,
        _command: bcs_service_api::application::session::ClaimSessionCallbackCommand,
    ) -> Result<Option<bcs_service_api::application::session::ClaimSessionCallbackOutcome>, SessionUseCaseError> {
        Err(SessionUseCaseError::Internal(
            bcs_service_api::ServiceError::InvalidOperation {
                message: "Session callback claim is not configured (Noop)".to_string(),
                request_id: None,
            },
        ))
    }

    async fn complete_callback(
        &self,
        _command: bcs_service_api::application::session::CompleteSessionCallbackCommand,
    ) -> Result<bool, SessionUseCaseError> {
        Err(SessionUseCaseError::Internal(
            bcs_service_api::ServiceError::InvalidOperation {
                message: "Session callback completion is not configured (Noop)".to_string(),
                request_id: None,
            },
        ))
    }

    async fn complete_running_service_activation(
        &self,
        _session_id: &str,
        _expected_activation_count: i32,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        // Fail closed: a CAS completion lane must never report success for
        // a store that cannot hold the activation.
        Err(SessionUseCaseError::Internal(
            bcs_service_api::ServiceError::InvalidOperation {
                message: "Session completion activation CAS is not configured (Noop)".into(),
                request_id: None,
            },
        ))
    }

    async fn collect(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _operation: &bcs_service_api::types::BotOperationContext,
    ) -> Result<(), SessionUseCaseError> {
        Err(SessionUseCaseError::Internal(
            bcs_service_api::ServiceError::InvalidOperation {
                message: "collect is not configured (Noop)".into(),
                request_id: None,
            },
        ))
    }

    async fn uncollect(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _operation: &bcs_service_api::types::BotOperationContext,
    ) -> Result<(), SessionUseCaseError> {
        // Idempotent by contract; a store that cannot hold the mark has
        // nothing to remove — but the audit-bearing write lane stays an
        // explicit error rather than a silent inherited no-op.
        Err(SessionUseCaseError::Internal(
            bcs_service_api::ServiceError::InvalidOperation {
                message: "uncollect is not configured (Noop)".into(),
                request_id: None,
            },
        ))
    }

    async fn list_collected_by_group(
        &self,
        _group_id: &str,
        _bot_uuid: &str,
        _status: Option<SessionStatus>,
        _title_contains: Option<&str>,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        // Explicit empty read: the Noop store collects nothing.
        Ok(Vec::new())
    }

    async fn collected_at_map(
        &self,
        _session_ids: &[&str],
        _bot_uuid: &str,
    ) -> Result<Vec<(String, u64)>, SessionUseCaseError> {
        // Explicit empty read: no session was ever collected by the Noop.
        Ok(Vec::new())
    }
}
