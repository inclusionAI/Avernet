use super::*;
use bcs_service_api::{StateMachineDispatchPayload, StateMachineDispatchTarget, StateMachineDispatchClaim,
    StateMachineDispatchStatus, StateMachineDispatchResult};

fn target_reference(target: &BotDeliveryTarget) -> StateMachineDispatchTarget {
    match target {
        BotDeliveryTarget::WebSocket { .. } => StateMachineDispatchTarget::WebSocket,
        BotDeliveryTarget::HttpProvider { provider_id, provider_bot_ref, protocol_version, .. } => StateMachineDispatchTarget::HttpProvider {
            provider_id: provider_id.clone(), provider_bot_ref: provider_bot_ref.clone(), protocol_version: protocol_version.clone(),
        },
    }
}

impl CollaborationRuntime {
    pub(super) async fn prepare_node_dispatch(&self, group: &Group, run: &StateMachineRun, node_run: &StateMachineNodeRun,
        prompt: String, started_at: u64) -> Result<StateMachineDispatchPayload, CollaborationRuntimeError> {
        let assignee_bot_id = node_run.assignee_bot_id.clone().ok_or_else(|| CollaborationRuntimeError::InvalidRequest("dispatch has no Bot assignee".into()))?;
        let node_id = &node_run.node_id;
        let attempt = node_run.attempt;
        let delivery_request_id = format!("smnode-{}-{}-{}", run.run_id, node_id, attempt);
        let group_context = group_context_input(group, &run.session_id);
        let target = if let Some(registry) = self.bot_registry.as_ref() {
            registry.resolve_delivery_target(&assignee_bot_id).await?
        } else {
            BotDeliveryTarget::WebSocket {
                bot_id: assignee_bot_id.clone(),
            }
        };
        let provider_tags = if target.is_http_provider() {
            self.sessions
                .get(&run.session_id)
                .await
                .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
                .and_then(|session| {
                    session
                        .participants
                        .into_iter()
                        .find(|participant| participant.bot_uuid == assignee_bot_id)
                })
                .map(|participant| participant.tags)
                .unwrap_or_default()
        } else {
            Vec::new()
        };
        let mut frame = build_chat_send_frame(
            &delivery_request_id,
            &group.id,
            &group_context,
            &prompt,
            BCS_STATE_MACHINE_MESSAGE_SENDER,
            BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
            &[],
            &assignee_bot_id,
            &provider_tags,
            &None,
            &None,
            false,
            BCS_PROTOCOL_VERSION,
            None,
            Some("state_machine".to_string()),
            Some(&run.session_id),
        );
        if let Some(timeout_ms) = node_run.node_timeout_ms {
            if let BcsFrame::Request(request) = &mut frame {
                if let Some(params) = request.params.as_mut().and_then(Value::as_object_mut) {
                    params.insert("timeout_ms".to_string(), Value::from(timeout_ms));
                }
            }
        }
        log_state_machine_node_dispatch(group, run, node_id, attempt, &assignee_bot_id, &delivery_request_id, &prompt);
        Ok(StateMachineDispatchPayload {
            run_id: run.run_id.clone(), node_id: node_id.clone(), attempt, group_id: run.group_id.clone(), session_id: run.session_id.clone(),
            assignee_bot_id, delivery_request_id, target: target_reference(&target),
            request: serde_json::to_value(frame).map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?,
            started_at_ms: started_at,
            deadline_ms: started_at.saturating_add(node_run.node_timeout_ms.unwrap_or(self.provider_chat_run_timeout_ms)),
        })
    }

