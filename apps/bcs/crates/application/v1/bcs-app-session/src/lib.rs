//! Versioned Session application facade for the BCN V1 API.
//!
//! Implements both [`SessionService`] and [`SessionMessageService`]. The
//! facade owns authenticated-Caller resource authorization and V1 projections
//! while delegating the legacy session lifecycle to
//! [`bcs_service_api::application::session::SessionManagementService`]. No
//! HTTP type crosses this boundary.
//!
//! Module layout (plan Task 11): [`authorization`] owns the authenticated
//! caller's resource-authorization surface (live `BotAuthorityHook` role
//! facts + audit-identity construction), [`queries`] the read use cases,
//! [`mutations`] the write use cases (every one carrying the REQUIRED
//! `BotOperationContext` into Core/Repo, spec §12.5), and [`tests`] the pure
//! mapping tests.

mod authorization;
mod connection;
mod delivery_authorization;
mod file;
mod mutations;
mod queries;

#[cfg(test)]
mod tests;

pub use authorization::{SessionServiceConfig, SessionServiceImpl};
pub use connection::GroupSessionConnectionServiceImpl;
pub use delivery_authorization::DeliveryAuthorizationServiceImpl;
pub use file::SessionFileApplicationServiceImpl;

use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::{
    CollaborationRuntimeError, GroupUseCaseError, Participant, ServiceError, Session,
    SessionStatus as DomainSessionStatus, StateMachineRunView,
};

use bcs_service_api::application::session::SessionUseCaseError;
use bcs_service_api::application::v1::session::{
    SessionParticipant, SessionStatus as V1SessionStatus, SessionSummary, SessionDetail,
};

impl SessionServiceImpl {
    /// Project a stored Session into the full V1 detail view.
    pub(crate) async fn project_detail(
        &self,
        session: &Session,
        state_machine_run: Option<StateMachineRunView>,
    ) -> Result<SessionDetail, ApplicationError> {
        let mut participants = session.participants.clone();
        bcs_service_api::backfill_participant_names(self.registry.as_ref(), &mut participants)
            .await;
        let participants = participants
            .iter()
            .map(project_participant)
            .collect::<Vec<_>>();
        Ok(SessionDetail {
            session_id: session.id.clone(),
            version: session.group_version.unwrap_or(1),
            group_id: session.group_id.clone(),
            status: project_status(session.status),
            kind: session.session_kind,
            title: session.session_title.clone(),
            input: session.input.clone(),
            meta: session.meta.clone(),
            participants,
            caller_principal: session.caller_principal.clone(),
            created_by: session.created_by.clone(),
            created_at: session.created_at,
            updated_at: session.updated_at,
            state_machine_run_id: state_machine_run
                .as_ref()
                .map(|view| view.run.run_id.clone()),
            state_machine_run,
        })
    }
}

// ── projections ───────────────────────────────────────────────────────

pub(crate) fn project_participant(participant: &Participant) -> SessionParticipant {
    // Vey7i: pass `actor_kind` and the 4-value domain `ParticipantMode`
    // through verbatim so a Human participant inserted by the legacy
    // invitation-accept path (`actor_kind: Human, mode: Present`) is surfaced
    // as-is, not boot-truncated to `Auto`.
    SessionParticipant {
        actor_id: participant.bot_uuid.clone(),
        actor_kind: participant.actor_kind,
        name: participant.bot_name.clone(),
        role: participant.role,
        tags: participant.tags.clone(),
        mode: participant.effective_mode(),
        message_view_scope: participant.message_view_scope,
        joined_at: None,
    }
}

fn project_status(status: DomainSessionStatus) -> V1SessionStatus {
    match status {
        DomainSessionStatus::Running => V1SessionStatus::Running,
        DomainSessionStatus::Completed => V1SessionStatus::Completed,
    }
}

pub(crate) fn map_status_to_domain(status: V1SessionStatus) -> DomainSessionStatus {
    match status {
        V1SessionStatus::Running => DomainSessionStatus::Running,
        V1SessionStatus::Completed => DomainSessionStatus::Completed,
    }
}

pub(crate) fn project_summary(session: &Session) -> SessionSummary {
    SessionSummary {
        session_id: session.id.clone(),
        version: session.group_version.unwrap_or(1),
        group_id: session.group_id.clone(),
        status: project_status(session.status),
        title: session.session_title.clone(),
        participant_count: Some(session.participants.len()),
        caller_principal: session.caller_principal.clone(),
        created_by: session.created_by.clone(),
        created_at: session.created_at,
        updated_at: session.updated_at,
        collected: None,
    }
}

