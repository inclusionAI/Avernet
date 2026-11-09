//! Task-global ordered streams and independent, bounded node projections.
use async_trait::async_trait;
use serde::{Serialize, Deserialize};
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::{TaskEventStore, SessionStore};
use crate::{RuntimeStore, keys::*, persistence::*};
#[derive(Serialize, Deserialize)]
struct Projection { node: Option<String>, fence: DispatchFence, output: Vec<MessagePart> }
#[derive(Serialize, Deserialize)]
struct Publication { task_id: TaskId, fingerprint: String, cursor: EventCursor }
fn projection_prefix(task: &TaskId) -> String { format!("projection/{}/", digest(&[&task.value])) }
fn projection_key(task: &TaskId, node: &Option<String>) -> String { format!("{}{}", projection_prefix(task), digest(&[if node.is_some() { "node" } else { "direct" }, node.as_deref().unwrap_or("")])) }
fn event_prefix(task: &TaskId, epoch: u64) -> String { format!("event/{}/{epoch:020}/", digest(&[&task.value])) }
fn event_key(task: &TaskId, cursor: &EventCursor) -> String { format!("{}{:020}", event_prefix(task, cursor.epoch), cursor.sequence) }
fn fingerprint<T: Serialize>(value: &T) -> RuntimeResult<String> {
    let json = serde_json::to_string(value).map_err(|e| invalid(&e.to_string()))?; Ok(digest(&[&json]))
}
impl RuntimeStore {
    async fn prior_publication(&self, id: &EventId, task: &TaskId, fingerprint: &str) -> RuntimeResult<Option<EventCursor>> {
        let row = self.row(&id.scope, entity("event-published", &id.value)).await?;
        row.map(|r| {
            let receipt: Publication = decode(&r)?;
            if &receipt.task_id != task || receipt.fingerprint != fingerprint { return Err(conflict("event identity reused")); }
            Ok(receipt.cursor)
        }).transpose()
    }
    fn event_mutations(snapshot: &EventSnapshot, event: &TaskEvent, fingerprint: String) -> RuntimeResult<Vec<Mutation>> {
        Ok(vec![put(entity("snapshot", &event.task_id.value), snapshot)?, put(event_key(&event.task_id, &event.cursor), event)?,
            put(entity("event-published", &event.id.value), &Publication { task_id: event.task_id.clone(), fingerprint, cursor: event.cursor.clone() })?])
    }
    async fn connector_event(&self, fence: &DispatchFence, task: TaskId, id: EventId, expected: EventCursor, reset: Option<Vec<MessagePart>>, text: Option<String>) -> RuntimeResult<EventCursor> {
        let (dispatch, dispatch_condition) = self.dispatch(fence, None).await?;
        let (canonical_task, node) = (&dispatch.record.context.task_id, &dispatch.record.context.node_id);
        if canonical_task != &task || id.scope != task.scope || !matches!(dispatch.record.operation, DispatchOperation::Send { .. }) || matches!(dispatch.record.phase, DispatchPhase::Prepared | DispatchPhase::ResultRecorded | DispatchPhase::Unknown) { return Err(invalid("connector event authority/phase")); }
        let command_key = projection_command(&task, node);
        let command_row = self.row(&task.scope, command_key.clone()).await?;
        let command: CommandId = decode(command_row.as_ref().ok_or_else(|| conflict("projection command not committed"))?)?;
        if command != dispatch.record.context.command_id { return Err(conflict("projection command superseded")); }
        let command_condition = condition(command_key, &command_row);
        let fingerprint = fingerprint(&(fence, &task, &expected, &reset, &text))?;
        if let Some(cursor) = self.prior_publication(&id, &task, &fingerprint).await? {
            self.write(&task.scope, vec![dispatch_condition, command_condition], vec![]).await?; return Ok(cursor);
        }
        let key = entity("snapshot", &task.value);
        let row = self.row(&task.scope, key.clone()).await?;
        let mut snapshot: EventSnapshot = decode(row.as_ref().ok_or(RuntimeError::NotFound)?)?;
        if snapshot.cursor != expected { return Err(conflict("task projection cursor changed")); }
        if snapshot.task.status.is_terminal() { return Err(conflict("terminal task projection frozen")); }
        let prefix = projection_prefix(&task);
        let projections = self.page(&task.scope, &prefix, None, 33).await?;
        if projections.next_cursor.is_some() { return Err(RuntimeError::CapacityExceeded); }
        let mut projections = projections.entries.iter().map(|r| decode::<Projection>(&r.entry)).collect::<RuntimeResult<Vec<_>>>()?;
        let position = projections.iter().position(|p| &p.node == node);
        let requested_reset = reset.is_some();
        let mut appended = snapshot.task.output.clone();
        if let Some(text) = &text {
            if let Some(MessagePart::Text { text: tail }) = appended.last_mut() { tail.push_str(text); }
            else { appended.push(MessagePart::Text { text: text.clone() }); }
        }
        let output = if let Some(output) = reset { output } else {
            let prior = position.and_then(|p| projections.get(p)).ok_or_else(|| conflict("reset required before delta"))?;
            if prior.fence != *fence { return Err(conflict("new connector fence requires reset")); }
            let mut output = prior.output.clone();
            let text = text.clone().ok_or_else(|| invalid("delta text missing"))?;
            if let Some(MessagePart::Text { text: tail }) = output.last_mut() { tail.push_str(&text); } else { output.push(MessagePart::Text { text }); }
            output
        };
        let projection = Projection { node: node.clone(), fence: fence.clone(), output };
        if let Some(position) = position { projections.remove(position); }
        projections.push(Projection { node: projection.node.clone(), fence: projection.fence.clone(), output: projection.output.clone() });
        if projections.len() > 32 { return Err(RuntimeError::CapacityExceeded); }
        projections.sort_by(|a,b| a.node.cmp(&b.node));
        // The session header is immutable. Keep every private node projection for
        // recovery; only the frozen public selector affects aggregate token output.
        let spec = self.load_spec(&dispatch.record.context.session_id).await?.ok_or(RuntimeError::NotFound)?;
        snapshot.task.output = projections.into_iter().filter(|p| spec.live_output_nodes.as_ref()
            .is_none_or(|nodes| p.node.as_ref().is_some_and(|node| nodes.contains(node))))
            .flat_map(|p| p.output).collect();
        // Delta has no node/address field: use Reset unless a consumer's ordinary
        // tail append produces exactly the new aggregate (including part boundaries).
        let is_reset = requested_reset || appended != snapshot.task.output;
        snapshot.task.updated_at_ms = self.atomic.server_time_ms().await.map_err(storage)?;
        snapshot.cursor = if is_reset { EventCursor { epoch: expected.epoch.checked_add(1).ok_or_else(|| invalid("event epoch exhausted"))?, sequence: 1 } }
            else { EventCursor { epoch: expected.epoch, sequence: expected.sequence.checked_add(1).ok_or_else(|| invalid("event sequence exhausted"))? } };
        let kind = if is_reset { TaskEventKind::Reset { snapshot: snapshot.task.clone() } } else { TaskEventKind::Delta { text: text.ok_or_else(|| invalid("delta text missing"))? } };
        let event = TaskEvent { id: id.clone(), task_id: task.clone(), cursor: snapshot.cursor.clone(), kind };
        let mut mutations = Self::event_mutations(&snapshot, &event, fingerprint)?;
        mutations.push(put(projection_key(&task, node), &projection)?);
        self.write(&task.scope, vec![dispatch_condition, command_condition, condition(key, &row), Condition { key: entity("event-published", &id.value), expected: Expected::Absent }], mutations).await?;
        Ok(snapshot.cursor)
    }
}
#[async_trait]
impl TaskEventStore for RuntimeStore {
    async fn read_projection(&self, id: &DispatchId) -> RuntimeResult<Option<Vec<MessagePart>>> {
        use bcn_runtime_api::port::repo::DispatchStore;
        let record = self.read(id).await?.ok_or(RuntimeError::NotFound)?;
        if !matches!(record.operation, DispatchOperation::Send { .. }) { return Err(invalid("projection requires send")); }
        let task = &record.context.task_id; let node = &record.context.node_id;
        let rows = self.rows(&id.scope, &[projection_command(task, node), projection_key(task, node)]).await?;
        let command: CommandId = decode(rows[0].as_ref().ok_or_else(|| conflict("projection command absent"))?)?;
        if command != record.context.command_id { return Err(conflict("projection command superseded")); }
        rows[1].as_ref().map(|row| decode::<Projection>(row).map(|p| p.output)).transpose()
    }
    async fn publish_hard_event(&self, fence: &OwnershipFence, reference: CommittedEventRef) -> RuntimeResult<EventCursor> {
        if session_shard(&reference.session_id) != fence.shard || reference.event_id.scope != reference.session_id.scope { return Err(invalid("hard event scope/shard")); }
        self.ownership_condition(fence).await?;
        let row = self.row(&reference.event_id.scope, entity("hard-event", &reference.event_id.value)).await?;
        let (session, domain): (SessionId, DomainEvent) = decode(row.as_ref().ok_or(RuntimeError::NotFound)?)?;
        if session != reference.session_id { return Err(invalid("hard event session")); }
        let fingerprint = fingerprint(&domain)?;
        if let Some(cursor) = self.prior_publication(&reference.event_id, &domain.task_id, &fingerprint).await? {
            self.fenced_write(fence, vec![], vec![]).await?; return Ok(cursor);
        }
        let mut cp = self.load(&session).await?.ok_or(RuntimeError::NotFound)?;
        // Pending order is durable checkpoint commit order, including within a transition.
        // Published-ID retries above remain idempotent after this head has advanced.
        if cp.pending_events.first() != Some(&domain) { return Err(conflict("earlier committed hard event must publish first")); }
        let key = entity("snapshot", &domain.task_id.value);
        let row = self.row(&session.scope, key.clone()).await?;
        let mut snapshot: EventSnapshot = decode(row.as_ref().ok_or(RuntimeError::NotFound)?)?;
        match &domain.kind {
            DomainEventKind::TaskStatusChanged { status } => {
                if status.is_terminal() {
                    let result = self.row(&session.scope, entity("task-result", &domain.task_id.value)).await?;
                    let saved: SavedResult = decode(result.as_ref().ok_or_else(|| conflict("authoritative result not saved"))?)?;
                    if saved.intent.status != *status { return Err(conflict("terminal result status")); }
                    snapshot.task.active_interactions.clear();
                    snapshot.task.output = saved.intent.output; snapshot.task.error = saved.intent.error;
                }
                snapshot.task.status = *status;
            }
            DomainEventKind::InputRequired { interaction_id, prompt } => {
                snapshot.task.active_interactions.retain(|i| &i.interaction_id != interaction_id);
                snapshot.task.active_interactions.push(ActiveInteraction { interaction_id: interaction_id.clone(), prompt: prompt.clone() });
                snapshot.task.status = TaskStatus::InputRequired;
            }
            DomainEventKind::InteractionResolved { interaction_id } => {
                snapshot.task.active_interactions.retain(|i| &i.interaction_id != interaction_id);
            }
            _ => {}
        }
        snapshot.cursor.sequence = snapshot.cursor.sequence.checked_add(1).ok_or_else(|| invalid("event sequence exhausted"))?;
        snapshot.task.updated_at_ms = self.atomic.server_time_ms().await.map_err(storage)?;
        let event = TaskEvent { id: domain.event_id.clone(), task_id: domain.task_id.clone(), cursor: snapshot.cursor.clone(), kind: TaskEventKind::Domain { event: domain.clone() } };
        cp.pending_events.retain(|e| e.event_id != reference.event_id);
        let mut mutations = Self::event_mutations(&snapshot, &event, fingerprint)?;
        mutations.push(put(entity("checkpoint", &session.value), &cp)?);
        self.fenced_write(fence, vec![condition(key, &row), Condition { key: entity("checkpoint", &session.value), expected: Expected::Revision(cp.revision) }, Condition { key: entity("event-published", &reference.event_id.value), expected: Expected::Absent }], mutations).await?;
        Ok(snapshot.cursor)
    }
    async fn append(&self, fence: &DispatchFence, event: TaskEvent) -> RuntimeResult<EventCursor> {
        let TaskEventKind::Delta { text } = event.kind else { return Err(invalid("connector append accepts Delta only")); };
        self.connector_event(fence, event.task_id, event.id, event.cursor, None, Some(text)).await
    }
    async fn publish_reset(&self, fence: &DispatchFence, projection: ConnectorProjection, id: EventId) -> RuntimeResult<EventCursor> {
        self.connector_event(fence, projection.task_id, id, projection.expected_cursor, Some(projection.output), None).await
    }
    async fn read_after(&self, task: &TaskId, after: Option<EventCursor>, limit: u32) -> RuntimeResult<EventPage> {
        let key = entity("snapshot", &task.value);
        let row = self.row(&task.scope, key.clone()).await?;
        let snapshot: EventSnapshot = decode(row.as_ref().ok_or(RuntimeError::NotFound)?)?;
        let expired = after.as_ref().is_some_and(|c| c.epoch != snapshot.cursor.epoch);
        if after.as_ref().is_some_and(|c| c.epoch > snapshot.cursor.epoch || (c.epoch == snapshot.cursor.epoch && c.sequence > snapshot.cursor.sequence)) { return Err(invalid("cursor ahead of stream")); }
        let from = if expired { EventCursor { epoch: snapshot.cursor.epoch, sequence: 0 } } else { after.unwrap_or_else(|| snapshot.cursor.clone()) };
        let prefix = event_prefix(task, snapshot.cursor.epoch);
        let page = self.atomic.scan_page(&task.scope, PARTITION, &prefix, Some(ScanCursor { scope: task.scope.clone(), partition: PARTITION.into(), prefix: prefix.clone(), after_key: event_key(task, &from) }), limit as usize).await.map_err(storage)?;
        let latest = self.row(&task.scope, key).await?;
        if latest.as_ref().map(|r| r.revision) != row.as_ref().map(|r| r.revision) { return Err(conflict("stream changed during read; retry")); }
        Ok(EventPage { snapshot, events: page.entries.iter().map(|e| decode(&e.entry)).collect::<RuntimeResult<_>>()?, cursor_expired: expired })
    }
    async fn read_snapshot(&self, task: &TaskId) -> RuntimeResult<Option<EventSnapshot>> {
        self.row(&task.scope, entity("snapshot", &task.value)).await?.as_ref().map(decode).transpose()
    }
}
