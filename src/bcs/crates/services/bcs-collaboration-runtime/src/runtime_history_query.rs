use super::*;

impl CollaborationRuntime {
    pub(super) async fn message_store_history(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
        human_view: Option<HumanMessageView>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        if limit == 0 {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "history limit must be greater than 0".into(),
            ));
        }
        // Session membership is authorized by the application/HTTP entry point.
        // Resolving its group must not require even the existence of a Run.
        let Some(session) = self.sessions.get(session_id).await.map_err(|error| {
            CollaborationRuntimeError::Internal(ServiceError::InternalError(error.to_string()))
        })? else {
            return Ok(None);
        };
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
