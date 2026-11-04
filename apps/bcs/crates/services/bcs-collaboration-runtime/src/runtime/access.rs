use super::*;
impl CollaborationRuntime {

    pub(super) async fn authorize_human_for_run(
        &self,
        run: &StateMachineRun,
        actor_id: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        let belongs = self
            .sessions
            .belongs_to_group(&run.session_id, &run.group_id)
            .await
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?;
        if !belongs {
            return Err(CollaborationRuntimeError::Forbidden(
                "run session does not belong to the run group".to_string(),
            ));
        }
        let session = self
            .sessions
            .get(&run.session_id)
            .await
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
            .ok_or_else(|| {
                CollaborationRuntimeError::Forbidden("run session is unavailable".to_string())
            })?;
        self.sessions.authorize_collaboration(&run.session_id, actor_id).await
            .map_err(|error| match error {
                SessionUseCaseError::Internal(ServiceError::Forbidden(message)) => CollaborationRuntimeError::Forbidden(message),
                other => CollaborationRuntimeError::Internal(ServiceError::InternalError(other.to_string())),
            })?;
        let authorized = session.participants.iter().any(|participant| {
            participant.bot_uuid == actor_id
                && participant.is_human()
                && participant.effective_mode() == ParticipantMode::Present
        });
        if !authorized {
            return Err(CollaborationRuntimeError::Forbidden(
                "caller is not a present Human participant in the run session".to_string(),
            ));
        }
        Ok(())
    }

    pub(super) async fn authorize_run_access(
        &self,
        run: &StateMachineRun,
        authenticated_human: Option<&AuthenticatedHumanCaller>,
    ) -> Result<bool, CollaborationRuntimeError> {
        let compiled = self.load_run_definition_for_view(run).await?.execution;
        // COSEC: unauthenticated legacy access is retained only for Bot-only
        // runs; any run containing HumanInput remains identity-protected.
        if !compiled_has_human_input(&compiled) {
            return Ok(false);
        }
        let human = authenticated_human.ok_or(CollaborationRuntimeError::Unauthenticated)?;
        self.authorize_human_for_run(run, &human.actor_id).await?;
        Ok(true)
    }

    pub(super) async fn get_session_state_machine_permission_impl(
        &self,
        cmd: SessionStateMachinePermissionCommand,
    ) -> Result<SessionStateMachinePermissionView, CollaborationRuntimeError> {
        let session = self
            .sessions
            .get(&cmd.session_id)
            .await
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
            .ok_or_else(|| {
                CollaborationRuntimeError::InvalidRequest(format!(
                    "session not found: {}",
                    cmd.session_id
                ))
            })?;
        let group = self.groups.get(&session.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!(
                "group not found: {}",
                session.group_id
            ))
        })?;
        let active_run_id = self
            .runs
            .get_run_by_session_id(&session.id)
            .await?
            .filter(|run| {
                matches!(
                    run.status,
                    StateMachineRunStatus::Pending | StateMachineRunStatus::Running
                )
            })
            .map(|run| run.run_id);
        let denied = |reason_code: &str, message: &str| {
            session_state_machine_permission_view(
                &session.id,
                &group,
                &cmd.caller_bot_id,
                false,
                reason_code,
                message,
                active_run_id.clone(),
            )
        };

        if group.group_kind != GroupKind::Normal {
            return Ok(denied(
                "unsupported_group_kind",
                "state-machine runs are currently available only in normal groups",
            ));
        }
        if !matches!(
            group.group_strategy,
            GroupStrategy::Chat | GroupStrategy::ManagerWorker
        ) {
            return Ok(denied(
                "unsupported_group_strategy",
                "state-machine runs are currently available only in chat and manager_worker groups",
            ));
        }
        if group.status != GroupStatus::Active {
            return Ok(denied(
                "group_not_active",
                "the group must be active to start a state-machine run",
            ));
        }
        if session.session_kind != SessionKind::Chat {
            return Ok(denied(
                "unsupported_session_kind",
                "one-shot state-machine runs require a chat session",
            ));
        }
        if session.status != SessionStatus::Running {
            return Ok(denied(
                "session_not_running",
                "the session must be running to start a state-machine run",
            ));
        }
        if !session
            .participants
            .iter()
            .any(|participant| participant.is_bot() && participant.bot_uuid == cmd.caller_bot_id)
        {
            return Ok(denied(
                "caller_not_session_member",
                "the caller bot must be a member of the current session",
            ));
        }
        if cmd.caller_bot_id != group.driver_bot {
            return Ok(denied(
                "caller_not_group_owner",
                "only the current group owner bot may start a state-machine run",
            ));
        }
        if active_run_id.is_some() {
            return Ok(denied(
                "state_machine_run_active",
                "the current session already has an active state-machine run",
            ));
        }

        Ok(session_state_machine_permission_view(
            &session.id,
            &group,
            &cmd.caller_bot_id,
            true,
            "allowed",
            "the caller may start a one-shot state-machine run in this session",
            None,
        ))
    }

    pub(super) async fn list_pending_human_nodes_impl(
        &self,
        cmd: ListPendingHumanNodesCommand,
    ) -> Result<Vec<PendingHumanNodeView>, CollaborationRuntimeError> {
        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or(CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        if run.status != StateMachineRunStatus::Running {
            return Ok(Vec::new());
        }
        let compiled = self.load_run_definition_for_view(&run).await?.execution;
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => return Ok(Vec::new()),
        };
        let mut pending = Vec::new();
        for node_run in self.runs.list_node_runs(&run.run_id).await? {
            if node_run.status != StateMachineNodeStatus::Running
                || node_run.artifact_text.is_some()
            {
                continue;
            }
            if !state_machine
                .nodes
                .get(&node_run.node_id)
                .is_some_and(|node| {
                    node.kind == StateMachineNodeKind::HumanInput
                        && match &node.assignee {
                            None => true,
                            Some(StateMachineAssignee::RuntimeActor { actor }) => {
                                actor == &cmd.caller_actor_id
                            }
                            Some(StateMachineAssignee::BotBinding { .. }) => false,
                        }
                })
            {
                continue;
            }
            pending.push(
                self.pending_human_node_view(&compiled, &run, &node_run)
                    .await?,
            );
        }
        pending.sort_by(|left, right| left.node_id.cmp(&right.node_id));
        Ok(pending)
    }
}
