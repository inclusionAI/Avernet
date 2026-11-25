use super::*;
fn stale_event_reason(
    run: &StateMachineRun,
    node: Option<&StateMachineNodeRun>,
    correlation: &StateMachineDeliveryCorrelation,
    bot_id: &str,
) -> String {
    if run.status != StateMachineRunStatus::Running {
        return format!("run status is {:?}", run.status);
    }
    let Some(node) = node else {
        return "node run not found".to_string();
    };
    if node.status != StateMachineNodeStatus::Running {
        return format!("node status is {:?}", node.status);
    }
    if node.attempt != correlation.attempt {
        return format!(
            "node attempt is {}, correlation attempt is {}",
            node.attempt, correlation.attempt
        );
    }
    if node.delivery_request_id.as_deref() != Some(correlation.delivery_request_id.as_str()) {
        return "delivery_request_id no longer matches node run".to_string();
    }
    if correlation.assignee_bot_id != bot_id {
        return "bot_id does not match correlation assignee".to_string();
    }
    "event no longer matches current node run".to_string()
}

pub(super) fn extract_text(payload: &Value) -> Option<String> {
    payload
        .get("message")
        .and_then(|message| message.get("content"))
        .and_then(Value::as_array)
        .and_then(|blocks| {
            blocks.iter().find_map(|block| {
                block
                    .get("text")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
        })
        .or_else(|| {
            payload
                .get("data")
                .and_then(|data| data.get("text").or_else(|| data.get("delta")))
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .or_else(|| {
            payload
                .get("text")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .map(|text| text.trim().to_string())
        .filter(|text| !text.is_empty())
}

fn compact_bot_terminal_event_payload(
    correlation: &StateMachineDeliveryCorrelation,
    cmd: &HandleBotTerminalEventCommand,
) -> Option<Value> {
    if matches!(cmd.state, ChatEventState::Delta) {
        return None;
    }
    let text_len = extract_text(&cmd.event_payload).map(|text| text.chars().count());
    let payload_state = cmd
        .event_payload
        .get("state")
        .and_then(Value::as_str)
        .map(str::to_string);
    let stream = cmd
        .event_payload
        .get("stream")
        .and_then(Value::as_str)
        .map(str::to_string);
    Some(serde_json::json!({
        "run_id": correlation.state_machine_run_id.clone(),
        "node_id": correlation.node_id.clone(),
        "attempt": correlation.attempt,
        "delivery_request_id": correlation.delivery_request_id.clone(),
        "bot_delivery_run_id": cmd.run_id.clone(),
        "assignee_bot_id": correlation.assignee_bot_id.clone(),
        "bot_id": cmd.bot_id.clone(),
        "source_event_type": cmd.event_type.clone(),
        "state": chat_event_state_slug(&cmd.state),
        "payload_state": payload_state,
        "stream": stream,
        "text_len": text_len,
    }))
}impl CollaborationRuntime {

    pub(super) async fn handle_bot_terminal_event_impl(
        &self,
        cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        let Some(correlation) = self.runs.lookup_delivery_correlation(&cmd.run_id).await? else {
            return Ok(HandleBotTerminalEventOutcome {
                consumed: false,
                view: None,
            });
        };
        let run = self
            .runs
            .get_run(&correlation.state_machine_run_id)
            .await?
            .ok_or_else(|| {
                CollaborationRuntimeError::RunNotFound(correlation.state_machine_run_id.clone())
            })?;
        let node = self
            .runs
            .get_node_run(&correlation.state_machine_run_id, &correlation.node_id)
            .await?;
        let current_event = node.as_ref().is_some_and(|node| {
            run.status == StateMachineRunStatus::Running
                && node.status == StateMachineNodeStatus::Running
                && node.attempt == correlation.attempt
                && node.delivery_request_id.as_deref()
                    == Some(correlation.delivery_request_id.as_str())
                && correlation.assignee_bot_id == cmd.bot_id
        });
        if !current_event {
            self.events
                .append_event(
                    &correlation.state_machine_run_id,
                    Some(&correlation.node_id),
                    Some(correlation.attempt),
                    "state_machine.node.event_ignored",
                    serde_json::json!({
                        "run_id": correlation.state_machine_run_id.clone(),
                        "node_id": correlation.node_id.clone(),
                        "attempt": correlation.attempt,
                        "delivery_request_id": correlation.delivery_request_id.clone(),
                        "bot_delivery_run_id": cmd.run_id.clone(),
                        "bot_id": cmd.bot_id.clone(),
                        "event_type": cmd.event_type.clone(),
                        "state": chat_event_state_slug(&cmd.state),
                        "reason": stale_event_reason(&run, node.as_ref(), &correlation, &cmd.bot_id),
                        "payload": cmd.event_payload.clone(),
                    }),
                    bcs_protocol::now_ms(),
                )
                .await?;
            let view = self.run_view(&run.run_id).await?;
            if let Some(execution) = view.as_ref().and_then(|view| view.node_execution_metadata.as_ref())
                .and_then(|metadata| metadata.get(&correlation.node_id)) {
                loop_observability::log_loop_execution(&run, &correlation.node_id,
                    correlation.attempt, execution, "event_ignored", None);
            }
            return Ok(HandleBotTerminalEventOutcome { consumed: true, view });
        }
        if let Some(payload) = compact_bot_terminal_event_payload(&correlation, &cmd) {
            self.events
                .append_event(
                    &correlation.state_machine_run_id,
                    Some(&correlation.node_id),
                    Some(correlation.attempt),
                    "state_machine.node.bot_event",
                    payload,
                    bcs_protocol::now_ms(),
                )
                .await?;
        }
        let event_text_len = extract_text(&cmd.event_payload).map_or(0, |text| text.len());
        info!(target: RUNTIME_LOG_TARGET,
            run_id = %run.run_id,
            group_id = %run.group_id,
            session_id = %run.session_id,
            node_id = %correlation.node_id,
            attempt = correlation.attempt,
            bot_id = %cmd.bot_id,
            delivery_request_id = %correlation.delivery_request_id,
            bot_delivery_run_id = %cmd.run_id,
            event_type = %cmd.event_type,
            state = %chat_event_state_slug(&cmd.state),
            text_len = event_text_len,
            "state_machine: bot terminal event received"
        );
        log_state_machine_bot_event(&run, &correlation, &cmd, event_text_len);
        self.publish_state_machine_bot_event(
            &run,
            &cmd.bot_id,
            &cmd.run_id,
            &cmd.event_type,
            &cmd.event_payload,
            &cmd.state,
        )
        .await;
        let compiled = self.load_run_compiled(&run).await?;
        let group = self.groups.get(&run.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", run.group_id))
        })?;
        let now = bcs_protocol::now_ms();
        if matches!(cmd.state, ChatEventState::Final) && extract_text(&cmd.event_payload).is_none()
        {
            // A message-less final completes the bot attempt without an
            // artifact. It must advance the node into the normal failure/retry
            // path instead of leaving the node Running after an InvalidRequest.
            let error = "bot completed without visible output".to_string();
            let failed = self
                .fail_retryable_node_attempt(
                    &correlation.state_machine_run_id,
                    &correlation.node_id,
                    correlation.attempt,
                    error.clone(),
                    now,
                )
                .await?;
            let view = if failed {
                warn!(target: RUNTIME_LOG_TARGET,
                    run_id = %correlation.state_machine_run_id,
                    group_id = %run.group_id,
                    session_id = %run.session_id,
                    node_id = %correlation.node_id,
                    attempt = correlation.attempt,
                    error = %error,
                    "state_machine: node failed"
                );
                log_state_machine_node_result(
                    &run,
                    &correlation,
                    MessageLogStatus::Failed,
                    None,
                    Some(&error),
                    None,
                );
                self.resume_failed_node(
                    &compiled,
                    &group,
                    &run,
                    &correlation.node_id,
                    correlation.attempt,
                )
                .await?
            } else {
                self.run_view(&run.run_id).await?
            };
            return Ok(HandleBotTerminalEventOutcome {
                consumed: true,
                view,
            });
        }
        let mut view = None;
        match cmd.state {
            ChatEventState::Final => {
                let text = extract_text(&cmd.event_payload).unwrap_or_default();
                let artifact_len = text.len();
                let accepted_node = node.as_ref().ok_or_else(|| CollaborationRuntimeError::InvalidRequest("accepted node missing".into()))?;
                self.preserve_node_history(&compiled, &group, &run, accepted_node).await?;
                let judging = node_uses_judge(&compiled, &correlation.node_id);
                let preserve_acceptance = self.history_persistence_enabled || self.has_history_output(&run, accepted_node).await?;
                if preserve_acceptance && !self.accept_output_history(&compiled, &group, &run, accepted_node, &text, None, judging).await? {
                    return Ok(HandleBotTerminalEventOutcome { consumed: true, view: self.run_view(&run.run_id).await? });
                }
                if judging {
                    if preserve_acceptance || self.runs.begin_node_judging(&run.run_id, &correlation.node_id,
                        correlation.attempt, text, None).await? {
                        self.resume_judging(&compiled, &group, &run, &correlation.node_id, correlation.attempt).await?;
                    }
                    return Ok(HandleBotTerminalEventOutcome { consumed: true, view: self.run_view(&run.run_id).await? });
                }
                let evaluation = self
                    .evaluate_node_outcome(
                        &compiled,
                        &run,
                        &correlation.node_id,
                        correlation.attempt,
                        &text,
                    )
                    .await?;
                match evaluation {
                    JudgeEvaluationResult::Outcome(outcome, _) => {
                        let completed_at = bcs_protocol::now_ms();
                        let completed_output = text.clone();
                        let event = self.prepare_node_event(&compiled,
                            "state_machine.node.completed",
                            &run,
                            "state_machine.node",
                            &correlation.node_id,
                            &format!("{}:{}:completed", correlation.node_id, correlation.attempt),
                            Some(&cmd.bot_id),
                            Some(EventActorType::Bot),
                            completed_at,
                            BTreeMap::from([
                                ("run_id".to_string(), serde_json::json!(run.run_id.clone())),
                                (
                                    "node_id".to_string(),
                                    serde_json::json!(correlation.node_id.clone()),
                                ),
                                (
                                    "attempt".to_string(),
                                    serde_json::json!(correlation.attempt),
                                ),
                                ("outcome".to_string(), serde_json::json!(outcome.clone())),
                                (
                                    "output".to_string(),
                                    content_value(Value::String(completed_output.clone()))?,
                                ),
                                (
                                    "completed_at".to_string(),
                                    serde_json::json!(event_timestamp(completed_at)?),
                                ),
                                (
                                    "duration_ms".to_string(),
                                    serde_json::json!(
                                        completed_at.saturating_sub(
                                            node.as_ref()
                                                .and_then(|node| node.started_at)
                                                .unwrap_or(completed_at),
                                        )
                                    ),
                                ),
                            ]),
                        )?;
                        let completed = match event {
                            Some(event) => {
                                self.runs
                                    .commit_eventful_transition(
                                        StateMachineEventfulTransition::CompleteNode {
                                            run_id: correlation.state_machine_run_id.clone(),
                                            node_id: correlation.node_id.clone(),
                                            attempt: correlation.attempt,
                                            outcome: outcome.clone(),
                                            artifact_text: completed_output,
                                            responded_by: None,
                                            completed_at_ms: completed_at,
                                            event,
                                        },
                                    )
                                    .await?
                            }
                            None => {
                                self.runs
                                    .complete_node_attempt(
                                        &correlation.state_machine_run_id,
                                        &correlation.node_id,
                                        correlation.attempt,
                                        outcome.clone(),
                                        text,
                                        None,
                                        completed_at,
                                    )
                                    .await?
                            }
                        };
                        if completed {
                            self.observe_loop_node(&compiled, &run, &correlation.node_id,
                                correlation.attempt, "completed", Some(&outcome));
                            info!(target: RUNTIME_LOG_TARGET,
                                run_id = %correlation.state_machine_run_id,
                                group_id = %run.group_id,
                                session_id = %run.session_id,
                                node_id = %correlation.node_id,
                                attempt = correlation.attempt,
                                outcome = %outcome,
                                artifact_len = artifact_len,
                                "state_machine: node completed"
                            );
                            log_state_machine_node_result(
                                &run,
                                &correlation,
                                MessageLogStatus::Completed,
                                Some(&outcome),
                                None,
                                Some(artifact_len),
                            );
                            view = Some(
                                self.apply_completed_node_progression(
                                    &compiled,
                                    &group,
                                    &run,
                                    &correlation.node_id,
                                    &outcome,
                                    completed_at,
                                )
                                .await?,
                            );
                        }
                    }
                    JudgeEvaluationResult::Failed(error, _) => {
                        let failed_at = bcs_protocol::now_ms();
                        if self
                            .fail_retryable_node_attempt(
                                &correlation.state_machine_run_id,
                                &correlation.node_id,
                                correlation.attempt,
                                error.clone(),
                                failed_at,
                            )
                            .await?
                        {
                            warn!(target: RUNTIME_LOG_TARGET,
                                run_id = %correlation.state_machine_run_id,
                                group_id = %run.group_id,
                                session_id = %run.session_id,
                                node_id = %correlation.node_id,
                                attempt = correlation.attempt,
                                error = %error,
                                "state_machine: node failed"
                            );
                            log_state_machine_node_result(
                                &run,
                                &correlation,
                                MessageLogStatus::Failed,
                                None,
                                Some(&error),
                                None,
                            );
                            view = self
                                .resume_failed_node(
                                    &compiled,
                                    &group,
                                    &run,
                                    &correlation.node_id,
                                    correlation.attempt,
                                )
                                .await?;
                        } else {
                            view = self.run_view(&run.run_id).await?;
                        }
                    }
                }
            }
            ChatEventState::Error | ChatEventState::Aborted => {
                let error = extract_text(&cmd.event_payload)
                    .unwrap_or_else(|| format!("bot event state {:?}", cmd.state));
                if self
                    .fail_retryable_node_attempt(
                        &correlation.state_machine_run_id,
                        &correlation.node_id,
                        correlation.attempt,
                        error.clone(),
                        now,
                    )
                    .await?
                {
                    warn!(target: RUNTIME_LOG_TARGET,
                        run_id = %correlation.state_machine_run_id,
                        group_id = %run.group_id,
                        session_id = %run.session_id,
                        node_id = %correlation.node_id,
                        attempt = correlation.attempt,
                        state = %chat_event_state_slug(&cmd.state),
                        error = %error,
                        "state_machine: node failed"
                    );
                    log_state_machine_node_result(
                        &run,
                        &correlation,
                        MessageLogStatus::Failed,
                        None,
                        Some(&error),
                        None,
                    );
                    view = self
                        .resume_failed_node(
                            &compiled,
                            &group,
                            &run,
                            &correlation.node_id,
                            correlation.attempt,
                        )
                        .await?;
                }
            }
            _ => {
                view = self.run_view(&run.run_id).await?;
            }
        }
        Ok(HandleBotTerminalEventOutcome {
            consumed: true,
            view,
        })
    }
}
