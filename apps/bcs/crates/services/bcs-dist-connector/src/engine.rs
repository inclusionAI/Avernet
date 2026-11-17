//! The Connector's main loop and runtime control.
//!
//! ```text
//! receive → claim → ack → one actor per held call
//! ```
//!
//! A command entry is acknowledged only after the claim decision (claimed, or
//! nothing to do). The command stream is a hint: a repeated or late entry is
//! harmless, because the claim decides from the dispatch document.

use crate::call::{Call, CallStatus};
use crate::config::{Backoff, ConnectorConfig, ConnectorPorts};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::dist_runtime::DistRuntimeControlPort;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::dispatch::{
    ClaimOutcome, CommandDelivery, CommandFeedPort, DispatchClaimRequest, HeldDispatchPort,
    ResumeOwnedDispatch,
};
use bcs_service_api::port::repo::distributed::ownership::{MembershipLease, Renewal, RuntimeRole};
use bcs_service_api::types::dist_runtime::{DrainOutcome, RuntimeError, RuntimeSnapshot};
use std::collections::HashMap;
use std::num::NonZeroU32;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::sync::Notify;
use tokio::task::JoinHandle;
use tokio::time::Instant;

struct Actor {
    holder: Holder,
    status: Arc<CallStatus>,
    poke: Arc<Notify>,
    task: JoinHandle<()>,
}

struct Inner {
    config: Arc<ConnectorConfig>,
    ports: ConnectorPorts,
    actors: Mutex<HashMap<Did, Actor>>,
    lease: Mutex<Option<MembershipLease>>,
    /// Held calls whose actor died and whose resume has not succeeded yet.
    orphans: Mutex<Vec<(Did, Holder)>>,
    draining: AtomicBool,
    ready: AtomicBool,
    /// The main loop is running (it may be in the middle of a claim).
    looping: AtomicBool,
    claims: AtomicU64,
    wake: Notify,
    /// Interrupts the main loop's wait for hints when a drain starts.
    stop: Notify,
}

/// One Connector process: reads the command feed, claims dispatches and runs
/// one actor per held call.
#[derive(Clone)]
pub struct ConnectorEngine {
    inner: Arc<Inner>,
}

fn failed(code: &str) -> RuntimeError {
    RuntimeError {
        code: code.to_owned(),
    }
}

impl ConnectorEngine {
    pub fn new(config: ConnectorConfig, ports: ConnectorPorts) -> Result<Self, RuntimeError> {
        if config.max_active_calls == 0 || config.poll_interval.is_zero() || config.backoff_min.is_zero() {
            return Err(failed("connector_config_invalid"));
        }
        Ok(Self {
            inner: Arc::new(Inner {
                config: Arc::new(config),
                ports,
                actors: Mutex::new(HashMap::new()),
                lease: Mutex::new(None),
                orphans: Mutex::new(Vec::new()),
                draining: AtomicBool::new(false),
                ready: AtomicBool::new(false),
                looping: AtomicBool::new(false),
                claims: AtomicU64::new(0),
                wake: Notify::new(),
                stop: Notify::new(),
            }),
        })
    }

    /// Runs until `drain` is called or the membership registration is lost.
    /// Actors of held calls keep running after it returns.
    pub async fn run(&self) -> Result<(), RuntimeError> {
        self.inner.looping.store(true, Ordering::SeqCst);
        let result = self.run_loop().await;
        self.inner.ready.store(false, Ordering::SeqCst);
        self.inner.looping.store(false, Ordering::SeqCst);
        self.inner.wake.notify_waiters();
        result
    }

    async fn run_loop(&self) -> Result<(), RuntimeError> {
        let inner = &self.inner;
        let config = inner.config.clone();
        self.register().await;
        let mut feed = inner
            .ports
            .queue
            .notifications(config.pool.clone(), config.me.clone())
            .await
            .map_err(|error| {
                tracing::error!(?error, "command feed unavailable");
                failed("command_feed_unavailable")
            })?;
        inner.ready.store(true, Ordering::SeqCst);
        let mut renewed_at = Instant::now();
        let mut backoff = Backoff::new(&config);
        loop {
            if inner.draining.load(Ordering::SeqCst) {
                return Ok(());
            }
            if renewed_at.elapsed() >= config.membership_renew {
                renewed_at = Instant::now();
                if !self.renew().await {
                    return Err(failed("membership_lost"));
                }
            }
            self.reap().await;
            let free = config.max_active_calls.saturating_sub(self.live());
            let Some(free) = u32::try_from(free).ok().and_then(NonZeroU32::new) else {
                // At capacity: read no hints, other Connectors take them.
                let _ = tokio::time::timeout(config.receive_timeout, inner.wake.notified()).await;
                continue;
            };
            // Dropping the wait is safe: the feed keeps its in-flight reads.
            let received = tokio::select! {
                received = feed.receive(free, config.receive_timeout) => received,
                _ = inner.stop.notified() => continue,
            };
            match received {
                Ok(deliveries) => {
                    backoff = Backoff::new(&config);
                    for delivery in deliveries {
                        if inner.draining.load(Ordering::SeqCst) {
                            // Not acked: another Connector takes it over.
                            break;
                        }
                        self.handle(feed.as_mut(), delivery).await;
                    }
                }
                Err(error) => {
                    tracing::warn!(?error, "command feed read failed");
                    // Only until the next renewal is due: a feed outage,
                    // even one whose reads fail slowly, must not let this
                    // instance's registration expire.
                    let until_renewal = config.membership_renew.saturating_sub(renewed_at.elapsed());
                    backoff.wait_at_most(until_renewal).await;
                }
            }
        }
    }

