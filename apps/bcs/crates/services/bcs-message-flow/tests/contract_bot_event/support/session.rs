use super::super::{
    Participant,
    ParticipantMode,
    RwLock,
    Session,
    SessionKind,
    SessionManagementService,
    SessionStatus,
    SessionUseCaseError,
    Value
};

pub(in super::super) fn test_session(id: &str, group_id: &str, kind: SessionKind) -> Session {
    Session {
        id: id.to_string(),
        group_id: group_id.to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: kind,
        participants: Vec::new(),
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        activation_count: 1,
        message_visibility_version: 1,
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        collected_at: None,
    }
}

pub(in super::super) struct RecordingSessionManagement {
    sessions: RwLock<Vec<Session>>,
    pub(in super::super) completed: RwLock<Vec<(String, Option<Value>, Option<String>)>>,
}

impl RecordingSessionManagement {
    pub(in super::super) fn new(sessions: Vec<Session>) -> Self {
        Self {
            sessions: RwLock::new(sessions),
            completed: RwLock::new(Vec::new()),
        }
    }
}

#[async_trait::async_trait]
impl SessionManagementService for RecordingSessionManagement {
    async fn create_or_reactivate(
        &self,
        _cmd: bcs_service_api::CreateOrReactivateCommand,
    ) -> Result<bcs_service_api::CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn get(&self, session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self
            .sessions
            .read()
            .await
            .iter()
            .find(|session| session.id == session_id)
            .cloned())
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self
            .sessions
            .read()
            .await
            .iter()
            .any(|s| s.id == session_id && s.group_id == group_id))
    }

    async fn list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(self
            .sessions
            .read()
            .await
            .iter()
            .filter(|session| session.group_id == group_id)
            .filter(|session| status.map_or(true, |status| session.status == status))
            .cloned()
            .collect())
    }

    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<Value>,
        error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        self.completed
            .write()
            .await
            .push((session_id.to_string(), output.clone(), error.clone()));
        let mut sessions = self.sessions.write().await;
        let Some(session) = sessions.iter_mut().find(|session| session.id == session_id) else {
            return Ok(None);
        };
        if session.status == SessionStatus::Completed {
            return Ok(None);
        }
        session.status = SessionStatus::Completed;
        session.output = output;
        session.error_message = error;
        Ok(Some(session.clone()))
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
        unimplemented!("not needed by this test")
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}
