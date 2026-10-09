use super::*;

use super::replies::{
    send_empty_human_input_final, send_error, send_error_shape, send_ok,
    send_human_input_error_event,
};

#[derive(Debug, Deserialize)]
struct ChatSendParams {
    #[serde(alias = "sessionKey")]
    session_key: Option<String>,
    #[serde(default, alias = "bcs_session_id", alias = "sessionId")]
    session_id: Option<String>,
    message: String,
    group_id: String,
    bot_uuid: Option<String>,
    bot_id: Option<String>,
    bot_name: Option<String>,
    #[serde(default)]
    mentions: Vec<String>,
    thinking: Option<String>,
    #[serde(alias = "idempotencyKey")]
    idempotency_key: Option<String>,
    attachments: Option<Vec<bcs_protocol::Attachment>>,
}

#[derive(Debug, Serialize)]
struct ChatSendResponse {
    #[serde(rename = "runId")]
    run_id: String,
    status: String,
}

pub(super) async fn handle_chat_send(
    state: &Arc<WebDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<WorkbenchOutbound>,
    connection_state: &mut WebClientConnectionState,
    auth: &WorkbenchConnectionAuth,
) -> Result<()> {
    let bound_actor_id = auth.actor_id();
    let params: ChatSendParams = serde_json::from_value(req.params.clone().unwrap_or(Value::Null))
        .map_err(|e| {
            WebWsDispatchError::InvalidFrameFormat(format!("Invalid chat.send params: {}", e))
        })?;

    let mut from_id = params
        .bot_id
        .clone()
        .or_else(|| params.bot_uuid.clone())
        .unwrap_or_else(|| "unknown".to_string());
    let mut session_id = resolve_bcs_session_id(&params);
    let mut group_id = params.group_id.clone();
    if let WorkbenchConnectionAuth::SessionBound {
        actor_id,
        group_id: bound_group_id,
        session_id: bound_session_id,
        ..
    } = auth
    {
        if group_id != *bound_group_id || session_id.as_deref() != Some(bound_session_id) {
            send_error(
                tx,
                &req.id,
                "token_scope_mismatch",
                "Request scope does not match the connection token",
            )
            .await?;
            connection_state.phase = WebConnectionPhase::Closed;
            return Ok(());
        }
        group_id = bound_group_id.clone();
        session_id = Some(bound_session_id.clone());
        from_id = actor_id.clone();
    }

    info!(
        group_id = %group_id,
        session_id = ?session_id,
        bot_id = ?params.bot_id,
        bot_uuid = ?params.bot_uuid,
        bound_actor_id = ?bound_actor_id,
        "Processing chat.send for group"
    );

    if let Err(err) = state
        .workbench_sessions
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: bound_actor_id.map(str::to_string),
            group_id: group_id.clone(),
            from_actor_id: from_id.clone(),
            session_id: session_id.clone(),
        })
        .await
    {
        warn!(
            request_id = %bcs_observability::CurrentRequestId,
            from = %from_id,
            group_id = %group_id,
            bound_actor_id = ?bound_actor_id,
            error = ?err,
            "chat.send rejected by Workbench WS authorization"
        );
        let message = err.message();
        send_error(tx, &req.id, err.code(), &message).await?;
        return Ok(());
    }

    // COSEC: Human identity comes only from the authenticated WebSocket
    // connection. Never trust bot_id/bot_uuid from the chat.send payload as
    // the responder identity for a HumanInput node.
    match state
        .collaboration_runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: group_id.clone(),
            session_id: session_id.clone(),
            caller_actor_id: bound_actor_id.unwrap_or_default().to_string(),
            content: params.message.clone(),
            source: HumanResponseSource::Http,
        })
        .await
    {
        Ok(HandleSessionHumanInputOutcome::NotStateMachine) => {}
        Ok(HandleSessionHumanInputOutcome::Consumed { response }) => {
            let run_id = response.run.run_id;
            let response = ChatSendResponse {
                run_id: run_id.clone(),
                status: "accepted".to_string(),
            };
            send_ok(tx, &req.id, serde_json::to_value(response)?).await?;
            send_empty_human_input_final(tx, &group_id, session_id.as_deref(), &run_id).await?;
            return Ok(());
        }
        Err(error) => {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                group_id = %group_id,
                session_id = ?session_id,
                error = %error,
                "chat.send rejected by state-machine HumanInput routing"
            );
            let error_code = collaboration_error_code(&error);
            let error_message = error.to_string();
            send_error(tx, &req.id, error_code, &error_message).await?;
            send_human_input_error_event(
                tx,
                &group_id,
                session_id.as_deref(),
                error_code,
                &error_message,
            )
            .await?;
            return Ok(());
        }
    }

    let sender_subscription_key = session_id.as_deref().unwrap_or(&group_id);
    let exact_sender_subscription = connection_state
        .subscribed_sessions
        .iter()
        .find(|(key, _, _)| key.as_str() == sender_subscription_key);
    let sender_subscription = exact_sender_subscription
        .or_else(|| {
            connection_state
                .subscribed_sessions
                .iter()
                .find(|(key, _, _)| key == &group_id)
        });
    let sender_conn_id = sender_subscription.map(|(_, id, _)| *id);
    // A Session run must never inherit a broader Group subscription. User-bound
    // clients historically may send before connect, so resolve the authoritative
    // Session participant view on demand instead of registering an unrestricted
    // run channel.
    let mut sender_human_view = exact_sender_subscription.and_then(|(_, _, view)| view.clone());
    if from_id.starts_with("human_") {
        if let Some(session_id) = session_id.as_deref()
            && state
                .frontend_connections
                .scope_change_in_progress(session_id, &from_id)
                .await
        {
            send_error(
                tx,
                &req.id,
                "view_scope_change_in_progress",
                "Participant message view scope is changing; retry after reconnect",
            )
            .await?;
            return Ok(());
        }
        let outcome = match state
            .workbench_sessions
            .connect(WorkbenchConnectCommand {
                bound_actor_id: bound_actor_id.map(str::to_string),
                view_actor_id: Some(from_id.clone()),
                group_id: group_id.clone(),
                session_id: session_id.clone(),
            })
            .await
        {
            Ok(outcome) => outcome,
            Err(error) => {
                let message = error.message();
                send_error(tx, &req.id, error.code(), &message).await?;
                return Ok(());
            }
        };
        let Some(participant) = outcome
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == from_id)
        else {
            send_error(
                tx,
                &req.id,
                "forbidden_view_actor",
                "Authorized Human is not a participant of the requested scope",
            )
            .await?;
            return Ok(());
        };
        sender_human_view = Some(HumanMessageView {
            actor_id: from_id.clone(),
            scope: participant.message_view_scope,
            allow_legacy_unclassified_chat: true,
        });
    }

    let caller = caller_context_from_bound_actor(bound_actor_id, &from_id);

    info!("chat.send: calling message_flow.handle_web_send");
    let outcome = state
        .message_flow
        .handle_web_send(WebSendCommand {
            caller,
            group_id: group_id.clone(),
            session_id: session_id.clone(),
            from_actor_id: from_id,
            from_name: params.bot_name.clone(),
            message: params.message,
            mentions: params.mentions,
            attachments: params.attachments.map(|attachments| {
                attachments
                    .into_iter()
                    .map(bcs_domain::Attachment::from)
                    .collect()
            }),
            thinking: params.thinking,
            idempotency_key: params.idempotency_key,
            source_im_message_id: None,
            channel_sender_identity: None,
            sender_conn_id,
            provider_bypass_headers: Vec::new(),
        })
        .await?;
    info!(
        run_ids = ?outcome.active_run_ids.len(),
        delivered = outcome.bot_deliveries.iter().filter(|d| d.delivered).count(),
        failed = outcome.bot_deliveries.iter().filter(|d| !d.delivered).count(),
        "chat.send: message_flow processing complete"
    );

    connection_state
        .active_run_ids
        .extend(outcome.active_run_ids.iter().cloned());
    let run_session_key = session_id.unwrap_or_else(|| group_id.clone());
    // Task 16: run lanes carry the SAME real identity context as the
    // session subscription — run fallback / re-dispatch re-authorizes this
    // exact binding, never the registry's legacy actor slot.
    let protected_run_anchor = match sender_conn_id {
        Some(conn_id) => state
            .frontend_connections
            .channel_binding_of(&run_session_key, conn_id)
            .await,
        None => None,
    };
    for run_id in &outcome.active_run_ids {
        state
            .run_channels
            .register_workbench_with_view(
                run_id.clone(),
                run_session_key.clone(),
                tx.clone(),
                Some("workbench-ws".to_string()),
                bound_actor_id.map(str::to_string),
                // A protected lane has no inline visibility filter: the
                // application hook owns SkipMessage (a filter must never
                // mask a revoke). Legacy lanes keep the old view filter.
                if protected_run_anchor.is_some() {
                    None
                } else {
                    sender_human_view.clone()
                },
                protected_run_anchor.clone(),
            )
            .await;
    }

    let response = ChatSendResponse {
        run_id: outcome.primary_run_id,
        status: outcome.status,
    };
    let mut response = serde_json::to_value(response)?;
    if let Some(admission) = outcome.queue_admission {
        response["queue_admission"] = serde_json::to_value(admission)?;
    }
    send_ok(tx, &req.id, response).await?;
    Ok(())
}

