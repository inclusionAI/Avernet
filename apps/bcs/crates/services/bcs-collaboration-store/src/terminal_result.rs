//! Atomic abnormal Run terminal, private result admission and public summary.
use super::*;
use bcs_service_api::port::repo::{CommitStateMachineTerminal as Command, StateMachineRunResultV1 as ResultV1};
use bcs_service_api::{StateMachineMissingStartupFact as Missing, STATE_MACHINE_PREPARATION_GRACE_MS as GRACE};
use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError;

fn key(run: &str) -> String { format!("smrun:{run}:terminal-result") }
fn error(e: impl std::fmt::Display) -> ServiceError { ServiceError::InternalError(format!("Run terminal transaction: {e}")) }
fn active(run: &StateMachineRun) -> bool { matches!(run.status, StateMachineRunStatus::Pending | StateMachineRunStatus::Running) }

fn validate(run: &StateMachineRun, c: &Command) -> ServiceResult<()> {
    let r = &c.result;
    let safe = |s: &str| !s.is_empty() && s.len() <= 256 && !s.chars().any(char::is_control);
    if c.run_id != run.run_id || r.run_id != run.run_id || r.schema_version != 1
        || !matches!(r.status, StateMachineRunStatus::Failed | StateMachineRunStatus::Aborted)
        || !safe(&r.run_id) || !safe(&r.workflow_name)
        || r.failed_node_id.as_ref().is_some_and(|id| !safe(id))
        || r.completed_node_ids.iter().any(|id| !safe(id))
        || r.completed_node_ids.windows(2).any(|w| w[0] >= w[1])
        || r.attempt.is_some_and(|n| n < 0)
        || (r.reason_category == Some(bcs_service_api::port::repo::StateMachineRunReason::UserAborted) && r.status != StateMachineRunStatus::Aborted)
        || (r.status == StateMachineRunStatus::Aborted && r.reason_category.is_some_and(|reason| reason != bcs_service_api::port::repo::StateMachineRunReason::UserAborted))
        || (matches!(r.reason_category, Some(bcs_service_api::port::repo::StateMachineRunReason::StartupFailed | bcs_service_api::port::repo::StateMachineRunReason::PublicationFailed | bcs_service_api::port::repo::StateMachineRunReason::UserAborted)) && r.failed_node_id.is_some())
        || (matches!(r.reason_category, Some(bcs_service_api::port::repo::StateMachineRunReason::NodeFailed | bcs_service_api::port::repo::StateMachineRunReason::HumanTimeout)) && r.failed_node_id.is_none())
        || c.completed_at_ms > i64::MAX as u64
        || r.failed_node_id.is_some() != r.attempt.is_some() {
        return Err(error("invalid safe Run result"));
    }
    if let Some(a) = &c.admission {
        if a.message_id != r.message_id() || a.message.client_msg_id.as_deref() != Some(a.message_id.as_str())
            || a.message.group_id != run.group_id || a.message.session_id != run.session_id
            || a.message.sender_id != "system" || a.message.sender_type != bcs_domain::SenderType::System
            || a.targets.len() != 1 || run.created_by.as_deref() != Some(a.targets[0].target_bot_id.as_str())
            || a.targets[0].kind != bcs_domain::DeliveryType::Send || a.event.is_some() || a.display_message.is_some()
            || a.message.audience.as_ref() != Some(&bcs_domain::MessageAudience::directed([a.targets[0].target_bot_id.clone()]).map_err(error)?)
            || a.message.content["text"].as_str() != Some(r.text()?.as_str()) {
            return Err(error("Run result admission identity mismatch"));
        }
    }
    if let Some(event) = &c.event {
        let e = &event.event;
        let expected_type = if r.status == StateMachineRunStatus::Failed { "state_machine.run.failed" } else { "state_machine.run.aborted" };
        let expected_result = serde_json::to_value(r).map_err(error)?;
        if e.event_type != expected_type || e.subject.subject_type != "state_machine.run" || e.subject.id != run.run_id
            || e.scope.group_id.as_deref() != Some(run.group_id.as_str())
            || e.scope.session_id.as_deref() != Some(run.session_id.as_str())
            || e.scope.run_id.as_deref() != Some(run.run_id.as_str())
            || e.data.len() != 4 || e.data.get("result") != Some(&expected_result)
            || !e.data.get("completed_at").is_some_and(serde_json::Value::is_string)
            || !e.data.get("duration_ms").is_some_and(serde_json::Value::is_u64)
            || !e.data.get("scope_invalid").is_some_and(serde_json::Value::is_boolean)
            || e.data.get("scope_invalid") != Some(&serde_json::Value::Bool(r.reason_category.is_none())) {
            return Err(error("invalid public Run result event"));
        }
    }
    if let Some(s) = &c.startup {
        if s.run_id != c.run_id || s.failed_at_ms != c.completed_at_ms || r.status != StateMachineRunStatus::Failed {
            return Err(error("startup failure identity mismatch"));
        }
    }
    Ok(())
}

