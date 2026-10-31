//! Durable, many-to-one run/Task associations and cross-round context bindings.
use std::sync::Arc;
use async_trait::async_trait;
use bcs_db_api::{DbError, DbPlugin, DbSqlFlavor, DbStatement, DbTransactionStep, DbTransactionStepResult, DbValue};
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::port::repo::a2a_run::{A2aContextScope, A2aRunRecord, A2aRunRepoPort, A2aRunState};
use sha2::{Digest, Sha256};
#[path="a2a_codec.rs"]
mod codec;
use codec::*;

pub struct DbA2aRunStore { db: Arc<dyn DbPlugin>, env: String, flavor: DbSqlFlavor }
pub(super) fn error(_: impl std::fmt::Display) -> ServiceError {
    ServiceError::InternalError("A2A correlation persistence failed".into())
}
fn key(scope: &A2aContextScope) -> ServiceResult<String> {
    Ok(hex::encode(Sha256::digest(serde_json::to_vec(scope).map_err(error)?)))
}
fn task_key(record: &A2aRunRecord) -> ServiceResult<Option<String>> {
    record.remote_task_id.as_ref().map(|task| serde_json::to_vec(&serde_json::json!([
        record.scope.provider_id,record.scope.provider_bot_ref,record.scope.interface_url,record.scope.tenant,
        record.scope.route_fingerprint,task])).map(|v|hex::encode(Sha256::digest(v))).map_err(error)).transpose()
}
fn checked(sql: impl Into<String>, params: Vec<DbValue>) -> DbTransactionStep {
    DbTransactionStep::ExecuteChecked {statement:DbStatement::with_params(sql,params),expected_affected_rows:1}
}
impl DbA2aRunStore {
    pub fn new(db: Arc<dyn DbPlugin>, env: String, flavor: DbSqlFlavor) -> Self { Self { db, env, flavor } }

    fn context_step(&self, record: &A2aRunRecord, scope_key: &str, context: &str) -> DbTransactionStep {
        let suffix=self.flavor.on_conflict_update(&["env","scope_key"],&["context_id"],&[("gmt_modified","CURRENT_TIMESTAMP")]);
        let mut params=vec![self.env.clone().into(),scope_key.into()];params.extend(scope_values(&record.scope));params.push(context.into());
        params.extend(vec![self.env.clone().into(),scope_key.into(),context.into()]);
        DbTransactionStep::Execute(DbStatement::with_params(format!(
            "INSERT INTO bcs_a2a_contexts (env,scope_key,bot_id,session_id,provider_id,provider_bot_ref,interface_url,tenant,route_fingerprint,context_id) SELECT ?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM bcs_a2a_contexts WHERE env=? AND scope_key=? AND context_id=?) {suffix}"),params))
    }

    async fn task_steps(&self, record: &A2aRunRecord, remote_key: &str, scope_key: &str) -> ServiceResult<Vec<DbTransactionStep>> {
        let context=record.context_id.as_ref().ok_or_else(||error("missing context"))?;
        let task=record.remote_task_id.as_ref().ok_or_else(||error("missing task"))?;
        let pending=record.pending_input.as_ref().map(serde_json::to_string).transpose().map_err(error)?;
        if let Some(version)=record.task_version {
            return Ok(vec![checked("UPDATE bcs_a2a_tasks SET pending_input_json=?, version=version+1, gmt_modified=CURRENT_TIMESTAMP WHERE env=? AND task_key=? AND scope_key=? AND remote_task_id=? AND context_id=? AND version=?",
                vec![opt(&pending),self.env.clone().into(),remote_key.into(),scope_key.into(),task.clone().into(),context.clone().into(),num(version)?])]);
        }
        // First association: an existing Task may belong to another run in this
        // same scope. Read once, then fence adoption on its shared revision.
        let rows=self.db.query(DbStatement::with_params("SELECT scope_key,remote_task_id,context_id,pending_input_json,version FROM bcs_a2a_tasks WHERE env=? AND task_key=?",
            vec![self.env.clone().into(),remote_key.into()])).await.map_err(error)?;
        let step=if let Some(row)=rows.first() {
            if text(row,"scope_key")? != scope_key || text(row,"remote_task_id")? != *task || text(row,"context_id")? != *context {
                return Err(error("remote Task scope conflict"));
            }
            let stored=row.get_string("pending_input_json").map_err(error)?;
            if pending.is_some() && pending != stored { return Err(error("cannot replace input while adopting task")); }
            checked("UPDATE bcs_a2a_tasks SET version=version+1, gmt_modified=CURRENT_TIMESTAMP WHERE env=? AND task_key=? AND version=?",
                vec![self.env.clone().into(),remote_key.into(),row.get_i64("version").map_err(error)?.ok_or_else(||error("missing Task revision"))?.into()])
        } else {
            checked("INSERT INTO bcs_a2a_tasks (env,task_key,scope_key,remote_task_id,context_id,pending_input_json,version) VALUES (?,?,?,?,?,?,1)",
                vec![self.env.clone().into(),remote_key.into(),scope_key.into(),task.clone().into(),context.clone().into(),opt(&pending)])
        };
        Ok(vec![step,self.context_step(record,scope_key,context)])
    }
}