fn resolve_bcs_session_id(params: &ChatSendParams) -> Option<String> {
    params.session_id.clone().or_else(|| {
        params
            .session_key
            .as_deref()
            .filter(|session_key| {
                session_key
                    .strip_prefix(params.group_id.as_str())
                    .is_some_and(|suffix| suffix.starts_with(':'))
            })
            .map(str::to_string)
    })
}

fn collaboration_error_code(error: &CollaborationRuntimeError) -> &'static str {
    match error {
        CollaborationRuntimeError::RunNotFound(_)
        | CollaborationRuntimeError::NodeNotFound { .. }
        | CollaborationRuntimeError::DefinitionNotFound(_, _) => "not_found",
        CollaborationRuntimeError::InvalidDefinition(_) => "invalid_definition",
        CollaborationRuntimeError::InvalidParticipantBinding(_)
        | CollaborationRuntimeError::InvalidRequest(_) => "invalid_request",
        CollaborationRuntimeError::Unauthenticated => "unauthorized",
        CollaborationRuntimeError::Forbidden(_) => "forbidden",
        CollaborationRuntimeError::JudgeUnavailable(_) => "judge_unavailable",
        CollaborationRuntimeError::Conflict(_) => "conflict",
        CollaborationRuntimeError::Internal(_) => "internal_error",
    }
}

