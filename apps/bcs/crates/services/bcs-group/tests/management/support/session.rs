use super::*;

pub(super) struct StaticSessionManagement {
    session: Session,
    fail_create: bool,
    pub(super) commands: Mutex<Vec<bcs_service_api::CreateOrReactivateCommand>>,
}

impl StaticSessionManagement {
    pub(super) fn new(session: Session) -> Self {
        Self {
            session,
            fail_create: false,
            commands: Mutex::new(Vec::new()),
        }
    }

    pub(super) fn failing_create(session: Session) -> Self {
        Self {
            session,
            fail_create: true,
            commands: Mutex::new(Vec::new()),
        }
    }
}

/// The strict Session authorization snapshot over the SAME
/// static Session the management fake serves (F4): Workbench
/// admission reads Session facts through the snapshot Core only.
#[async_trait]
impl bcs_service_api::core::SessionAuthorizationCoreService for StaticSessionManagement {
    async fn read_authorization_snapshot(
        &self,
        session_id: &str,
    ) -> bcs_service_api::ServiceResult<
        Option<bcs_service_api::types::authorization_snapshot::SessionAuthorizationSnapshot>,
    > {
        use bcs_service_api::types::authorization_snapshot::{
            ParticipantAuthorizationSnapshot, SessionAuthorizationSnapshot,
        };
        Ok((self.session.id == session_id).then(|| SessionAuthorizationSnapshot {
            env: "dev".to_string(),
            id: self.session.id.clone(),
            live: true,
            group_id: self.session.group_id.clone(),
            status: self.session.status,
            session_kind: self.session.session_kind,
            created_by: self.session.created_by.clone(),
            message_visibility_version: self.session.message_visibility_version,
            participant_join_seq: self.session.participant_join_seq.clone(),
            participants: self
                .session
                .participants
                .iter()
                .map(|participant| ParticipantAuthorizationSnapshot {
                    actor_id: participant.bot_uuid.clone(),
                    actor_kind: participant.actor_kind,
                    role: participant.role,
                    effective_mode: participant.effective_mode(),
                    message_view_scope: participant.message_view_scope,
                })
                .collect(),
        }))
    }
}

#[async_trait]
impl SessionManagementService for StaticSessionManagement {
    async fn create_or_reactivate(
        &self,
        cmd: bcs_service_api::CreateOrReactivateCommand,
    ) -> Result<bcs_service_api::CreateOrReactivateOutcome, SessionUseCaseError> {
        self.commands.lock().await.push(cmd);
        if self.fail_create {
            return Err(SessionUseCaseError::Internal(ServiceError::InternalError(
                "session create failed".to_string(),
            )));
        }
        Ok(bcs_service_api::CreateOrReactivateOutcome {
            session: self.session.clone(),
            created: true,
        })
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
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
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
        unimplemented!("not needed by this test")
    }
}

pub(super) fn test_session(session_id: &str, group_id: &str, participants: Vec<Participant>) -> Session {
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
