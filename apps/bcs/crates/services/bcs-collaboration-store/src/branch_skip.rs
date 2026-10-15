//! Bounded scalar reads and all-or-nothing branch-skip CAS.
use super::*;
use bcs_service_api::port::repo::collaboration::STATE_MACHINE_NODE_BATCH_LIMIT;

fn validate_batch(nodes: &[String]) -> ServiceResult<()> {
    if nodes.len() > STATE_MACHINE_NODE_BATCH_LIMIT
        || nodes.iter().collect::<BTreeSet<_>>().len() != nodes.len()
    {
        return Err(ServiceError::InvalidOperation {
            message: format!("node batch requires at most {STATE_MACHINE_NODE_BATCH_LIMIT} distinct IDs"),
            request_id: None,
        });
    }
    Ok(())
}

fn unstarted(status: StateMachineNodeStatus) -> bool {
    matches!(status, StateMachineNodeStatus::Pending | StateMachineNodeStatus::Ready | StateMachineNodeStatus::RetryScheduled)
}

impl MemoryCollaborationStore {
    pub(super) async fn branch_node_statuses(&self, run: &str, nodes: &[String]) -> ServiceResult<BTreeMap<String, StateMachineNodeStatus>> {
        validate_batch(nodes)?;
        let inner = self.inner.read().await;
        Ok(nodes.iter().filter_map(|id| inner.nodes.get(&(run.into(), id.clone()))
            .map(|node| (id.clone(), node.status))).collect())
    }

    pub(super) async fn skip_branch_nodes(&self, run: &str, nodes: &[String], at: u64) -> ServiceResult<bool> {
        validate_batch(nodes)?;
        if nodes.is_empty() { return Ok(true); }
        let mut inner = self.inner.write().await;
        if !inner.runs.get(run).is_some_and(|run| run.status == StateMachineRunStatus::Running)
            || !nodes.iter().all(|id| inner.nodes.get(&(run.into(), id.clone())).is_some_and(|node| unstarted(node.status)))
        {
            return Ok(false);
        }
        for id in nodes {
            let node = inner.nodes.get_mut(&(run.into(), id.clone())).expect("checked under write lock");
            node.status = StateMachineNodeStatus::Skipped;
            node.completed_at = Some(at);
            node.timeout_deadline_ms = None;
        }
        Ok(true)
    }
}

impl MySqlCollaborationStore {
    pub(super) async fn branch_node_statuses(&self, run: &str, nodes: &[String]) -> ServiceResult<BTreeMap<String, StateMachineNodeStatus>> {
        validate_batch(nodes)?;
        if nodes.is_empty() { return Ok(BTreeMap::new()); }
        let placeholders = vec!["?"; nodes.len()].join(", ");
        let mut params = vec![DbValue::from(self.env.as_str()), DbValue::from(run)];
        params.extend(nodes.iter().map(|id| DbValue::from(id.as_str())));
        let rows = self.db.query(DbStatement::with_params(format!(
            "SELECT node_id, status FROM bcs_state_machine_node_runs \
             WHERE env = ? AND run_id = ? AND record_status = 'active' AND node_id IN ({placeholders})"
        ), params)).await.map_err(|error| ServiceError::InternalError(format!("node status batch: {error}")))?;
        rows.into_iter().map(|row| {
            let id: String = db_get_column(&row, "node_id")
                .map_err(|error| ServiceError::InternalError(format!("node status ID: {error}")))?;
            let status: String = db_get_column(&row, "status")
                .map_err(|error| ServiceError::InternalError(format!("node status: {error}")))?;
            Ok((id, parse_node_status(&status)?))
        }).collect()
    }

    pub(super) async fn skip_branch_nodes(&self, run: &str, nodes: &[String], at: u64) -> ServiceResult<bool> {
        validate_batch(nodes)?;
        if nodes.is_empty() { return Ok(true); }
        let placeholders = vec!["?"; nodes.len()].join(", ");
        let mut params = vec![DbValue::from(at), DbValue::from(self.env.as_str()), DbValue::from(run)];
        params.extend(nodes.iter().map(|id| DbValue::from(id.as_str())));
        let statement = DbStatement::with_params(format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'skipped', completed_at_ms = ?, timeout_deadline_ms = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id IN ({placeholders}) \
               AND status IN ('pending', 'ready', 'retry_scheduled') AND record_status = 'active' \
               AND EXISTS (SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active')",
            self.flavor.set_modified_now()
        ), params);
        // A single-row guarded UPDATE is already atomic; retain the old small
        // branch cost without adding BEGIN/COMMIT round trips.
        if nodes.len() == 1 {
            return self.db.execute(statement).await.map(|result| result.affected_rows == 1)
                .map_err(|error| ServiceError::InternalError(format!("node skip: {error}")));
        }
        // Partial row matches must ROLLBACK: otherwise a raced ancestor could
        // stay Running while this same statement incorrectly skips descendants.
        match self.db.transaction(vec![DbTransactionStep::ExecuteChecked {
            statement, expected_affected_rows: nodes.len() as u64,
        }]).await {
            Ok(_) => Ok(true),
            Err(DbError::ConditionFailed { .. }) => Ok(false),
            Err(error) => Err(ServiceError::InternalError(format!("node skip batch: {error}"))),
        }
    }
}
