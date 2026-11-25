use super::*;
pub(super) fn build_node_runs(
    compiled: &CompiledStateMachine,
    run: &StateMachineRun,
    resolved_participant_bindings: &BTreeMap<String, ResolvedParticipantBinding>,
) -> Result<Vec<StateMachineNodeRun>, CollaborationRuntimeError> {
    let state_machine = match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "runtime.kind must be state_machine".to_string(),
            ));
        }
    };
    let default_node_timeout_ms = state_machine.defaults.node_timeout_ms;
    let default_max_attempts = state_machine.defaults.max_attempts.max(1);
    let mut nodes = Vec::new();
    for (node_id, node) in &state_machine.nodes {
        let (assignee_bot_id, node_timeout_ms, max_attempts) = match node.kind {
            StateMachineNodeKind::BotTask => {
                let assignee_bot_id = match &node.assignee {
                    Some(StateMachineAssignee::BotBinding { binding }) => {
                        resolved_participant_bindings
                            .get(binding)
                            .and_then(|binding| binding.bot_ids.first().cloned())
                            .ok_or_else(|| {
                                CollaborationRuntimeError::InvalidParticipantBinding(format!(
                                    "node {node_id} assignee binding {binding} has no resolved bot_id"
                                ))
                            })?
                    }
                    _ => {
                        return Err(CollaborationRuntimeError::InvalidDefinition(format!(
                            "node {node_id} assignee is not supported"
                        )))
                    }
                };
                (
                    Some(assignee_bot_id),
                    node.node_timeout_ms
                        .or(default_node_timeout_ms)
                        .filter(|timeout_ms| *timeout_ms > 0),
                    node.max_attempts.unwrap_or(default_max_attempts).max(1),
                )
            }
            StateMachineNodeKind::HumanInput => (
                None,
                node.node_timeout_ms.filter(|timeout_ms| *timeout_ms > 0),
                1,
            ),
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(format!(
                    "node {node_id} kind is not supported"
                )));
            }
        };
        nodes.push(StateMachineNodeRun {
            run_id: run.run_id.clone(),
            node_id: node_id.clone(),
            status: StateMachineNodeStatus::Pending,
            attempt: 0,
            node_timeout_ms,
            timeout_deadline_ms: None,
            max_attempts,
            assignee_bot_id,
            outcome: None,
            responded_by: None,
            delivery_request_id: None,
            bot_delivery_run_id: None,
            artifact_text: None,
            error: None,
            started_at: None,
            completed_at: None,
        });
    }
    Ok(nodes)
}impl CollaborationRuntime {

    pub(super) async fn start_state_machine_run_impl(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        // Session one-shot launches are the only path that supplies transient
        // participant bindings. Keep their persistence and concurrency rules
        // separate from configured group state-machine runs.
        let is_one_shot_session_run =
            cmd.session_id.is_some() && cmd.participant_bindings.is_some();
        let mut group = self.groups.get(&cmd.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", cmd.group_id))
        })?;
        if cmd.opening_message_override.is_some()
            && (!is_one_shot_session_run
                || !matches!(
                    group.group_strategy,
                    GroupStrategy::Chat | GroupStrategy::ManagerWorker
                ))
        {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "opening_message override is supported only for one-shot session Runs".to_string(),
            ));
        }
        if let Some(opening_message) = cmd.opening_message_override.as_ref() {
            opening_message
                .validate_for(OpeningMessageScope::StateMachineRun)
                .map_err(|error| {
                    CollaborationRuntimeError::InvalidRequest(format!(
                        "invalid_opening_message: {error}"
                    ))
                })?;
        }
        if cmd.participant_bindings.is_some() {
            if let Some(session_id) = cmd.session_id.as_deref() {
                let participant_scope = self
                    .sessions
                    .get(session_id)
                    .await
                    .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
                    .ok_or_else(|| {
                        CollaborationRuntimeError::InvalidRequest(format!(
                            "session not found: {session_id}"
                        ))
                    })?;
                if participant_scope.group_id != group.id {
                    return Err(CollaborationRuntimeError::InvalidRequest(
                        "state-machine session does not belong to the target group".to_string(),
                    ));
                }
                group.participants = participant_scope.participants;
            }
        }
        let group_binding = self.bindings.get(&cmd.group_id).await?;
        let resolved_definition = self
            .resolve_definition(&cmd, group_binding.as_ref())
            .await?;
        let should_upsert_definition = resolved_definition.source
            == ResolvedDefinitionSource::Inline
            && !is_one_shot_session_run;
        let authenticated_human = cmd.authenticated_human.clone();
        let authoring = self.compile_for_execution(resolved_definition.definition)?;
        let compiled = authoring.execution;
        let definition = &authoring.definition;
        let execution_plan_snapshot = authoring.plan
            .map(crate::snapshot::snapshot_execution_plan).transpose()?;
        let has_human_input = compiled_has_human_input(&compiled);
        self.validate_human_input_channel_for_group(&cmd.group_id, &compiled)
            .await?;
        let participant_binding_override =
            cmd.participant_bindings
                .as_ref()
                .map(|participant_bindings| GroupRuntimeBinding {
                    group_id: group.id.clone(),
                    group_version: group.version,
                    default_definition: None,
                    participant_bindings: participant_bindings.clone(),
                    auto_start_on_service_invocation: false,
                });
        let resolved_participant_bindings = resolve_participant_bindings(
            &group,
            &compiled,
            participant_binding_override
                .as_ref()
                .or(group_binding.as_ref()),
        )?;
        if should_upsert_definition {
            self.definitions.upsert(definition.clone()).await?;
        }
        let (session_id, session_title, mut session) = match cmd.session_id {
            Some(session_id) => {
                let session = self
                    .sessions
                    .get(&session_id)
                    .await
                    .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
                    .ok_or_else(|| {
                        CollaborationRuntimeError::InvalidRequest(format!(
                            "session not found: {session_id}"
                        ))
                    })?;
                (session_id, session.session_title.clone(), session)
            }
            None => {
                let mut participants = group.participants.clone();
                if let Some(human) = authenticated_human.as_ref() {
                    participants.retain(|participant| participant.bot_uuid != human.actor_id);
                    let mut participant =
                        Participant::human(human.actor_id.clone(), ParticipantRole::Observer);
                    participant.bot_name = human.display_name.clone();
                    participant.mode = Some(ParticipantMode::Present);
                    participants.push(participant);
                }
                let outcome = self
                    .sessions
                    .create_or_reactivate(CreateOrReactivateCommand {
                        group_id: group.id.clone(),
                        session_id: None,
                        params: NewSessionParams {
                            session_kind: SessionKind::ServiceInvocation,
                            participants,
                            group_version: Some(group.version),
                            caller_id: cmd.caller_id.clone(),
                            input: Some(cmd.input.clone()),
                            session_title: Some(definition.name.clone()),
                            ..Default::default()
                        },
                    })
                    .await
                    .map_err(|error| {
                        CollaborationRuntimeError::InvalidRequest(error.to_string())
                    })?;
                let session = outcome.session;
                (session.id.clone(), session.session_title.clone(), session)
            }
        };
        if session.group_id != group.id {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "state-machine session does not belong to the target group".to_string(),
            ));
        }
        if has_human_input {
            // COSEC: caller_id is not proof of Human identity. Only a Human
            // established by the server-side authentication port may be
            // materialized into the session.
            if let Some(human) = authenticated_human.as_ref() {
                self.sessions.authorize_collaboration(&session_id, &human.actor_id).await
                    .map_err(|error| match error {
                        SessionUseCaseError::Internal(ServiceError::Forbidden(message)) => CollaborationRuntimeError::Forbidden(message),
                        other => CollaborationRuntimeError::Internal(ServiceError::InternalError(other.to_string())),
                    })?;
                let existing = session
                    .participants
                    .iter()
                    .find(|participant| participant.bot_uuid == human.actor_id);
                if existing.is_some_and(|participant| !participant.is_human()) {
                    return Err(CollaborationRuntimeError::Forbidden(
                        "authenticated Human actor ID conflicts with a non-Human session participant"
                            .to_string(),
                    ));
                }
                if existing.is_none() {
                    let mut participant =
                        Participant::human(human.actor_id.clone(), ParticipantRole::Observer);
                    participant.bot_name = human.display_name.clone();
                    participant.mode = Some(ParticipantMode::Present);
                    session = self
                        .sessions
                        .add_participant(&session_id, participant)
                        .await
                        .map_err(|error| {
                            CollaborationRuntimeError::InvalidRequest(error.to_string())
                        })?;
                } else if existing.is_some_and(|participant| {
                    participant.effective_mode() != ParticipantMode::Present
                }) {
                    session = self
                        .sessions
                        .update_participant_mode(
                            &session_id,
                            &human.actor_id,
                            ParticipantMode::Present,
                        )
                        .await
                        .map_err(|error| {
                            CollaborationRuntimeError::InvalidRequest(error.to_string())
                        })?;
                }
            }
            let present_humans = session
                .participants
                .iter()
                .filter(|participant| {
                    participant.is_human()
                        && participant.effective_mode() == ParticipantMode::Present
                })
                .collect::<Vec<_>>();
            if present_humans.is_empty() {
                return Err(CollaborationRuntimeError::InvalidRequest(
                    "state-machine definitions with human_input require a Present Human session participant"
                        .to_string(),
                ));
            }
            let missing_assignees = human_input_assignees(&compiled)
                .into_iter()
                .filter(|assignee| {
                    !present_humans
                        .iter()
                        .any(|participant| participant.bot_uuid == *assignee)
                })
                .collect::<Vec<_>>();
            if !missing_assignees.is_empty() {
                return Err(CollaborationRuntimeError::InvalidRequest(format!(
                    "HumanInput assignees must be Present Human session participants: {}",
                    missing_assignees.join(", ")
                )));
            }
        }
        let run_id = format!("sm-{}", Uuid::new_v4());
        let now = bcs_protocol::now_ms();
        let mut run = StateMachineRun {
            run_id: run_id.clone(),
            root_run_id: Some(run_id.clone()),
            rerun_of: None,
            definition_id: definition.id.clone(),
            definition_version: definition.version,
            group_id: group.id.clone(),
            group_version: group.version,
            session_id,
            session_activation_count: (session.session_kind == SessionKind::ServiceInvocation)
                .then_some(session.activation_count),
            created_by: cmd.caller_id.clone(),
            status: StateMachineRunStatus::Pending,
            input: cmd.input,
            opening_message_override: cmd.opening_message_override,
            output: None,
            error: None,
            created_at: now,
            updated_at: now,
            completed_at: None,
        };
        let nodes = build_node_runs(&compiled, &run, &resolved_participant_bindings)?;
        // Owner→caller relay (spec L62 direction, design 2026-10-30):
        // pre-collect the Execution-policy assignees before `nodes` moves
        // into the run store — a BOT-submitted caller-aware run whose
        // association the adapter could not build redeems the session's
        // captured identity for exactly these nodes. Runs without
        // Execution assignees never touch the relay seam.
        let relay_execution_assignees: Vec<String> = {
            let mut assignees: Vec<String> = Vec::new();
            if self.caller_relay_redeemer.is_some() {
                if let Some(resolver) = self.caller_policy_resolver.as_ref() {
                    let environment = bcs_config::resolve_env_str();
                    for node in &nodes {
                        let Some(assignee) = node.assignee_bot_id.as_deref() else { continue };
                        if assignees.iter().any(|existing| existing == assignee) {
                            continue;
                        }
                        if matches!(
                            resolver.resolve(assignee, &environment).await,
                            bcs_service_api::port::OrchestrationAssigneeResolution::Protected(_)
                        ) {
                            assignees.push(assignee.to_string());
                        }
                    }
                }
            }
            assignees
        };
        let node_count = nodes.len();
        let created = if is_one_shot_session_run {
            self.runs
                .create_run_if_session_idle(run.clone(), nodes)
                .await?
        } else {
            self.runs.create_run(run.clone(), nodes).await?;
            true
        };
        // §15.83 S2 (spec §8.5): a Human-triggered caller-aware run
        // registers its INSTANCE-level delegation-only association NOW —
        // before any node dispatch can claim it. The runtime stamps the
        // created run's id into the template the trusted adapter supplied;
        // the registrar owns the only identity material (in-memory
        // lineage). Refusal keeps the run running caller-closed (the
        // nodes then refuse closed at the protected wire) — never silent.
        if let Some(mut association) = cmd.caller_association.clone() {
            // Stamp the trusted scope facts the adapter left empty: the
            // created run's identity, its group, its session-anchored
            // mega-turn (the opening panel message id the runtime itself
            // renders), and the request's own clock.
            if association.workflow_run_id.trim().is_empty() {
                association.workflow_run_id = run.run_id.clone();
            }
            if association.group_id.trim().is_empty() {
                association.group_id = run.group_id.clone();
            }
            if association.session_id.trim().is_empty() {
                association.session_id = run.session_id.clone();
            }
            if association.message_turn_id.trim().is_empty() {
                association.message_turn_id = format!("{}:000-panel", run.run_id);
            }
            if association.now_ms <= 0 {
                association.now_ms = bcs_protocol::now_ms() as i64;
            }
            if let Some(registrar) = self.caller_instance_registrar.clone() {
                match registrar
                    .register_instance(association)
                    .await
                {
                    Ok(true) => {
                        tracing::info!(target: RUNTIME_LOG_TARGET,run_id = %run.run_id,
                            "state_machine: caller instance association registered (caller-aware run)");
                    }
                    Ok(false) => {
                        tracing::warn!(target: RUNTIME_LOG_TARGET,run_id = %run.run_id,
                            "state_machine: caller instance association refused (capacity); nodes stay caller-closed");
                    }
                    Err(error) => {
                        tracing::warn!(target: RUNTIME_LOG_TARGET,run_id = %run.run_id, %error,
                            "state_machine: caller instance registration refused; nodes stay caller-closed");
                    }
                }
            } else {
                // No registrar attached while the adapter carried an
                // association: the composition is mis-assembled for
                // caller-aware runs — refuse the whole submission rather
                // than silently degrading the human's caller intent.
                return Err(CollaborationRuntimeError::InvalidRequest(
                    "caller association supplied but no caller registrar is configured".into(),
                ));
            }
        } else if let Some(redeemer) = self.caller_relay_redeemer.clone() {
            // Owner→caller relay (spec L62 direction, design 2026-10-30): a
            // BOT-submitted caller-aware run whose association the trusted
            // adapter could not build. Only a run whose definition carries
            // execution-policy caller NODES may (and needs to) redeem —
            // every other bot submission keeps today's caller-closed
            // behavior byte-for-byte. A refused redemption fails the whole
            // submission: a half-caller run failing node-by-node is worse
            // than one explicit refusal.
            let environment = bcs_config::resolve_env_str();
            if !relay_execution_assignees.is_empty() {
                match redeemer
                    .redeem_submission(
                        &environment,
                        &run.group_id,
                        &run.session_id,
                        // The submitting bot's id — the session-run command
                        // carries it, the group-run path routes through
                        // `caller_id`; a missing/blank id fails the
                        // allowlist inside the redeemer, which is the
                        // intended fail-closed shape for anonymous callers.
                        cmd.caller_id.as_deref().unwrap_or(""),
                        &relay_execution_assignees,
                        bcs_protocol::now_ms() as i64,
                    )
                    .await
                {
                    Some(capture) => {
                        if let Some(registrar) = self.caller_instance_registrar.clone() {
                            let association = bcs_service_api::port::OrchestrationInstanceAssociation {
                                workflow_run_id: run.run_id.clone(),
                                environment,
                                group_id: run.group_id.clone(),
                                session_id: run.session_id.clone(),
                                message_turn_id: format!("{}:000-panel", run.run_id),
                                history_cutoff_seq: 0,
                                expires_at_ms: capture.expires_at_ms,
                                ingress: capture.ingress,
                                now_ms: bcs_protocol::now_ms() as i64,
                            };
                            match registrar.register_instance(association).await {
                                Ok(true) => tracing::info!(target: RUNTIME_LOG_TARGET,run_id = %run.run_id,
                                    "state_machine: caller instance association relayed from the session capture (owner→caller relay)"),
                                Ok(false) => tracing::warn!(target: RUNTIME_LOG_TARGET,run_id = %run.run_id,
                                    "state_machine: relayed instance association refused (capacity); nodes stay caller-closed"),
                                Err(error) => tracing::warn!(target: RUNTIME_LOG_TARGET,run_id = %run.run_id, %error,
                                    "state_machine: relayed instance registration refused; nodes stay caller-closed"),
                            }
                        }
                    }
                    None => {
                        return Err(CollaborationRuntimeError::InvalidRequest(
                            "caller relay refused: no live captured identity for this session, or the submitter/execution assignees are not allowlisted".into(),
                        ));
                    }
                }
            }
        }
        if !created {
            return Err(CollaborationRuntimeError::Conflict(
                "state_machine_run_active: the current session already has an active state-machine run"
                    .to_string(),
            ));
        }
        if let Err(error) = self
            .definitions
            .save_run_snapshot(
                &run,
                group.version,
                definition,
                Some(&resolved_participant_bindings),
                execution_plan_snapshot.as_ref(),
            )
            .await
        {
            self.converge_missing_startup(&run, bcs_service_api::StateMachineMissingStartupFact::Snapshot,
                Some(format!("state-machine run snapshot persistence failed: {error}"))).await?;
            return Err(error.into());
        }
        let opening_message =
            match render_state_machine_opening_message(&group, &run, session_title.as_deref()) {
                Ok(message) => message,
                Err(error) => {
                    self.fail_run(&run, error.to_string()).await?;
                    return Err(error);
                }
            };
        if let Err(error) = self.save_opening_payload(&run, &opening_message).await {
            self.converge_missing_startup(&run, bcs_service_api::StateMachineMissingStartupFact::Opening, Some(error.to_string())).await?;
            return Err(error);
        }
        let run_mode = if is_one_shot_session_run {
            "one_shot"
        } else {
            "configured"
        };
        let mut created_fields = BTreeMap::from([
            (
                "definition_id".to_string(),
                serde_json::json!(run.definition_id.clone()),
            ),
            (
                "definition_version".to_string(),
                serde_json::json!(run.definition_version),
            ),
            (
                "root_run_id".to_string(),
                serde_json::json!(run.root_run_id.clone()),
            ),
            ("run_mode".to_string(), serde_json::json!(run_mode)),
            ("status".to_string(), serde_json::json!("running")),
        ]);
        if let Some(activation_count) = run.session_activation_count {
            created_fields.insert(
                "session_activation_count".to_string(),
                serde_json::json!(activation_count),
            );
        }
        let created_event = self.prepare_public_event(
            "state_machine.run.created",
            &run,
            "state_machine.run",
            &run.run_id,
            "created",
            run.created_by.as_deref(),
            None,
            now,
            created_fields,
        )?;
        let started_event = self.prepare_public_event(
            "state_machine.run.started",
            &run,
            "state_machine.run",
            &run.run_id,
            "started",
            run.created_by.as_deref(),
            None,
            now,
            BTreeMap::from([
                ("run_mode".to_string(), serde_json::json!(run_mode)),
                (
                    "started_at".to_string(),
                    serde_json::json!(event_timestamp(now)?),
                ),
                ("input".to_string(), content_value(run.input.clone())?),
            ]),
        )?;
        match (created_event, started_event) {
            (Some(created_event), Some(started_event)) => {
                if !self
                    .runs
                    .commit_eventful_transition(StateMachineEventfulTransition::StartRun {
                        run_id: run.run_id.clone(),
                        started_at_ms: now,
                        events: vec![created_event, started_event],
                    })
                    .await?
                {
                    if !self.progression_run_is_active(&run.run_id).await? {
                        return Err(CollaborationRuntimeError::Conflict("state-machine run no longer active during start".into()));
                    }
                }
            }
            (None, None) => {
                if !self
                    .runs
                    .update_run_status(
                        &run.run_id,
                        StateMachineRunStatus::Running,
                        None,
                        None,
                        now,
                        None,
                    )
                    .await?
                {
                    if !self.progression_run_is_active(&run.run_id).await? {
                        return Err(CollaborationRuntimeError::Conflict("state-machine run no longer active during start".into()));
                    }
                }
            }
            _ => {
                return Err(CollaborationRuntimeError::Internal(
                    ServiceError::InternalError(
                        "state-machine run Event preparation was inconsistent".to_string(),
                    ),
                ));
            }
        }
        run.status = StateMachineRunStatus::Running;
        run.updated_at = now;
        info!(target: RUNTIME_LOG_TARGET,
            run_id = %run.run_id,
            group_id = %run.group_id,
            group_version = run.group_version,
            session_id = %run.session_id,
            definition_id = %run.definition_id,
            definition_version = run.definition_version,
            created_by = ?run.created_by,
            initial_nodes = ?compiled.initial_nodes,
            node_count = node_count,
            "state_machine: run started"
        );
        if let Err(error) = self
            .persist_saved_opening(&run)
            .await
        {
            self.fail_run(&run, error.to_string()).await?;
            return Err(error);
        }
        self.publish_state_machine_panel_event(&group, &run, &opening_message)
            .await;
        for node_id in &compiled.initial_nodes {
            self.dispatch_node(&compiled, &group, &run, node_id).await?;
        }
        let view = self
            .run_view(&run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run_id.clone()))?;
        Ok(StartStateMachineRunOutcome { view })
    }
}
