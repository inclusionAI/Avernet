//! A minimal V1 `SessionService` stand-in.
//!
//! Only `update_participant` is exercised (the PATCH member route prefers the
//! V1 application facade when the container configures one). Everything else
//! is unreachable from that route and stays unimplemented.

use std::sync::Arc;

use bcs_service_api::application::v1::{
    ApplicationError, DeleteResult, Page, SessionCompletionResult,
};
use bcs_service_api::application::v1::session::{
    AddSessionParticipant, CollectSession, CompleteSession, CreateSession, CreateSessionOutcome,
    DeleteSession, DeleteSessionParticipant, GetSession, ListSessions, SessionCollectionResult,
    SessionDetail, SessionParticipant, SessionService, SessionSummary, UncollectSession,
    UpdateSession, UpdateSessionParticipant,
};
use tokio::sync::Mutex;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum UpdateOutcome {
    Ok,
    ParticipantMissing,
    Forbidden,
}

pub(super) struct FakeSessionApplication {
    update: Mutex<UpdateOutcome>,
}

impl FakeSessionApplication {
    pub(super) fn new(outcome: UpdateOutcome) -> Arc<Self> {
        Arc::new(Self {
            update: Mutex::new(outcome),
        })
    }
}

#[async_trait::async_trait]
impl SessionService for FakeSessionApplication {
    async fn create(
        &self,
        _command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError> {
        unimplemented!("not used by the PATCH member route")
    }

    async fn list(&self, _command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        unimplemented!("not used by the PATCH member route")
    }

    async fn get(&self, _query: GetSession) -> Result<SessionDetail, ApplicationError> {
        unimplemented!("not used by the PATCH member route")
    }

    async fn update(&self, _command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        unimplemented!("not used by the PATCH member route")
    }

    async fn delete(&self, _command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        unimplemented!("not used by the PATCH member route")
    }

    async fn complete(
        &self,
        _command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        unimplemented!("not used by the PATCH member route")
    }

    async fn collect(
        &self,
        _command: CollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        unimplemented!("not used by the PATCH member route")
    }

    async fn uncollect(
        &self,
        _command: UncollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        unimplemented!("not used by the PATCH member route")
    }

    async fn add_participant(
        &self,
        _command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        unimplemented!("not used by the PATCH member route")
    }

    async fn update_participant(
        &self,
        command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        match *self.update.lock().await {
            UpdateOutcome::Ok => Ok(SessionParticipant {
                actor_id: command.bot_uuid,
                actor_kind: bcs_service_api::ActorKind::Human,
                name: Some("Test".to_string()),
                avatar_url: None,
                role: bcs_service_api::ParticipantRole::Observer,
                tags: Vec::new(),
                mode: command.mode.unwrap_or(bcs_service_api::ParticipantMode::Present),
                message_view_scope: command
                    .message_view_scope
                    .unwrap_or(bcs_domain::MessageViewScope::Full),
                joined_at: None,
            }),
            UpdateOutcome::ParticipantMissing => Err(ApplicationError::not_found(
                "participant_not_found",
                "participant is not in the session",
            )),
            UpdateOutcome::Forbidden => {
                Err(ApplicationError::forbidden("session is managed elsewhere"))
            }
        }
    }

    async fn delete_participant(
        &self,
        _command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        unimplemented!("not used by the PATCH member route")
    }
}
