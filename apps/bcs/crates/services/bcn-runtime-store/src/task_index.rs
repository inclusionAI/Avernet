//! Fixed four collection indexes; counts and ordering change with the task snapshot.
use serde::{Deserialize, Serialize};
use bcn_runtime_model::*;
use crate::{RuntimeStore, admission::AdmittedTask, keys::*, persistence::*};
#[derive(Serialize, Deserialize)]
pub(crate) struct QueryState { pub total: u64 }
pub(crate) fn state_key(prefix:&str)->String { format!("task-query/{}",digest(&[prefix])) }
pub(crate) fn index_key(prefix:&str,task:&TaskView)->String {format!("{prefix}{:020}/{}",u64::MAX-task.updated_at_ms,digest(&[&task.task_id.value]))}
pub(crate) fn timestamp(ms:u64)->RuntimeResult<()> {
    if ms>253402300799999 {return Err(invalid("timestamp exceeds RFC3339 range"))} Ok(())
}
fn prefixes(ctx:&CallerContext,task:&TaskView)->RuntimeResult<Vec<String>> {
    [(None,None),(Some(&task.target),None),(None,Some(&task.session_id)),(Some(&task.target),Some(&task.session_id))]
        .into_iter().map(|(t,s)|query_prefix(ctx,t,s)).collect()
}
impl RuntimeStore {
    pub(crate) async fn index_admission(&self,ctx:&CallerContext,task:&TaskView)->RuntimeResult<(Vec<Condition>,Vec<Mutation>)> {
        self.index_plan(ctx,None,task).await
    }
    pub(crate) async fn index_update(&self,old:&TaskView,new:&TaskView)->RuntimeResult<(Vec<Condition>,Vec<Mutation>)> {
        timestamp(new.updated_at_ms)?;
        let row=self.row(&old.task_id.scope,entity("task",&old.task_id.value)).await?;
        let admitted:AdmittedTask=decode(row.as_ref().ok_or(RuntimeError::NotFound)?)?;
        self.index_plan(&admitted.metadata.caller,Some(old),new).await
    }
    async fn index_plan(&self,ctx:&CallerContext,old:Option<&TaskView>,new:&TaskView)->RuntimeResult<(Vec<Condition>,Vec<Mutation>)> {
        let prefixes=prefixes(ctx,new)?;
        let keys=prefixes.iter().map(|p|state_key(p)).collect::<Vec<_>>();
        let rows=self.rows(&ctx.scope,&keys).await?;
        let mut conditions=vec![];let mut mutations=vec![];
        for ((prefix,key),row) in prefixes.iter().zip(keys).zip(rows) {
            let mut state=match row.as_ref(){Some(row)=>decode::<QueryState>(row)?,None if old.is_none()=>QueryState{total:0},None=>return Err(conflict("task query index missing; offline rebuild required"))};
            if old.is_none(){state.total=state.total.checked_add(1).filter(|n|*n<=i32::MAX as u64).ok_or(RuntimeError::CapacityExceeded)?;}
            conditions.push(condition(key.clone(),&row));
            mutations.push(put(key,&state)?); // Revision changes also invalidate cursors on updates.
            if let Some(old)=old {let key=index_key(prefix,old);if key!=index_key(prefix,new){mutations.push(Mutation::Delete{key});}}
            mutations.push(put(index_key(prefix,new),&new.task_id)?);
        }
        Ok((conditions,mutations))
    }
}
