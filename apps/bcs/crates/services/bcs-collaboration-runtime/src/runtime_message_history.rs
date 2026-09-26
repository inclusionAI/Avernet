//! Reliable local message projection. No external delivery takes place here.
use super::*;
use bcs_service_api::port::repo::collaboration_history::*;

fn identity(
    run: &StateMachineRun,
    node: &StateMachineNodeRun,
    event: &str,
) -> StateMachineHistoryIdentity {
    StateMachineHistoryIdentity {
        session_id: run.session_id.clone(),
        run_id: run.run_id.clone(),
        node_id: node.node_id.clone(),
        attempt: node.attempt,
        event: event.into(),
    }
}
fn payload_identity(payload: &StateMachineHistoryPayload) -> StateMachineHistoryIdentity {
    let state = &payload.message.content["metadata"]["state_machine"];
    StateMachineHistoryIdentity {
        session_id: payload.message.session_id.clone(),
        run_id: payload.message.run_id.clone(),
        node_id: state["node_id"].as_str().unwrap().into(),
        attempt: state["attempt"].as_i64().unwrap() as i32,
        event: state["event"].as_str().unwrap().into(),
    }
}
fn matches_message(saved: &bcs_domain::PersistedMessage, wanted: &NewMessage) -> bool {
    use bcs_domain::state_machine_history::StateMachineHistoryKey;
    let same_identity = saved
        .content
        .get("metadata")
        .and_then(StateMachineHistoryKey::from_metadata)
        == wanted
            .content
            .get("metadata")
            .and_then(StateMachineHistoryKey::from_metadata);
    let legacy = matches!(
        wanted.message_type.as_str(),
        bcs_domain::STATE_MACHINE_OUTPUT_MESSAGE_TYPE
            | bcs_domain::STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE
    ) && same_identity
        && wanted
            .content
            .get("metadata")
            .and_then(StateMachineHistoryKey::from_metadata)
            .is_some()
        && saved.content["metadata"]["state_machine"]["history_schema_version"].is_null();
    saved.group_id == wanted.group_id
        && saved.session_id == wanted.session_id
        && saved.run_id == wanted.run_id
        && saved.sender_id == wanted.sender_id
        && saved.sender_type == wanted.sender_type
        && saved.owner_bot_id == wanted.owner_bot_id
        && saved.visibility_domain == Some(wanted.visibility_domain)
        && saved.audience == wanted.audience
        && same_identity
        && saved.content["text"] == wanted.content["text"]
        && (saved.message_type == wanted.message_type
            || (legacy
                && wanted.message_type == bcs_domain::STATE_MACHINE_OUTPUT_MESSAGE_TYPE
                && saved.message_type
                    == bcs_domain::STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE))
        && (legacy
            || (saved.content == wanted.content
                && saved.created_at == wanted.created_at
                && saved.message_type == wanted.message_type))
}

