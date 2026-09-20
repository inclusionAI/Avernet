use super::*;
use bcs_service_api::{StateMachineTerminalImPayload, StateMachineTerminalImClaim,
    StateMachineTerminalImProgress as Progress, StateMachineTerminalImDelivery as Delivery,
    StateMachineTerminalImStatus as Status};

impl CollaborationRuntime {
    /// Freeze the complete target set before Session completion removes it from
    /// the Session recovery page. This is a local write, never an external send.
    pub(super) async fn prepare_terminal_im(&self, run: &StateMachineRun, workflow_name: String,
        status: StateMachineTerminalStatus, output: Option<String>) -> Result<(), CollaborationRuntimeError> {
        let Some(outbound) = self.session_channel_outbound.as_ref() else { return Ok(()); };
        if self.runs.get_terminal_im(&run.run_id).await?.is_some() { return Ok(()); }
        let terminal = self.runs.get_run(&run.run_id).await?.ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))?;
        let activation = terminal.session_activation_count.ok_or_else(|| CollaborationRuntimeError::InvalidRequest("terminal IM requires original Session activation".into()))?;
        let created_at_ms = terminal.completed_at.ok_or_else(|| CollaborationRuntimeError::InvalidRequest("terminal IM requires original Run completion time".into()))?;
        let event = StateMachineTerminalEvent { group_id: terminal.group_id, session_id: terminal.session_id,
            run_id: terminal.run_id, workflow_name, status, output };
        let notifications = outbound.prepare_state_machine_terminal(&event).await?;
        if !self.runs.save_terminal_im(StateMachineTerminalImPayload { event, session_activation_count: activation,
            notifications, created_at_ms, deadline_ms: created_at_ms.saturating_add(90_000) }).await? {
            return Err(CollaborationRuntimeError::Conflict("terminal IM intent did not match its Run/Session activation".into()));
        }
        Ok(())
    }

    pub(super) async fn resume_terminal_im(&self, run_id: &str) -> Result<(), CollaborationRuntimeError> {
        let Some(saved) = self.runs.get_terminal_im(run_id).await? else { return Ok(()); };
        if saved.status != Status::Pending { return Ok(()); }
        let snapshot = self.definitions.get_run_snapshot(run_id).await?.ok_or_else(||
            CollaborationRuntimeError::InvalidRequest("terminal IM recovery requires original Run snapshot".into()))?;
        let loaded = crate::snapshot::load_state_machine_snapshot(snapshot)?;
        if loaded.plan.is_some() && !self.loop_execution_enabled {
            return Err(CollaborationRuntimeError::InvalidRequest("v2 terminal IM recovery is disabled".into()));
        }
        if self.runs.supersede_terminal_im(run_id).await? { return Ok(()); }
        let now = bcs_protocol::now_ms();
        let Some(claim) = self.runs.claim_terminal_im(run_id, Uuid::new_v4().to_string(), now, now.saturating_add(30_000)).await? else { return Ok(()); };
        let result = self.deliver_claimed_terminal_im(&claim).await;
        self.runs.release_terminal_im(&claim).await?;
        result
    }

    async fn terminal_im_progress(&self, claim: &StateMachineTerminalImClaim, progress: &mut Progress, next: Progress) -> Result<bool, CollaborationRuntimeError> {
        if !self.runs.update_terminal_im_progress(claim, progress.clone(), next.clone(), bcs_protocol::now_ms()).await? { return Ok(false); }
        *progress = next; Ok(true)
    }

    async fn deliver_claimed_terminal_im(&self, claim: &StateMachineTerminalImClaim) -> Result<(), CollaborationRuntimeError> {
        let outbound = self.session_channel_outbound.as_ref().ok_or_else(|| CollaborationRuntimeError::InvalidRequest("terminal IM delivery is not configured".into()))?;
        let payload = &claim.checkpoint.payload;
        let mut progress = claim.checkpoint.progress.clone();
        if !progress.cleanup_completed {
            outbound.finish_state_machine_terminal(&payload.event).await?;
            let mut next = progress.clone(); next.cleanup_completed = true;
            if !self.terminal_im_progress(claim, &mut progress, next).await? { return Ok(()); }
        }
        for (index, notification) in payload.notifications.iter().enumerate() {
            let now = bcs_protocol::now_ms();
            let mut next = progress.clone();
            match &progress.deliveries[index] {
                Delivery::Delivered { .. } | Delivery::Failed { .. } => continue,
                Delivery::Sending => {
                    next.deliveries[index] = Delivery::Failed { error: "terminal IM result is unknown after interrupted delivery; not resent".into() };
                    if !self.terminal_im_progress(claim, &mut progress, next).await? { return Ok(()); }
                    continue;
                }
                Delivery::Pending { next_attempt_at_ms, .. } => {
                    if now >= payload.deadline_ms {
                        next.deliveries[index] = Delivery::Failed { error: "terminal IM original deadline reached before send".into() };
                        if !self.terminal_im_progress(claim, &mut progress, next).await? { return Ok(()); }
                        continue;
                    }
                    if now < *next_attempt_at_ms { continue; }
                }
            }
            // This port explicitly guarantees preflight performs no external send.
            if let Err(error) = outbound.validate_terminal_notification(notification).await {
                next.deliveries[index] = Delivery::Pending { next_attempt_at_ms: now.saturating_add(1_000).min(payload.deadline_ms), last_error: Some(error.to_string()) };
                if !self.terminal_im_progress(claim, &mut progress, next).await? { return Ok(()); }
                continue;
            }
            if bcs_protocol::now_ms() >= payload.deadline_ms {
                next.deliveries[index] = Delivery::Failed { error: "terminal IM original deadline reached during preflight".into() };
                if !self.terminal_im_progress(claim, &mut progress, next).await? { return Ok(()); }
                continue;
            }
            next.deliveries[index] = Delivery::Sending;
            if !self.terminal_im_progress(claim, &mut progress, next).await? { return Ok(()); }
            let remaining = claim.lease_until_ms.min(payload.deadline_ms).saturating_sub(bcs_protocol::now_ms());
            if remaining == 0 { return Ok(()); }
            let result = tokio::time::timeout(Duration::from_millis(remaining), outbound.deliver_terminal_notification(&payload.event, notification)).await;
            let mut next = progress.clone();
            next.deliveries[index] = match result {
                Ok(Ok(provider_message_ref)) => Delivery::Delivered { provider_message_ref },
                Ok(Err(error)) => Delivery::Failed { error: format!("terminal IM delivery failed or is unknown; not resent: {error}") },
                Err(_) => Delivery::Failed { error: "terminal IM delivery timed out; result unknown, not resent".into() },
            };
            if !self.terminal_im_progress(claim, &mut progress, next).await? { return Ok(()); }
            info!(run_id = %payload.event.run_id, session_id = %payload.event.session_id,
                activation = payload.session_activation_count, recipient = index, lease_token = claim.token,
                delivered = matches!(progress.deliveries[index], Delivery::Delivered { .. }), "state_machine: terminal IM checkpoint updated");
        }
        Ok(())
    }
}