    /// The claim decision for one hint, then the ack.
    async fn handle(&self, feed: &mut dyn CommandFeedPort, delivery: CommandDelivery) {
        let inner = &self.inner;
        let did = delivery.did.clone();
        let request = DispatchClaimRequest {
            did: did.clone(),
            me: inner.config.me.clone(),
            request_id: self.claim_request_id(&did),
        };
        match inner.ports.queue.claim(request).await {
            Ok(ClaimOutcome::Acquired(held)) => self.spawn(held).await,
            Ok(ClaimOutcome::OwnedByMe(holder)) => {
                if !self.poke(&did) {
                    // Held by this process but no actor runs it (it died, or
                    // a claim answer was lost): take a new generation.
                    self.resume(did.clone(), holder).await;
                }
            }
            Ok(ClaimOutcome::HeldByOther(_) | ClaimOutcome::Ended | ClaimOutcome::NotFound) => {}
            Err(error) => {
                // No decision, no ack: the hint stays pending and comes back.
                tracing::warn!(did = %did.0, ?error, "claim failed; hint left pending");
                return;
            }
        }
        if let Err(error) = feed.ack(&delivery).await {
            tracing::warn!(did = %did.0, ?error, "command ack failed; the hint will be redelivered");
        }
    }

    fn claim_request_id(&self, did: &Did) -> RequestId {
        let me = &self.inner.config.me;
        let n = self.inner.claims.fetch_add(1, Ordering::Relaxed);
        RequestId(format!("{}:{}:{}:{n}", me.node, me.incarnation, did.0))
    }

    /// Refreshes the actor of `did` now; `false` when no live actor runs it.
    fn poke(&self, did: &Did) -> bool {
        let actors = lock(&self.inner.actors);
        match actors.get(did) {
            Some(actor) if !actor.task.is_finished() => {
                actor.poke.notify_one();
                true
            }
            _ => false,
        }
    }

    /// Resumes a held call that has no actor. A failure is remembered and
    /// retried by the main loop, so the call is never silently left behind.
    async fn resume(&self, did: Did, previous: Holder) {
        // Only after the old actor is gone: stop and join it first.
        let old = lock(&self.inner.actors).remove(&did);
        if let Some(old) = old {
            old.task.abort();
            let _ = old.task.await;
        }
        let request = ResumeOwnedDispatch {
            did: did.clone(),
            previous,
            request_id: self.claim_request_id(&did),
        };
        match self.inner.ports.queue.resume_owned(request.clone()).await {
            Ok(ClaimOutcome::Acquired(held)) => self.spawn(held).await,
            // Held by this process under a generation no actor runs: an
            // earlier resume landed but its answer was lost. Resume that one.
            Ok(ClaimOutcome::OwnedByMe(holder)) => lock(&self.inner.orphans).push((did, holder)),
            Ok(_) => {}
            // A permanent refusal (the document cannot be resumed as asked)
            // is not retried; the call is left to its dispatch document.
            Err(error @ (StoreError::Invalid { .. } | StoreError::UnsupportedFormat { .. })) => {
                tracing::error!(did = %did.0, ?error, "resume refused; giving the call up");
            }
            Err(error) => {
                tracing::warn!(did = %did.0, ?error, "resume failed; retrying");
                lock(&self.inner.orphans).push((did, request.previous));
            }
        }
    }

    async fn spawn(&self, held: Box<dyn HeldDispatchPort>) {
        let inner = self.inner.clone();
        let did = held.view().spec.did.clone();
        // An older actor of this process for the same call is fenced in the
        // dispatch, but its agent calls and inbox appends are not: stop and
        // join it before the new generation starts.
        let older = lock(&self.inner.actors).remove(&did);
        if let Some(older) = older {
            older.task.abort();
            let _ = older.task.await;
        }
        let holder = held.holder().clone();
        let status = Arc::new(CallStatus::default());
        let poke = Arc::new(Notify::new());
        let call = Call::new(
            inner.ports.clone(),
            inner.config.clone(),
            held,
            status.clone(),
            poke.clone(),
        );
        let wake = self.inner.clone();
        let task = tokio::spawn(async move {
            call.run().await;
            wake.wake.notify_waiters();
        });
        let actor = Actor {
            holder,
            status,
            poke,
            task,
        };
        lock(&self.inner.actors).insert(did, actor);
    }