impl CollaborationRuntime {
    pub(super) async fn project_history_checkpoint(
        &self,
        checkpoint: &StateMachineHistoryCheckpoint,
    ) -> Result<(), CollaborationRuntimeError> {
        let repo = self.message_repo.as_ref().ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest("history message repository missing".into())
        })?;
        let p = &checkpoint.payload;
        // Stable IDs retain an existing row's original sequence and payload.
        let saved = repo.append_message_with_id(p.message_id.clone(), p.message.clone()).await.map_err(|error| {
            warn!(target:"state_machine_history",outcome="write_failed",stage="append","history projection failed");
            message_history_error(error)
        })?;
        if !matches_message(&saved, &p.message) {
            warn!(target: "state_machine_history", outcome = "conflict", run_id = %p.message.run_id, message_id = %p.message_id, "history projection rejected");
            return Err(CollaborationRuntimeError::Conflict(
                "history message conflicts with accepted fact".into(),
            ));
        }
        self.runs.confirm_history_message(checkpoint, bcs_protocol::now_ms()).await.map_err(|error| {
            warn!(target:"state_machine_history",outcome="write_failed",stage="confirm","history confirmation failed");
            CollaborationRuntimeError::from(error)
        })?;
        tracing::info!(target: "state_machine_history", outcome = if checkpoint.delivered_at_ms.is_some() {"idempotent"} else {"persisted"}, lag_ms = bcs_protocol::now_ms().saturating_sub(p.message.created_at), "history projection completed");
        Ok(())
    }

    async fn commit_history(
        &self,
        payload: StateMachineHistoryPayload,
        mutation: StateMachineHistoryMutation,
        event: Option<bcs_service_api::port::repo::AppendEventRecord>,
    ) -> Result<bool, CollaborationRuntimeError> {
        let id = payload_identity(&payload);
        if let Some(saved) = self.runs.get_history_message(&id).await? {
            if saved.payload.message.content["text"] != payload.message.content["text"]
                || saved.payload.message.sender_id != payload.message.sender_id
                || saved.payload.message.audience != payload.message.audience
            {
                return Ok(false);
            }
            self.project_history_checkpoint(&saved).await?;
            return Ok(true);
        }
        let accepted = self
            .runs
            .accept_history_message(AcceptStateMachineHistory {
                payload: payload.clone(),
                mutation,
                event,
            })
            .await;
        match accepted {
            Ok(Some(saved)) => {
                tracing::info!(target:"state_machine_history",outcome="accepted","history fact committed");
                self.project_history_checkpoint(&saved).await?;
                Ok(true)
            }
            Ok(None) => Ok(false),
            Err(error) => {
                // Only the exceptional race/unknown-commit path re-reads. Use
                // the winning timestamp and audience, never rebuild that fact.
                if let Some(saved) = self.runs.get_history_message(&id).await? {
                    if saved.payload.message.content["text"] == payload.message.content["text"]
                        && saved.payload.message.sender_id == payload.message.sender_id
                        && saved.payload.message.audience == payload.message.audience
                    {
                        self.project_history_checkpoint(&saved).await?;
                        return Ok(true);
                    }
                }
                Err(error.into())
            }
        }
    }

    async fn output_payload(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node: &StateMachineNodeRun,
        text: &str,
        responder: Option<&str>,
        at: u64,
    ) -> Result<StateMachineHistoryPayload, CollaborationRuntimeError> {
        let sender = responder
            .or(node.assignee_bot_id.as_deref())
            .ok_or_else(|| {
                CollaborationRuntimeError::InvalidRequest("output sender missing".into())
            })?;
        let mut frozen = node.clone();
        frozen.responded_by = responder.map(str::to_owned);
        let mut metadata = state_machine_message_metadata(
            run,
            &frozen,
            "output",
            crate::definition::execution_metadata_for_node(compiled, &node.node_id)?.as_ref(),
        );
        metadata["state_machine"]["history_schema_version"] = serde_json::json!(1);
        let name = if responder.is_some() {
            self.sessions
                .get(&run.session_id)
                .await
                .map_err(|e| CollaborationRuntimeError::InvalidRequest(e.to_string()))?
                .and_then(|s| s.participants.into_iter().find(|p| p.bot_uuid == sender))
                .and_then(|p| p.bot_name)
        } else {
            bot_display_name(group, sender)
        };
        Ok(StateMachineHistoryPayload {
            schema_version: 1,
            message_id: output_message_id(node),
            message: NewMessage {
                group_id: run.group_id.clone(),
                session_id: run.session_id.clone(),
                run_id: run.run_id.clone(),
                sender_id: sender.into(),
                sender_type: if responder.is_some() {
                    SenderType::Human
                } else {
                    SenderType::Bot
                },
                message_type: bcs_domain::STATE_MACHINE_OUTPUT_MESSAGE_TYPE.into(),
                content: serde_json::json!({"text":text,"bot_name":name,"metadata":metadata}),
                client_msg_id: Some(output_message_key(node)),
                owner_bot_id: None,
                visibility_domain: MessageVisibilityDomain::StateMachine,
                audience: Some(if responder.is_some() {
                    MessageAudience::Directed {
                        actor_ids: vec![sender.into()],
                    }
                } else {
                    MessageAudience::FullOnly
                }),
                created_at: at,
            },
        })
    }

    pub(super) async fn accept_output_history(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node: &StateMachineNodeRun,
        text: &str,
        responder: Option<&str>,
        judging: bool,
    ) -> Result<bool, CollaborationRuntimeError> {
        let payload = self
            .output_payload(
                compiled,
                group,
                run,
                node,
                text,
                responder,
                bcs_protocol::now_ms(),
            )
            .await?;
        self.commit_history(
            payload,
            StateMachineHistoryMutation::AcceptOutput { judging },
            None,
        )
        .await
    }

    async fn prompt_payload(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
        node: &StateMachineNodeRun,
    ) -> Result<StateMachineHistoryPayload, CollaborationRuntimeError> {
        let CollaborationRuntimeDefinition::StateMachine(machine) = &compiled.definition.runtime
        else {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "not a state machine".into(),
            ));
        };
        let definition = machine.nodes.get(&node.node_id).ok_or_else(|| {
            CollaborationRuntimeError::InvalidDefinition("missing human node".into())
        })?;
        let session = self
            .sessions
            .get(&run.session_id)
            .await
            .map_err(|e| CollaborationRuntimeError::InvalidRequest(e.to_string()))?
            .ok_or_else(|| CollaborationRuntimeError::InvalidRequest("Session missing".into()))?;
        let actors = human_input_prompt_actor_ids(definition, &session)?;
        let pending = self.pending_human_node_view(compiled, run, node).await?;
        let mut metadata = state_machine_human_input_prompt_metadata(run, node, &pending, &actors);
        metadata["state_machine"]["history_schema_version"] = serde_json::json!(1);
        let key = format!(
            "{}:{}:{}:human-input-prompt",
            run.run_id, node.node_id, node.attempt
        );
        Ok(StateMachineHistoryPayload {
            schema_version: 1,
            message_id: physical_message_id(&key),
            message: NewMessage {
                group_id: run.group_id.clone(),
                session_id: run.session_id.clone(),
                run_id: run.run_id.clone(),
                sender_id: BCS_STATE_MACHINE_MESSAGE_SENDER.into(),
                sender_type: SenderType::Bot,
                message_type: bcs_domain::STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE.into(),
                content: serde_json::json!({"text":pending.instruction,"bot_name":BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,"metadata":metadata}),
                client_msg_id: Some(key),
                owner_bot_id: None,
                visibility_domain: MessageVisibilityDomain::StateMachine,
                audience: Some(
                    MessageAudience::directed(actors)
                        .map_err(|e| CollaborationRuntimeError::InvalidRequest(e.to_string()))?,
                ),
                created_at: node.started_at.ok_or_else(|| {
                    CollaborationRuntimeError::InvalidRequest(
                        "prompt activation time missing".into(),
                    )
                })?,
            },
        })
    }
    pub(super) async fn activate_human_with_history(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
        node: &StateMachineNodeRun,
        command: MarkHumanNodeRunningCommand,
        event: Option<bcs_service_api::port::repo::AppendEventRecord>,
    ) -> Result<bool, CollaborationRuntimeError> {
        let mut activated = node.clone();
        activated.status = StateMachineNodeStatus::Running;
        activated.started_at = Some(command.started_at_ms);
        activated.timeout_deadline_ms = Some(command.timeout_deadline_ms);
        let payload = self.prompt_payload(compiled, run, &activated).await?;
        self.commit_history(
            payload,
            StateMachineHistoryMutation::ActivateHuman(command),
            event,
        )
        .await
    }

    /// Fence existing evidence before any overwrite, retry, Judge or completion.
    pub(super) async fn preserve_node_history(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node: &StateMachineNodeRun,
    ) -> Result<(), CollaborationRuntimeError> {
        let human = matches!(&compiled.definition.runtime, CollaborationRuntimeDefinition::StateMachine(m) if m.nodes.get(&node.node_id).is_some_and(|n|n.kind==StateMachineNodeKind::HumanInput));
        if human && node.started_at.is_some() && self.history_persistence_enabled {
            if let Some(saved) = self
                .runs
                .get_history_message(&identity(run, node, "human_input_prompt"))
                .await?
            {
                self.project_history_checkpoint(&saved).await?;
            } else {
                let payload = self.prompt_payload(compiled, run, node).await?;
                if !self
                    .commit_history(
                        payload,
                        StateMachineHistoryMutation::Preserve(node.clone()),
                        None,
                    )
                    .await?
                {
                    return Err(CollaborationRuntimeError::Conflict(
                        "human prompt changed while freezing history".into(),
                    ));
                }
            }
        }
        if let Some(text) = node.artifact_text.as_deref() {
            if let Some(saved) = self
                .runs
                .get_history_message(&identity(run, node, "output"))
                .await?
            {
                return self.project_history_checkpoint(&saved).await;
            }
            if self.history_persistence_enabled {
                // An unversioned in-flight input has no trustworthy acceptance
                // timestamp. Refuse to erase it; migration must resolve that gap.
                let at=node.completed_at.ok_or_else(||CollaborationRuntimeError::InvalidRequest("missing_source_evidence: existing output has no acceptance/completion time; drain legacy accepted inputs before enabling persistence".into()))?;
                let payload = self
                    .output_payload(
                        compiled,
                        group,
                        run,
                        node,
                        text,
                        node.responded_by.as_deref(),
                        at,
                    )
                    .await?;
                if !self
                    .commit_history(
                        payload,
                        StateMachineHistoryMutation::Preserve(node.clone()),
                        None,
                    )
                    .await?
                {
                    return Err(CollaborationRuntimeError::Conflict(
                        "output changed while freezing history".into(),
                    ));
                }
            }
        }
        Ok(())
    }

    pub(super) async fn recover_history_page(
        &self,
        cursor: Option<String>,
        limit: usize,
    ) -> Result<bcs_service_api::StateMachineProgressionRecoveryPage, CollaborationRuntimeError>
    {
        let after: Option<StateMachineHistoryCursor> = cursor
            .as_deref()
            .map(serde_json::from_str)
            .transpose()
            .map_err(|_| {
                CollaborationRuntimeError::InvalidRequest("invalid history cursor".into())
            })?;
        let batch = self
            .runs
            .list_history_messages_pending(after.as_ref(), limit.min(100))
            .await?;
        let mut page = bcs_service_api::StateMachineProgressionRecoveryPage {
            scanned: batch.checkpoints.len() + batch.failures.len(),
            next_run_id: batch
                .next
                .map(|c| serde_json::to_string(&c))
                .transpose()
                .map_err(|e| CollaborationRuntimeError::InvalidRequest(e.to_string()))?,
            ..Default::default()
        };
        tracing::info!(target:"state_machine_history",pending_in_page=batch.pending_count,oldest_pending_age_ms=batch.oldest_pending_at_ms.map(|t|bcs_protocol::now_ms().saturating_sub(t)),"history recovery page");
        for failure in batch.failures {
            page.failures
                .push(bcs_service_api::StateMachineProgressionRecoveryFailure {
                    run_id: failure.run_id,
                    error: "invalid history checkpoint".into(),
                });
        }
        for checkpoint in batch.checkpoints {
            match self.project_history_checkpoint(&checkpoint).await {
                Ok(()) => page.reconciled += 1,
                Err(_) => {
                    page.failures
                        .push(bcs_service_api::StateMachineProgressionRecoveryFailure {
                            run_id: checkpoint.payload.message.run_id,
                            error: "history projection failed".into(),
                        })
                }
            }
        }
        Ok(page)
    }
}

