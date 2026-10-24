//! One-process orchestration of durable state and causal context changes.
//! No network operation is issued while holding Bot mutation guards.
use crate::message_delivery::MessageDeliveryCore;
use async_trait::async_trait;
use bcs_domain::DeliveryType;
use bcs_domain::message_delivery::{MessageDeliveryStatus as Status, PersistedMessageDelivery};
use bcs_service_api::core::message_delivery::{
    DeliveryContextAction, DeliveryLifecycleEvent as Event, MessageDeliveryCoreService,
};
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::{
    DeliveryTransitionCommand, ManagedDeliveryError, ManagedMessageDeliveryService,
};
use std::collections::BTreeMap;
use std::sync::{Arc, Weak};
use tracing::Instrument;

/// Fail-safe queue TTL when an admission carries NO expiry of its own and
/// the live delivery policy configures NO `queue_ttl_ms` (the fresh-deploy
/// default). Before §15.98 the policy overwrite turned that combination into
/// a NULL expire — silent immortal queued rows for BOTH owner and caller
/// flows; the floor bounds the damage until someone configures a real TTL.
const QUEUE_TTL_FALLBACK_MS: i64 = 24 * 60 * 60 * 1000;

/// Warn about the config gap once per process, not per admission.
static QUEUE_TTL_FALLBACK_WARNED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub(crate) const BOT_TERMINAL_ERROR_CODE: &str = "bot_terminal_error";

// Once persistence starts, cancellation of the caller must not release the Bot
// guard while an async DB driver is still committing. Return guards on success
// so the caller also retains its existing notification ordering.
async fn persist_with_guards<T: Send + 'static>(
    guards: Vec<tokio::sync::OwnedMutexGuard<()>>,
    work: impl Future<Output = Result<T, MessageDeliveryRepoError>> + Send + 'static,
) -> Result<(T, Vec<tokio::sync::OwnedMutexGuard<()>>), ManagedDeliveryError> {
    let (result, guards) = tokio::spawn(async move {
        let result = work.await;
        (result, guards)
    }.in_current_span()).await.map_err(|err| MessageDeliveryRepoError::Storage(format!("delivery persistence task: {err}")))?;
    Ok((result?, guards))
}

/// One live lock per Bot. The directory mutex is never held across lock waits
/// or repository I/O. Weak entries are swept in batches to bound idle metadata.
#[derive(Default)]
struct BotMutationLocks {
    directory: tokio::sync::Mutex<(BTreeMap<String, Weak<tokio::sync::Mutex<()>>>, usize)>,
}

#[cfg(test)]
#[path = "managed_delivery_lock_tests.rs"]
mod lock_tests;
impl BotMutationLocks {
    async fn acquire(&self, bots: impl IntoIterator<Item = String>) -> Vec<tokio::sync::OwnedMutexGuard<()>> {
        let bots: std::collections::BTreeSet<_> = bots.into_iter().collect();
        let locks = {
            let mut directory = self.directory.lock().await;
            directory.1 += 1;
            if directory.1 >= 128 {
                directory.0.retain(|_, lock| lock.strong_count() > 0);
                directory.1 = 0;
            }
            bots.into_iter().map(|bot| {
                let lock = directory.0.get(&bot).and_then(Weak::upgrade)
                    .unwrap_or_else(|| Arc::new(tokio::sync::Mutex::new(())));
                directory.0.insert(bot, Arc::downgrade(&lock));
                lock
            }).collect::<Vec<_>>()
        };
        let mut guards = Vec::with_capacity(locks.len());
        // Global lexical ordering prevents A→B and B→A reply deadlocks.
        for lock in locks { guards.push(lock.lock_owned().await); }
        guards
    }
}

pub struct ManagedMessageDelivery {
    repo: Arc<dyn MessageDeliveryRepoPort>,
    mutations: BotMutationLocks,
    admission_available: std::sync::atomic::AtomicBool,
    changes: tokio::sync::broadcast::Sender<Vec<PersistedMessageDelivery>>,
    admission_slots: tokio::sync::Semaphore,
    retry_backoff_ms: i64,
    policy: Option<Arc<crate::delivery_policy::LiveDeliveryPolicy>>,
    instrumentation: Option<Arc<dyn bcs_service_api::application::message_delivery::DeliveryInstrumentation>>,
}

impl ManagedMessageDelivery {
    pub fn new(repo: Arc<dyn MessageDeliveryRepoPort>) -> Self {
        Self {
            repo,
            mutations: Default::default(),
            admission_available: std::sync::atomic::AtomicBool::new(true),
            changes: tokio::sync::broadcast::channel(256).0,
            admission_slots: tokio::sync::Semaphore::new(64),
            retry_backoff_ms: 1000,
            policy: None,
            instrumentation: None,
        }
    }

    pub fn with_retry_backoff(mut self, backoff_ms: i64) -> Self {
        assert!(backoff_ms > 0);
        self.retry_backoff_ms = backoff_ms;
        self
    }
    pub fn with_policy(mut self, policy: Arc<crate::delivery_policy::LiveDeliveryPolicy>) -> Self {
        self.policy = Some(policy);
        self
    }
    pub fn with_instrumentation(mut self, hook: Arc<dyn bcs_service_api::application::message_delivery::DeliveryInstrumentation>) -> Self {
        self.instrumentation = Some(hook); self
    }

    pub fn set_admission_available(&self, available: bool) {
        self.admission_available
            .store(available, std::sync::atomic::Ordering::SeqCst);
    }

    /// Best-effort, post-commit notifications. Subscription does not replay
    /// recovery history and a slow consumer cannot block the transaction.
    pub fn subscribe(&self) -> tokio::sync::broadcast::Receiver<Vec<PersistedMessageDelivery>> {
        self.changes.subscribe()
    }

