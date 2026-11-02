//! Single scheduler with bounded preparation/send tasks and an independent,
//! small abort task set. Admission flags never filter already-persisted work.
use crate::message_delivery::MessageDeliveryCore;
use bcs_domain::{
    DeliveryType,
    message_delivery::{MessageDeliveryStatus as Status, PersistedMessageDelivery},
};
use bcs_protocol::BcsFrame;
use bcs_service_api::core::message_delivery::{
    DeliveryLifecycleEvent as Event, DeliveryScheduleAction, DeliveryScheduleEntry,
    DeliveryScheduleSnapshot, MessageDeliverySchedulingCoreService,
};
use bcs_service_api::{BotAbortDeliveryResult, BotDeliveryPort, BotDeliveryResult, ServiceResult};
use bcs_service_api::{
    DeliveryTransitionCommand, ManagedDeliveryError, ManagedDeliveryPreparationService,
    ManagedMessageDeliveryService, PreparedManagedDelivery,
};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::task::JoinSet;
use bcs_service_api::port::repo::message_delivery::{DeliveryLookup, DeliveryWorkBatch};

#[derive(Clone)]
pub struct DeliveryRuntimePolicy {
    pub max_running: usize,
    pub min_send_interval_ms: u64,
}

#[derive(Clone)]
pub struct DeliveryRuntimeConfig {
    pub max_safe_retries: u32,
    pub pause_dispatch: bool,
    pub bots: BTreeMap<String, DeliveryRuntimePolicy>,
    pub tick: Duration,
    pub expiry_tick: Duration,
    pub io_timeout: Duration,
    pub run_timeout: Duration,
    pub cancel_timeout: Duration,
    /// Gives WebSocket Bots a short chance to reconnect after this scheduler
    /// process or master epoch starts. This is not an offline queue policy.
    pub startup_recovery_grace: Duration,
    pub max_tasks: usize,
    pub max_abort_tasks: usize,
}

#[derive(Clone)]
pub struct DeliveryRuntime {
    pub policy: Option<Arc<crate::delivery_policy::LiveDeliveryPolicy>>,
    pub service: Arc<dyn ManagedMessageDeliveryService>,
    pub preparation: Arc<dyn ManagedDeliveryPreparationService>,
    pub transport: Arc<dyn BotDeliveryPort>,
    pub config: DeliveryRuntimeConfig,
    /// Protected caller dispatch (spec §7.5.1). `None` keeps the §15.4
    /// anti-downgrade guard: caller rows converge to Failed before any
    /// context read (current production behavior until composition opts in).
    pub caller_dispatch: Option<Arc<crate::caller_dispatch_pipeline::CallerDispatchPipeline>>,
}

enum WorkResult {
    CallerFinished(String, bool),
    Prepared(
        PersistedMessageDelivery,
        ServiceResult<PreparedManagedDelivery>,
    ),
    Sent(String, String, Option<ServiceResult<BotDeliveryResult>>),
}

fn now_ms() -> i64 {
    chrono::Utc::now().timestamp_millis()
}

pub(crate) const TASK_ABORT_RESERVE: Duration = Duration::from_secs(5 * 60);

fn run_timeout_for(row: &PersistedMessageDelivery, provider_timeout: Duration) -> Duration {
    let is_worker_task = row.flow_kind == bcs_domain::message_delivery::DeliveryFlowKind::Task
        && matches!(crate::queued_task::intent(row), Ok(Some(task)) if task.leg == crate::queued_task::TaskLeg::Dispatch);
    if is_worker_task {
        if let Ok(Some(task)) = crate::queued_task::intent(row) {
            if let Some(seconds) = task.timeout_s { return Duration::from_secs(seconds); }
        }
        provider_timeout.checked_sub(TASK_ABORT_RESERVE).filter(|time| !time.is_zero())
            .unwrap_or(provider_timeout / 2)
    } else {
        provider_timeout
    }
}

fn event(row: &PersistedMessageDelivery, kind: Event) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand {
        caller_dispatch_commit: None,
        caller_cancel: None,
        caller_terminal: None,
        delivery_id: row.delivery_id.clone(),
        expected_state_version: row.state.state_version,
        event: kind,
        now_ms: now_ms(),
        request_id: None,
        actor_id: None,
        reply: None,
        transport_context_json: None,
        deadline_at_ms: None,
    }
}

/// The trusted ordinary sender facts a caller admission committed alongside
/// the `caller_required` marker (written only by the trusted producers —
/// caller_admission / queued_admission, never a request body). They are the
/// envelope `from` zone's source; the protected caller subject never
/// substitutes for them. None = the row predates the fact or was misbuilt:
/// the protected dispatch fails closed rather than shipping `from: null`.
pub(super) fn caller_channel_facts(row: &PersistedMessageDelivery) -> Option<(String, String)> {
    let channel = row.semantic_projection_json.get("caller_channel")?;
    let actor_id = channel
        .get("actor_id")
        .and_then(serde_json::Value::as_str)
        .map(str::trim)
        .filter(|id| !id.is_empty())?;
    let actor_name = channel
        .get("actor_name")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_string();
    Some((actor_id.to_string(), actor_name))
}

