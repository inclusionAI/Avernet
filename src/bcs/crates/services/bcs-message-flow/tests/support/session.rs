use bcs_service_api::{
    Participant, ParticipantMode, Session, SessionKind, SessionManagementService, SessionStatus,
    SessionUseCaseError,
};

pub struct StaticSessionManagement {
    session: Session,
}

impl StaticSessionManagement {
    pub fn new(session: Session) -> Self {
        Self { session }
    }
}

#[async_trait::async_trait]
impl SessionManagementService for StaticSessionManagement {
    async fn create_or_reactivate(
        &self,
        _cmd: bcs_service_api::CreateOrReactivateCommand,
    ) -> Result<bcs_service_api::CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn get(&self, session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok((self.session.id == session_id).then(|| self.session.clone()))
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self.session.id == session_id && self.session.group_id == group_id)
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
        Ok((self.session.group_id == group_id
            && status.is_none_or(|status| self.session.status == status))
        .then(|| self.session.clone())
        .into_iter()
        .collect())
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
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
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }
}

pub fn test_session(session_id: &str, group_id: &str, participants: Vec<Participant>) -> Session {
    Session {
        id: session_id.to_string(),
        group_id: group_id.to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants,
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
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