    async fn apply(
        &self,
        mut command: DeliveryTransitionCommand,
    ) -> Result<PersistedMessageDelivery, ManagedDeliveryError> {
        // Only discover immutable ownership before locking. State is reloaded
        // and validated below after all source/reply-target locks are held.
        let initial = self.repo.get_delivery(&command.delivery_id).await?.ok_or(ManagedDeliveryError::NotFound)?;
        let owner = initial.target_bot_id.clone();
        let mut bots = vec![owner.clone()];
        if let Some(reply) = &command.reply { bots.extend(reply.targets.iter().map(|t| t.target_bot_id.clone())); }
        if command.reply.is_none() && matches!(command.event, Event::Failed | Event::Aborted | Event::AbortUnconfirmed | Event::CancelRequested
            | Event::PreparationFailed | Event::TransportRejected | Event::ResolveStopped | Event::ResolveNotSent
            | Event::DefinitelyNotSent { .. }) {
            if let Some(task) = crate::queued_task::intent(&initial).map_err(|e| MessageDeliveryRepoError::Storage(e.to_string()))? {
                if task.leg == crate::queued_task::TaskLeg::Dispatch { bots.push(task.manager); }
            }
        }
        let waiting = crate::reply_timing::Timer::new("delivery.mutation_lock_wait");
        let _guards = self.mutations.acquire(bots).await;
        drop(waiting);
        let _held = crate::reply_timing::Timer::new("delivery.mutation_lock_held");
        let policy = match &self.policy { Some(live) => Some(live.snapshot.read().await), None => None };
        if command.event == Event::StartSend {
            if let Some(policy) = &policy {
                let version = command.transport_context_json.as_mut().and_then(|v| v.as_object_mut()).and_then(|v| v.remove("policy_version")).and_then(|v| v.as_u64());
                if version != Some(policy.version) || policy.policy.pause_dispatch { return Err(ManagedDeliveryError::Conflict); }
            }
        }
        if let (Some(policy), Some(reply)) = (&policy, command.reply.as_mut()) {
            for target in &mut reply.targets { target.max_queued = policy.policy.bot(&target.target_bot_id).max_queued; }
            reply.expire_at_ms = policy.policy.queue_ttl_ms.map(|ttl| reply.now_ms.saturating_add(ttl as i64));
        }
        let primary = self.repo.get_delivery(&command.delivery_id).await?.ok_or(ManagedDeliveryError::NotFound)?;
        if primary.target_bot_id != owner { return Err(ManagedDeliveryError::Conflict); }
        let mut rows = vec![primary.clone()];
        if primary.state.kind == DeliveryType::Send && matches!(command.event, Event::CancelRequested | Event::Completed | Event::Failed | Event::TransportRejected | Event::Aborted | Event::PreparationFailed | Event::QueueExpired | Event::DefinitelyNotSent { .. } | Event::ResolveNotSent | Event::ResolveStopped) {
            rows.extend(self.repo.lookup(DeliveryLookup::Bound(primary.delivery_id.clone())).await?);
            if !primary.state.may_have_been_sent || matches!(command.event, Event::DefinitelyNotSent { .. } | Event::ResolveNotSent) {
                rows.extend(self.repo.lookup(DeliveryLookup::Successor { bot: primary.target_bot_id.clone(), session: primary.session_id.clone(), after_seq: primary.source_session_seq, exclude: primary.delivery_id.clone(), now_ms: command.now_ms }).await?);
            }
        }
        if primary.state.kind == DeliveryType::Inject && primary.state.status == Status::Bound {
            if let Some(id) = &primary.bound_to_delivery_id {
                if !rows.iter().any(|d| &d.delivery_id == id) {
                    if let Some(carrier) = self.repo.get_delivery(id).await? { rows.push(carrier); }
                }
            }
        }
        if command.event == Event::StartSend {
            if primary.expire_at_ms.is_some_and(|t| t <= command.now_ms) || primary.available_at_ms > command.now_ms || self.repo.lane_blocked(&primary).await? { return Err(ManagedDeliveryError::Conflict); }
            if let Some(policy) = &policy { if self.repo.active_count(&primary.target_bot_id).await? >= u64::from(policy.policy.bot(&primary.target_bot_id).max_running) { return Err(ManagedDeliveryError::Conflict); } }
            if let Some(value) = command.transport_context_json.as_ref().and_then(|v| v.get("context_selection")) {
                let selection: bcs_domain::message_delivery::DeliveryContextSelection = serde_json::from_value(value.clone()).map_err(|_| ManagedDeliveryError::Conflict)?;
                let contexts = self.repo.bounded_contexts(&primary.delivery_id, selection.max_messages as usize + 1).await?;
                let required = |d: &PersistedMessageDelivery| d.semantic_projection_json.get("required_context").and_then(|v| v.as_bool()) == Some(true);
                let selected_rows: Vec<_> = contexts.rows.iter().filter(|d| selection.selected.iter().any(|s| s.delivery_id == d.delivery_id)).collect();
                if selection.version != 1 || !(1..=1024).contains(&selection.max_messages) || !(512..=16_777_216).contains(&selection.max_bytes)
                    || selection.bound_count != contexts.total || selected_rows.iter().filter(|d| !required(d)).count() > selection.max_messages as usize
                    || selection.selected.len() != selected_rows.len() || selection.history_bytes > selection.max_bytes
                    || selection.selected.iter().zip(&selected_rows).any(|(s,d)| d.delivery_id != s.delivery_id || d.state.state_version != s.state_version || (required(d) && s.body_start != 0))
                    || contexts.rows.iter().filter(|d| required(d)).any(|d| !selection.selected.iter().any(|s| s.delivery_id == d.delivery_id))
                    || selection.selected.iter().skip(1).any(|s| s.body_start != 0)
                    || (selection.selected.len() > 1 && selection.selected[0].body_start != 0)
                { return Err(ManagedDeliveryError::Conflict); }
                if primary.context_selection_json.as_ref().is_some_and(|old| old != value) { return Err(ManagedDeliveryError::Conflict); }
                if primary.context_selection_json.is_none() {
                    if let Some(policy) = &policy { if selection.max_messages != policy.policy.max_context_messages || selection.max_bytes != policy.policy.max_context_bytes { return Err(ManagedDeliveryError::Conflict); } }
                }
            }
        }
        if rows.iter().any(|row| row.target_bot_id != owner) { return Err(ManagedDeliveryError::Conflict); }
        let original = rows
            .iter()
            .find(|d| d.delivery_id == command.delivery_id)
            .ok_or(ManagedDeliveryError::NotFound)?;
        if command.reply.is_some()
            && !matches!(
                command.event,
                Event::Completed | Event::Failed | Event::Aborted | Event::AbortUnconfirmed
            )
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        if matches!(command.event, Event::DefinitelyNotSent { .. }) && command.request_id.is_none()
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        if command
            .request_id
            .as_ref()
            .is_some_and(|id| original.request_id.as_ref() != Some(id))
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        let mut event = command.event;
        let manual = matches!(event, Event::ResolveNotSent | Event::ResolveStopped);
        if manual && (!matches!(original.state.status, Status::Unknown | Status::CancelUnknown)
            || command.actor_id.as_ref().is_none_or(|id| id.trim().is_empty())
            || command.transport_context_json.as_ref().and_then(|v| v.get("reason")).and_then(|v| v.as_str())
                .is_none_or(|reason| reason.trim().is_empty() || reason.len() > 1024)) {
            return Err(ManagedDeliveryError::Conflict);
        }
        if matches!(event, Event::DefinitelyNotSent { retry: true }) {
            if let Some(policy) = &policy {
                event = Event::DefinitelyNotSent { retry: original.attempt_no <= policy.policy.safe_retry.as_ref().map_or(0, |retry| retry.max_retries) };
            }
        }
        // Spec §6.8 execution-ready barrier (review-V P1-3): a barrier-parked
        // Dispatching row is NOT restart uncertainty — its protected send was
        // refused BEFORE acceptance (retryable HISTORY_NOT_READY), so the
        // startup recovery sweep must leave it parked with its committed
        // digest/attempt for the same-dispatch redrive. The run-deadline
        // control remains its convergence bound.
        if event == Event::Recover
            && original.state.status == Status::Dispatching
            && original.wait_reason
                == Some(bcs_domain::message_delivery::DeliveryWaitReason::HistoryBarrier)
        {
            return Ok(original.clone());
        }
        let mut changed = BTreeMap::new();
        if event == Event::CancelRequested
            && original.state.kind == DeliveryType::Inject
            && original.state.status == Status::Bound
        {
            let carrier = rows
                .iter()
                .find(|d| Some(&d.delivery_id) == original.bound_to_delivery_id.as_ref())
                .ok_or(ManagedDeliveryError::Conflict)?;
            if carrier.state.status != Status::Queued || carrier.state.may_have_been_sent || carrier.context_selection_json.is_some() {
                return Err(ManagedDeliveryError::Conflict);
            }
            changed.insert(carrier.delivery_id.clone(), carrier.clone());
            event = Event::WithdrawBoundContext;
        }
        let outcome = MessageDeliveryCore.transition(
            original.state,
            command.expected_state_version,
            event,
        )?;
        let start_abort = matches!(event, Event::StartAbort | Event::ScopeAbortRequested)
            && outcome.request_abort;
        let submitted = event == Event::Submitted
            && matches!(
                original.state.status,
                Status::Dispatching
                    | Status::Running
                    | Status::Unknown
                    | Status::Cancelling
                    | Status::CancelUnknown
            )
            && original.submitted_at_ms.is_none();
        if !outcome.changed && !start_abort && !submitted {
            return Ok(original.clone());
        }
        if command.reply.is_none() && outcome.changed {
            command.reply = crate::task_failure::fallback(original, outcome.state, command.now_ms,
                command.actor_id.as_deref(), command.transport_context_json.as_ref())
                .map_err(|e| MessageDeliveryRepoError::Storage(e.to_string()))?;
            if let (Some(policy), Some(reply)) = (&policy, command.reply.as_mut()) {
                for target in &mut reply.targets { target.max_queued = policy.policy.bot(&target.target_bot_id).max_queued; }
                reply.expire_at_ms = policy.policy.queue_ttl_ms.map(|ttl| reply.now_ms.saturating_add(ttl as i64));
            }
        }
        if start_abort
            && original.abort_request_id.is_some()
            && !(event == Event::ScopeAbortRequested
                && original.state.status == Status::CancelUnknown)
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        let mut primary = original.clone();
        primary.state = outcome.state;
        if primary.state.status != original.state.status
            && matches!(primary.state.status, Status::Unknown | Status::CancelUnknown)
            // Caller rows never re-arm the queue TTL: uncertainty resolves
            // only on stop/terminal evidence, never on TTL expiry.
            && primary.semantic_projection_json.get("caller_required").is_none()
        {
            primary.expire_at_ms = policy
                .as_ref()
                .and_then(|snapshot| snapshot.policy.queue_ttl_ms)
                .map(|ttl| command.now_ms.saturating_add(ttl as i64));
        }
        if event == Event::QueueExpired
            && matches!(original.state.status, Status::Unknown | Status::CancelUnknown)
        {
            primary.last_error_code = Some("unknown_ttl_expired".into());
        }
        if event == Event::Failed {
            primary.last_error_code = Some(BOT_TERMINAL_ERROR_CODE.into());
        }
        if manual {
            let metadata = primary.transport_context_json.get_or_insert_with(|| serde_json::json!({}));
            let metadata = metadata.as_object_mut().ok_or(ManagedDeliveryError::Conflict)?;
            let resolution = if event == Event::ResolveNotSent { "confirmed_not_sent" } else { "confirmed_stopped" };
            metadata.insert("manual_resolution".into(), serde_json::json!({
                "resolution": resolution,
                "actor_id": command.actor_id,
                "reason": command.transport_context_json.as_ref().and_then(|v| v.get("reason")),
                "resolved_at_ms": command.now_ms,
                "previous_state_version": original.state.state_version,
            }));
            primary.last_error_code = Some(format!("manually_{resolution}"));
        }
        if event == (Event::DefinitelyNotSent { retry: true })
            && primary.state.status == Status::Queued
        {
            let backoff = policy.as_ref().map_or(self.retry_backoff_ms, |p| p.policy.safe_retry.as_ref().map_or(1000, |r| r.backoff_ms as i64));
            primary.available_at_ms = command.now_ms.saturating_add(backoff);
            primary.wait_reason =
                Some(bcs_domain::message_delivery::DeliveryWaitReason::RetryBackoff);
            primary.transport_context_json = None;
            primary.run_deadline_at_ms = None;
        }
        if submitted {
            primary.submitted_at_ms = Some(command.now_ms);
        }
        if start_abort {
            primary.abort_request_id = Some(uuid::Uuid::new_v4().to_string());
            primary.abort_started_at_ms = Some(command.now_ms);
            primary.cancel_deadline_at_ms = command.deadline_at_ms;
        }
        // Queue expiry is never a stop substitute for a maybe-sent caller
        // dispatch: the batch loop converts TTL lapse into a cancel intent
        // and any other caller of this CAS must not bypass the arbitration
        // (spec 队列 TTL 与执行期限). A queued caller row that never started
        // may still expire normally.
        if matches!(command.event, Event::QueueExpired)
            && primary.semantic_projection_json.get("caller_required").is_some()
            && primary.state.may_have_been_sent
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        let cancel_reason = command.transport_context_json.as_ref().and_then(|v| v.get("cancel_reason")).and_then(|v| v.as_str()).map(str::to_owned);
        let task_timeout = command.transport_context_json.as_ref().and_then(|v| v.get("task_timeout")).and_then(|v| v.as_bool()) == Some(true);
        if event == Event::StartSend {
            primary.wait_reason = None;
            if primary.run_id.is_none() || primary.idempotency_key.is_none() {
                return Err(ManagedDeliveryError::Conflict);
            }
            primary.attempt_no = primary
                .attempt_no
                .checked_add(1)
                .ok_or(ManagedDeliveryError::Conflict)?;
            primary.request_id = Some(uuid::Uuid::new_v4().to_string());
            primary.send_started_at_ms = Some(command.now_ms);
            if let Some(selection) = command.transport_context_json.as_mut().and_then(|v| v.as_object_mut()).and_then(|v| v.remove("context_selection")) {
                primary.context_selection_json = Some(selection);
            }
            primary.transport_context_json = command.transport_context_json;
            primary.run_deadline_at_ms = command.deadline_at_ms;
            // Caller rows clear the queue TTL in the same CAS once the
            // attempt authorizes: from here on the row keeps only the run
            // deadline, and an uncertain attempt must not expire into a
            // "stopped" lane release (spec 队列 TTL 与执行期限).
            if primary.semantic_projection_json.get("caller_required").is_some() {
                primary.expire_at_ms = None;
            }
        }
        if event == Event::Accepted {
            primary.accepted_at_ms = Some(command.now_ms);
        }
        if event == Event::CommitCallerDispatch {
            // Typed caller dispatch commit (§7.5.1/wire contract): merge the
            // canonical digest/deadline/revision into the caller row's semantic
            // projection. Never stamps submitted/accepted; never carries body,
            // identity or binding material.
            let Some(commit) = command.caller_dispatch_commit.clone() else {
                return Err(ManagedDeliveryError::Conflict);
            };
            if commit.request_digest.len() != 71 || commit.not_after_ms <= command.now_ms {
                return Err(ManagedDeliveryError::Conflict);
            }
            let deadline_ok = primary
                .run_deadline_at_ms
                .map(|run_deadline| commit.not_after_ms <= run_deadline)
                .unwrap_or(true);
            if !deadline_ok {
                return Err(ManagedDeliveryError::Conflict);
            }
            if primary.semantic_projection_json.is_null() {
                primary.semantic_projection_json = serde_json::json!({});
            }
            let object = match primary.semantic_projection_json.as_object_mut() {
                Some(object) => object,
                None => return Err(ManagedDeliveryError::Conflict),
            };
            // Idempotent at the same state version: identical payload = no
            // conflict; a different payload at the same version conflicts.
            if let Some(existing) = object.get("caller_dispatch") {
                let identical = existing.get("version").and_then(|v| v.as_i64()) == Some(1)
                    && existing.get("caller_request_digest").and_then(|v| v.as_str()) == Some(commit.request_digest.as_str())
                    && existing.get("caller_not_after_ms").and_then(|v| v.as_i64()) == Some(commit.not_after_ms)
                    && existing.get("caller_control_revision").and_then(|v| v.as_u64()) == Some(commit.control_revision);
                if !identical {
                    return Err(ManagedDeliveryError::Conflict);
                }
            } else {
                object.insert(
                    "caller_dispatch".to_string(),
                    serde_json::json!({
                        "version": 1i64,
                        "caller_request_digest": commit.request_digest,
                        "caller_not_after_ms": commit.not_after_ms,
                        "caller_control_revision": commit.control_revision,
                    }),
                );
            }
        }
        if command.caller_terminal.is_some() {
            // Typed Provider terminal observation (spec "终态仲裁"): the
            // outcome/event_seq/control_revision evidence becomes durable in
            // the SAME CAS as the ordinary terminal transition. An already
            // committed terminal is immutable — only the method-level
            // duplicate pre-check may pass; in-place mutation refuses.
            // Never a result body, identity or credential.
            let Some(terminal) = command.caller_terminal.clone() else {
                return Err(ManagedDeliveryError::Conflict);
            };
            if primary.semantic_projection_json.is_null() {
                primary.semantic_projection_json = serde_json::json!({});
            }
            let object = match primary.semantic_projection_json.as_object_mut() {
                Some(object) => object,
                None => return Err(ManagedDeliveryError::Conflict),
            };
            if object.get("caller_terminal").is_some() {
                return Err(ManagedDeliveryError::Conflict);
            }
            object.insert(
                "caller_terminal".to_string(),
                serde_json::json!({
                    "version": 1i64,
                    "outcome": terminal.outcome.as_str(),
                    "event_seq": terminal.event_seq,
                    "caller_control_revision": terminal.control_revision,
                }),
            );
        }
        if event == Event::CancelCallerDispatch {
            // Typed caller cancel intent (spec §"取消意图与回执"): the stable
            // cancel_request_id / frozen revision / reason / original not_after
            // become durable in the SAME CAS that moves the row to Cancelling.
            // A committed intent is never overwritten here — replay is
            // pre-checked by `cancel_caller_dispatch`; the apply arm refuses
            // any in-place mutation of an existing intent. Never identity
            // material; the lane stays held until fenced/stopped evidence.
            let Some(cancel) = command.caller_cancel.clone() else {
                return Err(ManagedDeliveryError::Conflict);
            };
            if cancel.cancel_request_id.trim().is_empty() {
                return Err(ManagedDeliveryError::Conflict);
            }
            if primary.semantic_projection_json.is_null() {
                primary.semantic_projection_json = serde_json::json!({});
            }
            let object = match primary.semantic_projection_json.as_object_mut() {
                Some(object) => object,
                None => return Err(ManagedDeliveryError::Conflict),
            };
            if object.get("caller_cancel").is_some() {
                return Err(ManagedDeliveryError::Conflict);
            }
            object.insert(
                "caller_cancel".to_string(),
                serde_json::json!({
                    "version": 1i64,
                    "cancel_request_id": cancel.cancel_request_id,
                    "caller_control_revision": cancel.control_revision,
                    "reason": cancel.reason.as_str(),
                    "not_after_ms": cancel.not_after_ms,
                    "cancel_state": "requested",
                }),
            );
            primary.cancel_requested_at_ms = Some(command.now_ms);
            primary.cancel_deadline_at_ms = Some(cancel.not_after_ms);
        }
        if matches!(
            event,
            Event::CancelRequested | Event::WithdrawBoundContext | Event::ScopeAbortRequested
        ) {
            if let Some(reason) = cancel_reason {
                primary.transport_context_json.get_or_insert_with(|| serde_json::json!({}))["cancel_reason"] = serde_json::json!(reason);
            }
            if task_timeout {
                primary.transport_context_json.get_or_insert_with(|| serde_json::json!({}))["task_timeout"] = serde_json::json!(true);
            }
            primary.cancel_requested_at_ms = Some(command.now_ms);
            primary.cancel_requested_by = command.actor_id;
            primary.cancel_deadline_at_ms = command.deadline_at_ms;
        }
        if matches!(
            primary.state.status,
            Status::Completed | Status::Failed | Status::Cancelled | Status::Expired
        ) {
            primary.terminal_at_ms = Some(command.now_ms);
        }
        if event == Event::WithdrawBoundContext {
            primary.bound_to_delivery_id = None;
        }
        changed.insert(primary.delivery_id.clone(), primary.clone());
        if original.state.kind == DeliveryType::Send
            && outcome.context_action != DeliveryContextAction::Keep
        {
            let consumed_selection: Option<bcs_domain::message_delivery::DeliveryContextSelection> = if outcome.context_action == DeliveryContextAction::Consume {
                original.context_selection_json.clone().map(serde_json::from_value).transpose().map_err(|_| ManagedDeliveryError::Conflict)?
            } else { None };
            for context in rows.iter().filter(|d| {
                d.state.kind == DeliveryType::Inject
                    && d.state.status == Status::Bound
                    && d.bound_to_delivery_id.as_deref() == Some(original.delivery_id.as_str())
            }) {
                let mut next = context.clone();
                match outcome.context_action {
                    DeliveryContextAction::Consume => {
                        if consumed_selection.as_ref().is_some_and(|s| !s.selected.iter().any(|s| s.delivery_id == next.delivery_id)) {
                            if next.semantic_projection_json.get("required_context").and_then(|v| v.as_bool()) == Some(true) { return Err(ManagedDeliveryError::Conflict); }
                            next.state.status = Status::DiscardedContext;
                            next.last_error_code = Some("context_limit".into());
                        } else { next.state.status = Status::Consumed; }
                        next.terminal_at_ms = Some(command.now_ms);
                    }
                    DeliveryContextAction::Release => {
                        next.bound_to_delivery_id = None;
                        next.state.status =
                            if next.expire_at_ms.is_some_and(|t| t <= command.now_ms) {
                                Status::Expired
                            } else {
                                Status::PendingContext
                            };
                        if next.state.status == Status::PendingContext {
                            if let Some(carrier) = rows
                                .iter()
                                .filter(|d| {
                                    d.delivery_id != original.delivery_id
                                        && d.target_bot_id == context.target_bot_id
                                        && d.session_id == context.session_id
                                        && d.state.kind == DeliveryType::Send
                                        && d.state.status == Status::Queued
                                        && !d.state.may_have_been_sent
                                        && d.source_session_seq > context.source_session_seq
                                        && d.expire_at_ms.is_none_or(|t| t > command.now_ms)
                                })
                                .min_by_key(|d| d.source_session_seq)
                            {
                                next.state.status = Status::Bound;
                                next.bound_to_delivery_id = Some(carrier.delivery_id.clone());
                                // Invalidate a prepared payload once, even if several
                                // contexts are rebound to the same queued successor.
                                changed
                                    .entry(carrier.delivery_id.clone())
                                    .or_insert_with(|| carrier.clone());
                            }
                        }
                    }
                    DeliveryContextAction::Keep => {}
                }
                changed.insert(next.delivery_id.clone(), next);
            }
        }
        let mut updates = Vec::new();
        for (id, mut next) in changed {
            let old = rows
                .iter()
                .find(|d| d.delivery_id == id)
                .ok_or(ManagedDeliveryError::NotFound)?;
            next.state.state_version = old
                .state
                .state_version
                .checked_add(1)
                .ok_or(ManagedDeliveryError::Conflict)?;
            next.updated_at_ms = command.now_ms;
            // Fresh queued successors have no committed selection. Never
            // replace a saved attempt's history under the same idempotency key.
            if next.delivery_id != primary.delivery_id && next.state.kind == DeliveryType::Send && next.context_selection_json.is_some() { return Err(ManagedDeliveryError::Conflict); }
            if next.delivery_id == primary.delivery_id {
                primary = next.clone();
            }
            updates.push(DeliveryCompareAndSet {
                expected_state_version: old.state.state_version,
                delivery: next,
            });
        }
        let mut notifications: Vec<_> = updates
            .iter()
            .map(|update| update.delivery.clone())
            .collect();
        let (committed_reply, _guards) = {
            let _timing = crate::reply_timing::Timer::new("delivery.commit_repository");
            let repo = self.repo.clone();
            persist_with_guards(_guards, async move { repo.commit_transition(updates, command.reply).await }).await?
        };
        if let Some(reply) = committed_reply {
            if !reply.duplicate {
                if let Some(hook) = &self.instrumentation { for row in &reply.deliveries { hook.event("admitted", row); } }
                notifications.extend(reply.deliveries);
            }
        }
        let _ = self.changes.send(notifications);
        if let Some(hook) = &self.instrumentation {
            if event == Event::StartSend { hook.event("started", &primary); }
            if primary.state.status != original.state.status && matches!(primary.state.status, Status::Completed | Status::Failed | Status::Cancelled | Status::Expired | Status::RejectedCapacity) { hook.event("terminal", &primary); }
        }
        tracing::debug!(message_id = %primary.source_message_id, delivery_id = %primary.delivery_id,
            bot_id = %primary.target_bot_id, session_id = %primary.session_id, flow_kind = ?primary.flow_kind,
            status = ?primary.state.status, state_version = primary.state.state_version,
            run_id = ?primary.run_id, request_id = ?primary.request_id, attempt_no = primary.attempt_no,
            abort_request_id = ?primary.abort_request_id, "managed delivery state committed");
        if primary.state.status != original.state.status
            && matches!(primary.state.status, Status::Unknown | Status::CancelUnknown)
        {
            tracing::warn!(delivery_id = %primary.delivery_id, bot_id = %primary.target_bot_id,
                session_id = %primary.session_id, run_id = ?primary.run_id,
                request_id = ?primary.request_id, status = ?primary.state.status,
                "managed delivery uncertain; lane remains paused");
        }
        if primary.state.kind == DeliveryType::Send
            && primary.state.status != original.state.status
            && primary.terminal_at_ms.is_some()
        {
            tracing::info!(message_id = %primary.source_message_id,
                delivery_id = %primary.delivery_id, bot_id = %primary.target_bot_id,
                session_id = %primary.session_id, run_id = ?primary.run_id,
                flow_kind = ?primary.flow_kind, status = ?primary.state.status,
                queued_ms = ?primary.send_started_at_ms.map(|started| started.saturating_sub(primary.created_at_ms)),
                run_ms = ?primary.send_started_at_ms.zip(primary.terminal_at_ms).map(|(started, terminal)| terminal.saturating_sub(started)),
                "managed delivery run settled");
        }
        Ok(primary)
    }
}