impl CollaborationRuntime {
    pub(super) async fn has_history_output(
        &self,
        run: &StateMachineRun,
        node: &StateMachineNodeRun,
    ) -> Result<bool, CollaborationRuntimeError> {
        Ok(self
            .runs
            .get_history_message(&identity(run, node, "output"))
            .await?
            .is_some())
    }

    pub(super) async fn resume_accepted_history_output(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node: &StateMachineNodeRun,
    ) -> Result<(), CollaborationRuntimeError> {
        // Old uncheckpointed Running inputs remain on the legacy recovery path.
        if !self.has_history_output(run, node).await? {
            if self.history_persistence_enabled {
                self.preserve_node_history(compiled, group, run, node)
                    .await?;
            }
            return Ok(());
        }
        self.preserve_node_history(compiled, group, run, node)
            .await?;
        let text = node.artifact_text.as_deref().ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest("accepted output missing".into())
        })?;
        let JudgeEvaluationResult::Outcome(outcome, _) = self
            .evaluate_node_outcome(compiled, run, &node.node_id, node.attempt, text)
            .await?
        else {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "non-Judge output did not produce an outcome".into(),
            ));
        };
        let now = bcs_protocol::now_ms();
        let (actor, actor_type) = if let Some(actor) = node.responded_by.as_deref() {
            (Some(actor), EventActorType::Human)
        } else {
            (node.assignee_bot_id.as_deref(), EventActorType::Bot)
        };
        let event = self.prepare_node_event(
            compiled,
            "state_machine.node.completed",
            run,
            "state_machine.node",
            &node.node_id,
            &format!("{}:{}:completed", node.node_id, node.attempt),
            actor,
            Some(actor_type),
            now,
            BTreeMap::from([
                ("run_id".into(), serde_json::json!(run.run_id)),
                ("node_id".into(), serde_json::json!(node.node_id)),
                ("attempt".into(), serde_json::json!(node.attempt)),
                ("outcome".into(), serde_json::json!(outcome)),
                ("output".into(), content_value(Value::String(text.into()))?),
                (
                    "completed_at".into(),
                    serde_json::json!(event_timestamp(now)?),
                ),
                (
                    "duration_ms".into(),
                    serde_json::json!(now.saturating_sub(node.started_at.unwrap_or(now))),
                ),
            ]),
        )?;
        if let Some(event) = event {
            self.runs
                .commit_eventful_transition(StateMachineEventfulTransition::CompleteNode {
                    run_id: run.run_id.clone(),
                    node_id: node.node_id.clone(),
                    attempt: node.attempt,
                    outcome,
                    artifact_text: text.into(),
                    responded_by: node.responded_by.clone(),
                    completed_at_ms: now,
                    event,
                })
                .await?;
        } else {
            self.runs
                .complete_node_attempt(
                    &run.run_id,
                    &node.node_id,
                    node.attempt,
                    outcome,
                    text.into(),
                    node.responded_by.clone(),
                    now,
                )
                .await?;
        }
        Ok(())
    }
}