fn schedule_rows(rows: &[PersistedMessageDelivery], bot: &str) -> Vec<DeliveryScheduleEntry> {
    rows.iter()
        .filter(|d| d.target_bot_id == bot && d.state.kind == DeliveryType::Send)
        .map(|d| DeliveryScheduleEntry {
            delivery_id: d.delivery_id.clone(),
            session_id: d.session_id.clone(),
            source_session_seq: d.source_session_seq as u64,
            state: d.state,
            available_at_ms: d.available_at_ms,
            expire_at_ms: d.expire_at_ms,
        })
        .collect()
}

impl DeliveryRuntime {
    async fn policies(&self, bot_ids: &[String]) -> (BTreeMap<String, DeliveryRuntimePolicy>, bool, u32, Option<u64>) {
        if let Some(live) = &self.policy {
            let current = live.snapshot.read().await;
            let bots = bot_ids.iter().map(|bot| {
                let policy = current.policy.bot(bot);
                (bot.clone(), DeliveryRuntimePolicy { max_running: policy.max_running as usize, min_send_interval_ms: policy.min_send_interval_ms })
            }).collect();
            (bots, current.policy.pause_dispatch, current.policy.safe_retry.as_ref().map_or(0, |r| r.max_retries), Some(current.version))
        } else { (self.config.bots.clone(), self.config.pause_dispatch, self.config.max_safe_retries, None) }
    }
    async fn get(&self, id: &str) -> Result<Option<PersistedMessageDelivery>, ManagedDeliveryError> {
        Ok(self.service.lookup(DeliveryLookup::Id(id.into())).await?.into_iter().next())
    }
    async fn bot_available(&self, bot_id: &str) -> bool {
        tokio::time::timeout(
            self.config.io_timeout,
            self.preparation.is_available(bot_id),
        )
        .await
        .unwrap_or(false)
    }

    async fn transition(
        &self,
        command: DeliveryTransitionCommand,
    ) -> Result<Option<PersistedMessageDelivery>, ManagedDeliveryError> {
        match self.service.transition(command).await {
            Ok(row) => Ok(Some(row)),
            Err(ManagedDeliveryError::Conflict | ManagedDeliveryError::NotFound)
            | Err(ManagedDeliveryError::Repository(
                bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Conflict,
            ))
            | Err(ManagedDeliveryError::Lifecycle(
                bcs_service_api::core::message_delivery::DeliveryLifecycleError::StaleVersion {
                    ..
                },
            )) => Ok(None),
            Err(error) => Err(error),
        }
    }
    /// Deployment guarantees one scheduler per database. A
    /// shutdown aborts owned I/O futures and reconciles possible sends to Unknown.
    /// Protected caller branch for one caller-required row (spec §7.5.1).
    /// Never inserts the row into the ordinary preparation pipeline. The
    /// method itself converges the row through the ordinary lifecycle
    /// events; every outcome is fail-closed by construction.
    /// Transition wrapper for the caller branch: CAS outcomes are advisory
    /// here (a lost race means the row already converged elsewhere), and
    /// storage failures surface as warnings — the scheduler retry policy
    /// owns retry/backoff.
    async fn transition_ok(&self, command: DeliveryTransitionCommand) -> Option<PersistedMessageDelivery> {
        match self.transition(command).await {
            Ok(row) => row,
            Err(error) => {
                tracing::warn!(%error, "caller protected dispatch: transition rejected");
                None
            }
        }
    }