#[async_trait]
impl ManagedMessageDeliveryService for ManagedMessageDelivery {
    /// Typed caller dispatch commit (§7.5.1): the only legitimate producer of
    /// the `caller_dispatch` projection keys. Six-field association must match
    /// the committed row exactly (env/target/session/run/attempt/dispatch);
    /// non-caller rows, wrong status, revision≠0 or misordered deadlines
    /// refuse. Returns true when a new commit was written, false for an
    /// identical same-version resubmission.
    async fn commit_caller_dispatch(
        &self,
        command: bcs_service_api::application::message_delivery::CommitCallerDispatchCommand,
    ) -> Result<bool, ManagedDeliveryError> {
        let commit = command.commit;
        if commit.request_digest.len() != 71 || commit.control_revision != 0 {
            return Err(ManagedDeliveryError::Conflict);
        }
        if commit.not_after_ms <= command.now_ms {
            return Err(ManagedDeliveryError::Conflict);
        }
        let row = self
            .repo
            .get_delivery(&command.delivery_id)
            .await?
            .ok_or(ManagedDeliveryError::NotFound)?;
        if row.env != command.dispatch.environment
            || row.target_bot_id != command.dispatch.target_bot_id
            || row.session_id != command.dispatch.session_id
            || row.run_id.as_deref() != Some(command.dispatch.run_id.as_str())
            || row.attempt_no != command.dispatch.attempt
            || row.request_id.as_deref() != Some(command.dispatch.dispatch_id.as_str())
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        // Caller rows only: the ordinary path must never carry the keys.
        if row.semantic_projection_json.get("caller_required").is_none() {
            return Err(ManagedDeliveryError::Conflict);
        }
        // Idempotent same-version resubmission: an identical existing
        // commit is a no-write success.
        if let Some(existing) = row.semantic_projection_json.get("caller_dispatch") {
            let identical = existing.get("version").and_then(|v| v.as_i64()) == Some(1)
                && existing.get("caller_request_digest").and_then(|v| v.as_str())
                    == Some(commit.request_digest.as_str())
                && existing.get("caller_not_after_ms").and_then(|v| v.as_i64())
                    == Some(commit.not_after_ms)
                && existing.get("caller_control_revision").and_then(|v| v.as_u64())
                    == Some(commit.control_revision);
            if identical && row.state.state_version == command.expected_state_version {
                return Ok(false);
            }
        }
        // A changed payload at any version conflicts; stale versions persist
        // through the ordinary CAS below.
        let transition = self
            .transition(DeliveryTransitionCommand {
                delivery_id: command.delivery_id,
                expected_state_version: command.expected_state_version,
                event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CommitCallerDispatch,
                now_ms: command.now_ms,
                request_id: Some(command.dispatch.dispatch_id.clone()),
                actor_id: None,
                reply: None,
                transport_context_json: None,
                deadline_at_ms: None,
                caller_dispatch_commit: Some(commit),
                caller_cancel: None,
                caller_terminal: None,
            })
            .await
            .map_err(|_| ManagedDeliveryError::Conflict)?;
        let _ = transition;
        Ok(true)
    }

