//! Explicit column mapping for A2A run bindings; no serialized Rust record in SQL.
use super::error;
use bcs_db_api::{DbRow, DbValue};
use bcs_service_api::{ServiceResult, port::repo::a2a_run::{A2aContextScope, A2aRunRecord, A2aRunState},
    types::{a2a_bot::{A2aBinding, A2aInterface, A2aUpdateMode}, run_budget::{RunBudgetState, RunBudgetPhase}}};

pub(super) const RUN_COLUMNS: &str = "env,run_id,task_key,scope_key,bot_id,session_id,provider_id,provider_bot_ref,interface_url,tenant,route_fingerprint,group_id,protocol_version,binding,update_mode,credential_kind,extensions_json,message_id,request_digest,request_context_id,state,version,deadline_ms,updated_at_ms,budget_remaining_ms,budget_total_deadline_ms,budget_input_wait_ms,budget_phase,budget_phase_started_ms,budget_phase_deadline_ms";
pub(super) const SELECT_RUN: &str = "SELECT r.*,t.remote_task_id,COALESCE(t.context_id,r.request_context_id) AS context_id,t.pending_input_json,t.version AS task_version FROM bcs_a2a_run_bindings r LEFT JOIN bcs_a2a_tasks t ON t.env=r.env AND t.task_key=r.task_key";

pub(super) fn num(value: u64) -> ServiceResult<DbValue> { Ok(i64::try_from(value).map_err(error)?.into()) }
pub(super) fn opt(value: &Option<String>) -> DbValue { value.clone().map(DbValue::from).unwrap_or(DbValue::Null) }
pub(super) fn text(row: &DbRow, name: &str) -> ServiceResult<String> {
    row.get_string(name).map_err(error)?.ok_or_else(|| error("missing column"))
}
fn unsigned(row: &DbRow, name: &str) -> ServiceResult<u64> {
    u64::try_from(row.get_i64(name).map_err(error)?.ok_or_else(|| error("missing integer"))?).map_err(error)
}
pub(super) fn scope(row: &DbRow) -> ServiceResult<A2aContextScope> {
    Ok(A2aContextScope { bot_id:text(row,"bot_id")?, session_id:text(row,"session_id")?,
        provider_id:text(row,"provider_id")?,provider_bot_ref:text(row,"provider_bot_ref")?,
        interface_url:text(row,"interface_url")?,tenant:row.get_string("tenant").map_err(error)?,
        route_fingerprint:row.get_string("route_fingerprint").map_err(error)? })
}
pub(super) fn scope_values(scope: &A2aContextScope) -> Vec<DbValue> {
    vec![scope.bot_id.clone().into(),scope.session_id.clone().into(),scope.provider_id.clone().into(),
        scope.provider_bot_ref.clone().into(),scope.interface_url.clone().into(),opt(&scope.tenant),opt(&scope.route_fingerprint)]
}
pub(super) fn state(value: A2aRunState) -> &'static str {
    match value { A2aRunState::Submitting=>"submitting",A2aRunState::Running=>"running",A2aRunState::InputRequired=>"input_required",
        A2aRunState::Completed=>"completed",A2aRunState::Failed=>"failed",A2aRunState::Canceled=>"canceled",A2aRunState::Unknown=>"unknown" }
}
pub(super) fn budget_values(record: &A2aRunRecord) -> ServiceResult<Vec<DbValue>> {
    let Some(budget)=&record.budget else { return Ok(vec![DbValue::Null;6]); };
    let (phase,started,deadline)=match budget.phase {
        RunBudgetPhase::Executing {started_at_ms}=>("executing",started_at_ms,DbValue::Null),
        RunBudgetPhase::Waiting {started_at_ms,deadline_ms}=>("waiting",started_at_ms,num(deadline_ms)?),
    };
    Ok(vec![num(budget.remaining_execution_ms)?,num(budget.total_deadline_ms)?,num(budget.input_wait_limit_ms)?,phase.into(),num(started)?,deadline])
}
pub(super) fn run_values(record: &A2aRunRecord, env: &str, scope_key: String) -> ServiceResult<Vec<DbValue>> {
    let mut values=vec![env.into(),record.run_id.clone().into(),DbValue::Null,scope_key.into()];
    values.extend(scope_values(&record.scope));
    values.extend(vec![record.group_id.clone().into(),record.interface.protocol_version.clone().into(),
        match record.interface.binding {A2aBinding::HttpJson=>"http_json",A2aBinding::Grpc=>"grpc"}.into(),
        "stream".into(),record.credential_kind.clone().into(),serde_json::to_string(&record.extensions).map_err(error)?.into(),
        record.message_id.clone().into(),record.request_digest.clone().into(),opt(&record.context_id),state(record.state).into(),
        num(record.version)?,num(record.deadline_ms)?,num(record.updated_at_ms)?]);
    values.extend(budget_values(record)?);Ok(values)
}
pub(super) fn decode(row: &DbRow) -> ServiceResult<A2aRunRecord> {
    let scope=scope(row)?;
    let binding=match text(row,"binding")?.as_str() {"http_json"=>A2aBinding::HttpJson,"grpc"=>A2aBinding::Grpc,_=>return Err(error("unknown binding"))};
    if text(row,"update_mode")? != "stream" { return Err(error("unknown update mode")); }
    let budget=if row.get_i64("budget_remaining_ms").map_err(error)?.is_none() {None} else {
        let phase=match text(row,"budget_phase")?.as_str() {
            "executing"=>RunBudgetPhase::Executing {started_at_ms:unsigned(row,"budget_phase_started_ms")?},
            "waiting"=>RunBudgetPhase::Waiting {started_at_ms:unsigned(row,"budget_phase_started_ms")?,deadline_ms:unsigned(row,"budget_phase_deadline_ms")?},
            _=>return Err(error("unknown budget phase")),
        };
        Some(RunBudgetState {remaining_execution_ms:unsigned(row,"budget_remaining_ms")?,total_deadline_ms:unsigned(row,"budget_total_deadline_ms")?,
            input_wait_limit_ms:unsigned(row,"budget_input_wait_ms")?,phase})
    };
    let pending_input=row.get_string("pending_input_json").map_err(error)?.map(|s|serde_json::from_str(&s).map_err(error)).transpose()?;
    Ok(A2aRunRecord {run_id:text(row,"run_id")?,group_id:text(row,"group_id")?,
        interface:A2aInterface {url:scope.interface_url.clone(),tenant:scope.tenant.clone(),protocol_version:text(row,"protocol_version")?,binding,update_mode:A2aUpdateMode::Stream},
        scope,credential_kind:text(row,"credential_kind")?,extensions:serde_json::from_str(&text(row,"extensions_json")?).map_err(error)?,
        message_id:text(row,"message_id")?,request_digest:text(row,"request_digest")?,remote_task_id:row.get_string("remote_task_id").map_err(error)?,
        context_id:row.get_string("context_id").map_err(error)?,task_version:row.get_i64("task_version").map_err(error)?.map(|v|u64::try_from(v).map_err(error)).transpose()?,
        state:serde_json::from_value(serde_json::Value::String(text(row,"state")?)).map_err(error)?,deadline_ms:unsigned(row,"deadline_ms")?,
        budget,pending_input,updated_at_ms:unsigned(row,"updated_at_ms")?,version:unsigned(row,"version")?})
}
