//! Async setup shared by legacy chat and explicit direct AgentInvoke.
use super::*;
use bcs_service_api::application::agent_invoke::AgentInvocationInput;

impl A2aChat {
    pub(super) async fn start_async_with_invocation(
        &self,
        cmd: AsyncA2aChatCommand,
        invocation: Option<AgentInvocationInput>,
    ) -> ServiceResult<AsyncA2aChatAccepted> {
        dispatch::validate_message(&cmd.message)?;
        if let Some(input) = invocation.as_ref() {
            self.authorize_direct_invocation(
                input,
                &cmd.caller,
                &cmd.target_bot_id,
                cmd.from_actor_id.as_deref(),
                cmd.authenticated_staff_id.as_deref(),
                cmd.organization_code.as_deref(),
                cmd.a2a_test_user.is_some(),
            )
            .await?;
            if cmd
                .run_channel_from
                .as_deref()
                .is_some_and(|from| from != input.originator)
            {
                return Err(ServiceError::Forbidden(
                    "AgentInvoke channel lineage mismatch".into(),
                ));
            }
        }
        let (response_tx, response_rx) = mpsc::channel::<String>(64);
        let queued = cmd.a2a_test_user.is_none() && self.queue_enabled(&cmd.target_bot_id).await;
        if let Some(selection) = &cmd.a2a_test_user {
            if !matches!(&cmd.caller, CallerContext::Bot(actor) if actor.bot_uuid == selection.caller_bot_id)
            {
                return Err(ServiceError::Forbidden(
                    "A2A test user selection denied".into(),
                ));
            }
        }
        if !queued {
            bcs_observability::observe_value(
                "chat.channel.register",
                self.chat_run_events.register(
                    cmd.run_id.clone(),
                    cmd.session_key.clone(),
                    response_tx,
                    Some("http-chat-async".to_string()),
                    cmd.run_channel_from.clone(),
                ),
            )
            .await;
            bcs_observability::observe_value(
                "chat.context.register",
                self.register_bot_run_context(
                    &cmd.run_id,
                    &cmd.target_bot_id,
                    &cmd.session_key,
                    cmd.timeout_ms,
                ),
            )
            .await;
        }
        let expires_at_ms = now_ms().saturating_add(cmd.timeout_ms);
        let chat_result = bcs_observability::observe_result(
            "chat.prepare_and_deliver",
            self.chat_with_queue_mode(
                A2aChatCommand {
                    a2a_test_user: cmd.a2a_test_user,
                    caller: cmd.caller,
                    target_bot_id: cmd.target_bot_id.clone(),
                    message: cmd.message,
                    from_actor_id: cmd.from_actor_id,
                    authenticated_staff_id: cmd.authenticated_staff_id,
                    run_id: Some(cmd.run_id.clone()),
                    async_mode: true,
                    session_key: Some(cmd.session_key.clone()),
                    timeout_ms: Some(cmd.timeout_ms),
                    client: cmd.client.or_else(|| Some("http-chat-async".to_string())),
                    tags: cmd.tags,
                    response_mode: cmd.response_mode,
                    caller_wait_mode: cmd.caller_wait_mode,
                    organization_code: cmd.organization_code,
                    provider_bypass_headers: cmd.provider_bypass_headers,
                },
                Some(queued),
                invocation,
            ),
        )
        .await;

        let chat_outcome = match chat_result {
            Ok(outcome) => outcome,
            Err(err) => {
                self.chat_run_events.unregister(&cmd.run_id).await;
                return Err(err);
            }
        };

        let stored = self.run_store.try_get(&cmd.run_id).await?;
        let expires_at_ms = stored.as_ref().map_or(expires_at_ms, |r| r.expires_at_ms);
        let managed = stored.as_ref().is_some_and(|r| r.delivery_id.is_some());
        if managed || chat_outcome.status == "completed" {
            self.chat_run_events.unregister(&cmd.run_id).await;
        } else {
            let service = self.clone();
            let run_id = cmd.run_id.clone();
            let timeout_ms = cmd.timeout_ms;
            tokio::spawn(bcs_tracing_api::in_current_log_context(
                bcs_observability::in_current_context(async move {
                    service
                        .drain_async_run(run_id, response_rx, timeout_ms)
                        .await;
                }),
            ));
        }

        let delivery = if managed {
            let record = stored.ok_or_else(|| queued::invalid("direct run missing"))?;
            self.managed_status(record, None)
                .await?
                .response
                .and_then(|v| v.get("delivery").cloned())
        } else {
            None
        };
        Ok(AsyncA2aChatAccepted {
            delivery,
            run_id: cmd.run_id,
            bot_uuid: cmd.target_bot_id,
            session_id: cmd.session_key,
            status: chat_outcome.status,
            expires_at_ms,
        })
    }
}