impl CollaborationRuntime {
    pub(super) async fn persisted_history_attempts(
        &self,
        group: &str,
        session: &str,
        limit: u64,
        before: Option<u64>,
        view: Option<HumanMessageView>,
    ) -> Result<Vec<GroupMessage>, CollaborationRuntimeError> {
        let Some(repo) = self.message_repo.as_ref() else {
            return Ok(Vec::new());
        };
        // A fixed 2/3 filtered queries, bounded by the requested page. Never scan
        // ordinary chat first: it can otherwise hide older persisted attempts.
        let mut kinds = vec![
            bcs_domain::STATE_MACHINE_OUTPUT_MESSAGE_TYPE,
            bcs_domain::STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE,
        ];
        if view
            .as_ref()
            .is_some_and(|v| v.scope == bcs_domain::MessageViewScope::Participant)
        {
            kinds.push(bcs_domain::STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE);
        }
        let mut messages = Vec::new();
        for kind in kinds {
            let page = repo
                .query_messages(MessageQuery {
                    group_id: group.into(),
                    session_id: session.into(),
                    cursor: before,
                    limit: limit.min(1_000) as u32,
                    keyword: None,
                    sender_id: None,
                    message_type: Some(kind.into()),
                    owner_filter: MessageOwnerFilter::Any,
                    time_range: None,
                    visible_from_seq: None,
                    human_view: view.clone(),
                })
                .await
                .map_err(message_history_error)?;
            messages.extend(
                page.messages
                    .into_iter()
                    .filter(|m| {
                        m.group_id == group
                            && m.visibility_domain == Some(MessageVisibilityDomain::StateMachine)
                    })
                    .filter_map(|m| project_state_machine_message(&m)),
            );
        }
        Ok(messages)
    }
}

