//! Explicit local result adapter. Never selected automatically or for production.
use std::sync::Arc;
use async_trait::async_trait;
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::{ResultStore, TaskQueryStore};
use crate::{RuntimeStore, keys::*, persistence::*};
pub struct LocalResultStore { runtime: RuntimeStore, queries: Arc<dyn TaskQueryStore> }
impl LocalResultStore {
    pub fn new(runtime: RuntimeStore, queries: Arc<dyn TaskQueryStore>, profile: RuntimeProfile) -> RuntimeResult<Self> {
        if profile == RuntimeProfile::Production { return Err(RuntimeError::Unsupported("local result adapter cannot serve production".into())); }
        Ok(Self { runtime, queries })
    }
}
#[async_trait]
impl ResultStore for LocalResultStore {
    async fn put_if_absent(&self, intent: ResultIntent) -> RuntimeResult<SavedResult> {
        let id = &intent.result_id;
        let key = entity("local-result", &id.value);
        let rows = self.runtime.rows(&id.scope, &[entity("intent", &id.value), key.clone()]).await?;
        let committed: ResultIntent = decode(rows[0].as_ref().ok_or_else(|| conflict("result intent not committed"))?)?;
        if committed != intent { return Err(conflict("immutable result intent")); }
        if let Some(row) = &rows[1] { return decode(row); }
        let saved = SavedResult { intent: intent.clone(), saved_at_ms: self.runtime.atomic.server_time_ms().await.map_err(storage)? };
        let mut conditions = vec![condition(key.clone(), &rows[1])];
        let mut mutations = vec![put(key, &saved)?, put(format!("group-result/{}/{}", digest(&[&intent.task_id.value]), digest(&[&id.value])), &saved)?];
        if intent.node_id.is_none() {
            let task_key = entity("task-result", &intent.task_id.value);
            let row = self.runtime.row(&id.scope, task_key.clone()).await?;
            if let Some(row) = &row { if decode::<SavedResult>(row)?.intent != intent { return Err(conflict("task result already authoritative")); } }
            conditions.push(condition(task_key.clone(), &row)); mutations.push(put(task_key, &saved)?);
        }
        self.runtime.write(&id.scope, conditions, mutations).await?;
        Ok(saved)
    }
    async fn get(&self, id: &ResultId) -> RuntimeResult<Option<SavedResult>> {
        self.runtime.row(&id.scope, entity("local-result", &id.value)).await?.as_ref().map(decode).transpose()
    }
    async fn list_tasks(&self, ctx: &CallerContext, query: TaskQuery) -> RuntimeResult<TaskPage> { self.queries.list_tasks(ctx, query).await }
    async fn read_group_records(&self, task: &TaskId, after: Option<String>, limit: u32) -> RuntimeResult<Page<SavedResult>> {
        let page = self.runtime.page(&task.scope, &format!("group-result/{}/", digest(&[&task.value])), after, limit).await?;
        Ok(Page { items: page.entries.iter().map(|e| decode(&e.entry)).collect::<RuntimeResult<_>>()?, next_cursor: cursor(page.next_cursor)? })
    }
}