    pub async fn run(
        self,
        mut shutdown: tokio::sync::watch::Receiver<bool>,
    ) -> Result<(), ManagedDeliveryError> {
        if self.config.tick.is_zero()
            || self.config.expiry_tick.is_zero()
            || self.config.io_timeout.is_zero()
            || self.config.run_timeout.is_zero()
            || self.config.cancel_timeout.is_zero()
            || self.config.max_tasks == 0
            || self.config.max_abort_tasks == 0
            || self.config.bots.values().any(|p| p.max_running == 0)
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        macro_rules! storage {
            ($work:expr) => {
                crate::storage_retry::retry(Some(shutdown.clone()), "scheduler",
                    crate::storage_retry::managed_storage, || $work).await?
            };
        }
        storage!(self.service.recover(now_ms()));
        let epoch = Instant::now();
        // Never store old-policy future deadlines: new intervals apply to the
        // same last-start timestamp without resetting rate history.
        let mut last_send: BTreeMap<String, u64> = BTreeMap::new();
        let mut cursors: BTreeMap<String, String> = BTreeMap::new();
        // §15.98 visibility: bots whose rows got the NoPolicy wait-marker
        // this run — they stop being re-scanned until a policy lands.
        let mut no_policy_marked: BTreeSet<String> = BTreeSet::new();
        let mut preparing = BTreeSet::new();
        let mut caller_tasks: BTreeMap<String, (String, futures::future::AbortHandle)> = BTreeMap::new();
        let mut aborting = BTreeSet::new();
        let mut work = JoinSet::new();
        let mut abort_work: JoinSet<(
            String,
            String,
            Option<String>,
            Option<ServiceResult<BotAbortDeliveryResult>>,
        )> = JoinSet::new();
        let mut ticker = tokio::time::interval(self.config.tick);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut expiry_ticker = tokio::time::interval(self.config.expiry_tick);
        expiry_ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut last_bot = String::new();
        let outcome = async {
        loop {
            if *shutdown.borrow() { break; }
            tokio::select! {
                _ = shutdown.changed() => { break; }
                _ = expiry_ticker.tick() => {
                    let expired = storage!(self.service.work_batch(DeliveryWorkBatch::Expired, now_ms(), "", 100));
                    for row in expired {
                        // A maybe-sent caller row never expires into a
                        // "stopped" lane release: TTL lapse converts to a
                        // cancel intent; only stop/terminal evidence settles
                        // it (spec 队列 TTL 与执行期限).
                        if matches!(row.state.status, Status::Unknown | Status::CancelUnknown)
                            && row.semantic_projection_json.get("caller_required").is_some()
                        {
                            let mut command = event(&row, Event::CancelRequested);
                            command.deadline_at_ms = Some(now_ms().saturating_add(self.config.cancel_timeout.as_millis() as i64));
                            storage!(self.transition(command.clone()));
                        } else {
                            storage!(self.transition(event(&row, Event::QueueExpired)));
                        }
                    }
                }
                _ = ticker.tick() => {
                    let tick_started = Instant::now();
                    let rows = storage!(self.service.work_batch(DeliveryWorkBatch::Control, now_ms(), "", 32));
                    for row in &rows {
                        if row.state.status == Status::Cancelling
                            || (matches!(row.state.status, Status::Dispatching | Status::Running)
                                && row.run_deadline_at_ms.is_some_and(|t| t <= now_ms())) {
                            if let Some((_, handle)) = caller_tasks.get(&row.delivery_id) {
                                handle.abort();
                            }
                        }
                        if row.state.status == Status::PendingContext && row.expire_at_ms.is_some_and(|t| t <= now_ms()) {
                            storage!(self.transition(event(row, Event::QueueExpired)));
                        } else if matches!(row.state.status, Status::Dispatching | Status::Running)
                            && row.run_deadline_at_ms.is_some_and(|t| t <= now_ms()) {
                            let mut command = event(row, Event::CancelRequested);
                            command.deadline_at_ms = Some(now_ms().saturating_add(self.config.cancel_timeout.as_millis() as i64));
                            if matches!(crate::queued_task::intent(row), Ok(Some(task))
                                if task.leg == crate::queued_task::TaskLeg::Dispatch) {
                                command.transport_context_json = Some(serde_json::json!({
                                    "cancel_reason": crate::task_failure::TASK_TIMEOUT_REASON,
                                    "task_timeout": true,
                                }));
                            }
                            storage!(self.transition(command.clone()));
                        } else if row.state.status == Status::Unknown
                            && row.run_deadline_at_ms.is_some_and(|t| t <= now_ms())
                            && row.semantic_projection_json.get("caller_required").is_some()
                        {
                            // Run-deadline convergence (spec 队列 TTL 与执行期
                            // 限): a maybe-sent caller dispatch never expires
                            // into a "stopped" lane release, but its lapsed
                            // execution budget is durable evidence the
                            // dispatch will not settle on its own — convert
                            // it to a cancel intent exactly like the queue-TTL
                            // lapse, so the stop/terminal arbitration and the
                            // manual resolution keep their authority.
                            let mut command = event(row, Event::CancelRequested);
                            command.deadline_at_ms = Some(now_ms().saturating_add(self.config.cancel_timeout.as_millis() as i64));
                            tracing::warn!(
                                delivery_id = %row.delivery_id,
                                bot_id = %row.target_bot_id,
                                run_deadline_at_ms = ?row.run_deadline_at_ms,
                                "uncertain caller delivery passed its run deadline; converting to a cancel intent"
                            );
                            storage!(self.transition(command.clone()));
                        } else if row.state.status == Status::Cancelling && !aborting.contains(&row.delivery_id) {
                            if row.cancel_deadline_at_ms.is_some_and(|t| t <= now_ms()) {
                                storage!(self.transition(event(row, Event::AbortUnconfirmed)));
                            } else if row.abort_request_id.is_none() && abort_work.len() < self.config.max_abort_tasks {
                                let mut cmd = match tokio::time::timeout(self.config.io_timeout, self.preparation.prepare_abort(row)).await {
                                    Ok(Ok(cmd)) => cmd,
                                    Ok(Err(error)) => {
                                        tracing::warn!(delivery_id=%row.delivery_id, error_kind=error.as_ref(), reason=abort_reason(&error), "delivery abort preparation failed");
                                        storage!(self.transition(event(row, Event::AbortUnconfirmed))); continue;
                                    }
                                    Err(_) => {
                                        tracing::warn!(delivery_id=%row.delivery_id, "delivery abort preparation timed out");
                                        storage!(self.transition(event(row, Event::AbortUnconfirmed))); continue;
                                    }
                                };
                                let mut start = event(row, Event::StartAbort);
                                start.deadline_at_ms = Some(now_ms().saturating_add(self.config.cancel_timeout.as_millis() as i64));
                                let Some(started) = storage!(self.transition(start.clone())) else { continue; };
                                cmd.command_id = started.abort_request_id.clone().ok_or(ManagedDeliveryError::Conflict)?;
                                aborting.insert(row.delivery_id.clone());
                                let io = self.transport.clone();
                                let timeout = self.config.cancel_timeout;
                                let connection_id = started.transport_context_json.as_ref()
                                    .and_then(|v| v.get("connection_id")).and_then(|v| v.as_str()).map(str::to_owned);
                                let abort_request_id = cmd.command_id.clone();
                                // Provider aborts use a Bot/Session scope, so the wire run_id is None.
                                // Keep the validated run identity for checking the abort response.
                                let downstream_run_id = cmd.run_id.clone().or_else(|| started.transport_context_json.as_ref()
                                    .and_then(|context| context.get("downstream_run_id"))
                                    .and_then(|value| value.as_str()).map(str::to_owned))
                                    .or_else(|| started.run_id.clone());
                                abort_work.spawn(async move {
                                    let result = tokio::time::timeout(timeout, async {
                                        match connection_id {
                                            Some(id) => io.abort_on_connection(cmd, &id).await,
                                            None => io.abort(cmd).await,
                                        }
                                    }).await.ok();
                                    (started.delivery_id, abort_request_id, downstream_run_id, result)
                                });
                            }
                        }
                    }
                    let mut bots = storage!(self.service.queued_bots(&last_bot, 32));
                    if bots.is_empty() && !last_bot.is_empty() {
                        last_bot.clear();
                        // At most one wrap, sharing this tick's task/time budget.
                        bots = storage!(self.service.queued_bots("", 32));
                    }
                    let (policies, paused, _, _) = self.policies(&bots).await;
                    for bot in bots {
                        if work.len() >= self.config.max_tasks || tick_started.elapsed() >= Duration::from_millis(50) {
                            self.service.observe_operation("budget_exhausted", tick_started.elapsed().as_secs_f64(), work.len(), true); break;
                        }
                        last_bot = bot.clone();
                        let Some(policy) = policies.get(&bot) else {
                // §15.98 visibility: a policy-less bot's rows used to be
                // invisible — never scanned, never claimed, no wait_reason.
                // Mark them once so the stall is observable on the row.
                if no_policy_marked.insert(bot.clone()) {
                    let entries = storage!(self.service.queued_heads(&bot, "", 8));
                    if !entries.is_empty() {
                        let waiting: Vec<(String, bcs_domain::message_delivery::DeliveryWaitReason)> = entries
                            .iter()
                            .map(|entry| {
                                (
                                    entry.delivery_id.clone(),
                                    bcs_domain::message_delivery::DeliveryWaitReason::NoPolicy,
                                )
                            })
                            .collect();
                        storage!(self.service.update_wait_reasons(waiting.clone(), now_ms()));
                    }
                }
                continue;
            };
                        // Conservatively reserve capacity until the protected future finishes;
                        // its StartSend may not have reached durable storage yet.
                        let reserved = caller_tasks.values().filter(|(id, _)| id == &bot).count();
                        let active = storage!(self.service.active_count(&bot)) as usize + reserved;
                        let after = cursors.get(&bot).map(String::as_str).unwrap_or("");
                        let mut entries = storage!(self.service.queued_heads(&bot, after, 8));
                        if entries.is_empty() && !after.is_empty() { entries = storage!(self.service.queued_heads(&bot, "", 8)); }
                        if let Some(last) = entries.last() { cursors.insert(bot.clone(), last.session_id.clone()); }
                        if entries.is_empty() { continue; }
                        let rate_ready = epoch.elapsed().as_millis() as u64 >= last_send.get(&bot).copied().unwrap_or(0).saturating_add(policy.min_send_interval_ms);
                        let transport_ready = !paused && active < policy.max_running && rate_ready;
                        let online = if transport_ready { self.bot_available(&bot).await } else { true };
                        let selected = match MessageDeliveryCore.select(DeliveryScheduleSnapshot {
                            entries: &entries, max_running: policy.max_running.saturating_sub(active).max(1), bot_online: online,
                            paused, now_ms: now_ms(), monotonic_now_ms: epoch.elapsed().as_millis() as u64,
                            next_send_tick_ms: last_send.get(&bot).copied().unwrap_or(0).saturating_add(policy.min_send_interval_ms), after_session_id: None,
                            preparing_delivery_ids: &preparing,
                        }) {
                            Ok(selected) => selected,
                            // §15.98 visibility: one bot's corrupted
                            // snapshot must cost THAT bot's tick, not the
                            // whole scheduler loop.
                            Err(error) => {
                                tracing::error!(bot_id = %bot, error = ?error,
                                    "delivery schedule snapshot rejected: skipping this bot this tick");
                                continue;
                            }
                        };
                        if active >= policy.max_running {
                            storage!(self.service.update_wait_reasons(entries.iter().map(|d| (d.delivery_id.clone(), bcs_domain::message_delivery::DeliveryWaitReason::BotCapacity)).collect(), now_ms()));
                            continue;
                        }
                        if selected.action.is_none()
                            && transport_ready
                            && !online
                            && epoch.elapsed() >= self.config.startup_recovery_grace
                        {
                            let offline_ids: BTreeSet<_> = selected.waiting.iter()
                                .filter(|(_, reason)| *reason == bcs_domain::message_delivery::DeliveryWaitReason::BotOffline)
                                .map(|(id, _)| id.clone())
                                .collect();
                            let waiting: Vec<_> = selected.waiting.iter()
                                .filter(|(id, _)| !offline_ids.contains(id))
                                .cloned()
                                .collect();
                            storage!(self.service.update_wait_reasons(waiting.clone(), now_ms()));
                            for delivery_id in offline_ids {
                                let Some(row) = storage!(self.get(&delivery_id)) else { continue; };
                                tracing::warn!(
                                    delivery_id = %row.delivery_id,
                                    bot_id = %row.target_bot_id,
                                    startup_recovery_grace_ms = self.config.startup_recovery_grace.as_millis() as u64,
                                    "delivery target remained offline after startup recovery grace; marking failed"
                                );
                                storage!(self.transition(event(&row, Event::PreparationFailed)));
                            }
                            continue;
                        }
                        storage!(self.service.update_wait_reasons(selected.waiting.clone(), now_ms()));
                        match selected.action {
                            Some(DeliveryScheduleAction::Expire { delivery_id, .. }) => {
                                if let Some(row) = storage!(self.get(&delivery_id)) { storage!(self.transition(event(&row, Event::QueueExpired))); }
                            }
                            Some(DeliveryScheduleAction::Prepare { delivery_id, .. }) => {
                                let row = storage!(self.get(&delivery_id)).ok_or(ManagedDeliveryError::NotFound)?;
                                // Caller anti-downgrade guard (spec §7.5.1): the
                                // key's presence alone decides for the ordinary
                                // path — anomalous false/null reject too. With
                                // no protected pipeline attached, or without the
                                // committed digest/deadline facts, the row stays
                                // fail-closed exactly as before.
                                if row.semantic_projection_json.get("caller_required").is_some() {
                                    // Plan-A identity-readiness gate (the P2
                                    // admission race): the admission
                                    // reserve()s this row's slot BEFORE the
                                    // queue commit and publish()es only
                                    // after it, so a tick landing in between
                                    // sees a Reserved-but-unpublished slot.
                                    // Reserved means the identity is still
                                    // IN FLIGHT — park the attempt here (no
                                    // StartSend, no last_send bump, no
                                    // spawn) and pick the row up again on a
                                    // later tick once the publish lands.
                                    // Converging now would permanently fail
                                    // an accepted task (retry:false) whose
                                    // identity arrives microseconds later.
                                    // Every other readiness (Published, or
                                    // Gone via the None/Claimed slots)
                                    // proceeds, so the fail-closed
                                    // convergence and the DefinitelyNotSent
                                    // evidence rules stay untouched — the
                                    // queue TTL stays the parked row's
                                    // convergence bound.
                                    if let Some(pipeline) = self.caller_dispatch.as_ref() {
                                        if matches!(
                                            pipeline.dispatch_identity_readiness(&row).await,
                                            bcs_service_api::application::caller_handoff::HandoffIdentityReadiness::Reserved
                                        ) {
                                            tracing::debug!(
                                                delivery_id = %row.delivery_id,
                                                bot_id = %row.target_bot_id,
                                                "caller identity reserved but not yet published; the row parks this tick"
                                            );
                                            continue;
                                        }
                                    }
                                    let (handle, registration) = futures::future::AbortHandle::new_pair();
                                    caller_tasks.insert(delivery_id.clone(), (bot.clone(), handle));
                                    preparing.insert(delivery_id.clone());
                                    last_send.insert(bot.clone(), epoch.elapsed().as_millis() as u64);
                                    let runtime = self.clone();
                                    work.spawn(async move {
                                        let result = futures::future::Abortable::new(
                                            tokio::time::timeout(runtime.config.io_timeout,
                                                runtime.attempt_caller_protected_dispatch(&row)),
                                            registration,
                                        ).await;
                                        WorkResult::CallerFinished(delivery_id, matches!(result, Ok(Err(_))))
                                    });
                                    continue;
                                }
                                preparing.insert(delivery_id);
                                let preparation = self.preparation.clone();
                                let timeout = self.config.io_timeout;
                                work.spawn(async move {
                                    let result = tokio::time::timeout(timeout, preparation.prepare(&row)).await
                                        .unwrap_or_else(|_| Err(bcs_service_api::ServiceError::InternalError("delivery preparation timed out".into())));
                                    WorkResult::Prepared(row, result)
                                });
                            }
                            None => {}
                        }
                    }
                    self.service.observe_operation("tick", tick_started.elapsed().as_secs_f64(), work.len(), true);
                }
                Some(result) = work.join_next(), if !work.is_empty() => {
                    let result = result.map_err(|_| ManagedDeliveryError::Conflict)?;
                    match result {
                        WorkResult::CallerFinished(id, timed_out) => {
                            preparing.remove(&id);
                            caller_tasks.remove(&id);
                            if timed_out {
                                tracing::warn!(delivery_id = %id, "caller protected dispatch timed out; no automatic resend");
                                if let Some(row) = storage!(self.get(&id)) {
                                    let next = match row.state.status {
                                        Status::Queued => Some(Event::PreparationFailed),
                                        Status::Dispatching | Status::Running => Some(Event::TransportUnknown),
                                        // Preserve cancellation and terminal evidence; abort work
                                        // independently establishes stopped vs uncertain.
                                        _ => None,
                                    };
                                    if let Some(next) = next {
                                        storage!(self.transition(event(&row, next)));
                                    }
                                }
                            }
                        }
                        WorkResult::Prepared(prepared_row, result) => {
                            preparing.remove(&prepared_row.delivery_id);
                            let Some(current) = storage!(self.get(&prepared_row.delivery_id)) else { continue; };
                            let row = &current;
                            if row.state.state_version != prepared_row.state.state_version || row.state.status != Status::Queued { continue; }
                            let mut prepared = match result {
                                Ok(prepared) => prepared,
                                Err(_) => { storage!(self.transition(event(row, Event::PreparationFailed))); continue; }
                            };
                            let bot = &row.target_bot_id;
                            let (policies, paused, retries, policy_version) = self.policies(&[bot.clone()]).await;
                            let Some(policy) = policies.get(bot) else { continue; };
                            if storage!(self.service.active_count(bot)) + caller_tasks.values().filter(|(id, _)| id == bot).count() as u64 >= policy.max_running as u64 { continue; }
                            if storage!(self.service.lane_blocked(row)) { continue; }
                            let entries = schedule_rows(std::slice::from_ref(row), bot);
                            let excluded = entries.iter().filter(|e| e.delivery_id != row.delivery_id).map(|e| e.delivery_id.clone()).collect();
                            let rate_ready = epoch.elapsed().as_millis() as u64 >= last_send.get(bot).copied().unwrap_or(0).saturating_add(policy.min_send_interval_ms);
                            let transport_ready = !paused && rate_ready;
                            let online = if transport_ready { self.bot_available(bot).await } else { true };
                            let selected = match MessageDeliveryCore.select(DeliveryScheduleSnapshot {
                                entries: &entries, max_running: policy.max_running, bot_online: online,
                                paused, now_ms: now_ms(), monotonic_now_ms: epoch.elapsed().as_millis() as u64,
                                next_send_tick_ms: last_send.get(bot).copied().unwrap_or(0).saturating_add(policy.min_send_interval_ms), after_session_id: None, preparing_delivery_ids: &excluded,
                            }) {
                                Ok(selected) => selected,
                                // Same §15.98 demotion as the claim loop: a
                                // corrupted record costs one bot one tick.
                                Err(error) => {
                                    tracing::error!(bot_id = %bot, error = ?error,
                                        "delivery schedule snapshot rejected: skipping this bot this tick");
                                    continue;
                                }
                            };
                            if matches!(selected.action, Some(DeliveryScheduleAction::Expire { .. })) {
                                storage!(self.transition(event(row, Event::QueueExpired)));
                                continue;
                            }
                            if selected.action.is_none()
                                && transport_ready
                                && !online
                                && epoch.elapsed() >= self.config.startup_recovery_grace
                            {
                                tracing::warn!(
                                    delivery_id = %row.delivery_id,
                                    bot_id = %row.target_bot_id,
                                    startup_recovery_grace_ms = self.config.startup_recovery_grace.as_millis() as u64,
                                    "prepared delivery target disconnected after startup recovery grace; marking failed"
                                );
                                storage!(self.transition(event(row, Event::PreparationFailed)));
                                continue;
                            }
                            if !matches!(selected.action, Some(DeliveryScheduleAction::Prepare { ref delivery_id, .. }) if delivery_id == &row.delivery_id) { continue; }
                            if !tokio::time::timeout(self.config.io_timeout, self.preparation.still_valid(&prepared)).await.unwrap_or(false) { continue; }
                            if prepared.command.target_bot_id() != bot || prepared.command.run_id.as_str() != row.run_id.as_deref().unwrap_or("")
                                || prepared.transport_context_json.get("provider_route_headers").cloned().unwrap_or_else(|| serde_json::json!([]))
                                    != serde_json::json!(prepared.command.provider_bypass_headers) { return Err(ManagedDeliveryError::Conflict); }
                            let mut command = event(row, Event::StartSend);
                            if let Some(version) = policy_version { prepared.transport_context_json["policy_version"] = serde_json::json!(version); }
                            command.transport_context_json = Some(prepared.transport_context_json);
                            command.deadline_at_ms = Some(if row.flow_kind == bcs_domain::message_delivery::DeliveryFlowKind::DirectA2a {
                                row.semantic_projection_json.get("expires_at_ms").and_then(|v| v.as_i64()).ok_or(ManagedDeliveryError::Conflict)?
                            } else { now_ms().saturating_add(run_timeout_for(row, self.config.run_timeout).as_millis() as i64) });
                            let Some(started) = storage!(self.transition(command.clone())) else { continue; };
                            // §7.2.1 delegation-only lineage (review batch-3
                            // §3.1): an ORDINARY Started row carrying the
                            // trusted `caller_lineage` marker is an Owner
                            // target of a lease-admitted Human turn — its
                            // delegation-only root association registers
                            // best-effort here (the ordinary dispatch never
                            // depends on it). Without an attached pipeline
                            // the marker is REPORTED, never silently
                            // dropped (spec CALLER_LINEAGE_UNSUPPORTED).
                            if started.semantic_projection_json.get("caller_lineage").is_some() {
                                match self.caller_dispatch.as_ref() {
                                    Some(pipeline) => {
                                        pipeline
                                            .register_delegation_only_lineage(&started, now_ms())
                                            .await;
                                    }
                                    None => {
                                        tracing::warn!(
                                            delivery_id = %started.delivery_id,
                                            bot_id = %started.target_bot_id,
                                            "delegation-only lineage marker without a protected pipeline (CALLER_LINEAGE_UNSUPPORTED); derivation stays refused"
                                        );
                                    }
                                }
                            }
                            let request_id = started.request_id.clone().ok_or(ManagedDeliveryError::Conflict)?;
                            match &mut prepared.command.frame {
                                BcsFrame::Request(frame) if frame.method == "chat.send" => frame.id = request_id.clone(),
                                _ => return Err(ManagedDeliveryError::Conflict),
                            }
                            last_send.insert(bot.clone(), epoch.elapsed().as_millis() as u64);
                            cursors.insert(bot.clone(), row.session_id.clone());
                            // All subsequent failures are conservative possible sends.
                            // Registration failure is provably before transport I/O.
                            if !matches!(tokio::time::timeout(self.config.io_timeout, self.preparation.before_send(&started, &prepared.command)).await, Ok(Ok(()))) {
                                let Some(current) = storage!(self.get(&started.delivery_id)) else { continue; };
                                let retry = current.attempt_no <= retries;
                                let mut failed = event(&current, Event::DefinitelyNotSent { retry });
                                failed.request_id = Some(request_id);
                                storage!(self.transition(failed.clone()));
                                continue;
                            }
                            let io = self.transport.clone();
                            let timeout = self.config.io_timeout;
                            let connection_id = started.transport_context_json.as_ref()
                                .and_then(|v| v.get("connection_id")).and_then(|v| v.as_str()).map(str::to_owned);
                            work.spawn(async move {
                                let result = tokio::time::timeout(timeout, async {
                                    match connection_id {
                                        Some(id) => io.deliver_on_connection(prepared.command, &id).await,
                                        None => io.deliver(prepared.command).await,
                                    }
                                }).await.ok();
                                WorkResult::Sent(started.delivery_id, request_id, result)
                            });
                        }
                        WorkResult::Sent(id, request_id, result) => {
                            if let Some(row) = storage!(self.get(&id)) {
                                // Submission is not ACK. A delivered=true result keeps
                                // dispatching until a trusted Bot event proves receipt.
                                {
                                    let next = match &result {
                                        Some(Err(bcs_service_api::ServiceError::DeliveryNotSent { code, retryable }))
                                        | Some(Ok(BotDeliveryResult {
                                            delivered: false,
                                            error: Some(bcs_service_api::ServiceError::DeliveryNotSent { code, retryable }),
                                            ..
                                        })) => {
                                            tracing::warn!(delivery_id = %id, %request_id, code, retryable, "delivery failed before submission");
                                            let (_, _, retries, _) = self.policies(&[row.target_bot_id.clone()]).await;
                                            Event::DefinitelyNotSent { retry: *retryable && row.attempt_no <= retries }
                                        }
                                        Some(Ok(result)) if result.delivered => Event::Submitted,
                                        Some(Ok(result)) => {
                                            tracing::warn!(
                                                delivery_id = %id,
                                                %request_id,
                                                error = ?result.error,
                                                "delivery explicitly rejected; marking terminal failed"
                                            );
                                            Event::TransportRejected
                                        }
                                        _ => {
                                            tracing::warn!(delivery_id = %id, %request_id, "delivery outcome unknown; no automatic resend");
                                            Event::TransportUnknown
                                        }
                                    };
                                    let mut command = event(&row, next);
                                    command.request_id = Some(request_id);
                                    storage!(self.transition(command.clone()));
                                }
                            }
                        }
                    }
                }
                Some(result) = abort_work.join_next(), if !abort_work.is_empty() => {
                    let (id, abort_request_id, downstream_run_id, result) = result.map_err(|_| ManagedDeliveryError::Conflict)?;
                    aborting.remove(&id);
                    if let Some(Err(error)) = &result {
                        tracing::warn!(delivery_id=%id, error_kind=error.as_ref(), reason=abort_reason(error), "delivery abort transport failed");
                    }
                    if let Some(row) = storage!(self.get(&id)) {
                        if row.abort_request_id.as_ref() != Some(&abort_request_id) { continue; }
                        let acknowledged = matches!(result, Some(Ok(ref r)) if r.target_bot_id == row.target_bot_id
                            && downstream_run_id.as_ref().is_some_and(|id| r.aborted_run_ids.len() == 1 && r.aborted_run_ids.contains(id)));
                        let mut command = event(&row, if acknowledged { Event::Aborted } else { Event::AbortUnconfirmed });
                        if matches!(result, Some(Err(bcs_service_api::ServiceError::BotMethodUnsupported { .. } | bcs_service_api::ServiceError::InvalidOperation { .. }))) {
                            command.transport_context_json = Some(serde_json::json!({"task_cancel_rejected":true}));
                        }
                        storage!(self.transition(command.clone()));
                    }
                }
            }
        }
        Ok::<(), ManagedDeliveryError>(())
        }.await;
        work.abort_all();
        abort_work.abort_all();
        while work.join_next().await.is_some() {}
        while abort_work.join_next().await.is_some() {}
        self.service.recover(now_ms()).await?;
        outcome
    }
}