#[derive(Debug, Serialize)]
struct ChatAbortResult {
    aborted: bool,
    aborted_run_ids: Vec<String>,
    /// Deprecated response alias retained for one compatibility version.
    run_ids: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct ClientChatAbortParams {
    #[serde(default)]
    group_id: Option<String>,
    #[serde(default, alias = "bcs_session_id", alias = "sessionId")]
    session_id: Option<String>,
    #[serde(default, alias = "bot_uuid")]
    bot_id: Option<String>,
    #[serde(default)]
    run_id: Option<String>,
}

pub(super) async fn handle_chat_abort(
    state: &Arc<WebDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<WorkbenchOutbound>,
    connection_state: &mut WebClientConnectionState,
    auth: &WorkbenchConnectionAuth,
) -> Result<()> {
    let params: ClientChatAbortParams =
        serde_json::from_value(req.params.clone().unwrap_or(Value::Null)).map_err(|e| {
            WebWsDispatchError::InvalidFrameFormat(format!("Invalid chat.abort params: {}", e))
        })?;

    let Some(bound_actor_id) = auth.actor_id() else {
        send_error(
            tx,
            &req.id,
            "unauthorized",
            "An authenticated Human is required to abort chat runs",
        )
        .await?;
        return Ok(());
    };
    let run_id = params.run_id.clone();
    let (requested_group_id, bound_session_id) = match auth {
        WorkbenchConnectionAuth::UserBound { .. } => {
            let Some(group_id) = params.group_id.clone() else {
                send_error(tx, &req.id, "invalid_request", "group_id is required").await?;
                return Ok(());
            };
            (group_id, None)
        }
        WorkbenchConnectionAuth::SessionBound {
            group_id: bound_group_id,
            session_id,
            ..
        } => {
            if params
                .group_id
                .as_deref()
                .is_some_and(|group_id| group_id != bound_group_id)
                || params
                    .session_id
                    .as_deref()
                    .is_some_and(|requested| requested != session_id)
            {
                send_error(
                    tx,
                    &req.id,
                    "token_scope_mismatch",
                    "Request scope does not match the connection token",
                )
                .await?;
                connection_state.phase = WebConnectionPhase::Closed;
                return Ok(());
            }
            (bound_group_id.clone(), Some(session_id.clone()))
        }
    };

    let (group_id, session_id, bot_id) = match (
        params.session_id.clone().or(bound_session_id),
        params.bot_id.clone(),
    ) {
        (Some(session_id), Some(bot_id)) => (requested_group_id.clone(), session_id, bot_id),
        _ => {
            let Some(legacy_run_id) = run_id.as_deref() else {
                send_error(
                    tx,
                    &req.id,
                    "invalid_request",
                    "session_id and bot_id are required",
                )
                .await?;
                return Ok(());
            };
            let Some(scope) = state
                .message_flow
                .resolve_chat_abort_scope(&requested_group_id, legacy_run_id)
                .await?
            else {
                send_error(
                    tx,
                    &req.id,
                    "run_not_found",
                    "The legacy run_id is unknown or outside this Group",
                )
                .await?;
                return Ok(());
            };
            (scope.group_id, scope.session_id, scope.bot_id)
        }
    };

    let subscribed = connection_state
        .subscribed_sessions
        .iter()
        .any(|(subscription, _, _)| subscription == &session_id);
    if !subscribed {
        send_error(
            tx,
            &req.id,
            "session_not_subscribed",
            "The connection is not subscribed to the requested Session",
        )
        .await?;
        return Ok(());
    }

    if let Err(err) = state
        .workbench_sessions
        .authorize_chat_abort(WorkbenchChatAbortAuthorizationCommand {
            bound_actor_id: Some(bound_actor_id.to_string()),
            group_id: group_id.clone(),
            session_id: session_id.clone(),
            target_bot_id: bot_id.clone(),
        })
        .await
    {
        warn!(
            request_id = %bcs_observability::CurrentRequestId,
            actor_id = %bound_actor_id,
            group_id = %group_id,
            session_id = %session_id,
            bot_id = %bot_id,
            error = ?err,
            "chat.abort rejected by Workbench WS authorization"
        );
        send_error(tx, &req.id, err.code(), &err.message()).await?;
        return Ok(());
    }

    let caller = caller_context_from_bound_actor(Some(bound_actor_id), bound_actor_id);

    info!(
        group_id = %group_id,
        session_id = %session_id,
        bot_id = %bot_id,
        run_id = ?run_id,
        "Processing chat.abort"
    );

    let outcome = state
        .message_flow
        .handle_chat_abort(ChatAbortCommand {
            caller,
            group_id: group_id.clone(),
            session_id: session_id.clone(),
            bot_id: bot_id.clone(),
            // A legacy run_id only resolves the canonical Bot/Session scope.
            // The abort itself remains scope-based and fans out to every
            // active run owned by that Bot in the Session.
            run_id: None,
        })
        .await?;

    for aborted_run_id in &outcome.aborted_run_ids {
        if let Err(error) = state
            .interactions
            .invalidate_run(aborted_run_id, "chat_abort", bcs_protocol::now_ms())
            .await
        {
            // MessageFlow already committed the abort. Interaction cleanup is
            // best-effort and must not turn that successful command into a WS
            // failure.
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                run_id = %aborted_run_id,
                %error,
                "failed to invalidate aborted run interactions"
            );
        }
    }

