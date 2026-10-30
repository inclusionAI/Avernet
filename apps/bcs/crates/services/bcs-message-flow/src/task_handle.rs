//! Task-only API. Never infer safe resend from absence or transport uncertainty.
use crate::{BcsMessageFlow, task_store::{TaskEntry, TaskLedgerStatus}};
use bcs_domain::message_delivery::{MessageDeliveryStatus as D, PersistedMessageDelivery};
use bcs_service_api::{ServiceError, ServiceResult, TaskHandleStatus as S, TaskHandleView as View};
use bcs_service_api::port::repo::message_delivery::DeliveryLookup;
use std::collections::BTreeMap;
use std::time::Duration;

const RETENTION_MS: i64 = 24 * 60 * 60 * 1000;
fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation { message: message.into(), request_id: None }
}
fn prefix(value: &str) -> ServiceResult<String> {
    let value = value.strip_prefix("T-").or_else(|| value.strip_prefix("bcs_intent_")).unwrap_or(value);
    let value = if value.len() == 36 && uuid::Uuid::parse_str(value).is_ok() { value.replace('-', "") } else { value.into() };
    // COSEC: restrict correlation selectors; never accept wildcard patterns.
    if !(8..=32).contains(&value.len()) || !value.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(invalid("task ref must contain an 8..32 hexadecimal prefix"));
    }
    Ok(value.to_ascii_lowercase())
}

async fn legacy_suppressions(flow: &BcsMessageFlow, prefixes: &[String]) -> ServiceResult<Vec<String>> {
    let mut known = std::collections::BTreeSet::new();
    for prefix in prefixes { known.extend(flow.task_store.known_legacy_intents(prefix).await); }
    // Fail closed rather than dropping candidates that might conceal a collision.
    // The bound keeps a 20-selector SQLite query below its 999 parameter limit.
    if known.len() > 256 { return Err(ServiceError::InternalError("task lookup legacy correlation budget exceeded".into())); }
    Ok(known.into_iter().collect())
}

enum Task { Uncertain, Rejected, Managed(PersistedMessageDelivery), Legacy(TaskEntry) }
async fn lookup(flow: &BcsMessageFlow, value: &str) -> ServiceResult<Vec<Task>> {
    let prefix = prefix(value)?;
    let cutoff = chrono::Utc::now().timestamp_millis().saturating_sub(RETENTION_MS);
    let legacy_intents = legacy_suppressions(flow, std::slice::from_ref(&prefix)).await?;
    let rows = match &flow.managed_deliveries {
        Some(service) => service.lookup(DeliveryLookup::TaskPrefix { prefix, retained_after_ms: cutoff, legacy_intents:legacy_intents.clone() }).await
            .map_err(|_| ServiceError::InternalError("task lookup unavailable".into()))?,
        None => Vec::new(),
    };
    resolve_rows(flow, value, &rows, &legacy_intents).await
}

