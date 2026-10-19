use super::*;

#[async_trait]
impl SessionMessageService for SessionServiceImpl {
    async fn list(
        &self,
        query: ListSessionMessages,
    ) -> Result<Vec<GroupMessage>, ApplicationError> {
        self.list_with_options(query, MessageHistoryOptions::default())
            .await
    }

    async fn list_with_options(
        &self,
        query: ListSessionMessages,
        options: MessageHistoryOptions,
    ) -> Result<Vec<GroupMessage>, ApplicationError> {
        if query.limit == 0 || query.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        let view_actor_id = self
            .resolve_view_actor(&query.caller, query.view_bot_id.as_deref())
            .await?;
        let session = self.load_session(&query.session_id).await?;
        if !session
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == view_actor_id)
        {
            return Err(ApplicationError::forbidden(
                "The selected View Actor is not a Session Participant",
            ));
        }
        let group = self.load_group(&session.group_id).await?;
        let view_participant = session
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == view_actor_id)
            .expect("membership checked above");
        if group.group_strategy == GroupStrategy::StateMachine {
            let history = if view_participant.actor_kind == ActorKind::Human {
                self.collaboration_runtime
                    .get_state_machine_session_history_for_view(
                        &query.session_id,
                        query.limit,
                        query.before,
                        HumanMessageView {
                            actor_id: view_actor_id,
                            scope: view_participant.message_view_scope,
                            allow_legacy_unclassified_chat: false,
                        },
                    )
                    .await
            } else {
                self.collaboration_runtime
                    .get_state_machine_session_history(&query.session_id, query.limit, query.before)
                    .await
            };
            return history
                .map(|result| result.map_or_else(Vec::new, |result| result.messages))
                .map_err(map_runtime_error);
        }

        let user = require_authenticated_user(&query.caller)?;
        let session_id = query.session_id.clone();
        let human_view = (view_participant.actor_kind == ActorKind::Human
            && view_participant.message_view_scope == MessageViewScope::Participant)
            .then(|| HumanMessageView {
                actor_id: view_actor_id.clone(),
                scope: view_participant.message_view_scope,
                allow_legacy_unclassified_chat: false,
            });
        let result = self
            .history
            .get_session_history_with_options(
                SessionHistoryCommand {
                    caller: CallerContext::Human(HumanActor {
                        actor_id: format!("human_{}", user.id),
                        staff_no: user.id.clone(),
                    }),
                    group_id: session.group_id.clone(),
                    session_id: query.session_id,
                    session_participants: session.participants,
                    view_bot_id: Some(view_actor_id),
                    limit: query.limit,
                    before: query.before,
                },
                options,
            )
            .await
            .map_err(map_group_use_case_error)?;
        let Some(human_view) = human_view else {
            return Ok(result.messages);
        };

        // One-shot StateMachine runs can live inside Chat or ManagerWorker
        // Sessions. Responses created before they became durable messages are
        // still present in the run snapshot, so merge the participant-safe
        // projection for backwards compatibility. The runtime projection only
        // contains this Human's own outputs and directed prompts; persisted
        // history remains authoritative when the stable IDs overlap.
        let snapshot = self
            .collaboration_runtime
            .get_state_machine_session_history_for_view(
                &session_id,
                query.limit,
                query.before,
                human_view,
            )
            .await
            .map_err(map_runtime_error)?;
        Ok(merge_participant_state_machine_snapshot(
            result.messages,
            snapshot.map(|history| history.messages),
            query.limit,
        ))
    }
}
