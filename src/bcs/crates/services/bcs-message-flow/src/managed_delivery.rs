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
        let owner = self.repo.get_delivery(&command.delivery_id).await?.ok_or(ManagedDeliveryError::NotFound)?.target_bot_id;
        let mut bots = vec![owner.clone()];
        if let Some(reply) = &command.reply { bots.extend(reply.targets.iter().map(|t| t.target_bot_id.clone())); }
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
        if primary.state.kind == DeliveryType::Send && matches!(command.event, Event::CancelRequested | Event::Completed | Event::Failed | Event::Aborted | Event::PreparationFailed | Event::QueueExpired | Event::DefinitelyNotSent { .. }) {
            rows.extend(self.repo.lookup(DeliveryLookup::Bound(primary.delivery_id.clone())).await?);
            if !primary.state.may_have_been_sent || matches!(command.event, Event::DefinitelyNotSent { .. }) {
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
                if selection.version != 1 || !(1..=1024).contains(&selection.max_messages) || !(512..=16_777_216).contains(&selection.max_bytes)
                    || selection.bound_count != contexts.total || selection.selected.len() > selection.max_messages as usize
                    || selection.selected.len() > contexts.rows.len() || selection.history_bytes > selection.max_bytes
                    || selection.selected.iter().zip(&contexts.rows).any(|(s,d)| d.delivery_id != s.delivery_id || d.state.state_version != s.state_version)
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
                Event::Completed | Event::Failed | Event::Aborted
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
        if matches!(event, Event::DefinitelyNotSent { .. }) {
            if let Some(policy) = &policy {
                event = Event::DefinitelyNotSent { retry: original.attempt_no <= policy.policy.safe_retry.as_ref().map_or(0, |retry| retry.max_retries) };
            }
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
        if start_abort
            && original.abort_request_id.is_some()
            && !(event == Event::ScopeAbortRequested
                && original.state.status == Status::CancelUnknown)
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        let mut primary = original.clone();
        primary.state = outcome.state;
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
        }
        if event == Event::Accepted {
            primary.accepted_at_ms = Some(command.now_ms);
        }
        if matches!(
            event,
            Event::CancelRequested | Event::WithdrawBoundContext | Event::ScopeAbortRequested
        ) {
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
            if row.state.status != Status::Queued || row.wait_reason == Some(*reason) {
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
        mut command: AdmitMessageDeliveries,
    ) -> Result<DeliveryAdmissionResult, ManagedDeliveryError> {
        let _slot = self
            .admission_slots
            .try_acquire()
            .map_err(|_| ManagedDeliveryError::Conflict)?;
        let _guards = self.mutations.acquire(command.targets.iter().map(|t| t.target_bot_id.clone())).await;
        let policy = match &self.policy { Some(live) => Some(live.snapshot.read().await), None => None };
        if let Some(policy) = &policy {
            for target in &mut command.targets {
                if !policy.policy.manages_group(&target.target_bot_id)
                    || target.semantic_projection_json.get("policy_version").and_then(|v| v.as_u64()).is_some_and(|version| version != policy.version) {
                    return Err(ManagedDeliveryError::Conflict);
                }
                target.max_queued = policy.policy.bot(&target.target_bot_id).max_queued;
            }
            command.expire_at_ms = policy.policy.queue_ttl_ms.map(|ttl| command.now_ms.saturating_add(ttl as i64));
        }
        if !self
            .admission_available
            .load(std::sync::atomic::Ordering::SeqCst)
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        let repo = self.repo.clone();
        let (result, _guards) = persist_with_guards(_guards, async move { repo.admit(command).await }).await?;
        tracing::debug!(message_id = %result.message.message_id, session_id = %result.message.session_id,
            duplicate = result.duplicate, target_count = result.deliveries.len(),
            rejected_count = result.deliveries.iter().filter(|row| row.state.status == Status::RejectedCapacity).count(),
            "managed message admission committed");
        if !result.duplicate {
            if let Some(hook) = &self.instrumentation { for row in &result.deliveries { hook.event("admitted", row); } }
            let _ = self.changes.send(result.deliveries.clone());
        }
        Ok(result)
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
                delivery_id: row.delivery_id,
                expected_state_version: row.state.state_version,
                event: Event::Recover,
                now_ms,
                request_id: None,
                actor_id: None,
                reply: None,
                transport_context_json: None,
                deadline_at_ms: None,
            })
            .await?;
        }
        }
        Ok(())
    }
}
