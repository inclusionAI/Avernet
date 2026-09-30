use super::*;
use bcs_domain::STATE_MACHINE_OUTPUT_MESSAGE_TYPE;
use bcs_domain::state_machine_history;

impl CollaborationRuntime {
    pub(super) fn prepare_node_event(
        &self, compiled: &CompiledStateMachine, event_type: &str, run: &StateMachineRun,
        subject_type: &str, node_id: &str, producer_key_suffix: &str,
        actor_id: Option<&str>, actor_type: Option<EventActorType>, occurred_at_ms: u64,
        mut data: BTreeMap<String, Value>,
    ) -> Result<Option<bcs_service_api::port::repo::AppendEventRecord>, CollaborationRuntimeError> {
        if let Some(execution) = crate::definition::execution_metadata_for_node(compiled, node_id)? {
            data.insert("execution".into(), serde_json::json!(execution));
        }
        self.prepare_public_event(event_type, run, subject_type, node_id, producer_key_suffix,
            actor_id, actor_type, occurred_at_ms, data)
    }

    /// The Completed node is the immutable source; the existing message primary
    /// key makes foreground/recovery writes idempotent without a new transaction.
    pub(super) async fn persist_node_output(
        &self, compiled: &CompiledStateMachine, group: &Group, run: &StateMachineRun, node: &StateMachineNodeRun,
    ) -> Result<(), CollaborationRuntimeError> {
        if self.history_persistence_enabled || self.has_history_output(run, node).await? {
            return self.preserve_node_history(compiled, group, run, node).await;
        }
        // Preserve the pre-Loop v1 synthetic-history path.
        if compiled.execution_plan.is_none() { return Ok(()); }
        let execution = crate::definition::execution_metadata_for_node(compiled, &node.node_id)?;
        if node.status != StateMachineNodeStatus::Completed {
            return Err(CollaborationRuntimeError::InvalidRequest("output history requires a Completed node".into()));
        }
        let text = node.artifact_text.as_ref().ok_or_else(||
            CollaborationRuntimeError::InvalidRequest("Completed node has no output".into()))?;
        let created_at = node.completed_at.ok_or_else(||
            CollaborationRuntimeError::InvalidRequest("Completed node has no timestamp".into()))?;
        let sender = node.responded_by.as_ref().or(node.assignee_bot_id.as_ref()).ok_or_else(||
            CollaborationRuntimeError::InvalidRequest("Completed node has no sender".into()))?;
        let is_human = node.responded_by.is_some();
        let audience = if is_human {
            MessageAudience::Directed { actor_ids: vec![sender.clone()] }
        } else { MessageAudience::FullOnly };
        let content = serde_json::json!({
            "text": text,
            "metadata": state_machine_message_metadata(run, node, "output", execution.as_ref()),
        });
        let repo = self.message_repo.as_ref().ok_or_else(||
            CollaborationRuntimeError::InvalidRequest("output history repository is not configured".into()))?;
        let id = output_message_id(node);
        // Keep the client key so recovery reuses outputs already stored by older SQLite builds.
        let client_id = output_message_key(node);
        let saved = repo.append_message_with_id(id.clone(), NewMessage {
            group_id: group.id.clone(), session_id: run.session_id.clone(), sender_id: sender.clone(),
            sender_type: if is_human { SenderType::Human } else { SenderType::Bot },
            message_type: STATE_MACHINE_OUTPUT_MESSAGE_TYPE.into(), content: content.clone(), client_msg_id: Some(client_id.clone()),
            owner_bot_id: None, created_at, run_id: run.run_id.clone(),
            visibility_domain: MessageVisibilityDomain::StateMachine, audience: Some(audience.clone()),
        }).await.map_err(message_history_error)?;
        if saved.content != content || saved.group_id != run.group_id || saved.session_id != run.session_id
            || saved.run_id != run.run_id || saved.sender_id != *sender || saved.created_at != created_at
            || saved.sender_type != if is_human { SenderType::Human } else { SenderType::Bot }
            || saved.message_type != STATE_MACHINE_OUTPUT_MESSAGE_TYPE || saved.client_msg_id.as_deref() != Some(&client_id)
            || saved.visibility_domain != Some(MessageVisibilityDomain::StateMachine) || saved.audience != Some(audience) {
            return Err(CollaborationRuntimeError::Conflict("output history conflicts with its immutable node".into()));
        }
        Ok(())
    }
}

pub(super) fn output_message_id(node: &StateMachineNodeRun) -> String {
    physical_message_id(&output_message_key(node))
}

pub(super) fn output_message_key(node: &StateMachineNodeRun) -> String {
    state_machine_history::output_message_key(&node.run_id, &node.node_id, node.attempt)
}

pub(super) fn message_history_error(error: bcs_service_api::port::repo::MessageRepoError) -> CollaborationRuntimeError {
    CollaborationRuntimeError::Internal(ServiceError::InternalError(format!("state-machine output history failed: {error}")))
}

pub(super) fn stored_output_message(message: &bcs_domain::PersistedMessage) -> Result<GroupMessage, CollaborationRuntimeError> {
    if message.content.get("text").and_then(Value::as_str).is_none() || message.content.get("metadata").is_none() {
        return Err(CollaborationRuntimeError::InvalidRequest("stored output has no text or metadata".into()));
    }
    project_state_machine_message(message).ok_or_else(||
        CollaborationRuntimeError::InvalidRequest("stored output has an unexpected message type".into()))
}
