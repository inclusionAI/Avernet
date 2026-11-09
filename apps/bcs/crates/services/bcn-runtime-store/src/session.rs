//! Checkpoint CAS, immutable outboxes and logical-shard wake lifecycle.
use async_trait::async_trait;
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::{SessionStore, InboxPort};
use crate::{RuntimeStore, keys::*, persistence::*, admission::{SessionHeader, AdmittedTask}};
#[async_trait]
impl SessionStore for RuntimeStore {
    async fn begin_recovery(&self, session: &SessionId, fence: &OwnershipFence, threshold: u32) -> RuntimeResult<SessionRecovery> {
        self.begin_session_recovery(session, fence, threshold).await
    }
    async fn recovery_status(&self, session: &SessionId) -> RuntimeResult<Option<SessionRecovery>> {
        self.row(&session.scope, entity("recovery", &session.value)).await?.as_ref().map(decode).transpose()
    }
    async fn activate(&self, spec: SessionSpec, state: RunnerState, fence: OwnershipFence) -> RuntimeResult<SessionCheckpoint> {
        if session_shard(&spec.session_id) != fence.shard || state.session_id != spec.session_id || state.schema_version != CHECKPOINT_SCHEMA_VERSION { return Err(invalid("activation identity/schema")); }
        self.ownership_condition(&fence).await?;
        let key = entity("checkpoint", &spec.session_id.value);
        let rows = self.rows(&spec.session_id.scope, &[entity("session-header", &spec.session_id.value), key.clone()]).await?;
        let header: SessionHeader = decode(rows[0].as_ref().ok_or(RuntimeError::NotFound)?)?;
        if encode(&header.spec)? != encode(&spec)? { return Err(conflict("session spec frozen")); }
        let mut cp = match &rows[1] {
            Some(row) => decode::<SessionCheckpoint>(row)?,
            None => SessionCheckpoint { spec, revision: Revision(0), fence: fence.clone(), consumed_input: 0, state, pending_effects: vec![], pending_events: vec![], deadlines: vec![] },
        };
        cp.fence = fence.clone();
        let tx = self.fenced_write(&fence, vec![condition(key.clone(), &rows[1])], vec![put(key.clone(), &cp)?]).await?;
        cp.revision = tx.revisions.iter().find(|r| r.key == key).ok_or_else(|| invalid("missing checkpoint revision"))?.revision;
        Ok(cp)
    }
    async fn load_spec(&self, session: &SessionId) -> RuntimeResult<Option<SessionSpec>> {
        self.row(&session.scope, entity("session-header", &session.value)).await?.as_ref().map(|r| decode::<SessionHeader>(r).map(|h| h.spec)).transpose()
    }
    async fn load(&self, session: &SessionId) -> RuntimeResult<Option<SessionCheckpoint>> {
        self.row(&session.scope, entity("checkpoint", &session.value)).await?.as_ref().map(|row| {
            let mut cp: SessionCheckpoint = decode(row)?;
            if cp.state.schema_version != CHECKPOINT_SCHEMA_VERSION { return Err(RuntimeError::IncompatibleSchema(cp.state.schema_version)); }
            cp.revision = row.revision; Ok(cp)
        }).transpose()
    }
    async fn commit(&self, commit: SessionCommit) -> RuntimeResult<SessionCheckpoint> {
        if session_shard(&commit.session_id) != commit.fence.shard { return Err(invalid("commit shard")); }
        self.ownership_condition(&commit.fence).await?;
        let mut cp = self.load(&commit.session_id).await?.ok_or(RuntimeError::NotFound)?;
        if cp.revision != commit.expected_revision { return Err(conflict("checkpoint revision")); }
        if cp.fence != commit.fence { return Err(RuntimeError::OwnershipLost); }
        if commit.consumed_input < cp.consumed_input || commit.consumed_input - cp.consumed_input > 100 { return Err(invalid("input batch bound")); }
        let state = &commit.transition.next_state;
        if state.schema_version != CHECKPOINT_SCHEMA_VERSION || state.session_id != cp.spec.session_id || state.target != cp.spec.target || state.kind != cp.spec.kind || state.policy != cp.spec.policy || state.bindings != cp.spec.bindings || state.graph != cp.state.graph { return Err(invalid("checkpoint frozen identity/schema")); }
        if state.pending_tasks.len() > cp.spec.policy.max_pending_tasks as usize || commit.transition.effects.len() > 64 || commit.transition.events.len() > 32 { return Err(RuntimeError::CapacityExceeded); }
        let count = (commit.consumed_input - cp.consumed_input) as u32;
        let consumed = if count == 0 { vec![] } else { self.read_batch(&commit.session_id, cp.consumed_input, count).await? };
        if consumed.len() as u64 != commit.consumed_input - cp.consumed_input { return Err(invalid("input cursor past durable inbox")); }
        let header_key = entity("session-header", &commit.session_id.value);
        let header_row = self.row(&commit.session_id.scope, header_key.clone()).await?;
        let mut header: SessionHeader = decode(header_row.as_ref().ok_or(RuntimeError::NotFound)?)?;
        let checkpoint_key = entity("checkpoint", &commit.session_id.value);
        let mut conditions = vec![Condition { key: checkpoint_key.clone(), expected: Expected::Revision(cp.revision) }, condition(header_key.clone(), &header_row)];
        let mut mutations = Vec::new();
        let mut immutable = Vec::new();
        let mut projection_commands = std::collections::BTreeMap::new();
        let mut projection_nodes = std::collections::BTreeSet::new();
        let status_keys = commit.transition.effects.iter().map(|e| effect_command_id(&commit.session_id, e).map(|id| entity("delivery", &id.value)))
            .chain(commit.transition.events.iter().map(|e| Ok(entity("event-published", &e.event_id.value)))).collect::<RuntimeResult<Vec<_>>>()?;
        let statuses = self.rows(&commit.session_id.scope, &status_keys).await?;
        for (effect_index, effect) in commit.transition.effects.iter().enumerate() {
            let id = effect_command_id(&commit.session_id, effect)?;
            let command = Command { command_id: id.clone(), session_id: commit.session_id.clone(), effect: effect.clone() };
            let task = match effect { EffectIntent::SendAgent { task_id, .. } | EffectIntent::CancelAgent { task_id, .. } => task_id, EffectIntent::Judge { request } => &request.task_id, EffectIntent::SaveResult { intent } => &intent.task_id };
            if task.scope != commit.session_id.scope || !header.accepted.contains(task) { return Err(invalid("effect task not admitted in session")); }
            immutable.push((entity("command", &id.value), encode(&command)?));
            if let EffectIntent::SendAgent { task_id, node_id, .. } = effect {
                let key = projection_command(task_id, node_id);
                if !projection_nodes.insert(key.clone()) { return Err(invalid("multiple sends for one task/node in a transition")); }
                projection_commands.insert(entity("command", &id.value), (key, id));
            }
            if let EffectIntent::SaveResult { intent } = effect {
                if !intent.status.is_terminal() || intent.result_id.scope != task.scope { return Err(invalid("result intent")); }
                immutable.push((entity("intent", &intent.result_id.value), encode(intent)?));
            }
            if statuses[effect_index].is_none() && !cp.pending_effects.contains(effect) { cp.pending_effects.push(effect.clone()); }
        }
        for (event_index, event) in commit.transition.events.iter().enumerate() {
            if event.event_id.scope != commit.session_id.scope || !header.accepted.contains(&event.task_id) { return Err(invalid("event task not admitted")); }
            immutable.push((entity("hard-event", &event.event_id.value), encode(&(commit.session_id.clone(), event.clone()))?));
            if statuses[commit.transition.effects.len() + event_index].is_none() && !cp.pending_events.contains(event) { cp.pending_events.push(event.clone()); }
        }
        if immutable.len() > 100 || cp.pending_effects.len() > 64 || cp.pending_events.len() > 64 { return Err(RuntimeError::CapacityExceeded); }
        let existing = self.rows(&commit.session_id.scope, &immutable.iter().map(|(key, _)| key.clone()).collect::<Vec<_>>()).await?;
        for ((key, value), row) in immutable.into_iter().zip(existing) {
            if let Some(row) = &row { if row.value != value { return Err(conflict("immutable outbox identity reused")); } }
            else {
                mutations.push(Mutation::Put { key: key.clone(), value });
                // Only the first commit of this immutable command supersedes the prior send.
                // Replaying an older outbox effect must never restore its authority.
                if let Some((key, id)) = projection_commands.remove(&key) { mutations.push(put(key, &id)?); }
            }
            conditions.push(condition(key, &row));
        }
        let submitted_ids = consumed.iter().filter_map(|i| if let SessionInputKind::Submit { task } = &i.input.kind { Some(task.task_id.clone()) } else { None }).collect::<Vec<_>>();
        let task_rows = self.rows(&commit.session_id.scope, &submitted_ids.iter().map(|t| entity("task", &t.value)).collect::<Vec<_>>()).await?;
        for row in task_rows {
            let task: AdmittedTask = decode(row.as_ref().ok_or(RuntimeError::NotFound)?)?;
            mutations.push(Mutation::Delete { key: entity("repair", &task.admission_id.value) });
        }
        for item in &consumed {
            if let SessionInputKind::ResultSaved { result } = &item.input.kind {
                if result.intent.node_id.is_none() { header.accepted.retain(|t| t != &result.intent.task_id); }
            }
        }
        if commit.consumed_input > cp.consumed_input || commit.transition.next_state != cp.state {
            mutations.push(put(entity("recovery", &commit.session_id.value), &SessionRecovery { fence: commit.fence.clone(), takeovers: 0, quarantined: false })?);
        }
        cp.state = commit.transition.next_state;
        cp.consumed_input = commit.consumed_input;
        for change in commit.transition.timer_changes {
            match change {
                TimerChange::Upsert(deadline) => { cp.deadlines.retain(|d| d.timer_id != deadline.timer_id); cp.deadlines.push(deadline); }
                TimerChange::Cancel(id) => cp.deadlines.retain(|d| d.timer_id != id),
            }
        }
        if cp.deadlines.len() > 64 { return Err(RuntimeError::CapacityExceeded); }
        mutations.extend([put(header_key, &header)?, put(checkpoint_key.clone(), &cp)?, put(wake(&commit.session_id), &commit.session_id)?]);
        let tx = self.fenced_write(&commit.fence, conditions, mutations).await?;
        cp.revision = tx.revisions.iter().find(|r| r.key == checkpoint_key).ok_or_else(|| invalid("missing checkpoint revision"))?.revision;
        Ok(cp)
    }
    async fn deactivate(&self, session: &SessionId, fence: &OwnershipFence, expected: Revision) -> RuntimeResult<()> {
        let cp = self.load(session).await?.ok_or(RuntimeError::NotFound)?;
        if cp.fence != *fence { return Err(RuntimeError::OwnershipLost); }
        if cp.revision != expected { return Err(conflict("checkpoint revision")); }
        let seq_key = entity("input-sequence", &session.value);
        let header_key = entity("session-header", &session.value);
        let mut rows = self.rows(&session.scope, &[seq_key.clone(), header_key.clone()]).await?;
        let header_row = rows.pop().ok_or(RuntimeError::NotFound)?;
        let header: SessionHeader = decode(header_row.as_ref().ok_or(RuntimeError::NotFound)?)?;
        let seq = rows.pop().ok_or(RuntimeError::NotFound)?;
        let head = seq.as_ref().map(decode::<u64>).transpose()?.unwrap_or(0);
        if !header.accepted.is_empty() || head != cp.consumed_input || cp.state.current_task.is_some() || !cp.state.pending_tasks.is_empty() || !cp.pending_effects.is_empty() || !cp.pending_events.is_empty() || !cp.deadlines.is_empty() { return Err(conflict("session has pending work")); }
        self.fenced_write(fence, vec![Condition { key: entity("checkpoint", &session.value), expected: Expected::Revision(expected) }, condition(seq_key, &seq), condition(header_key, &header_row)], vec![Mutation::Delete { key: wake(session) }]).await?;
        Ok(())
    }
    async fn scan_active(&self, shard: &ShardId, after: Option<String>, limit: u32) -> RuntimeResult<Page<SessionId>> {
        let page = self.page(&shard.scope, &format!("wake/{:04}/", shard.number), after, limit).await?;
        Ok(Page { items: page.entries.iter().map(|r| decode(&r.entry)).collect::<RuntimeResult<_>>()?, next_cursor: cursor(page.next_cursor)? })
    }
}
