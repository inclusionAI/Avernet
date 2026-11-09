//! Indexed admission metadata is query authority; result bodies are not identities.
use async_trait::async_trait;
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::TaskQueryStore;
use crate::{RuntimeStore, keys::*, persistence::*, admission::AdmittedTask};
#[async_trait]
impl TaskQueryStore for RuntimeStore {
    async fn get_task(&self, ctx: &CallerContext, task: &TaskId) -> RuntimeResult<TaskView> {
        if ctx.scope != task.scope { return Err(RuntimeError::AccessDenied); }
        let rows = self.rows(&ctx.scope, &[entity("task", &task.value), entity("snapshot", &task.value)]).await?;
        let admitted: AdmittedTask = decode(rows[0].as_ref().ok_or(RuntimeError::NotFound)?)?;
        if admitted.metadata.caller.principal != ctx.principal { return Err(RuntimeError::AccessDenied); }
        Ok(decode::<EventSnapshot>(rows[1].as_ref().ok_or(RuntimeError::NotFound)?)?.task)
    }
    async fn list_tasks(&self, ctx: &CallerContext, query: TaskQuery) -> RuntimeResult<Page<TaskView>> {
        if query.target.as_ref().is_some_and(|t| target_scope(t) != &ctx.scope) || query.session_id.as_ref().is_some_and(|s| s.scope != ctx.scope) { return Err(RuntimeError::AccessDenied); }
        let page = self.page(&ctx.scope, &query_prefix(ctx, query.target.as_ref(), query.session_id.as_ref())?, query.cursor, query.limit).await?;
        let ids = page.entries.iter().map(|e| decode::<TaskId>(&e.entry)).collect::<RuntimeResult<Vec<_>>>()?;
        let rows = self.rows(&ctx.scope, &ids.iter().map(|id| entity("snapshot", &id.value)).collect::<Vec<_>>()).await?;
        let items = rows.iter().map(|r| decode::<EventSnapshot>(r.as_ref().ok_or(RuntimeError::NotFound)?).map(|s| s.task)).collect::<RuntimeResult<_>>()?;
        Ok(Page { items, next_cursor: cursor(page.next_cursor)? })
    }
}