impl MemoryCollaborationStore {
    pub(super) async fn terminal_result(&self, run: &str) -> ServiceResult<Option<ResultV1>> {
        Ok(self.inner.read().await.terminal_results.get(run).cloned())
    }
    pub(super) async fn commit_run_terminal(&self, c: Command) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(run) = inner.runs.get(&c.run_id) else { return Ok(false); };
        if !active(run) { return Ok(false); }
        validate(run, &c)?;
        let completed = inner.nodes.iter().filter(|((id, _), n)| id == &c.run_id && n.status == StateMachineNodeStatus::Completed)
            .map(|((_, id), _)| id.clone()).collect::<Vec<_>>();
        if completed != c.result.completed_node_ids { return Err(ServiceError::Conflict("completed Nodes changed concurrently".into())); }
        let startup = if let Some(s) = &c.startup {
            if (s.preparation_error.is_none() && s.failed_at_ms < run.created_at.saturating_add(GRACE))
                || match s.missing { Missing::Snapshot => inner.run_snapshots.contains_key(&c.run_id), Missing::Opening => inner.openings.contains_key(&c.run_id) } {
                return Ok(false);
            }
            Some(recovery_gap::failure(run, s)?)
        } else { None };
        let mut staged = inner.clone();
        let run = staged.runs.get_mut(&c.run_id).expect("checked Run");
        run.status = c.result.status; run.output = None; run.error = startup.as_ref().map(|s| s.error.clone()).or(c.error);
        run.updated_at = c.completed_at_ms; run.completed_at = Some(c.completed_at_ms);
        if let Some(fact) = startup { staged.startup_failures.insert(c.run_id.clone(), fact); }
        staged.terminal_results.insert(c.run_id.clone(), c.result);
        for d in staged.dispatches.values_mut().filter(|d| d.payload.run_id == c.run_id
            && matches!(d.status, bcs_service_api::StateMachineDispatchStatus::Pending | bcs_service_api::StateMachineDispatchStatus::Delivering)) {
            d.status = bcs_service_api::StateMachineDispatchStatus::Superseded; d.lease_owner = None; d.lease_until_ms = None;
        }
        let events = c.event.into_iter().collect::<Vec<_>>();
        if let Some(messages) = &self.message_store {
            messages.compose_delivery_transaction(Vec::new(), c.admission.into_iter().collect(), events, true, || {
                *inner = staged; Ok(())
            }).await.map_err(error)?;
        } else if c.admission.is_some() {
            return Err(error("message store composition is not configured"));
        } else if !events.is_empty() {
            self.event_store.as_ref().ok_or_else(|| error("event store not configured"))?
                .commit_business_mutations(&events, || { *inner = staged; Ok(()) }).await.map_err(error)?;
        } else { *inner = staged; }
        Ok(true)
    }
}