async fn resolve_rows(flow: &BcsMessageFlow, value: &str, rows: &[PersistedMessageDelivery], legacy_intents: &[String]) -> ServiceResult<Vec<Task>> {
    let prefix = prefix(value)?;
    let cutoff = chrono::Utc::now().timestamp_millis().saturating_sub(RETENTION_MS);
    let mut tasks = BTreeMap::new();
    let mut covered_intents: std::collections::BTreeSet<_> = legacy_intents.iter().cloned().collect();
    let mut uncertain = std::collections::BTreeSet::new();
    for entry in flow.task_store.matching_handles(&prefix, cutoff.max(0) as u64).await {
        covered_intents.insert(entry.task_id.replace('-', "").to_ascii_lowercase());
        if let Some(id) = entry.assignment_intent_id.as_deref().and_then(|id| id.strip_prefix("bcs_intent_")) {
            let id = id.to_ascii_lowercase();
            if !legacy_intents.contains(&id) && rows.iter().any(|row| row.semantic_projection_json["task_uncertain_id"] == id) {
                // A legacy task appeared after the SQL suppression snapshot. Do
                // not discard a capped candidate and accidentally hide a collision.
                return Err(ServiceError::InternalError("task lookup changed during legacy admission".into()));
            }
            covered_intents.insert(id);
        }
        tasks.insert(entry.task_id.replace('-', "").to_ascii_lowercase(), Task::Legacy(entry));
    }
    for row in rows {
        if let Some(id) = row.semantic_projection_json["task_uncertain_id"].as_str() {
            if id.starts_with(&prefix) { uncertain.insert(id.to_owned()); }
            continue;
        }
        if let Some(id) = row.semantic_projection_json["task_rejected_id"].as_str() {
            covered_intents.insert(id.to_owned());
            if id.starts_with(&prefix) { tasks.insert(id.into(), Task::Rejected); }
            continue;
        }
        let Some(intent) = crate::queued_task::intent(row)? else { continue; };
        if let Some(id) = intent.assignment_intent_id.as_deref().and_then(|id| id.strip_prefix("bcs_intent_")) { covered_intents.insert(id.to_ascii_lowercase()); }
        let id = intent.task_id.replace('-', "").to_ascii_lowercase();
        covered_intents.insert(id.clone());
        if id.starts_with(&prefix) || intent.assignment_intent_id.as_deref().unwrap_or("").trim_start_matches("bcs_intent_").to_ascii_lowercase().starts_with(&prefix) {
            tasks.insert(id, Task::Managed(row.clone()));
        }
    }
    for id in uncertain.difference(&covered_intents) { tasks.insert(format!("unknown:{id}"), Task::Uncertain); }
    Ok(tasks.into_values().take(2).collect())
}
fn managed_status(row: &PersistedMessageDelivery) -> S {
    match row.state.status {
        D::Queued | D::PendingContext | D::Dispatching => S::Queued,
        D::Running => S::Running,
        D::Completed => S::Completed,
        D::Cancelled if crate::task_failure::is_task_timeout(row) => S::TimedOut,
        D::Cancelled => S::Cancelled,
        D::Expired if row.state.may_have_been_sent => S::Unknown,
        D::Failed | D::RejectedCapacity | D::Expired => S::Failed,
        _ => S::Unknown,
    }
}
fn legacy_status(entry: &TaskEntry) -> S {
    if entry.stopped_confirmed {
        return if entry.cancel_reason.as_deref() == Some(crate::task_failure::TASK_TIMEOUT_REASON) { S::TimedOut } else { S::Cancelled };
    }
    match entry.status {
        TaskLedgerStatus::Queued => S::Queued,
        TaskLedgerStatus::Dispatched if entry.cancel_started => S::Unknown,
        TaskLedgerStatus::Dispatched if entry.running_at_ms.is_some() => S::Running,
        TaskLedgerStatus::Dispatched => S::Queued,
        TaskLedgerStatus::Replied => S::Completed,
        TaskLedgerStatus::Failed => S::Failed,
        TaskLedgerStatus::Cancelled => S::Cancelled,
        TaskLedgerStatus::TimedOut => S::TimedOut,
    }
}
async fn view(flow: &BcsMessageFlow, value: String, task: &Task) -> View {
    let (status, start, activity) = match task {
        Task::Uncertain => (S::Unknown, None, None),
        Task::Rejected => (S::Failed, None, None),
        Task::Managed(row) => {
            let entry = match crate::queued_task::intent(row) {
                Ok(Some(intent)) => flow.task_store.get(&intent.task_id).await,
                _ => None,
            };
            (managed_status(row), row.accepted_at_ms.map(|t| t.max(0) as u64), entry.and_then(|t| t.last_activity_at_ms))
        }
        Task::Legacy(entry) => (legacy_status(entry), entry.running_at_ms, entry.last_activity_at_ms),
    };
    let mut result = View::new(value, status);
    if result.status == S::Running {
        let now = chrono::Utc::now().timestamp_millis().max(0) as u64;
        result.running_s = start.map(|t| now.saturating_sub(t) / 1000);
        result.idle_s = activity.map(|t| now.saturating_sub(t) / 1000);
    }
    result
}
pub(crate) async fn status(flow: &BcsMessageFlow, refs: Vec<String>) -> ServiceResult<Vec<View>> {
    if refs.is_empty() || refs.len() > 20 { return Err(invalid("task_refs requires 1..20 refs")); }
    let prefixes = refs.iter().map(|value| prefix(value)).collect::<ServiceResult<std::collections::BTreeSet<_>>>()?;
    let prefixes: Vec<_> = prefixes.into_iter().collect();
    let legacy_intents = legacy_suppressions(flow, &prefixes).await?;
    let rows = match &flow.managed_deliveries {
        Some(service) => service.lookup(DeliveryLookup::TaskPrefixes {
            prefixes, legacy_intents:legacy_intents.clone(), retained_after_ms: chrono::Utc::now().timestamp_millis().saturating_sub(RETENTION_MS),
        }).await.map_err(|_| ServiceError::InternalError("task lookup unavailable".into()))?,
        None => Vec::new(),
    };
    let mut result = Vec::new();
    let mut cache: BTreeMap<String, View> = BTreeMap::new();
    for value in refs {
        let key = prefix(&value)?;
        if let Some(old) = cache.get(&key) {
            let mut cached: View = old.clone(); cached.task_ref = value; result.push(cached); continue;
        }
        let tasks = resolve_rows(flow, &value, &rows, &legacy_intents).await?;
        result.push(match tasks.as_slice() {
            [] => View::new(value, S::NotFound),
            [task] => view(flow, value, task).await,
            _ => View::new(value, S::Ambiguous),
        });
        cache.insert(key, result.last().unwrap().clone());
    }
    Ok(result)
}
fn finished(status: &S) -> bool { matches!(status, S::Completed | S::Failed | S::Cancelled | S::TimedOut) }

