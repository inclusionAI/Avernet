use super::*;
use bcs_service_api::{StateMachineChatResultPayload, StateMachineChatResultClaim,
    StateMachineChatResultStatus as Status, StateMachineChatResultOutcome as Outcome};

const CHAT_RESULT_TIMEOUT_MS: u64 = 90_000;

impl CollaborationRuntime {
    /// True only when the publication barrier is satisfied or inapplicable.
    /// Persistence errors propagate; they must not become a publication failure.
    pub(super) async fn ensure_chat_result(&self, run: &StateMachineRun, session: &Session,
        output: Option<&str>, completed_at_ms: u64) -> Result<bool, CollaborationRuntimeError> {
        let mut saved = self.runs.get_chat_result(&run.run_id).await?;
        if saved.is_none() {
            let (Some(sender_bot_id), Some(content)) = (run.created_by.as_ref(), output) else { return Ok(true); };
            if !session.participants.iter().any(|p| p.is_bot() && p.bot_uuid == *sender_bot_id) {
                return Ok(true);
            }
            // Node completion and its snapshot are the immutable source. No IO
            // can have started in this version before this save succeeds.
            let payload = StateMachineChatResultPayload {
                command: StateMachineResultPublishCommand { run_id: run.run_id.clone(), group_id: run.group_id.clone(),
                    session_id: run.session_id.clone(), sender_bot_id: sender_bot_id.clone(), content: content.into(),
                    created_at_ms: completed_at_ms },
                deadline_ms: completed_at_ms.saturating_add(CHAT_RESULT_TIMEOUT_MS),
            };
            if !self.runs.save_chat_result(payload).await? { return Ok(false); }
            saved = self.runs.get_chat_result(&run.run_id).await?;
        }
        let saved = saved.ok_or_else(|| CollaborationRuntimeError::InvalidRequest("Chat result checkpoint disappeared".into()))?;
        let cmd = &saved.payload.command;
        if cmd.run_id != run.run_id || cmd.group_id != run.group_id || cmd.session_id != run.session_id
            || Some(&cmd.sender_bot_id) != run.created_by.as_ref() || Some(cmd.content.as_str()) != output {
            return Err(CollaborationRuntimeError::InvalidRequest("Chat result checkpoint conflicts with original Run output or target".into()));
        }
        match saved.status {
            Status::Delivered => return Ok(true),
            Status::Superseded => return Ok(false),
            Status::Failed => {
                let error = saved.error.ok_or_else(|| CollaborationRuntimeError::InvalidRequest("Chat result failure has no saved error".into()))?;
                self.fail_run(run, format!("state-machine result publication failed: {error}")).await?;
                return Ok(false);
            }
            Status::Pending | Status::Delivering => {}
        }
        let now = bcs_protocol::now_ms();
        if now >= saved.payload.deadline_ms {
            self.runs.expire_chat_result(&run.run_id, now).await?;
        } else if saved.status == Status::Pending {
            if let Some(claim) = self.runs.claim_chat_result(&run.run_id, Uuid::new_v4().to_string(), now,
                now.saturating_add(30_000).min(saved.payload.deadline_ms)).await? {
                let result = self.publish_claimed_chat_result(session, &claim).await;
                // A failed ACK write preserves Delivering, even after release.
                self.runs.release_chat_result(&claim).await?;
                result?;
            }
        }
        let saved = self.runs.get_chat_result(&run.run_id).await?.ok_or_else(||
            CollaborationRuntimeError::InvalidRequest("Chat result checkpoint disappeared".into()))?;
        match saved.status {
            Status::Delivered => Ok(true),
            Status::Superseded => Ok(false),
            Status::Failed => {
                let error = saved.error.ok_or_else(|| CollaborationRuntimeError::InvalidRequest("Chat result failure has no saved error".into()))?;
                self.fail_run(run, format!("state-machine result publication failed: {error}")).await?;
                Ok(false)
            }
            Status::Pending | Status::Delivering => Ok(false),
        }
    }

    async fn publish_claimed_chat_result(&self, session: &Session, claim: &StateMachineChatResultClaim) -> Result<(), CollaborationRuntimeError> {
        let cmd = &claim.payload.command;
        let error = if !session.participants.iter().any(|p| p.is_bot() && p.bot_uuid == cmd.sender_bot_id) {
            Some("original Chat result sender is no longer a Session Bot".to_string())
        } else if self.result_publisher.is_none() {
            Some("state-machine chat result publisher is not configured".to_string())
        } else { None };
        if let Some(error) = error {
            self.runs.finish_chat_result(claim, Outcome::Failed { error }, bcs_protocol::now_ms()).await?;
            return Ok(());
        }
        if !self.runs.begin_chat_result_send(claim, bcs_protocol::now_ms()).await? { return Ok(()); }
        let remaining = claim.lease_until_ms.saturating_sub(bcs_protocol::now_ms());
        let publisher = self.result_publisher.as_ref().expect("checked publisher");
        let result = tokio::time::timeout(Duration::from_millis(remaining), publisher.publish_state_machine_result(cmd.clone())).await;
        let outcome = match result {
            Ok(Ok(())) => Outcome::Published,
            Ok(Err(error)) => Outcome::Failed { error: error.to_string() },
            // The lease expires with the call. Recovery must wait for the saved
            // deadline and fail uncertainty; it cannot accept this as an ACK.
            Err(_) => {
                warn!(run_id = %cmd.run_id, session_id = %cmd.session_id, lease_token = claim.token,
                    "state_machine: Chat result publication timed out; delivery is unknown");
                return Ok(());
            }
        };
        let committed = self.runs.finish_chat_result(claim, outcome, bcs_protocol::now_ms()).await?;
        info!(run_id = %cmd.run_id, session_id = %cmd.session_id, lease_token = claim.token, committed,
            "state_machine: Chat result publication acknowledgement");
        Ok(())
    }
}