    async fn cancel_caller_dispatch(
        &self,
        command: bcs_service_api::application::message_delivery::CancelCallerDispatchCommand,
    ) -> Result<bool, ManagedDeliveryError> {
        let cancel = command.cancel;
        if cancel.cancel_request_id.trim().is_empty() {
            return Err(ManagedDeliveryError::Conflict);
        }
        let row = self
            .repo
            .get_delivery(&command.delivery_id)
            .await?
            .ok_or(ManagedDeliveryError::NotFound)?;
        if row.env != command.dispatch.environment
            || row.target_bot_id != command.dispatch.target_bot_id
            || row.session_id != command.dispatch.session_id
            || row.run_id.as_deref() != Some(command.dispatch.run_id.as_str())
            || row.attempt_no != command.dispatch.attempt
            || row.request_id.as_deref() != Some(command.dispatch.dispatch_id.as_str())
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        // Caller rows only: the ordinary path must never carry the keys.
        if row.semantic_projection_json.get("caller_required").is_none() {
            return Err(ManagedDeliveryError::Conflict);
        }
        // The intent freezes the committed dispatch facts: a revision or
        // deadline that drifted from the persisted commit conflicts.
        let Some(dispatch) = row.semantic_projection_json.get("caller_dispatch") else {
            return Err(ManagedDeliveryError::Conflict);
        };
        if dispatch.get("caller_control_revision").and_then(|v| v.as_u64()) != Some(cancel.control_revision) {
            return Err(ManagedDeliveryError::Conflict);
        }
        if dispatch.get("caller_not_after_ms").and_then(|v| v.as_i64()) != Some(cancel.not_after_ms) {
            return Err(ManagedDeliveryError::Conflict);
        }
        // A durably committed intent is replay-idempotent only for the SAME
        // intent at the row's current version; a different intent never
        // overwrites a committed one (spec §"取消意图与回执" 1).
        if let Some(existing) = row.semantic_projection_json.get("caller_cancel") {
            let identical = existing.get("version").and_then(|v| v.as_i64()) == Some(1)
                && existing.get("cancel_request_id").and_then(|v| v.as_str())
                    == Some(cancel.cancel_request_id.as_str())
                && existing.get("caller_control_revision").and_then(|v| v.as_u64())
                    == Some(cancel.control_revision)
                && existing.get("reason").and_then(|v| v.as_str()) == Some(cancel.reason.as_str())
                && existing.get("not_after_ms").and_then(|v| v.as_i64()) == Some(cancel.not_after_ms)
                && existing.get("cancel_state").and_then(|v| v.as_str()) == Some("requested");
            if !identical {
                return Err(ManagedDeliveryError::Conflict);
            }
            if matches!(row.state.status, Status::Cancelling | Status::CancelUnknown)
                && row.state.state_version == command.expected_state_version
            {
                return Ok(false);
            }
            return Err(ManagedDeliveryError::Conflict);
        }
        let transition = self
            .transition(DeliveryTransitionCommand {
                delivery_id: command.delivery_id,
                expected_state_version: command.expected_state_version,
                event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelCallerDispatch,
                now_ms: command.now_ms,
                request_id: Some(command.dispatch.dispatch_id.clone()),
                actor_id: None,
                reply: None,
                transport_context_json: None,
                deadline_at_ms: None,
                caller_dispatch_commit: None,
                caller_cancel: Some(cancel),
                caller_terminal: None,
            })
            .await
            .map_err(|_| ManagedDeliveryError::Conflict)?;
        let _ = transition;
        Ok(true)
    }

