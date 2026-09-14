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

pub struct DeliveryRuntimeConfig {
    pub max_safe_retries: u32,
    pub pause_dispatch: bool,
    pub bots: BTreeMap<String, DeliveryRuntimePolicy>,
    pub tick: Duration,
    pub io_timeout: Duration,
    pub run_timeout: Duration,
    pub cancel_timeout: Duration,
    pub max_tasks: usize,
    pub max_abort_tasks: usize,
}

pub struct DeliveryRuntime {
    pub policy: Option<Arc<crate::delivery_policy::LiveDeliveryPolicy>>,
    pub service: Arc<dyn ManagedMessageDeliveryService>,
    pub preparation: Arc<dyn ManagedDeliveryPreparationService>,
    pub transport: Arc<dyn BotDeliveryPort>,
    pub config: DeliveryRuntimeConfig,
}

enum WorkResult {
    Prepared(
        PersistedMessageDelivery,
        ServiceResult<PreparedManagedDelivery>,
    ),
    Sent(String, String, Option<ServiceResult<BotDeliveryResult>>),
}

fn now_ms() -> i64 {
    chrono::Utc::now().timestamp_millis()
}

fn event(row: &PersistedMessageDelivery, kind: Event) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand {
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
    pub async fn run(
        self,
        mut shutdown: tokio::sync::watch::Receiver<bool>,
    ) -> Result<(), ManagedDeliveryError> {
        if self.config.tick.is_zero()
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
        let mut preparing = BTreeSet::new();
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
        let mut last_bot = String::new();
        let outcome = async {
        loop {
            if *shutdown.borrow() { break; }
            tokio::select! {
                _ = shutdown.changed() => { break; }
                _ = ticker.tick() => {
                    let tick_started = Instant::now();
                    let expired = storage!(self.service.work_batch(DeliveryWorkBatch::Expired, now_ms(), "", 100));
                    for row in expired { storage!(self.transition(event(&row, Event::QueueExpired))); }
                    let rows = storage!(self.service.work_batch(DeliveryWorkBatch::Control, now_ms(), "", 32));
                    for row in &rows {
                        if row.state.status == Status::PendingContext && row.expire_at_ms.is_some_and(|t| t <= now_ms()) {
                            storage!(self.transition(event(row, Event::QueueExpired)));
                        } else if matches!(row.state.status, Status::Dispatching | Status::Running)
                            && row.run_deadline_at_ms.is_some_and(|t| t <= now_ms()) {
                            let mut command = event(row, Event::CancelRequested);
                            command.deadline_at_ms = Some(now_ms().saturating_add(self.config.cancel_timeout.as_millis() as i64));
                            storage!(self.transition(command.clone()));
                        } else if row.state.status == Status::Cancelling && !aborting.contains(&row.delivery_id) {
                            if row.cancel_deadline_at_ms.is_some_and(|t| t <= now_ms()) {
                                storage!(self.transition(event(row, Event::AbortUnconfirmed)));
                            } else if row.abort_request_id.is_none() && abort_work.len() < self.config.max_abort_tasks {
                                let mut cmd = match tokio::time::timeout(self.config.io_timeout, self.preparation.prepare_abort(row)).await {
                                    Ok(Ok(cmd)) => cmd,
                                    _ => { storage!(self.transition(event(row, Event::AbortUnconfirmed))); continue; }
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
                                let downstream_run_id = cmd.run_id.clone();
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
                        let Some(policy) = policies.get(&bot) else { continue; };
                        let active = storage!(self.service.active_count(&bot)) as usize;
                        let after = cursors.get(&bot).map(String::as_str).unwrap_or("");
                        let mut entries = storage!(self.service.queued_heads(&bot, after, 8));
                        if entries.is_empty() && !after.is_empty() { entries = storage!(self.service.queued_heads(&bot, "", 8)); }
                        if let Some(last) = entries.last() { cursors.insert(bot.clone(), last.session_id.clone()); }
                        if entries.is_empty() { continue; }
                        let rate_ready = epoch.elapsed().as_millis() as u64 >= last_send.get(&bot).copied().unwrap_or(0).saturating_add(policy.min_send_interval_ms);
                        let online = if !paused && active < policy.max_running && rate_ready { self.bot_available(&bot).await } else { true };
                        let selected = MessageDeliveryCore.select(DeliveryScheduleSnapshot {
                            entries: &entries, max_running: policy.max_running.saturating_sub(active).max(1), bot_online: online,
                            paused, now_ms: now_ms(), monotonic_now_ms: epoch.elapsed().as_millis() as u64,
                            next_send_tick_ms: last_send.get(&bot).copied().unwrap_or(0).saturating_add(policy.min_send_interval_ms), after_session_id: None,
                            preparing_delivery_ids: &preparing,
                        })?;
                        if active >= policy.max_running {
                            storage!(self.service.update_wait_reasons(entries.iter().map(|d| (d.delivery_id.clone(), bcs_domain::message_delivery::DeliveryWaitReason::BotCapacity)).collect(), now_ms()));
                            continue;
                        }
                        storage!(self.service.update_wait_reasons(selected.waiting.clone(), now_ms()));
                        match selected.action {
                            Some(DeliveryScheduleAction::Expire { delivery_id, .. }) => {
                                if let Some(row) = storage!(self.get(&delivery_id)) { storage!(self.transition(event(&row, Event::QueueExpired))); }
                            }
                            Some(DeliveryScheduleAction::Prepare { delivery_id, .. }) => {
                                let row = storage!(self.get(&delivery_id)).ok_or(ManagedDeliveryError::NotFound)?;
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
                            if storage!(self.service.active_count(bot)) >= policy.max_running as u64 { continue; }
                            if storage!(self.service.lane_blocked(row)) { continue; }
                            let entries = schedule_rows(std::slice::from_ref(row), bot);
                            let excluded = entries.iter().filter(|e| e.delivery_id != row.delivery_id).map(|e| e.delivery_id.clone()).collect();
                            let selected = MessageDeliveryCore.select(DeliveryScheduleSnapshot {
                                entries: &entries, max_running: policy.max_running, bot_online: self.bot_available(bot).await,
                                paused, now_ms: now_ms(), monotonic_now_ms: epoch.elapsed().as_millis() as u64,
                                next_send_tick_ms: last_send.get(bot).copied().unwrap_or(0).saturating_add(policy.min_send_interval_ms), after_session_id: None, preparing_delivery_ids: &excluded,
                            })?;
                            if !matches!(selected.action, Some(DeliveryScheduleAction::Prepare { ref delivery_id, .. }) if delivery_id == &row.delivery_id) { continue; }
                            if !tokio::time::timeout(self.config.io_timeout, self.preparation.still_valid(&prepared)).await.unwrap_or(false) { continue; }
                            if prepared.command.target_bot_id() != bot || prepared.command.run_id.as_str() != row.run_id.as_deref().unwrap_or("")
                                || prepared.transport_context_json.get("provider_route_headers").cloned().unwrap_or_else(|| serde_json::json!([]))
                                    != serde_json::json!(prepared.command.provider_bypass_headers) { return Err(ManagedDeliveryError::Conflict); }
                            let mut command = event(row, Event::StartSend);
                            if let Some(version) = policy_version { prepared.transport_context_json["policy_version"] = serde_json::json!(version); }
                            command.transport_context_json = Some(prepared.transport_context_json);
                            command.deadline_at_ms = Some(now_ms().saturating_add(self.config.run_timeout.as_millis() as i64));
                            let Some(started) = storage!(self.transition(command.clone())) else { continue; };
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
                                    let submitted = matches!(result, Some(Ok(ref result)) if result.delivered);
                                    let mut command = event(&row, if submitted { Event::Submitted } else { Event::TransportUnknown });
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
                    if let Some(row) = storage!(self.get(&id)) {
                        if row.abort_request_id.as_ref() != Some(&abort_request_id) { continue; }
                        let acknowledged = matches!(result, Some(Ok(ref r)) if r.target_bot_id == row.target_bot_id
                            && downstream_run_id.as_ref().is_some_and(|id| r.aborted_run_ids.len() == 1 && r.aborted_run_ids.contains(id)));
                        storage!(self.transition(event(&row, if acknowledged { Event::Aborted } else { Event::AbortUnconfirmed })));
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
