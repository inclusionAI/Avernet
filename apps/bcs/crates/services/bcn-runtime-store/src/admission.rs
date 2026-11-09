//! Atomic admission authority: receipt, metadata, initial snapshot, inbox and wake.
use async_trait::async_trait;
use serde::{Serialize, Deserialize};
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::AdmissionStore;
use crate::{RuntimeStore, keys::*, persistence::*};
#[derive(Serialize, Deserialize)]
pub(crate) struct SessionHeader { pub spec: SessionSpec, pub accepted: Vec<TaskId> }
#[derive(Serialize, Deserialize)]
pub(crate) struct AdmittedTask { pub metadata: TaskMetadata, pub admission_id: AdmissionId }
#[async_trait]
impl AdmissionStore for RuntimeStore {
    async fn admit(&self, req: AdmissionRequest) -> RuntimeResult<AdmissionReceipt> {
        let scope = &req.admission_id.scope;
        // Reserve output (896 KiB) and active questions (64 KiB) independently.
        // Bound identity metadata before durable acceptance, including direct repository users.
        let target_id = match &req.task.target { ExecutionTarget::Agent { agent_id } => &agent_id.value, ExecutionTarget::Group { group_id } => &group_id.value };
        if [&req.task.task_id.value, &req.task.session_id.value, target_id].iter().any(|v| v.trim().is_empty() || v.len() > 256)
            || !req.task.output.is_empty() || !req.task.active_interactions.is_empty() || req.task.error.is_some()
            || encode(&req.task)?.len() > 16384 || encode(&req.spec)?.len() > 32768 {
            return Err(invalid("admission metadata or initial projection exceeds reserved budget"));
        }
        if &req.caller.scope != scope || &req.spec.session_id.scope != scope || &req.task.task_id.scope != scope || &req.input.event_id.scope != scope
            || target_scope(&req.spec.target) != scope || req.task.session_id != req.spec.session_id || req.task.target != req.spec.target
            || req.task.status != TaskStatus::Submitted || req.spec.policy.max_pending_tasks == 0 || req.spec.policy.max_pending_tasks > 16 {
            return Err(invalid("admission identity, status or queue policy"));
        }
        match &req.input.kind {
            SessionInputKind::Submit { task } if task.task_id == req.task.task_id && task.terminal_intent.is_none() && task.message.message_id.scope == *scope => {}
            _ => return Err(invalid("admission requires matching Submit")),
        }
        let receipt_key = entity("admission", &req.admission_id.value);
        let header_key = entity("session-header", &req.spec.session_id.value);
        let task_key = entity("task", &req.task.task_id.value);
        let rows = self.rows(scope, &[receipt_key.clone(), header_key.clone(), task_key.clone()]).await?;
        if let Some(row) = &rows[0] {
            let receipt: AdmissionReceipt = decode(row)?;
            if receipt.request_digest != req.request_digest || receipt.caller.principal != req.caller.principal || receipt.task.task_id != req.task.task_id { return Err(conflict("admission identity reused")); }
            return Ok(receipt);
        }
        if rows[2].is_some() { return Err(conflict("task already admitted")); }
        let mut header: SessionHeader = match &rows[1] {
            Some(row) => decode(row)?, None => SessionHeader { spec: req.spec.clone(), accepted: vec![] },
        };
        if serde_json::to_value(&header.spec).map_err(|e| invalid(&e.to_string()))? != serde_json::to_value(&req.spec).map_err(|e| invalid(&e.to_string()))? { return Err(conflict("session spec frozen")); }
        if header.accepted.len() >= header.spec.policy.max_pending_tasks as usize { return Err(RuntimeError::CapacityExceeded); }
        header.accepted.push(req.task.task_id.clone());
        let (position, mut conditions, mut mutations) = self.input_plan(&req.spec.session_id, &req.input).await?;
        let receipt = AdmissionReceipt { admission_id: req.admission_id.clone(), caller: req.caller.clone(), request_digest: req.request_digest, task: req.task.clone(), input_position: position };
        conditions.extend([condition(receipt_key.clone(), &rows[0]), condition(header_key.clone(), &rows[1]), condition(task_key.clone(), &rows[2])]);
        mutations.extend([
            put(receipt_key, &receipt)?, put(entity("repair", &req.admission_id.value), &receipt)?, put(header_key, &header)?,
            put(task_key, &AdmittedTask { metadata: TaskMetadata { caller: req.caller.clone(), task: req.task.clone() }, admission_id: req.admission_id.clone() })?,
            put(entity("snapshot", &req.task.task_id.value), &EventSnapshot { task: req.task.clone(), cursor: EventCursor { epoch: 0, sequence: 0 } })?,
        ]);
        for (target, session) in [(None,None), (Some(&req.task.target),None), (None,Some(&req.task.session_id)), (Some(&req.task.target),Some(&req.task.session_id))] {
            mutations.push(put(format!("{}{}", query_prefix(&req.caller, target, session)?, digest(&[&req.task.task_id.value])), &req.task.task_id)?);
        }
        self.write(scope, conditions, mutations).await?;
        Ok(receipt)
    }
    async fn get_receipt(&self, id: &AdmissionId) -> RuntimeResult<Option<AdmissionReceipt>> {
        self.row(&id.scope, entity("admission", &id.value)).await?.as_ref().map(decode).transpose()
    }
    async fn repair_pending(&self, scope: &Scope, after: Option<String>, limit: u32) -> RuntimeResult<Page<AdmissionReceipt>> {
        let page = self.page(scope, "repair/", after, limit).await?;
        Ok(Page { items: page.entries.iter().map(|e| decode(&e.entry)).collect::<RuntimeResult<_>>()?, next_cursor: cursor(page.next_cursor)? })
    }
}
