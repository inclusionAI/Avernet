use async_trait::async_trait;
use bcs_service_api::{
    CreateOrReactivateCommand, CreateOrReactivateOutcome, EnsureOwnerEdgesResult, Participant,
    ParticipantMode, RelationCoreService, RelationEdge, ServiceError, ServiceResult, Session,
    SessionManagementService, SessionStatus, SessionUseCaseError, SystemMessageEvent,
    SystemMessageService,
};

#[derive(Debug)]
pub(crate) struct EmptyRelationCoreService;

#[async_trait]
impl RelationCoreService for EmptyRelationCoreService {
    async fn upsert_edge(&self, _edge: RelationEdge) -> ServiceResult<()> {
        Ok(())
    }

    async fn delete_edge(&self, _from_id: &str, _to_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_edge(
        &self,
        _from_id: &str,
        _to_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        Ok(None)
    }

    async fn ensure_owner_edges(
        &self,
        _human_id: &str,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn ensure_owner_edges_counted(
        &self,
        _human_id: &str,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult> {
        Err(ServiceError::InternalError(
            "relation service must be configured for owner edge counting".to_string(),
        ))
    }

    async fn add_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friend_edges(&self, _actor_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn add_relation_edge(&self, _caller: &str, _target: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn list_friends_via_relation(
        &self,
        _actor_id: &str,
        _env: &str,
    ) -> ServiceResult<Vec<String>> {
        Ok(Vec::new())
    }
}

/// Noop system-message service used by `with_defaults` and standalone tests.
#[derive(Debug, Default)]
pub(crate) struct NoopSystemMessageService;

#[async_trait]
impl SystemMessageService for NoopSystemMessageService {
    async fn notify(&self, _group_id: &str, _event: SystemMessageEvent, _session_id: &str, _session_participants: &[Participant]) -> ServiceResult<usize> {
        Ok(0)
    }
}

/// Minimal session service used by `with_defaults` and standalone tests.
#[derive(Debug, Default)]
pub(crate) struct EmptySessionManagementService;

#[async_trait]
impl SessionManagementService for EmptySessionManagementService {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        let session_id = cmd
            .params
            .id
            .clone()
            .unwrap_or_else(|| format!("{}:{}", cmd.group_id, uuid::Uuid::new_v4()));
        Ok(CreateOrReactivateOutcome {
            session: Session {
                id: session_id,
                group_id: cmd.group_id,
                session_title: cmd.params.session_title,
                env: None,
                status: SessionStatus::Running,
                session_kind: cmd.params.session_kind,
                participants: cmd.params.participants,
                group_version: cmd.params.group_version,
                caller_id: cmd.params.caller_id,
                input: cmd.params.input,
                output: None,
                error_message: None,
                callback_status: None,
                activation_count: 1,
                caller_principal: cmd.params.caller_principal,
                created_by: cmd.params.created_by,
                current_msg_seq: 0,
                participant_join_seq: None,
                created_at: 0,
                updated_at: 0,
                completed_at: None,
                meta: cmd.params.meta,
            },
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

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        Ok(0)
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(())
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
        Err(SessionUseCaseError::Conflict("EmptySessionManagementService".to_string()))
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("EmptySessionManagementService".to_string()))
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("EmptySessionManagementService".to_string()))
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("EmptySessionManagementService".to_string()))
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
}