mod caller_delegate;

mod caller_dispatch;

// Only fixed, locally defined diagnostics are safe to log here. Remote error
// bodies, command payloads and credential-bearing target Debug values stay out.
fn abort_reason(error: &bcs_service_api::ServiceError) -> &'static str {
    use bcs_service_api::ServiceError;
    let message = match error {
        ServiceError::InternalError(message) | ServiceError::InvalidOperation { message, .. } => message.as_str(),
        _ => return "see_error_kind",
    };
    match message {
        "queue original transport is unknown" => "original_transport_missing",
        "invalid queue transport metadata" => "transport_metadata_invalid",
        "unsupported queue transport version" => "transport_version_unsupported",
        "queue run identity missing" => "run_identity_missing",
        "queue original A2A owner changed" => "a2a_owner_changed",
        "A2A: cancel scope mismatch" => "a2a_scope_mismatch",
        "A2A: cancel Run not found" => "a2a_run_missing",
        "A2A: cancel rejected or unconfirmed" => "a2a_remote_unconfirmed",
        "A2A: cancel outcome unknown" => "a2a_remote_timeout",
        "A2A: cancel response identity mismatch" => "a2a_remote_identity_mismatch",
        "A2A: credential unavailable" => "a2a_credential_unavailable",
        "A2A: cancel persistence unconfirmed" => "a2a_persistence_unconfirmed",
        _ => "see_error_kind",
    }
}