impl CollaborationRuntime {
    /// Repair only local rows from original producer facts. Network ACKs are untouched.
    pub(super) async fn repair_existing_history(
        &self,
        run: &StateMachineRun,
    ) -> Result<(), CollaborationRuntimeError> {
        let repo = self.message_repo.as_ref().ok_or_else(||
            CollaborationRuntimeError::InvalidRequest("history message repository missing".into()))?;
        if let Some(opening) = self.runs.get_run_opening(&run.run_id).await? {
            let rendered = self.opening_from_payload(run, &opening.payload)?;
            let key = opening.payload.client_msg_id;
            let expected = NewMessage {
                group_id: run.group_id.clone(),
                session_id: run.session_id.clone(),
                run_id: run.run_id.clone(),
                sender_id: BCS_STATE_MACHINE_MESSAGE_SENDER.into(),
                sender_type: SenderType::Bot,
                message_type: STATE_MACHINE_PANEL_MESSAGE_TYPE.into(),
                content: serde_json::json!({"text":rendered.content,"bot_name":BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
                    "metadata":state_machine_panel_metadata(run,rendered.component.as_deref())}),
                client_msg_id: Some(key.clone()),
                owner_bot_id: None,
                visibility_domain: MessageVisibilityDomain::StateMachine,
                audience: Some(MessageAudience::Public),
                created_at: opening.payload.created_at_ms,
            };
            let saved = repo
                .append_message_with_id(physical_message_id(&key), expected.clone())
                .await
                .map_err(message_history_error)?;
            if !matches_message(&saved, &expected) {
                return Err(CollaborationRuntimeError::Conflict(
                    "opening history conflicts with original fact".into(),
                ));
            }
        }
        if let Some(publication) = self.runs.get_chat_result(&run.run_id).await? {
            if publication.status != bcs_service_api::StateMachineChatResultStatus::Delivered {
                return Ok(());
            }
            let cmd = publication.payload.command;
            if cmd.group_id != run.group_id
                || cmd.session_id != run.session_id
                || cmd.run_id != run.run_id
            {
                return Err(CollaborationRuntimeError::Conflict(
                    "publication history scope mismatch".into(),
                ));
            }
            let key = format!("state-machine-result:{}", run.run_id);
            let expected = NewMessage {
                group_id: cmd.group_id,
                session_id: cmd.session_id,
                run_id: cmd.run_id,
                sender_id: cmd.sender_bot_id,
                sender_type: SenderType::Bot,
                message_type: "chat".into(),
                content: Value::String(cmd.content),
                client_msg_id: Some(key.clone()),
                owner_bot_id: None,
                visibility_domain: MessageVisibilityDomain::StateMachine,
                audience: Some(MessageAudience::Public),
                created_at: cmd.created_at_ms,
            };
            let saved = repo
                .append_message_with_id(physical_message_id(&key), expected.clone())
                .await
                .map_err(message_history_error)?;
            if !matches_message(&saved, &expected) {
                return Err(CollaborationRuntimeError::Conflict(
                    "publication history conflicts with original fact".into(),
                ));
            }
        }
        Ok(())
    }
}