// ── error mappers ──────────────────────────────────────────────────────

pub(crate) fn map_session_error(error: SessionUseCaseError) -> ApplicationError {
    match error {
        SessionUseCaseError::NotFound(sid) => {
            ApplicationError::not_found("session_not_found", format!("Session '{sid}' was not found"))
        }
        SessionUseCaseError::InvalidParams(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        SessionUseCaseError::CallbackPending(message) => {
            ApplicationError::conflict("conflict", message)
        }
        SessionUseCaseError::Conflict(message) => ApplicationError::conflict("conflict", message),
        SessionUseCaseError::Internal(service_error) => map_service_error(service_error),
    }
}

pub(crate) fn map_launch_error(error: bcs_service_api::SessionLaunchError) -> ApplicationError {
    use bcs_service_api::SessionLaunchError;
    match error {
        SessionLaunchError::GroupNotFound(group_id) => ApplicationError::not_found(
            "group_not_found",
            format!("Group '{group_id}' was not found"),
        ),
        SessionLaunchError::SessionNotFound(session_id) => ApplicationError::not_found(
            "session_not_found",
            format!("Session '{session_id}' was not found"),
        ),
        SessionLaunchError::Forbidden(message) => ApplicationError::forbidden(message),
        SessionLaunchError::InvalidRole(message) => {
            ApplicationError::invalid("invalid_participant", message)
        }
        SessionLaunchError::InvalidRequest(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        SessionLaunchError::Conflict(message) => ApplicationError::conflict("conflict", message),
        SessionLaunchError::CallbackPending(message) => {
            ApplicationError::conflict("conflict", message)
        }
        SessionLaunchError::Runtime(error) => map_runtime_error(error),
        SessionLaunchError::Internal(error) => map_service_error(error),
    }
}

pub(crate) fn map_service_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::SessionNotFound(sid) => ApplicationError::not_found(
            "session_not_found",
            format!("Session '{sid}' was not found"),
        ),
        ServiceError::GroupNotFound(id) => {
            ApplicationError::not_found("group_not_found", format!("Group '{id}' was not found"))
        }
        ServiceError::BotNotFound(id) | ServiceError::BotNotRegistered(id) => {
            ApplicationError::not_found("bot_not_found", format!("Bot '{id}' was not found"))
        }
        ServiceError::ParticipantNotFound(id) => ApplicationError::not_found(
            "participant_not_found",
            format!("Participant '{id}' was not found"),
        ),
        ServiceError::Unauthorized(_) => ApplicationError::Unauthenticated,
        ServiceError::Forbidden(message) => ApplicationError::forbidden(message),
        ServiceError::Conflict(message) => ApplicationError::conflict("conflict", message),
        ServiceError::SessionInvalidParams(message)
        | ServiceError::InvalidOperation { message, .. } => {
            ApplicationError::invalid("invalid_request", message)
        }
        other => ApplicationError::internal(other.to_string()),
    }
}

pub(crate) fn map_runtime_error(error: CollaborationRuntimeError) -> ApplicationError {
    match error {
        CollaborationRuntimeError::Unauthenticated => ApplicationError::Unauthenticated,
        CollaborationRuntimeError::Forbidden(message) => ApplicationError::forbidden(message),
        CollaborationRuntimeError::InvalidDefinition(message)
        | CollaborationRuntimeError::InvalidParticipantBinding(message)
        | CollaborationRuntimeError::InvalidRequest(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        CollaborationRuntimeError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        other => ApplicationError::internal(other.to_string()),
    }
}

/// Map the legacy `GroupUseCaseError` returned by the shared
/// Session-history application service into the stable V1 error surface.
pub(crate) fn map_group_use_case_error(error: GroupUseCaseError) -> ApplicationError {
    match error {
        GroupUseCaseError::Unauthorized(_) => ApplicationError::Unauthenticated,
        GroupUseCaseError::Forbidden(message) => ApplicationError::forbidden(message),
        GroupUseCaseError::InvalidHistoryLimit(limit) => ApplicationError::invalid(
            "invalid_request",
            format!("limit must be greater than zero, got {limit}"),
        ),
        GroupUseCaseError::Service(service_error) => map_service_error(service_error),
        other => ApplicationError::internal(other.to_string()),
    }
}