pub(crate) async fn cancel(flow: &BcsMessageFlow, value: String, reason: String) -> ServiceResult<View> {
    prefix(&value)?;
    if reason.trim().is_empty() || reason.len() > 1024 { return Err(invalid("reason requires 1..1024 bytes")); }
    let wait_ms = match &flow.delivery_policy {
        Some(policy) => policy.snapshot.read().await.policy.task_cancel_wait_ms,
        None => 5000,
    };
    // Bound lookup, transition, abort and all polls together, not just sleep time.
    match tokio::time::timeout(Duration::from_millis(wait_ms), cancel_inner(flow, &value, &reason)).await {
        Ok(result) => result,
        Err(_) => Ok(View::new(value, S::Unknown)),
    }
}
async fn cancel_inner(flow: &BcsMessageFlow, value: &str, reason: &str) -> ServiceResult<View> {
    use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
    let mut tasks = lookup(flow, value).await?;
    let simple = |s| View::new(value.into(), s);
    if tasks.is_empty() { return Ok(simple(S::NotFound)); }
    if tasks.len() > 1 { return Ok(simple(S::Ambiguous)); }
    let task = tasks.pop().unwrap();
    let current = view(flow, value.into(), &task).await;
    if finished(&current.status) {
        return Ok(simple(if current.status == S::Cancelled { S::Cancelled } else { S::AlreadyFinished }));
    }
    match task {
        Task::Uncertain => Ok(simple(S::Unknown)),
        Task::Rejected => Ok(simple(S::AlreadyFinished)),
        Task::Managed(mut row) => {
            let service = flow.managed_deliveries.as_ref().unwrap();
            let mut changes = service.subscribe_changes();
            loop {
                let current = managed_status(&row);
                if current == S::Cancelled { return Ok(simple(S::Cancelled)); }
                if finished(&current) { return Ok(simple(S::AlreadyFinished)); }
                if row.last_error_code.as_deref() == Some("task_cancel_rejected") { return Ok(simple(S::CancelFailed)); }
                if row.state.status == D::CancelUnknown || (row.state.status == D::Expired && row.state.may_have_been_sent) { return Ok(simple(S::Unknown)); }
                if row.state.status != D::Cancelling {
                    let command = bcs_service_api::DeliveryTransitionCommand {
                        caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None,
                        delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
                        event: Event::CancelRequested, now_ms: chrono::Utc::now().timestamp_millis(),
                        request_id: None, actor_id: Some("task-manager".into()), reply: None,
                        transport_context_json: Some(serde_json::json!({"cancel_reason": reason})),
                        deadline_at_ms: Some(chrono::Utc::now().timestamp_millis() + 60_000),
                    };
                    match service.transition(command).await {
                        Ok(updated) => { row = updated; continue; }
                        Err(bcs_service_api::ManagedDeliveryError::Conflict
                            | bcs_service_api::ManagedDeliveryError::Lifecycle(bcs_service_api::core::message_delivery::DeliveryLifecycleError::StaleVersion { .. })
                            | bcs_service_api::ManagedDeliveryError::Repository(bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Conflict)) => {},
                        Err(error) => return Err(ServiceError::InternalError(format!("task cancellation transition failed: {error}"))),
                    }
                }
                if let Some(receiver) = changes.as_mut() {
                    if let Ok(Some(next)) = tokio::time::timeout(Duration::from_secs(1), async {
                        loop {
                            let batch = receiver.recv().await.ok()?;
                            if let Some(next) = batch.into_iter().find(|next| next.delivery_id == row.delivery_id) { return Some(next); }
                        }
                    }).await { row = next; continue; }
                } else { tokio::time::sleep(Duration::from_secs(1)).await; }
                let Some(next) = service.lookup(DeliveryLookup::Id(row.delivery_id.clone())).await
                    .map_err(|_| ServiceError::InternalError("task cancellation lookup unavailable".into()))?.into_iter().next() else {
                    return Ok(simple(S::Unknown));
                };
                row = next;
            }
        }
        Task::Legacy(entry) => {
            let Some(owner) = flow.terminal_owner.get().and_then(std::sync::Weak::upgrade) else { return Ok(simple(S::CancelFailed)); };
            if flow.task_store.begin_cancel(&entry.task_id, reason).await {
                let task = entry.clone();
                // Retain the confirmation handler after this HTTP wait times out.
                tokio::spawn(async move {
                    let result = crate::group_flow::handle_chat_abort(&owner, bcs_service_api::ChatAbortCommand {
                        caller: bcs_service_api::CallerContext::Public,
                        group_id: task.group_id.clone(), session_id: task.session_id.clone().unwrap_or(task.group_id.clone()),
                        bot_id: task.target_bot.clone(), run_id: Some(task.task_id.clone()),
                    }).await;
                    match result {
                        Ok(outcome) if outcome.aborted_run_ids.contains(&task.task_id) => {
                            // Existing abort flow publishes the terminal result with the saved reason.
                            // finish_legacy owns the cancelled/timed_out terminal classification.
                        }
                        Ok(outcome) if outcome.failures.iter().any(|f| matches!(f.code.as_str(), "chat_abort_not_supported" | "chat_abort_rejected")) => {
                            owner.task_store.cancel_rejected(&task.task_id).await;
                        }
                        Err(ServiceError::InvalidOperation { .. } | ServiceError::BotMethodUnsupported { .. }) => {
                            owner.task_store.cancel_rejected(&task.task_id).await;
                        }
                        _ => {},
                    }
                });
            }
            loop {
                let Some(entry) = flow.task_store.get(&entry.task_id).await else { return Ok(simple(S::Unknown)); };
                let current = legacy_status(&entry);
                if current == S::Cancelled { return Ok(simple(S::Cancelled)); }
                if finished(&current) { return Ok(simple(S::AlreadyFinished)); }
                if entry.cancel_rejected { return Ok(simple(S::CancelFailed)); }
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reference_validation() {
        assert_eq!(prefix("T-ABCDEF12").unwrap(), "abcdef12");
        assert!(prefix("abc").is_err());
        assert!(prefix("abcdef12%").is_err());
        assert!(prefix("bcs_intent_0123456789abcdef0123456789abcdef").is_ok());
    }
}

/// Legacy sends have no managed scheduler row. Retain only a weak owner while
/// waiting, then use the same bounded cancel workflow; never fabricate a stop.
pub(crate) fn schedule_legacy_timeout(flow: &BcsMessageFlow, task_id: String, seconds: u64) {
    let Some(owner) = flow.terminal_owner.get().cloned() else { return; };
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(seconds)).await;
        let Some(owner) = owner.upgrade() else { return; };
        let Some(task) = owner.task_store.get(&task_id).await else { return; };
        if task.managed || finished(&legacy_status(&task)) { return; }
        let result = cancel(&owner, task_id.clone(), crate::task_failure::TASK_TIMEOUT_REASON.into()).await;
        if matches!(result, Ok(ref result) if matches!(result.status, S::Unknown | S::CancelFailed)) {
            let cmd = bcs_service_api::TaskDispatchCommand {
                caller_delegation: None,
                driver_bot_id: task.driver_bot, group_id:task.group_id, target_bot_id:task.target_bot, target_bot_name:task.target_bot_name,
                payload:serde_json::json!({"assignment_intent_id":task.assignment_intent_id, "bcs_session_id":task.session_id, "legacy_task_id":task_id, "timeout_unknown":true}),
            };
            if crate::task_rejection::notice(&owner, &cmd, None).await.is_err() {
                tracing::warn!("legacy timeout uncertainty notice could not be persisted");
            }
        }
    });
}