    /// Forgets finished actors; one that died without finishing its run is
    /// resumed under a new holder generation.
    async fn reap(&self) {
        let mut crashed = Vec::new();
        {
            let mut actors = lock(&self.inner.actors);
            actors.retain(|did, actor| {
                if !actor.task.is_finished() {
                    return true;
                }
                if !actor.status.done.load(Ordering::SeqCst) {
                    crashed.push((did.clone(), actor.holder.clone()));
                }
                false
            });
        }
        for (did, holder) in &crashed {
            tracing::error!(did = %did.0, "call actor died; resuming under a new generation");
            let _ = holder;
        }
        // Earlier failed resumes are tried again together with the new ones.
        let mut pending = std::mem::take(&mut *lock(&self.inner.orphans));
        pending.extend(crashed);
        for (did, holder) in pending {
            self.resume(did, holder).await;
        }
    }

    fn live(&self) -> usize {
        lock(&self.inner.actors)
            .values()
            .filter(|actor| !actor.task.is_finished())
            .count()
    }

    /// Registers this instance. The membership provider is pending from the
    /// executor team: until it exists the registration fails and the
    /// Connector runs without one.
    async fn register(&self) {
        let config = &self.inner.config;
        let registered = self
            .inner
            .ports
            .membership
            .register(config.me.clone(), RuntimeRole::Connector, config.membership_ttl)
            .await;
        match registered {
            Ok(lease) => *lock(&self.inner.lease) = Some(lease),
            Err(error) => tracing::warn!(?error, "membership registration unavailable; running without it"),
        }
    }

    /// `false` when the registration is definitely lost: the process must
    /// stop claiming and restart under a new incarnation.
    async fn renew(&self) -> bool {
        let Some(lease) = lock(&self.inner.lease).clone() else {
            return true;
        };
        match self.inner.ports.membership.renew(&lease, self.inner.config.membership_ttl).await {
            Ok(Renewal::Renewed) => true,
            Ok(Renewal::Lost) => {
                tracing::error!("membership lost; no new claims (held calls stay fenced by their dispatch)");
                false
            }
            Err(error) => {
                tracing::warn!(?error, "membership renewal failed");
                true
            }
        }
    }
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[async_trait]
impl DistRuntimeControlPort for ConnectorEngine {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        let inner = &self.inner;
        let (active, pending) = {
            let actors = lock(&inner.actors);
            let live = actors.values().filter(|actor| !actor.task.is_finished());
            live.fold((0u64, 0u64), |(active, pending), actor| {
                let unsettled = actor.status.pending_effect.load(Ordering::SeqCst);
                (active + 1, pending + u64::from(unsettled))
            })
        };
        // A held call waiting for its resume is unsettled work too.
        let orphans = lock(&inner.orphans).len() as u64;
        let (active, pending) = (active + orphans, pending + orphans);
        Ok(RuntimeSnapshot {
            instance: inner.config.me.clone(),
            ready: inner.ready.load(Ordering::SeqCst) && !inner.draining.load(Ordering::SeqCst),
            draining: inner.draining.load(Ordering::SeqCst),
            active_work: active,
            pending_effects: pending,
        })
    }

    /// Stops claiming and waits up to `grace` for held calls to end. Calls
    /// still running are left held and recoverable, never aborted.
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        let inner = &self.inner;
        inner.draining.store(true, Ordering::SeqCst);
        inner.stop.notify_one();
        inner.wake.notify_waiters();
        let lease = lock(&inner.lease).clone();
        if let Some(lease) = lease {
            if let Err(error) = inner.ports.membership.set_draining(&lease).await {
                tracing::warn!(?error, "membership drain mark failed");
            }
        }
        let deadline = Instant::now() + grace;
        // First the main loop must leave its iteration: a claim in flight may
        // still take a call. Then the held calls get the rest of the grace.
        // Short waits instead of one long one: a wake-up can be missed.
        let step = Duration::from_millis(20);
        let mut renewed_at = Instant::now();
        while (inner.looping.load(Ordering::SeqCst) || self.live() > 0) && Instant::now() < deadline {
            let _ = tokio::time::timeout(step, inner.wake.notified()).await;
            // The main loop no longer renews. Without this the registration
            // would expire during the grace period and other Connectors could
            // take over calls whose actors are still running here.
            if renewed_at.elapsed() >= inner.config.membership_renew {
                renewed_at = Instant::now();
                self.renew().await;
            }
        }
        let leftover: Vec<(Did, Holder)> = lock(&inner.actors)
            .iter()
            .filter(|(_, actor)| !actor.task.is_finished())
            .map(|(did, actor)| (did.clone(), actor.holder.clone()))
            .collect();
        let leftover: Vec<(Did, Holder)> = leftover
            .into_iter()
            .chain(lock(&inner.orphans).iter().cloned())
            .collect();
        if leftover.is_empty() && !inner.looping.load(Ordering::SeqCst) {
            return Ok(DrainOutcome::Drained);
        }
        for (did, holder) in &leftover {
            // D8: handing a call over is not implemented yet; the call stays
            // held and is recovered by the Worker's deadline re-post.
            if let Err(error) = inner.ports.recovery.release(did, holder).await {
                tracing::warn!(did = %did.0, ?error, "call left held after drain");
            }
        }
        Ok(DrainOutcome::DeadlineReached {
            remaining: leftover.len() as u64,
        })
    }
}