    if !outcome.failures.is_empty() {
        let abort_not_supported = outcome.aborted_run_ids.is_empty()
            && outcome
                .failures
                .iter()
                .all(|failure| failure.code == "chat_abort_not_supported");
        send_error_shape(
            tx,
            &req.id,
            ErrorShape {
                code: if abort_not_supported {
                    "chat_abort_not_supported".to_string()
                } else {
                    "chat_abort_partial_failure".to_string()
                },
                message: if abort_not_supported {
                    "Target Bot plugin does not support chat.abort".to_string()
                } else {
                    "Some chat runs could not be aborted".to_string()
                },
                details: Some(serde_json::json!({
                    "bot_id": bot_id,
                    "resolution": if abort_not_supported { Some("restart_bot") } else { None },
                    "aborted_run_ids": outcome.aborted_run_ids,
                    "failures": outcome.failures.iter().map(|failure| serde_json::json!({
                        "run_id": failure.run_id,
                        "code": failure.code,
                        "message": failure.message,
                    })).collect::<Vec<_>>(),
                })),
                retryable: !abort_not_supported,
                retry_after_ms: None,
            },
        )
        .await?;
        return Ok(());
    }

    let result = ChatAbortResult {
        aborted: outcome.aborted,
        aborted_run_ids: outcome.aborted_run_ids.clone(),
        run_ids: outcome.aborted_run_ids,
    };

    info!(
        group_id = %group_id,
        aborted = result.aborted,
        "Chat abort completed"
    );

    send_ok(tx, &req.id, serde_json::to_value(result)?).await?;
    Ok(())
}

fn caller_context_from_bound_actor(
    bound_actor_id: Option<&str>,
    fallback_actor_id: &str,
) -> CallerContext {
    let actor_id = bound_actor_id.unwrap_or(fallback_actor_id).to_string();
    let staff_no = actor_id
        .strip_prefix("human_")
        .unwrap_or(actor_id.as_str())
        .to_string();
    CallerContext::Human(HumanActor { actor_id, staff_no })
}