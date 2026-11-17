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

    async fn send(
        &self,
        command: SendSessionMessage,
    ) -> Result<SendSessionMessageOutcome, ApplicationError> {
        if command.message.trim().is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "message must not be empty",
            ));
        }
        // The Gateway security boundary (user: required, app: required)
        // admits only Human callers here; a Human always speaks as its own
        // `human_<user.id>` actor (caller-closed, same rule as the legacy
        // POST /sessions/{sid}/chat adapter).
        let user = require_authenticated_user(&command.caller)?;
        let actor_id = format!("human_{}", user.id);

        let session = self.load_session(&command.session_id).await?;
        // Fail-closed for non-participants: the explicit
        // POST .../participants use case is the sole membership
        // admission path on this surface.
        if !session
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == actor_id)
        {
            return Err(ApplicationError::forbidden(
                "The caller is not a Session Participant",
            ));
        }

        // A pending one-shot StateMachine HumanInput consumes the message
        // through the runtime first. Mirror the legacy `POST /sessions/{sid}/chat`
        // route: consult the runtime for every group strategy and only fall
        // through to the ordinary group-chat flow on `NotStateMachine`. The
        // runtime decides from the run/session binding, not from the group
        // strategy alone (ManagerWorker and Chat groups may carry one-shot runs).
        let outcome = self
            .collaboration_runtime
            .handle_session_human_input(HandleSessionHumanInputCommand {
                group_id: session.group_id.clone(),
                session_id: Some(command.session_id.clone()),
                caller_actor_id: actor_id.clone(),
                content: command.message.clone(),
                source: HumanResponseSource::Http,
                caller_ingress: None,
            })
            .await
            .map_err(map_runtime_error)?;
        if let HandleSessionHumanInputOutcome::Consumed { .. } = outcome {
            return Ok(SendSessionMessageOutcome {
                session_id: command.session_id,
                group_id: session.group_id,
                sender: actor_id,
                // The runtime accepted this Human reply as the pending node
                // input; it is not a fan-out delivery.
                delivered_count: 1,
            });
        }

        let message_flow = self.message_flow.as_deref().ok_or_else(|| {
            ApplicationError::internal("session message flow is not wired")
        })?;
        let outcome = message_flow
            .handle_group_chat(GroupChatCommand {
                caller: CallerContext::Human(HumanActor {
                    actor_id: actor_id.clone(),
                    staff_no: user.id.clone(),
                }),
                group_id: session.group_id.clone(),
                requested_sender_id: Some(actor_id.clone()),
                message: command.message,
                session_id: Some(command.session_id.clone()),
                provider_bypass_headers: Vec::new(),
                caller_ingress: None,
            })
            .await
            .map_err(map_service_error)?;
        Ok(SendSessionMessageOutcome {
            session_id: command.session_id,
            group_id: session.group_id,
            sender: actor_id,
            delivered_count: outcome.delivered_count as u64,
        })
    }
}
