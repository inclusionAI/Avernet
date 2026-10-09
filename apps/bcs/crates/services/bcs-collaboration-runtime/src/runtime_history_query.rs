use super::*;

impl CollaborationRuntime {
    pub(super) async fn message_store_history(
        &self,
        session: &Session,
        limit: u64,
        before: Option<u64>,
        human_view: Option<HumanMessageView>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        if limit == 0 {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "history limit must be greater than 0".into(),
            ));
        }
        // Reuse the Session loaded for cutoff selection; never resolve a Run.
        let session_id = session.id.as_str();
        let repo = self.message_repo.as_ref().ok_or_else(|| {
            CollaborationRuntimeError::Internal(ServiceError::InternalError(
                "StateMachine message repository is not configured".into(),
            ))
        })?;
        let page = repo.list_state_machine_history(
            &session.group_id, session_id, human_view,
            before.map(|at| (at, 0)), limit.min(1_000) as u32,
        ).await.map_err(message_history_error)?;
        let messages = page.messages.iter().map(|message| {
            project_state_machine_message(message).ok_or_else(|| {
                CollaborationRuntimeError::Internal(ServiceError::InternalError(
                    "invalid StateMachine history message type".into(),
                ))
            })
        }).collect::<Result<Vec<_>, _>>()?;
        Ok(Some(SessionHistoryResult {
            session_id: session_id.into(), messages, limit, before,
            next_before: page.next_cursor.map(|cursor| cursor.0),
        }))
    }
}
