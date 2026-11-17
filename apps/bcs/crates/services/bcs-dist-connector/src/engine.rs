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
    draining: AtomicBool,
    ready: AtomicBool,
    claims: AtomicU64,
    wake: Notify,
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
                draining: AtomicBool::new(false),
                ready: AtomicBool::new(false),
                claims: AtomicU64::new(0),
                wake: Notify::new(),
            }),
        })
    }

    /// Runs until `drain` is called or the membership registration is lost.
    /// Actors of held calls keep running after it returns.
    pub async fn run(&self) -> Result<(), RuntimeError> {
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
        let result = loop {
            if inner.draining.load(Ordering::SeqCst) {
                break Ok(());
            }
            if renewed_at.elapsed() >= config.membership_renew {
                renewed_at = Instant::now();
                if !self.renew().await {
                    break Err(failed("membership_lost"));
                }
            }
            self.reap().await;
            let free = config.max_active_calls.saturating_sub(self.live());
            let Some(free) = u32::try_from(free).ok().and_then(NonZeroU32::new) else {
                // At capacity: read no hints, other Connectors take them.
                let _ = tokio::time::timeout(config.receive_timeout, inner.wake.notified()).await;
                continue;
            };
            match feed.receive(free, config.receive_timeout).await {
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
                    backoff.wait().await;
                }
            }
        };
        inner.ready.store(false, Ordering::SeqCst);
        result
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
            Ok(ClaimOutcome::Acquired(held)) => self.spawn(held),
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
        match self.inner.ports.queue.resume_owned(request).await {
            Ok(ClaimOutcome::Acquired(held)) => self.spawn(held),
            Ok(_) => {}
            Err(error) => tracing::warn!(did = %did.0, ?error, "resume failed; a later hint retries"),
        }
    }

    fn spawn(&self, held: Box<dyn HeldDispatchPort>) {
        let inner = self.inner.clone();
        let did = held.view().spec.did.clone();
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
        if let Some(old) = lock(&self.inner.actors).insert(did, actor) {
            // A newer generation replaces it; the old one is fenced anyway.
            old.task.abort();
        }
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
        for (did, holder) in crashed {
            tracing::error!(did = %did.0, "call actor died; resuming under a new generation");
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
        inner.wake.notify_waiters();
        let lease = lock(&inner.lease).clone();
        if let Some(lease) = lease {
            if let Err(error) = inner.ports.membership.set_draining(&lease).await {
                tracing::warn!(?error, "membership drain mark failed");
            }
        }
        let deadline = Instant::now() + grace;
        while self.live() > 0 && Instant::now() < deadline {
            let _ = tokio::time::timeout_at(deadline, inner.wake.notified()).await;
        }
        let leftover: Vec<(Did, Holder)> = lock(&inner.actors)
            .iter()
            .filter(|(_, actor)| !actor.task.is_finished())
            .map(|(did, actor)| (did.clone(), actor.holder.clone()))
            .collect();
        if leftover.is_empty() {
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