    async fn handle_caller_terminal(
        &self,
        command: bcs_service_api::application::message_delivery::CallerTerminalEventCommand,
    ) -> Result<bcs_service_api::application::message_delivery::CallerTerminalReceipt, ManagedDeliveryError> {
        let rows = self
            .repo
            .lookup(DeliveryLookup::Request(command.dispatch.dispatch_id.clone()))
            .await?;
        let row = rows
            .into_iter()
            .find(|row| {
                row.env == command.dispatch.environment
                    && row.target_bot_id == command.dispatch.target_bot_id
                    && row.session_id == command.dispatch.session_id
                    && row.run_id.as_deref() == Some(command.dispatch.run_id.as_str())
                    && row.attempt_no == command.dispatch.attempt
                    && row.request_id.as_deref() == Some(command.dispatch.dispatch_id.as_str())
            })
            .ok_or(ManagedDeliveryError::NotFound)?;
        // Caller rows only; a terminal observation for the ordinary path is
        // a contract violation, not a lifecycle event.
        if row.semantic_projection_json.get("caller_required").is_none() {
            return Err(ManagedDeliveryError::Conflict);
        }
        // The observation binds to committed dispatch facts — an
        // uncommitted row has nothing a Provider could attest against.
        if row.semantic_projection_json.get("caller_dispatch").is_none() {
            return Err(ManagedDeliveryError::Conflict);
        }
        let receipt_for = |row: &PersistedMessageDelivery| {
            let execution_state = serde_json::to_value(row.state.status)
                .ok()
                .and_then(|value| value.as_str().map(str::to_string))
                .unwrap_or_else(|| "unknown".to_string());
            bcs_service_api::application::message_delivery::CallerTerminalReceipt::Applied {
                delivery_id: row.delivery_id.clone(),
                execution_state,
                state_version: row.state.state_version,
            }
        };
        // 终态仲裁: an already-committed terminal is immutable — the exact
        // triple (outcome, event_seq, control_revision) replays as a
        // duplicate; anything else, including a higher sequence, conflicts.
        if let Some(existing) = row.semantic_projection_json.get("caller_terminal") {
            let identical = existing.get("version").and_then(|v| v.as_i64()) == Some(1)
                && existing.get("outcome").and_then(|v| v.as_str())
                    == Some(command.observation.outcome.as_str())
                && existing.get("event_seq").and_then(|v| v.as_u64()) == Some(command.observation.event_seq)
                && existing.get("caller_control_revision").and_then(|v| v.as_u64())
                    == Some(command.observation.control_revision);
            if !identical {
                return Err(ManagedDeliveryError::Conflict);
            }
            return Ok(match receipt_for(&row) {
                bcs_service_api::application::message_delivery::CallerTerminalReceipt::Applied {
                    delivery_id,
                    execution_state,
                    state_version,
                } => bcs_service_api::application::message_delivery::CallerTerminalReceipt::Duplicate {
                    delivery_id,
                    execution_state,
                    state_version,
                },
                other => other,
            });
        }
        // Outcome selects the ordinary terminal event; a cancellation that
        // already committed keeps its logical Cancelled state and records
        // the actual outcome independently.
        let event = if matches!(row.state.status, Status::Cancelling | Status::CancelUnknown) {
            Event::Aborted
        } else {
            match command.observation.outcome {
                bcs_service_api::application::message_delivery::CallerTerminalOutcome::Completed => Event::Completed,
                bcs_service_api::application::message_delivery::CallerTerminalOutcome::Failed => Event::Failed,
                bcs_service_api::application::message_delivery::CallerTerminalOutcome::Aborted => Event::Aborted,
            }
        };
        let updated = self
            .transition(DeliveryTransitionCommand {
                delivery_id: row.delivery_id.clone(),
                expected_state_version: row.state.state_version,
                event,
                now_ms: command.now_ms,
                request_id: Some(command.dispatch.dispatch_id.clone()),
                actor_id: None,
                reply: None,
                transport_context_json: None,
                deadline_at_ms: None,
                caller_dispatch_commit: None,
                caller_cancel: None,
                caller_terminal: Some(command.observation),
            })
            .await
            .map_err(|_| ManagedDeliveryError::Conflict)?;
        Ok(receipt_for(&updated))
    }

    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<BoundDeliveryContexts, ManagedDeliveryError> {
        let start = std::time::Instant::now();
        let result = self.repo.bounded_contexts(carrier, limit).await;
        self.observe_operation("bounded_contexts", start.elapsed().as_secs_f64(), result.as_ref().map_or(0, |v| v.rows.len()), result.is_ok());
        Ok(result?)
    }
    async fn lane_blocked(&self, row: &PersistedMessageDelivery) -> Result<bool, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.lane_blocked(row).await;
        self.observe_operation("lane_blocked", start.elapsed().as_secs_f64(), usize::from(result.is_ok()), result.is_ok()); Ok(result?)
    }
    fn observe_operation(&self, operation: &'static str, seconds: f64, rows: usize, success: bool) { if let Some(hook) = &self.instrumentation { hook.operation(operation, seconds, rows, success); } }
    async fn lookup(&self, scope: DeliveryLookup) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.lookup(scope).await;
        self.observe_operation("lookup", start.elapsed().as_secs_f64(), result.as_ref().map_or(0, Vec::len), result.is_ok()); Ok(result?)
    }
    async fn queued_bots(&self, after: &str, limit: usize) -> Result<Vec<String>, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.queued_bots(after, limit).await;
        self.observe_operation("queued_bots", start.elapsed().as_secs_f64(), result.as_ref().map_or(0, Vec::len), result.is_ok()); Ok(result?)
    }
    async fn active_count(&self, bot: &str) -> Result<u64, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.active_count(bot).await;
        self.observe_operation("active_count", start.elapsed().as_secs_f64(), usize::from(result.is_ok()), result.is_ok()); Ok(result?)
    }
    async fn queued_heads(&self, bot: &str, after: &str, limit: usize) -> Result<Vec<bcs_service_api::core::message_delivery::DeliveryScheduleEntry>, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.queued_heads(bot, after, limit).await;
        self.observe_operation("queued_heads", start.elapsed().as_secs_f64(), result.as_ref().map_or(0, Vec::len), result.is_ok()); Ok(result?)
    }
    async fn work_batch(&self, kind: DeliveryWorkBatch, now_ms: i64, after: &str, limit: usize) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.work_batch(kind, now_ms, after, limit).await;
        let operation = match kind { DeliveryWorkBatch::Expired => "expiry_batch", DeliveryWorkBatch::Control => "control_batch", DeliveryWorkBatch::Recovery => "recovery_batch" };
        self.observe_operation(operation, start.elapsed().as_secs_f64(), result.as_ref().map_or(0, Vec::len), result.is_ok()); Ok(result?)
    }
    async fn queue_statistics(&self) -> Result<Vec<DeliveryQueueStatistic>, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.queue_statistics().await;
        self.observe_operation("queue_statistics", start.elapsed().as_secs_f64(), result.as_ref().map_or(0, Vec::len), result.is_ok()); Ok(result?)
    }
    async fn unfinished(&self) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        Ok(self.repo.list_unfinished_deliveries().await?)
    }
    async fn update_wait_reasons(
        &self,
        waiting: Vec<(String, bcs_domain::message_delivery::DeliveryWaitReason)>,
        now_ms: i64,
    ) -> Result<(), ManagedDeliveryError> {
        if waiting.is_empty() {
            return Ok(());
        }
        let reasons: BTreeMap<_, _> = waiting.into_iter().collect();
        let mut owners = BTreeMap::new();
        for id in reasons.keys() {
            if let Some(row) = self.repo.get_delivery(id).await? { owners.insert(id.clone(), row.target_bot_id); }
        }
        let _guards = self.mutations.acquire(owners.values().cloned()).await;
        let mut updates = Vec::new();
        for id in reasons.keys() {
            let Some(mut row) = self.repo.get_delivery(id).await? else { continue; };
            if owners.get(id) != Some(&row.target_bot_id) { return Err(ManagedDeliveryError::Conflict); }
            let Some(reason) = reasons.get(&row.delivery_id) else {
                continue;
            };
            // Review-V P1-3: the barrier marker is the one wait reason that
            // rides a Dispatching (attempt-committed) row — every other
            // reason stays Queued-only.
            let barrier_park = matches!(
                reason,
                bcs_domain::message_delivery::DeliveryWaitReason::HistoryBarrier
            ) && matches!(row.state.status, Status::Dispatching);
            if (!barrier_park && row.state.status != Status::Queued) || row.wait_reason == Some(*reason) {
                continue;
            }
            let expected_state_version = row.state.state_version;
            row.state.state_version = expected_state_version
                .checked_add(1)
                .ok_or(ManagedDeliveryError::Conflict)?;
            row.wait_reason = Some(*reason);
            row.updated_at_ms = now_ms;
            updates.push(DeliveryCompareAndSet {
                expected_state_version,
                delivery: row,
            });
        }
        if !updates.is_empty() {
            let notifications = updates.iter().map(|u| u.delivery.clone()).collect();
            let repo = self.repo.clone();
            let (_, _guards) = persist_with_guards(_guards, async move { repo.commit_transition(updates, None).await }).await?;
            let _ = self.changes.send(notifications);
        }
        Ok(())
    }
    async fn accept_run(
        &self,
        request_id: &str,
        bot_id: &str,
        downstream_run_id: Option<&str>,
        now_ms: i64,
    ) -> Result<Option<PersistedMessageDelivery>, ManagedDeliveryError> {
        let _guards = self.mutations.acquire([bot_id.to_string()]).await;
        let rows = self.repo.lookup(DeliveryLookup::Request(request_id.into())).await?;
        let Some(original) = rows
            .iter()
            .find(|d| d.request_id.as_deref() == Some(request_id))
        else {
            return Ok(None);
        };
        if original.target_bot_id != bot_id || downstream_run_id.is_some_and(str::is_empty) {
            return Err(ManagedDeliveryError::Conflict);
        }
        let outcome = MessageDeliveryCore.transition(
            original.state,
            original.state.state_version,
            Event::Accepted,
        )?;
        if !matches!(
            original.state.status,
            Status::Dispatching
                | Status::Running
                | Status::Unknown
                | Status::Cancelling
                | Status::CancelUnknown
        ) {
            return Ok(Some(original.clone()));
        }
        let mut next = original.clone();
        if let Some(alias) = downstream_run_id {
            if self.repo.lookup(DeliveryLookup::Run { bot: bot_id.into(), alias: alias.into() }).await?.iter().any(|d| {
                d.delivery_id != original.delivery_id
                    && d.target_bot_id == bot_id
                    && (d.run_id.as_deref() == Some(alias)
                        || d.request_id.as_deref() == Some(alias)
                        || d.transport_context_json
                            .as_ref()
                            .and_then(|v| v.get("downstream_run_id"))
                            .and_then(|v| v.as_str())
                            == Some(alias))
            }) {
                return Err(ManagedDeliveryError::Conflict);
            }
            let metadata = next
                .transport_context_json
                .as_mut()
                .and_then(serde_json::Value::as_object_mut)
                .ok_or(ManagedDeliveryError::Conflict)?;
            if metadata
                .get("downstream_run_id")
                .and_then(|v| v.as_str())
                .is_some_and(|old| old != alias)
            {
                return Err(ManagedDeliveryError::Conflict);
            }
            metadata.insert(
                "downstream_run_id".into(),
                serde_json::Value::String(alias.into()),
            );
        }
        if !outcome.changed
            && original.accepted_at_ms.is_some()
            && next.transport_context_json == original.transport_context_json
        {
            return Ok(Some(original.clone()));
        }
        next.state = outcome.state;
        next.state.state_version = original
            .state
            .state_version
            .checked_add(1)
            .ok_or(ManagedDeliveryError::Conflict)?;
        next.accepted_at_ms.get_or_insert(now_ms);
        next.updated_at_ms = now_ms;
        let updates = vec![DeliveryCompareAndSet {
                    expected_state_version: original.state.state_version,
                    delivery: next.clone(),
                }];
        let repo = self.repo.clone();
        let (_, _guards) = persist_with_guards(_guards, async move { repo.commit_transition(updates, None).await }).await?;
        let _ = self.changes.send(vec![next.clone()]);
        Ok(Some(next))
    }

    async fn admit(
        &self,
        command: AdmitMessageDeliveries,
    ) -> Result<DeliveryAdmissionResult, ManagedDeliveryError> {
        self.admit_batch(vec![command]).await?.pop().ok_or(ManagedDeliveryError::Conflict)
    }
    async fn admit_batch(&self, mut commands: Vec<AdmitMessageDeliveries>) -> Result<Vec<DeliveryAdmissionResult>, ManagedDeliveryError> {
        let _slot = self
            .admission_slots
            .try_acquire()
            .map_err(|_| ManagedDeliveryError::Conflict)?;
        let bots: Vec<String> = commands.iter().flat_map(|c| c.targets.iter().map(|t| t.target_bot_id.clone())).collect();
        let _guards = self.mutations.acquire(bots).await;
        let policy = match &self.policy { Some(live) => Some(live.snapshot.read().await), None => None };
        if let Some(policy) = &policy {
          for command in &mut commands {
            if !matches!(command.flow_kind, bcs_domain::message_delivery::DeliveryFlowKind::Group | bcs_domain::message_delivery::DeliveryFlowKind::System | bcs_domain::message_delivery::DeliveryFlowKind::Task | bcs_domain::message_delivery::DeliveryFlowKind::DirectA2a) { return Err(ManagedDeliveryError::Conflict); }
            for target in &mut command.targets {
                let drain = target.kind == DeliveryType::Send && target.semantic_projection_json.get("drain_context").and_then(|v| v.as_bool()) == Some(true)
                    && !self.repo.lookup(DeliveryLookup::LanePendingContextCarrier { bot: target.target_bot_id.clone(), session: command.message.session_id.clone(), now_ms: command.now_ms }).await?.is_empty();
                let enabled = match command.flow_kind {
                    bcs_domain::message_delivery::DeliveryFlowKind::DirectA2a => policy.policy.manages_direct_a2a(&target.target_bot_id),
                    bcs_domain::message_delivery::DeliveryFlowKind::System => policy.policy.manages_system(&target.target_bot_id),
                    bcs_domain::message_delivery::DeliveryFlowKind::Task => policy.policy.manages_task(&target.target_bot_id),
                    _ => policy.policy.manages_group(&target.target_bot_id),
                };
                if (!enabled && !drain)
                    || (drain && target.semantic_projection_json.get("policy_version").and_then(|v| v.as_u64()) != Some(policy.version))
                    || target.semantic_projection_json.get("policy_version").and_then(|v| v.as_u64()).is_some_and(|version| version != policy.version) {
                    return Err(ManagedDeliveryError::Conflict);
                }
                target.max_queued = policy.policy.bot(&target.target_bot_id).max_queued;
            }
            // §15.98: the policy expiry CAPS the admission's own TTL instead of
            // replacing it (the old overwrite silently deleted a carried
            // expiry whenever queue_ttl_ms was unset); with no source at
            // all the fail-safe floor keeps rows mortal. The floor is
            // anchored to the SERVER clock, never command.now_ms — the
            // caller-provided clock may be arbitrary and must not decide
            // the effective protection window.
            let policy_expiry = policy.policy.queue_ttl_ms.map(|ttl| command.now_ms.saturating_add(ttl as i64));
            let admission_expiry = command.expire_at_ms;
            let fallback_needed = policy_expiry.is_none()
                && admission_expiry.is_none()
                && command.targets.iter().any(|t| t.semantic_projection_json.get("caller_required").is_some());
            if fallback_needed
                && !QUEUE_TTL_FALLBACK_WARNED.swap(true, std::sync::atomic::Ordering::Relaxed)
            {
                tracing::warn!(
                    "delivery policy sets no queue_ttl_ms and this caller admission carries no expiry: \
                     applying the {}h fail-safe floor — configure queue_ttl_ms to take control",
                    QUEUE_TTL_FALLBACK_MS / 3_600_000,
                );
            }
            let cap = |expiry: i64| {
                let mut capped = expiry;
                if let Some(policy_expiry) = policy_expiry {
                    capped = capped.min(policy_expiry);
                }
                if let Some(admission_expiry) = admission_expiry {
                    capped = capped.min(admission_expiry);
                }
                capped
            };
            command.expire_at_ms = if command.flow_kind == bcs_domain::message_delivery::DeliveryFlowKind::DirectA2a {
                let deadline = command.targets.first().and_then(|t| t.semantic_projection_json.get("expires_at_ms")).and_then(|v| v.as_i64()).ok_or(ManagedDeliveryError::Conflict)?;
                Some(cap(deadline))
            } else {
                Some(match (admission_expiry, policy_expiry) {
                    (Some(admission), Some(policy)) => admission.min(policy),
                    (Some(admission), None) => admission,
                    (None, Some(policy)) => policy,
                    (None, None) => (bcs_protocol::now_ms() as i64).saturating_add(QUEUE_TTL_FALLBACK_MS),
                })
            };
          }
        }
        if !self
            .admission_available
            .load(std::sync::atomic::Ordering::SeqCst)
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        let repo = self.repo.clone();
        let (results, _guards) = persist_with_guards(_guards, async move { repo.admit_batch(commands).await }).await?;
        for result in &results {
        tracing::debug!(message_id = %result.message.message_id, session_id = %result.message.session_id,
            duplicate = result.duplicate, target_count = result.deliveries.len(),
            rejected_count = result.deliveries.iter().filter(|row| row.state.status == Status::RejectedCapacity).count(),
            "managed message admission committed");
        if !result.duplicate {
            if let Some(hook) = &self.instrumentation { for row in &result.deliveries { hook.event("admitted", row); } }
            let _ = self.changes.send(result.deliveries.clone());
        }
        }
        Ok(results)
    }
    async fn snapshot(
        &self,
        session_id: Option<&str>,
    ) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        Ok(self.repo.list_deliveries(session_id).await?)
    }
    async fn transition(
        &self,
        command: DeliveryTransitionCommand,
    ) -> Result<PersistedMessageDelivery, ManagedDeliveryError> {
        self.apply(command).await
    }
    async fn recover(&self, now_ms: i64) -> Result<(), ManagedDeliveryError> {
        let mut cursor = String::new();
        loop {
        let batch = self.repo.work_batch(DeliveryWorkBatch::Recovery, now_ms, &cursor, 200).await?;
        if batch.is_empty() { break; }
        for row in batch {
            cursor = row.delivery_id.clone();
            self.apply(DeliveryTransitionCommand {
                caller_dispatch_commit: None,
                delivery_id: row.delivery_id,
                expected_state_version: row.state.state_version,
                event: Event::Recover,
                now_ms,
                request_id: None,
                actor_id: None,
                reply: None,
                transport_context_json: None,
                deadline_at_ms: None,
                caller_cancel: None,
                caller_terminal: None,
            })
            .await?;
        }
        }
        Ok(())
    }
}
