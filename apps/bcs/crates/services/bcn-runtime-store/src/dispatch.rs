//! Immutable operation correlation and pre-I/O connector fencing.
use async_trait::async_trait;
use serde::{Serialize, Deserialize};
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::DispatchStore;
use crate::{RuntimeStore, keys::*, persistence::*};
#[derive(Serialize, Deserialize)]
pub(crate) struct StoredDispatch { pub record: DispatchRecord, pub receipt_input: Option<SessionInput> }
fn correlation(command: &Command) -> RuntimeResult<(&TaskId, &Option<String>)> {
    match &command.effect { EffectIntent::SendAgent { task_id, node_id, .. } | EffectIntent::CancelAgent { task_id, node_id, .. } => Ok((task_id, node_id)), _ => Err(invalid("not a connector command")) }
}
fn operation(command: &Command) -> RuntimeResult<DispatchOperation> {
    match &command.effect {
        EffectIntent::SendAgent { request, .. } => Ok(DispatchOperation::Send { request: request.clone() }),
        EffectIntent::CancelAgent { dispatch_id, binding, request, .. } => Ok(DispatchOperation::Cancel { dispatch_id: dispatch_id.clone(), binding: binding.clone(), request: request.clone() }),
        _ => Err(invalid("not a connector command")),
    }
}
fn binding(operation: &DispatchOperation) -> &FrozenTargetBinding {
    match operation { DispatchOperation::Send { request } => &request.binding, DispatchOperation::Cancel { binding, .. } => binding }
}
fn valid_handle(operation: &DispatchOperation, handle: &ExecutionHandle) -> bool {
    let binding = binding(operation); handle.target == binding.target && handle.binding_version == binding.binding_version
}
fn terminal(receipt: &DispatchReceipt) -> bool {
    match receipt {
        DispatchReceipt::Cancellation { .. } => true,
        DispatchReceipt::Agent { event } => match event { AgentEvent::Completed { .. } | AgentEvent::Failed { .. } | AgentEvent::Aborted { .. } => true, AgentEvent::Snapshot { snapshot } => snapshot.status.is_terminal(), _ => false },
    }
}
impl RuntimeStore {
    pub(crate) async fn dispatch(&self, fence: &DispatchFence, expected: Option<Revision>) -> RuntimeResult<(StoredDispatch, Condition)> {
        let key = entity("dispatch", &fence.dispatch_id.value);
        let row = self.row(&fence.dispatch_id.scope, key.clone()).await?;
        let row = row.ok_or(RuntimeError::NotFound)?;
        let mut stored: StoredDispatch = decode(&row)?; stored.record.revision = row.revision;
        if stored.record.fence != *fence { return Err(RuntimeError::OwnershipLost); }
        if expected.is_some_and(|r| r != row.revision) { return Err(conflict("dispatch revision")); }
        Ok((stored, Condition { key, expected: Expected::Revision(row.revision) }))
    }
    async fn update_dispatch(&self, mut stored: StoredDispatch, conditions: Vec<Condition>, mut mutations: Vec<Mutation>) -> RuntimeResult<DispatchRecord> {
        stored.record.updated_at_ms = self.atomic.server_time_ms().await.map_err(storage)?;
        let id = &stored.record.fence.dispatch_id;
        let key = entity("dispatch", &id.value);
        mutations.push(put(key.clone(), &stored)?);
        if matches!(stored.record.phase, DispatchPhase::ResultRecorded | DispatchPhase::Unknown) { mutations.push(Mutation::Delete { key: entity("dispatch-active", &id.value) }); }
        else { mutations.push(put(entity("dispatch-active", &id.value), id)?); }
        let receipt = self.write(&id.scope, conditions, mutations).await?;
        stored.record.revision = receipt.revisions.iter().find(|r| r.key == key).ok_or_else(|| invalid("missing dispatch revision"))?.revision;
        Ok(stored.record)
    }
}
#[async_trait]
impl DispatchStore for RuntimeStore {
    async fn claim(&self, id: &CommandId, owner: NodeIdentity) -> RuntimeResult<DispatchRecord> {
        if owner.scope != id.scope { return Err(invalid("dispatch owner scope")); }
        let rows = self.rows(&id.scope, &[entity("command", &id.value), entity("delivery", &id.value)]).await?;
        if rows[1].is_none() { return Err(conflict("command not published")); }
        let command: Command = decode(rows[0].as_ref().ok_or(RuntimeError::NotFound)?)?;
        let operation = operation(&command)?;
        let binding = binding(&operation);
        if operation.dispatch_id().scope != id.scope || operation.operation_id().scope != id.scope || binding.target.scope != id.scope || correlation(&command)?.0.scope != id.scope { return Err(invalid("operation scope")); }
        match &operation {
            DispatchOperation::Send { request } => { if request.message.message_id.scope != id.scope || request.handle.as_ref().is_some_and(|h| !valid_handle(&operation, h)) { return Err(invalid("send binding")); } }
            DispatchOperation::Cancel { request, .. } => if !valid_handle(&operation, &request.handle) { return Err(invalid("cancel binding")); },
        }
        let key = entity("dispatch", &operation.dispatch_id().value);
        let row = self.row(&id.scope, key.clone()).await?;
        if let Some(row) = row {
            let mut existing: StoredDispatch = decode(&row)?;
            if existing.record.context.command_id != command.command_id || existing.record.operation != operation { return Err(conflict("dispatch identity reused")); }
            if existing.record.fence.owner != owner { return Err(RuntimeError::OwnershipLost); }
            existing.record.revision = row.revision; return Ok(existing.record);
        }
        let (task_id, node_id) = correlation(&command)?;
        let context = DispatchContext { command_id: command.command_id.clone(), session_id: command.session_id.clone(), task_id: task_id.clone(), node_id: node_id.clone() };
        let record = DispatchRecord { context, fence: DispatchFence { dispatch_id: operation.dispatch_id().clone(), owner, epoch: 1 }, revision: Revision(0), operation, phase: DispatchPhase::Prepared, handle: None, receipt: None, updated_at_ms: 0 };
        self.update_dispatch(StoredDispatch { record, receipt_input: None }, vec![Condition { key, expected: Expected::Absent }], vec![]).await
    }
    async fn read(&self, id: &DispatchId) -> RuntimeResult<Option<DispatchRecord>> {
        self.row(&id.scope, entity("dispatch", &id.value)).await?.map(|row| { let mut stored: StoredDispatch = decode(&row)?; stored.record.revision = row.revision; Ok(stored.record) }).transpose()
    }
    async fn scan_active(&self, scope: &Scope, after: Option<String>, limit: u32) -> RuntimeResult<Page<DispatchRecord>> {
        let page = self.page(scope, "dispatch-active/", after, limit).await?;
        let ids = page.entries.iter().map(|r| decode::<DispatchId>(&r.entry)).collect::<RuntimeResult<Vec<_>>>()?;
        let rows = self.rows(scope, &ids.iter().map(|id| entity("dispatch", &id.value)).collect::<Vec<_>>()).await?;
        let mut items = vec![];
        for row in rows.into_iter().flatten() {
            let mut stored: StoredDispatch = decode(&row)?; stored.record.revision = row.revision;
            if !matches!(stored.record.phase, DispatchPhase::ResultRecorded | DispatchPhase::Unknown) { items.push(stored.record); }
        }
        Ok(Page { items, next_cursor: cursor(page.next_cursor)? })
    }
    async fn mark_may_have_sent(&self, fence: &DispatchFence, expected: Revision) -> RuntimeResult<DispatchRecord> {
        let (mut stored, condition) = self.dispatch(fence, Some(expected)).await?;
        if stored.record.phase != DispatchPhase::Prepared { return Err(conflict("only PREPARED may start I/O")); }
        stored.record.phase = DispatchPhase::MayHaveSent;
        self.update_dispatch(stored, vec![condition], vec![]).await
    }
    async fn save_handle(&self, fence: &DispatchFence, expected: Revision, handle: ExecutionHandle) -> RuntimeResult<DispatchRecord> {
        let (mut stored, condition) = self.dispatch(fence, Some(expected)).await?;
        if !valid_handle(&stored.record.operation, &handle) || matches!(stored.record.phase, DispatchPhase::Prepared | DispatchPhase::ResultRecorded | DispatchPhase::Unknown) { return Err(invalid("handle binding or phase")); }
        if stored.record.handle.as_ref().is_some_and(|h| h != &handle) { return Err(conflict("remote handle frozen")); }
        stored.record.handle = Some(handle); stored.record.phase = DispatchPhase::Accepted;
        self.update_dispatch(stored, vec![condition], vec![]).await
    }
    async fn save_receipt(&self, fence: &DispatchFence, expected: Revision, receipt: DispatchReceipt, session: &SessionId, input: SessionInput) -> RuntimeResult<DispatchRecord> {
        let (mut stored, condition) = self.dispatch(fence, None).await?;
        if &stored.record.context.session_id != session || stored.record.phase == DispatchPhase::Prepared { return Err(invalid("receipt session or phase")); }
        let (task, node) = (&stored.record.context.task_id, &stored.record.context.node_id);
        let valid = match (&stored.record.operation, &receipt, &input.kind) {
            (DispatchOperation::Send { .. }, DispatchReceipt::Agent { event }, SessionInputKind::Agent { task_id, node_id, dispatch_id, event: actual }) => task_id == task && node_id == node && dispatch_id == &fence.dispatch_id && event == actual,
            (DispatchOperation::Cancel { request, .. }, DispatchReceipt::Cancellation { outcome }, SessionInputKind::Cancellation { task_id, node_id, dispatch_id, operation_id, outcome: actual }) => task_id == task && node_id == node && dispatch_id == &fence.dispatch_id && operation_id == &request.operation_id && outcome == actual,
            _ => false,
        };
        if !valid { return Err(invalid("receipt kind/correlation")); }
        if matches!(stored.record.phase, DispatchPhase::ResultRecorded | DispatchPhase::Unknown) {
            if stored.record.receipt.as_ref() != Some(&receipt) || stored.receipt_input.as_ref() != Some(&input) { return Err(conflict("terminal receipt immutable")); }
            self.write(&session.scope, vec![condition], vec![]).await?;
            return Ok(stored.record);
        }
        if stored.record.revision != expected { return Err(conflict("dispatch revision")); }
        if let DispatchReceipt::Agent { event } = &receipt {
            let handle = match event { AgentEvent::Accepted { handle } | AgentEvent::InputRequired { handle, .. } | AgentEvent::AuthRequired { handle } => Some(handle), AgentEvent::Snapshot { snapshot } => Some(&snapshot.handle), _ => None };
            if let Some(handle) = handle {
                if !valid_handle(&stored.record.operation, handle) || stored.record.handle.as_ref().is_some_and(|h| h != handle) { return Err(invalid("receipt handle")); }
                stored.record.handle = Some(handle.clone());
            }
        }
        let (_, mut conditions, mutations) = self.input_plan(session, &input).await?;
        conditions.push(condition);
        stored.record.phase = if terminal(&receipt) { DispatchPhase::ResultRecorded } else { DispatchPhase::Accepted };
        stored.record.receipt = Some(receipt);
        stored.receipt_input = Some(input);
        self.update_dispatch(stored, conditions, mutations).await
    }
    async fn takeover(&self, current: &DispatchFence, next: NodeIdentity) -> RuntimeResult<DispatchRecord> {
        let (mut stored, condition) = self.dispatch(current, None).await?;
        if next.scope != current.dispatch_id.scope { return Err(invalid("takeover scope")); }
        if matches!(stored.record.phase, DispatchPhase::ResultRecorded | DispatchPhase::Unknown) { return Err(conflict("dispatch already finished")); }
        stored.record.fence.owner = next;
        stored.record.fence.epoch = current.epoch.checked_add(1).ok_or_else(|| invalid("dispatch epoch exhausted"))?;
        self.update_dispatch(stored, vec![condition], vec![]).await
    }
}