impl MySqlCollaborationStore {
    pub(super) async fn terminal_result(&self, run: &str) -> ServiceResult<Option<ResultV1>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT payload_json FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_key = ? AND operation_kind = 'terminal_result'",
            vec![self.env.as_str().into(), key(run).into()])).await.map_err(error)?;
        rows.first().map(|row| serde_json::from_str(&db_get_column::<String>(row, "payload_json").map_err(error)?).map_err(error)).transpose()
    }
    pub(super) async fn commit_run_terminal(&self, c: Command) -> ServiceResult<bool> {
        let Some(run) = self.get_run(&c.run_id).await? else { return Ok(false); };
        if !active(&run) { return Ok(false); }
        validate(&run, &c)?;
        let mut predicate = String::new();
        let mut startup = None;
        if let Some(s) = &c.startup {
            if s.preparation_error.is_none() && s.failed_at_ms < run.created_at.saturating_add(GRACE) { return Ok(false); }
            predicate = match s.missing {
                Missing::Snapshot => " AND NOT EXISTS (SELECT 1 FROM bcs_state_machine_definition_snapshots s WHERE s.env = bcs_state_machine_runs.env AND s.run_id = bcs_state_machine_runs.run_id)",
                Missing::Opening => " AND NOT EXISTS (SELECT 1 FROM bcs_collaboration_delivery_checkpoints c WHERE c.env = bcs_state_machine_runs.env AND c.aggregate_id = bcs_state_machine_runs.run_id AND c.operation_kind = 'run_opening')",
            }.into();
            startup = Some(recovery_gap::failure(&run, s)?);
        }
        let diagnostic = startup.as_ref().map(|s| s.error.as_str()).or(c.error.as_deref());
        // Fence the safe summary against a Node completion between projection
        // and terminal CAS. No artifact content is read or copied.
        predicate.push_str(" AND (SELECT COUNT(*) FROM bcs_state_machine_node_runs n WHERE n.env = bcs_state_machine_runs.env AND n.run_id = bcs_state_machine_runs.run_id AND n.status = 'completed' AND n.record_status = 'active') = ?");
        if !c.result.completed_node_ids.is_empty() {
            predicate.push_str(&format!(" AND NOT EXISTS (SELECT 1 FROM bcs_state_machine_node_runs n WHERE n.env = bcs_state_machine_runs.env AND n.run_id = bcs_state_machine_runs.run_id AND n.status = 'completed' AND n.record_status = 'active' AND n.node_id NOT IN ({}))", vec!["?"; c.result.completed_node_ids.len()].join(",")));
        }
        let mut params = vec![run_status_to_str(c.result.status).into(), diagnostic.into(), c.completed_at_ms.into(), c.completed_at_ms.into(), self.env.as_str().into(), c.run_id.as_str().into(), run.created_at.into(), (c.result.completed_node_ids.len() as i64).into()];
        params.extend(c.result.completed_node_ids.iter().map(|id| id.as_str().into()));
        let mut steps = vec![DbTransactionStep::ExecuteChecked { statement: DbStatement::with_params(format!(
            "UPDATE bcs_state_machine_runs SET status = ?, output_text = NULL, error_message = ?, updated_at_ms = ?, completed_at_ms = ?, {} \
             WHERE env = ? AND run_id = ? AND created_at_ms = ? AND record_status = 'active' AND status IN ('pending','running'){predicate}", self.flavor.set_modified_now()),
            params), expected_affected_rows: 1 }];
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = 'superseded', lease_owner = NULL, lease_until_ms = NULL WHERE env = ? AND aggregate_id = ? AND operation_kind = 'bot_dispatch' AND status IN ('pending','delivering')",
            vec![self.env.as_str().into(), c.run_id.as_str().into()])));
        for (operation, identity, value) in std::iter::once(("terminal_result", key(&c.run_id), serde_json::to_string(&c.result).map_err(error)?))
            .chain(startup.as_ref().map(|s| Ok::<_, ServiceError>(("startup_failure", recovery_gap::key(&c.run_id), serde_json::to_string(s).map_err(error)?))).transpose()?) {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms) VALUES (?, ?, 'state_machine_run', ?, ?, ?, 'failed', ?)",
                vec![self.env.as_str().into(), identity.into(), c.run_id.as_str().into(), operation.into(), value.into(), c.completed_at_ms.into()])));
        }
        match self.message_store.compose_delivery_transaction(Vec::new(), c.admission.into_iter().collect(), steps, c.event.into_iter().collect()).await {
            Ok(_) => Ok(true),
            Err(MessageDeliveryRepoError::Conflict) => {
                if self.get_run(&c.run_id).await?.is_some_and(|r| !active(&r)) { Ok(false) }
                else if let Some(startup) = c.startup {
                    let appeared = match startup.missing {
                        Missing::Snapshot => self.get_run_snapshot(&c.run_id).await?.is_some(),
                        Missing::Opening => self.get_run_opening(&c.run_id).await?.is_some(),
                    };
                    if appeared { Ok(false) } else { Err(ServiceError::Conflict("Run startup changed concurrently".into())) }
                }
                else { Err(ServiceError::Conflict("Run terminal transaction changed concurrently".into())) }
            }
            Err(e) => Err(error(e)),
        }
    }
}