    pub(super) async fn resume_node_dispatch(&self, compiled: &CompiledStateMachine, group: &Group, run: &StateMachineRun,
        node_id: &str, attempt: i32) -> Result<(), CollaborationRuntimeError> {
        let Some(node) = self.runs.get_node_run(&run.run_id, node_id).await? else { return Ok(()); };
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt || node.artifact_text.is_some()
            || !self.progression_run_is_active(&run.run_id).await? { return Ok(()); }
        let Some(saved) = self.runs.get_node_dispatch(&run.run_id, node_id, attempt).await? else {
            // A saved Provider run identity is positive acceptance evidence,
            // including legacy rows without a request checkpoint. Keep waiting
            // for its existing events/Node timeout, especially when disabled.
            if node.bot_delivery_run_id.is_some() { return Ok(()); }
            if self.runs.fail_missing_dispatch(&run.run_id, node_id, attempt, bcs_protocol::now_ms()).await? {
                Box::pin(self.resume_failed_node(compiled, group, run, node_id, attempt)).await?;
                return Ok(());
            }
            return Err(CollaborationRuntimeError::InvalidRequest("Running Bot has no persisted dispatch payload; original deadline/preparation grace has not expired or state changed".into()));
        };
        let p = &saved.payload;
        if p.run_id != run.run_id || p.node_id != node_id || p.attempt != attempt || p.group_id != run.group_id || p.session_id != run.session_id
            || node.started_at != Some(p.started_at_ms) || node.delivery_request_id.as_ref() != Some(&p.delivery_request_id)
            || node.assignee_bot_id.as_ref() != Some(&p.assignee_bot_id)
            || node.timeout_deadline_ms.is_some_and(|deadline| deadline != p.deadline_ms) {
            return Err(CollaborationRuntimeError::InvalidRequest("dispatch checkpoint does not match Node identity".into()));
        }
        match saved.status {
            StateMachineDispatchStatus::Delivered | StateMachineDispatchStatus::Superseded => return Ok(()),
            StateMachineDispatchStatus::Failed => {
                let error = saved.error.ok_or_else(|| CollaborationRuntimeError::InvalidRequest("failed dispatch has no saved error".into()))?;
                return self.fail_dispatched_node(compiled, group, run, node_id, attempt, error).await;
            }
            StateMachineDispatchStatus::Pending | StateMachineDispatchStatus::Delivering => {}
        }
        let now = bcs_protocol::now_ms();
        if now >= p.deadline_ms {
            let error = format!("state-machine node '{node_id}' dispatch reached its original deadline {}", p.deadline_ms);
            let retry = attempt >= 0 && attempt.checked_add(1).is_some_and(|next| next < node.max_attempts.max(1));
            if self.runs.expire_node_dispatch(FailStateMachineNodeAttempt {
                run_id: run.run_id.clone(), node_id: node_id.into(), attempt, error, completed_at_ms: now,
                action: if retry { StateMachineFailureAction::Retry } else { StateMachineFailureAction::FailRun },
            }).await? {
                Box::pin(self.resume_failed_node(compiled, group, run, node_id, attempt)).await?;
            }
            return Ok(());
        }
        // The transport contract has no uniform idempotent redelivery guarantee.
        // A send marker survives lost ACKs and write failures; never resend it.
        if saved.status == StateMachineDispatchStatus::Delivering { return Ok(()); }
        let Some(claim) = self.runs.claim_node_dispatch(&run.run_id, node_id, attempt,
            Uuid::new_v4().to_string(), now, now.saturating_add(30_000).min(p.deadline_ms)).await? else { return Ok(()); };
        let result = self.send_claimed_node_dispatch(compiled, group, run, &claim).await;
        // Release errors remain visible. This never clears the send marker or a newer owner.
        self.runs.release_node_dispatch(&claim).await?;
        result
    }

