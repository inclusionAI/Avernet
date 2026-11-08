//! Compatibility correlation for task RPCs without a shared invocation key.
//! Starts are evidence, never authority or dispatch triggers. See docs/native-task-caller.md.
use std::collections::{BTreeSet, HashMap};
use std::sync::Mutex;
use bcs_service_api::{BotEventCommand, TaskDispatchCommand, TaskDispatchOutcome, ServiceError, ServiceResult};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

const CAPACITY: usize = 4096;
const START_WINDOW_MS: i64 = 30_000;
const RETENTION_MS: i64 = 600_000;
type Key = [u8; 32];

fn key(value: Value) -> Key {
    Sha256::digest(serde_json::to_vec(&value).expect("JSON value")).into()
}
fn fingerprint(bot: &str, group: &str, session: &str, target: &str, payload: &Value) -> Key {
    key(json!([bot, group, session, target, payload["message"],
        payload["response_mode"], payload["timeout_s"]]))
}
fn refused(reason: &str) -> ServiceError {
    ServiceError::InvalidOperation { message: format!("CALLER_NATIVE_TASK_UNAVAILABLE: {reason}"), request_id: None }
}

struct Candidate {
    event: BotEventCommand,
    invocation: String,
    attempt: u32,
    managed_parent: bool,
    match_until: i64,
    used_or_ambiguous: bool,
}
struct Receipt {
    fingerprint: Key,
    created_at_ms: i64,
    result: Option<(String, String, Option<String>)>,
}
#[derive(Default)]
struct Index {
    candidates: HashMap<Key, Candidate>,
    requests: HashMap<Key, Receipt>,
    expiry: BTreeSet<(i64, bool, Key)>,
}
impl Index {
    fn sweep(&mut self, now: i64) {
        // Bounded work under the local lock, never a queue/DB scan. A backlog
        // may cause conservative capacity refusal; live entries are not evicted.
        for _ in 0..64 {
            let Some(&(expires, request, key)) = self.expiry.first() else { break; };
            if expires > now { break; }
            self.expiry.pop_first();
            if request { self.requests.remove(&key); } else { self.candidates.remove(&key); }
        }
    }
}
#[derive(Default)]
pub(crate) struct NativeTaskCorrelation(Mutex<Index>);

impl NativeTaskCorrelation {
    pub(crate) fn record_start(&self, flow: &crate::BcsMessageFlow, event: &BotEventCommand, data: &Value, managed_parent: bool) {
        if data["name"] != "bcs_assign_task" { return; }
        let invocation = data["toolCallId"].as_str().filter(|v| !v.is_empty() && v.len() <= 256);
        let Some(target) = data["args"]["target_bot"].as_str() else { return; };
        if !data["args"]["message"].is_string() { return; }
        let now = chrono::Utc::now().timestamp_millis();
        let lineage = flow.caller_root_lineage.as_ref().and_then(|v| v.current(&event.run_id, now));
        let attempt = lineage.as_ref().map_or(0, |v| v.attempt);
        let session = event.bcs_session_id.as_deref().unwrap_or(&event.group_id);
        let key = fingerprint(&event.bot_id, &event.group_id, session, target, &data["args"]);
        // Missing authority is still ambiguity evidence, not permission to
        // silently borrow an older start. Bound retained metadata as well.
        let valid = invocation.is_some() && attempt > 0
            && [&event.bot_id, &event.group_id, &event.run_id, session].iter()
                .all(|v| !v.is_empty() && v.len() <= 512);
        let invocation = invocation.unwrap_or("");
        let mut index = self.0.lock().expect("native task correlation");
        index.sweep(now);
        if let Some(old) = index.candidates.get_mut(&key) {
            // Exact retransmission does not refresh freshness. A second call,
            // even after success, cannot disambiguate changed-request-ID replay.
            if !valid || old.event.run_id != event.run_id || old.invocation != invocation
                || old.attempt != attempt || old.managed_parent != managed_parent {
                old.used_or_ambiguous = true;
            }
            return;
        }
        if index.candidates.len() >= CAPACITY { return; }
        // Never clone/retain the tool payload or unbounded event metadata.
        let facts = BotEventCommand {
            bot_id: if valid { event.bot_id.clone() } else { String::new() },
            group_id: if valid { event.group_id.clone() } else { String::new() },
            run_id: if valid { event.run_id.clone() } else { String::new() },
            bcs_session_id: valid.then(|| session.to_owned()),
            event_type: "agent".into(), state: bcs_service_api::ChatEventState::Delta,
            event_payload: Value::Null,
        };
        index.candidates.insert(key, Candidate { event: facts, invocation: invocation.into(),
            attempt, managed_parent, match_until: now + START_WINDOW_MS, used_or_ambiguous: !valid });
        index.expiry.insert((lineage.map_or(now + RETENTION_MS, |v| v.expires_at_ms.max(now + RETENTION_MS)), false, key));
    }

