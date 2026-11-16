//! Indexed admission metadata is query authority; result bodies are not identities.
use async_trait::async_trait;
use serde::{Serialize,Deserialize};
use crate::task_index::{QueryState,state_key,index_key};
#[derive(Serialize,Deserialize)]
struct TaskCursor { scan: ScanCursor, revision: Option<Revision>, limit: u32 }
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
    async fn list_tasks(&self, ctx: &CallerContext, query: TaskQuery) -> RuntimeResult<TaskPage> {
        if query.target.as_ref().is_some_and(|t| target_scope(t) != &ctx.scope) || query.session_id.as_ref().is_some_and(|s| s.scope != ctx.scope) { return Err(RuntimeError::AccessDenied); }
        let prefix=query_prefix(ctx, query.target.as_ref(), query.session_id.as_ref())?;
        let state_key=state_key(&prefix);
        let before=self.row(&ctx.scope,state_key.clone()).await?;
        let revision=before.as_ref().map(|r|r.revision);
        let total=before.as_ref().map(decode::<QueryState>).transpose()?.map_or(0,|s|s.total);
        let token=query.cursor.map(|s|serde_json::from_str::<TaskCursor>(&s).map_err(|_|invalid("invalid task cursor"))).transpose()?;
        if token.as_ref().is_some_and(|t|t.revision!=revision || t.limit!=query.limit) {return Err(invalid("task collection changed; restart pagination"))}
        let page=self.atomic.scan_page(&ctx.scope,PARTITION,&prefix,token.map(|t|t.scan),query.limit as usize).await.map_err(storage)?;
        let ids=page.entries.iter().map(|e|decode::<TaskId>(&e.entry)).collect::<RuntimeResult<Vec<_>>>()?;
        let rows=self.rows(&ctx.scope,&ids.iter().map(|id|entity("snapshot",&id.value)).collect::<Vec<_>>()).await?;
        let items=rows.iter().map(|r|decode::<EventSnapshot>(r.as_ref().ok_or(RuntimeError::NotFound)?).map(|s|s.task)).collect::<RuntimeResult<Vec<_>>>()?;
        let after=self.row(&ctx.scope,state_key).await?;
        if after.as_ref().map(|r|r.revision)!=before.as_ref().map(|r|r.revision) {return Err(conflict("task collection changed during page read; retry"))}
        if items.iter().zip(&page.entries).any(|(task,entry)|index_key(&prefix,task)!=entry.key) {return Err(conflict("task index snapshot mismatch"))}
        let next_cursor=page.next_cursor.map(|scan|serde_json::to_string(&TaskCursor{scan,revision,limit:query.limit}).map_err(|e|invalid(&e.to_string()))).transpose()?;
        Ok(TaskPage{items,next_cursor,total_size:total})
    }
}
