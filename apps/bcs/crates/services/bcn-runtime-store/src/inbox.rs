//! Ordered inbox with immutable event dedup and bounded committed reclamation.
use async_trait::async_trait;
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::{InboxPort, SessionStore};
use crate::{RuntimeStore, keys::*, persistence::*, admission::AdmittedTask};
impl RuntimeStore {
    pub(crate) async fn input_plan(&self, session: &SessionId, input: &SessionInput) -> RuntimeResult<(u64, Vec<Condition>, Vec<Mutation>)> {
        if input.event_id.scope != session.scope { return Err(invalid("input scope")); }
        let seq = entity("input-sequence", &session.value);
        let dedup = input_id(session, &input.event_id);
        let rows = self.rows(&session.scope, &[seq.clone(), dedup.clone()]).await?;
        if let Some(row) = &rows[1] {
            let entry: InboxEntry = decode(row)?;
            if &entry.input != input { return Err(conflict("input identity reused")); }
            return Ok((entry.position, vec![condition(dedup, &rows[1])], vec![]));
        }
        let position = rows[0].as_ref().map(decode::<u64>).transpose()?.unwrap_or(0).checked_add(1).ok_or_else(|| invalid("input sequence exhausted"))?;
        let entry = InboxEntry { position, input: input.clone() };
        Ok((position, vec![condition(seq.clone(), &rows[0]), condition(dedup.clone(), &rows[1])], vec![put(seq, &position)?, put(dedup, &entry)?, put(inbox(session, position), &entry)?, put(wake(session), session)?]))
    }
}
#[async_trait]
impl InboxPort for RuntimeStore {
    async fn append_and_wake(&self, session: &SessionId, input: SessionInput) -> RuntimeResult<u64> {
        if self.load_spec(session).await?.is_none() { return Err(RuntimeError::NotFound); }
        // New tasks enter only through atomic admission; cancellation bypasses capacity.
        if matches!(input.kind, SessionInputKind::Submit { .. }) { return Err(invalid("Submit requires admission")); }
        let (position, mut conditions, mut mutations) = self.input_plan(session, &input).await?;
        if let SessionInputKind::ResultSaved { result } = &input.kind {
            let intent = &result.intent;
            if intent.result_id.scope != session.scope || intent.task_id.scope != session.scope { return Err(invalid("saved result scope")); }
            let receipt_key = entity("saved-receipt", &intent.result_id.value);
            let rows = self.rows(&session.scope, &[entity("intent", &intent.result_id.value), entity("task", &intent.task_id.value), receipt_key.clone()]).await?;
            let committed: ResultIntent = decode(rows[0].as_ref().ok_or_else(|| conflict("result intent not committed"))?)?;
            let admitted: AdmittedTask = decode(rows[1].as_ref().ok_or(RuntimeError::NotFound)?)?;
            if committed != *intent || admitted.metadata.task.session_id != *session { return Err(conflict("saved result intent/session mismatch")); }
            if let Some(row) = &rows[2] {
                if decode::<SavedResult>(row)? != *result { return Err(conflict("saved result receipt immutable")); }
            } else { mutations.push(put(receipt_key.clone(), result)?); }
            conditions.push(condition(receipt_key, &rows[2]));
            if intent.node_id.is_none() {
                let key = entity("task-result", &intent.task_id.value);
                let row = self.row(&session.scope, key.clone()).await?;
                if let Some(row) = &row { if decode::<SavedResult>(row)?.intent != *intent { return Err(conflict("task result immutable")); } }
                else { mutations.push(put(key.clone(), result)?); }
                conditions.push(condition(key, &row));
            }
        }
        self.write(&session.scope, conditions, mutations).await?;
        Ok(position)
    }
    async fn read_batch(&self, session: &SessionId, after: u64, limit: u32) -> RuntimeResult<Vec<InboxEntry>> {
        let prefix = inbox_prefix(session);
        let cursor = ScanCursor { scope: session.scope.clone(), partition: PARTITION.into(), prefix: prefix.clone(), after_key: inbox(session, after) };
        let page = self.atomic.scan_page(&session.scope, PARTITION, &prefix, Some(cursor), limit as usize).await.map_err(storage)?;
        page.entries.iter().map(|e| decode(&e.entry)).collect()
    }
    async fn advance(&self, session: &SessionId, fence: &OwnershipFence, through: u64) -> RuntimeResult<()> {
        let cp = self.load(session).await?.ok_or(RuntimeError::NotFound)?;
        if cp.fence != *fence || through > cp.consumed_input { return Err(invalid("uncommitted inbox cursor")); }
        let rows = self.read_batch(session, 0, 100).await?;
        let mutations = rows.into_iter().take_while(|e| e.position <= through).map(|e| Mutation::Delete { key: inbox(session, e.position) }).collect();
        self.fenced_write(fence, vec![Condition { key: entity("checkpoint", &session.value), expected: Expected::Revision(cp.revision) }], mutations).await?;
        Ok(())
    }
}