#[async_trait]
impl A2aRunRepoPort for DbA2aRunStore {
    async fn get(&self, run_id: &str) -> ServiceResult<Option<A2aRunRecord>> {
        self.db.query(DbStatement::with_params(format!("{SELECT_RUN} WHERE r.env=? AND r.run_id=? LIMIT 1"),
            vec![self.env.clone().into(),run_id.into()])).await.map_err(error)?.first().map(decode).transpose()
    }
    async fn create(&self, record: &A2aRunRecord) -> ServiceResult<bool> {
        if record.version!=1 || record.remote_task_id.is_some() || record.task_version.is_some()
            || record.pending_input.is_some() || record.state!=A2aRunState::Submitting
            || record.interface.url!=record.scope.interface_url || record.interface.tenant!=record.scope.tenant {
            return Err(error("invalid initial state"));
        }
        let values=run_values(record,&self.env,key(&record.scope)?)?;
        let placeholders=vec!["?";values.len()].join(",");
        let sql=format!("{} INTO bcs_a2a_run_bindings ({RUN_COLUMNS}) VALUES ({placeholders})",self.flavor.insert_or_ignore());
        Ok(self.db.execute(DbStatement::with_params(sql,values)).await.map_err(error)?.affected_rows==1)
    }
    async fn compare_and_set(&self, record: &A2aRunRecord, expected_version: u64) -> ServiceResult<Option<A2aRunRecord>> {
        if expected_version.checked_add(1)!=Some(record.version)
            || record.context_id.as_ref().is_some_and(String::is_empty)
            || (record.remote_task_id.is_some() && record.context_id.is_none())
            || (record.remote_task_id.is_none() && (record.task_version.is_some() || record.pending_input.is_some()))
            || record.interface.url!=record.scope.interface_url || record.interface.tenant!=record.scope.tenant {
            return Err(error("invalid correlation state"));
        }
        let scope_key=key(&record.scope)?;
        let remote_key=task_key(record)?;
        let mut steps=if let Some(remote_key)=&remote_key { self.task_steps(record,remote_key,&scope_key).await? } else {vec![]};
        // A standalone Message can establish a context without a Task. Commit
        // its scope mapping and terminal binding in the same fenced transaction.
        let message_context=if remote_key.is_none() {record.context_id.clone()} else {None};
        if record.state==A2aRunState::Completed {
            if let Some(context)=&message_context {steps.push(self.context_step(record,&scope_key,context));}
        }
        let mut params=vec![state(record.state).into(),num(record.version)?,num(record.deadline_ms)?,num(record.updated_at_ms)?];
        params.extend(budget_values(record)?);params.push(opt(&remote_key));params.push(opt(&message_context));
        params.extend(vec![self.env.clone().into(),record.run_id.clone().into(),scope_key.into(),num(expected_version)?,
            // Already-bound runs cannot detach, change Task, or bypass its revision.
            record.task_version.map(num).transpose()?.unwrap_or(DbValue::Null),opt(&remote_key),
            record.task_version.map(num).transpose()?.unwrap_or(DbValue::Null),
            opt(&remote_key),opt(&message_context),
            record.group_id.clone().into(),record.message_id.clone().into(),record.request_digest.clone().into(),
            record.interface.protocol_version.clone().into(),match record.interface.binding {bcs_service_api::types::a2a_bot::A2aBinding::HttpJson=>"http_json",_=>"grpc"}.into(),
            record.credential_kind.clone().into(),serde_json::to_string(&record.extensions).map_err(error)?.into()]);
        steps.push(checked("UPDATE bcs_a2a_run_bindings SET state=?,version=?,deadline_ms=?,updated_at_ms=?,budget_remaining_ms=?,budget_total_deadline_ms=?,budget_input_wait_ms=?,budget_phase=?,budget_phase_started_ms=?,budget_phase_deadline_ms=?,task_key=?,request_context_id=COALESCE(?,request_context_id),gmt_modified=CURRENT_TIMESTAMP WHERE env=? AND run_id=? AND scope_key=? AND version=? AND state NOT IN ('completed','failed','canceled') AND ((task_key IS NULL AND ? IS NULL) OR (task_key=? AND ? IS NOT NULL)) AND (? IS NOT NULL OR request_context_id IS NULL OR request_context_id=?) AND group_id=? AND message_id=? AND request_digest=? AND protocol_version=? AND binding=? AND credential_kind=? AND extensions_json=?",params));
        steps.push(DbTransactionStep::Query(DbStatement::with_params(format!("{SELECT_RUN} WHERE r.env=? AND r.run_id=?"),vec![self.env.clone().into(),record.run_id.clone().into()])));
        match self.db.transaction(steps).await {
            Ok(results)=>match results.last() {
                Some(DbTransactionStepResult::Rows(rows)) if rows.len()==1=>Ok(Some(decode(&rows[0])?)),
                _=>Err(error("missing committed binding")),
            },
            Err(DbError::ConditionFailed {..})=>Ok(None),
            Err(err)=>Err(error(err)),
        }
    }
    async fn context(&self, scope: &A2aContextScope) -> ServiceResult<Option<String>> {
        let rows=self.db.query(DbStatement::with_params("SELECT bot_id,session_id,provider_id,provider_bot_ref,interface_url,tenant,route_fingerprint,context_id FROM bcs_a2a_contexts WHERE env=? AND scope_key=? LIMIT 1",
            vec![self.env.clone().into(),key(scope)?.into()])).await.map_err(error)?;
        let Some(row)=rows.first() else {return Ok(None);};
        if codec::scope(row)? != *scope {return Err(error("scope mismatch"));}
        Ok(Some(text(row,"context_id")?))
    }
    async fn has_other_active_task_run(&self, record: &A2aRunRecord) -> ServiceResult<bool> {
        let Some(remote_key)=task_key(record)? else {return Ok(false);};
        // Also protects cleanup after a failed FIRST binding: the run itself
        // may still be unbound, while another run already owns this remote ID.
        let rows=self.db.query(DbStatement::with_params("SELECT 1 AS present FROM bcs_a2a_run_bindings WHERE env=? AND task_key=? AND run_id<>? AND state IN ('submitting','running','input_required','unknown') LIMIT 1",
            vec![self.env.clone().into(),remote_key.into(),record.run_id.clone().into()])).await.map_err(error)?;
        Ok(!rows.is_empty())
    }
    async fn active(&self, scope: &A2aContextScope) -> ServiceResult<Vec<A2aRunRecord>> {
        let rows=self.db.query(DbStatement::with_params(format!("{SELECT_RUN} WHERE r.env=? AND r.scope_key=? AND r.state IN ('submitting','running','input_required','unknown') LIMIT 2"),
            vec![self.env.clone().into(),key(scope)?.into()])).await.map_err(error)?;
        rows.iter().map(|row| {let record=decode(row)?;if record.scope!=*scope {return Err(error("scope mismatch"));}Ok(record)}).collect()
    }
}