    pub(crate) async fn assignment(&self, flow: &crate::BcsMessageFlow, cmd: &TaskDispatchCommand,
        group: &str, session: &str, target: &str) -> ServiceResult<bcs_service_api::CallerDelegationAssignment> {
        let now = chrono::Utc::now().timestamp_millis();
        let (event, invocation, attempt, managed_parent) = {
            let mut index = self.0.lock().expect("native task correlation");
            index.sweep(now);
            let key = fingerprint(&cmd.driver_bot_id, group, session, &cmd.target_bot_id, &cmd.payload);
            let entry = index.candidates.get_mut(&key).ok_or_else(|| refused("tool start missing"))?;
            if entry.used_or_ambiguous || entry.match_until <= now {
                return Err(refused("tool start stale, ambiguous or already consumed"));
            }
            entry.used_or_ambiguous = true; // Claim before await; failures never refund.
            (entry.event.clone(), entry.invocation.clone(), entry.attempt, entry.managed_parent)
        };
        crate::caller_delegation::prepare_native_assignment(flow, &event, &invocation, target, attempt, managed_parent).await
    }
}

pub(crate) async fn dispatch(flow: &crate::BcsMessageFlow, request_id: &str, cmd: TaskDispatchCommand)
    -> ServiceResult<TaskDispatchOutcome> {
    if request_id.is_empty() || request_id.len() > 256 || cmd.caller_delegation.is_some() {
        return Err(refused("invalid native request identity"));
    }
    let now = chrono::Utc::now().timestamp_millis();
    let request = key(json!([cmd.driver_bot_id, request_id]));
    let fingerprint = key(json!([cmd.group_id, cmd.target_bot_id, cmd.target_bot_name, cmd.payload]));
    {
        let mut index = flow.native_tasks.0.lock().expect("native task correlation");
        index.sweep(now);
        if let Some(receipt) = index.requests.get(&request) {
            if receipt.fingerprint != fingerprint { return Err(refused("request identity reused with different input")); }
            return match &receipt.result {
                Some((task_id, status, real_run_id)) => Ok(TaskDispatchOutcome {
                    task_id: task_id.clone(), status: status.clone(), real_run_id: real_run_id.clone(),
                    bot_deliveries: vec![], frontend_deliveries: vec![],
                }),
                None => Err(refused("request in progress or previous outcome unconfirmed; inspect task status")),
            };
        }
        if index.requests.len() >= CAPACITY { return Err(refused("request correlation capacity reached")); }
        index.requests.insert(request, Receipt { fingerprint, created_at_ms: now, result: None });
        index.expiry.insert((now + RETENTION_MS, true, request));
    }
    let outcome = crate::task_flow::dispatch_native(flow, cmd).await;
    if let Ok(result) = &outcome {
        if let Some(receipt) = flow.native_tasks.0.lock().expect("native task correlation").requests.get_mut(&request) {
            // A very late outcome cannot overwrite a new receipt after expiry.
            if receipt.fingerprint == fingerprint && receipt.created_at_ms == now {
                receipt.result = Some((result.task_id.clone(), result.status.clone(), result.real_run_id.clone()));
            }
        }
    }
    outcome
}