    async fn send_claimed_node_dispatch(&self, compiled: &CompiledStateMachine, group: &Group, run: &StateMachineRun,
        claim: &StateMachineDispatchClaim) -> Result<(), CollaborationRuntimeError> {
        let p = &claim.payload;
        let frame: BcsFrame = serde_json::from_value(p.request.clone()).map_err(|_| CollaborationRuntimeError::InvalidRequest("invalid persisted dispatch request".into()))?;
        if !matches!(&frame, BcsFrame::Request(request) if request.id == p.delivery_request_id && request.method == "chat.send") {
            return Err(CollaborationRuntimeError::InvalidRequest("persisted dispatch request identity mismatch".into()));
        }
        let resolved = if let Some(registry) = self.bot_registry.as_ref() {
            registry.resolve_delivery_target(&p.assignee_bot_id).await
        } else { Ok(BotDeliveryTarget::WebSocket { bot_id: p.assignee_bot_id.clone() }) };
        let target = match resolved {
            Ok(target) if target_reference(&target) == p.target && target.bot_id() == p.assignee_bot_id => target,
            Ok(_) => {
                let error = "saved dispatch target reference has changed".to_string();
                self.reject_claimed_dispatch(compiled, group, run, claim, error.clone()).await?;
                return Err(CollaborationRuntimeError::InvalidRequest(error));
            }
            Err(error) => {
                self.reject_claimed_dispatch(compiled, group, run, claim, error.to_string()).await?;
                return Err(error.into());
            }
        };
        self.runs.upsert_delivery_correlation(StateMachineDeliveryCorrelation {
            state_machine_run_id: p.run_id.clone(), node_id: p.node_id.clone(), attempt: p.attempt, assignee_bot_id: p.assignee_bot_id.clone(),
            delivery_request_id: p.delivery_request_id.clone(), bot_delivery_run_id: None,
        }).await?;
        if let Some(context) = self.bot_run_context.as_ref() {
            context.put_context(BotRunContext { run_id: p.delivery_request_id.clone(), bot_id: p.assignee_bot_id.clone(),
                // Keep the trusted V3 run scope so uplink identity can be
                // validated. The delivery correlation above makes the runtime
                // consume these responses before ordinary group relay.
                group_id: p.group_id.clone(), bcs_session_id: Some(p.session_id.clone()),
                deadline_ms: p.deadline_ms, terminal: false }).await;
        }
        if !self.runs.begin_node_dispatch_send(claim, bcs_protocol::now_ms()).await? { return Ok(()); }
        info!(run_id = %run.run_id, group_id = %group.id, session_id = %run.session_id, node_id = %p.node_id,
            attempt = p.attempt, assignee_bot_id = %p.assignee_bot_id, delivery_request_id = %p.delivery_request_id,
            "state_machine: node dispatch started");
        let delivered = self.bot_delivery.deliver(BotDeliveryCommand { target, run_id: p.delivery_request_id.clone(), frame,
            delivery_kind: BotDeliveryKind::TaskDispatch, provider_transport: bcs_service_api::ProviderTransportPreference::SseFirst, provider_bypass_headers: Vec::new() }).await;
        let (target_bot, accepted, error) = match &delivered {
            Ok(result) => (Some(result.target_bot_id.clone()), result.delivered, result.error.as_ref().map(|error| error.to_string())),
            Err(error) => (None, false, Some(error.to_string())),
        };
        log_state_machine_delivery_result(group, run, &p.node_id, p.attempt, &p.assignee_bot_id, &p.delivery_request_id,
            target_bot.as_deref(), accepted, error.as_deref(), if accepted { None } else { Some("deliver") });
        info!(run_id = %run.run_id, group_id = %group.id, session_id = %run.session_id, node_id = %p.node_id,
            attempt = p.attempt, assignee_bot_id = %p.assignee_bot_id, delivery_request_id = %p.delivery_request_id,
            delivered = accepted, error = ?error, "state_machine: node dispatch completed");
        if !accepted {
            let error = format!("state-machine node delivery failed for bot '{}': {}", p.assignee_bot_id,
                error.unwrap_or_else(|| "delivery target did not accept the request".into()));
            self.reject_claimed_dispatch(compiled, group, run, claim, error.clone()).await?;
            return Err(match delivered { Err(cause) => cause.into(), Ok(_) => CollaborationRuntimeError::InvalidRequest(error) });
        }
        // A terminal event may already have advanced the Node. A stale ACK is
        // not authority to alter that newer state; its checkpoint can be retired.
        self.runs.finish_node_dispatch(claim, StateMachineDispatchResult::Accepted, bcs_protocol::now_ms()).await?;
        self.runs.supersede_inactive_node_dispatches(&run.run_id).await?;
        Ok(())
    }

    async fn reject_claimed_dispatch(&self, compiled: &CompiledStateMachine, group: &Group, run: &StateMachineRun,
        claim: &StateMachineDispatchClaim, error: String) -> Result<(), CollaborationRuntimeError> {
        if self.runs.finish_node_dispatch(claim, StateMachineDispatchResult::Rejected { error: error.clone() }, bcs_protocol::now_ms()).await? {
            self.fail_dispatched_node(compiled, group, run, &claim.payload.node_id, claim.payload.attempt, error.clone()).await?;
        }
        Ok(())
    }
}
