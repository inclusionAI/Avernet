//! The Connector's main loop and runtime control.
//!
//! ```text
//! receive → claim → ack → one actor per held call
//! ```
//!
//! A command entry is acknowledged only after the claim decision (claimed, or
//! nothing to do) (B2). The command stream is a hint: a repeated or late entry is
//! harmless, because the claim decides from the dispatch document (B19).
//!
//! Where this sits: the Worker writes a `disp` document and appends a hint to
//! the command stream. This engine reads hints, claims the dispatch, and hands
//! each held call to one actor (`crate::call::Call`), a spawned task that does
//! all the work with the agent. The engine itself never talks to the agent.
//!
//! Glossary:
//! - hint: a command-stream entry naming one dispatch (`did`); "go look at
//!   `disp:<did>`", never an instruction.
//! - ack: "the claim decision for this hint is made", not "the call is done".
//!   A hint that is not acked stays in the consumer group's pending list and
//!   another Connector (or this one) takes it over when it has been idle long
//!   enough (idle takeover).
//! - actor: the spawned task that runs one held call for one holder
//!   generation (holder epoch).
//! - poke: wake an actor so it re-reads its dispatch at once instead of at
//!   its next tick.
//! - resume: re-claim a call this process already holds under a new holder
//!   generation, because no actor runs it any more.
//! - orphan: a held call whose actor is gone and whose resume has not
//!   succeeded yet; retried on every main-loop pass.
//! - lease: this process's membership registration (who is alive). With the
//!   default stub membership there is none.
//! - partition owner: this process reads and runs the calls of the command
//!   partitions it owns (see [`partitions`]); a hint of another partition is
//!   acked without a claim.
//!
//! Locks: `actors`, `lease`, `orphans` and `ownership` are plain `std`
//! mutexes held only for a map/vector operation, never across an `.await`.

mod lifecycle;
mod partitions;
use lifecycle::{DrainExit, Exit, silence_heartbeat};
pub use partitions::rank_candidates;

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
use bcs_service_api::port::repo::distributed::ownership::{
    HandoffOutcome, HandoffRequest, MembershipLease, Renewal, RuntimeRole,
};
use bcs_service_api::types::dist_runtime::{DrainOutcome, RuntimeError, RuntimeSnapshot};
use std::collections::HashMap;
use std::num::NonZeroU32;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::sync::Notify;
use tokio::task::JoinHandle;
use tokio::time::Instant;

/// The engine's record of one running (or just finished) actor.
struct Actor {
    /// The holder `{instance, epoch}` the actor's claim created. Used to
    /// resume the call under a new generation if the actor dies, and to name
    /// the call on drain.
    holder: Holder,
    /// Flags the actor sets: `done` (returned normally) and `pending_effect`
    /// (something unsettled). Read by `reap` and `snapshot`.
    status: Arc<CallStatus>,
    /// Shared with the actor: `notify_one` here makes the actor tick at once.
    poke: Arc<Notify>,
    /// The spawned task. `is_finished()` is true once it returned, panicked
    /// or was aborted; `abort()` cancels it at its next `.await`.
    task: JoinHandle<()>,
}

/// State shared by every clone of the engine and by the spawned actors.
struct Inner {
    config: Arc<ConnectorConfig>,
    ports: ConnectorPorts,
    /// One entry per held call of this process, keyed by dispatch id. An entry
    /// may belong to a finished task until the next `reap` removes it, so
    /// "live" always also checks `task.is_finished()`.
    actors: Mutex<HashMap<Did, Actor>>,
    /// This process's membership registration. `Some` once `register`
    /// succeeded and never cleared; `None` when it never registered (stub
    /// membership, or a drain that came before the registration).
    lease: Mutex<Option<MembershipLease>>,
    /// Held calls whose actor died and whose resume has not succeeded yet.
    /// The `Holder` is the generation to resume from. Retried by `reap`,
    /// counted by `snapshot`, reported as leftovers by `drain`.
    orphans: Mutex<Vec<(Did, Holder)>>,
    /// Set by `drain`, never cleared: no new claims from now on.
    draining: AtomicBool,
    /// Registered and the command feed is open; cleared when the main loop
    /// returns. `snapshot` reports ready only while not draining as well.
    ready: AtomicBool,
    /// The main loop is running (it may be in the middle of a claim).
    looping: AtomicBool,
    /// Counter that makes every claim request id of this process unique (see
    /// `claim_request_id`). `Relaxed` is enough: only uniqueness matters, no
    /// other memory is ordered by it.
    claims: AtomicU64,
    /// "Something changed": an actor finished, the main loop exited, or a
    /// drain started. Sent with `notify_waiters`, which wakes only tasks
    /// waiting at that moment and stores no permit, so a wake-up can be
    /// missed. Its waiters (the main loop at capacity, `drain`) therefore
    /// also wait with a timeout.
    wake: Notify,
    /// Interrupts the main loop's wait for hints when a drain starts.
    /// Sent only by `drain`, with `notify_one`: if nobody waits yet, one
    /// permit is stored and the next wait (in the main loop or in
    /// `register`'s retry backoff) returns at once.
    stop: Notify,
    /// Serializes the end of `drain` (stop the leftover actors, hand the
    /// partitions over, retire). A second, concurrent drain must not retire
    /// while the first is still waiting for the actors it aborted: those may
    /// still act until then.
    stopping: tokio::sync::Mutex<()>,
    /// The partitions this process owns (see [`partitions`]).
    ownership: Mutex<partitions::Ownership>,
    /// Set by `drain`: every actor stops at its next safe point. Shared with
    /// the actors.
    yielding: Arc<AtomicBool>,
    /// The heartbeat task (`heartbeat`) found the registration lost; the main
    /// loop ends with `membership_lost`.
    lost: AtomicBool,
    /// The heartbeat must stop: the registration was retired, or `run`
    /// failed or was dropped (a stopped process must not look alive).
    quiet: AtomicBool,
    /// The heartbeat task; aborted when it must stop.
    heartbeat: Mutex<Option<JoinHandle<()>>>,
    /// Held (shared) by every claim the engine makes with a partition proof,
    /// for the whole claim; taken exclusively by `drain` before it hands the
    /// partitions over, so no claim checked against the old ownership can
    /// still write after the transfer.
    claim_gate: tokio::sync::RwLock<()>,
}

/// One Connector process: reads the command feed, claims dispatches and runs
/// one actor per held call.
///
/// Cloning is cheap and every clone is the same engine (shared `Inner`): one
/// clone runs `run`, another serves `snapshot`/`drain` through
/// `DistRuntimeControlPort`.
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
    /// Builds an engine that does nothing yet; `run` starts it. Refuses
    /// (`connector_config_invalid`) a zero capacity, poll interval, minimum
    /// backoff, ownership interval or peer timeout, each of which would make
    /// a loop spin or never claim.
    pub fn new(config: ConnectorConfig, ports: ConnectorPorts) -> Result<Self, RuntimeError> {
        if config.max_active_calls == 0
            || config.poll_interval.is_zero()
            || config.backoff_min.is_zero()
            || config.ownership_interval.is_zero()
            || config.peer_timeout.is_zero()
        {
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
                stopping: tokio::sync::Mutex::new(()),
                ownership: Mutex::new(partitions::Ownership::default()),
                yielding: Arc::new(AtomicBool::new(false)),
                lost: AtomicBool::new(false),
                quiet: AtomicBool::new(false),
                heartbeat: Mutex::new(None),
                claim_gate: tokio::sync::RwLock::new(()),
            }),
        })
    }

    /// Runs until `drain` is called or the membership registration is lost.
    /// Actors of held calls keep running after it returns (`drain` stops the
    /// ones still running at the end of its grace period).
    ///
    /// `Ok` after a drain; `Err` when the start fails (registration refused or
    /// not confirmed in time, command feed unavailable) or the registration is
    /// lost later (`membership_lost`). On the way out (also when the future
    /// is dropped) it clears `ready` and `looping` and wakes `drain`, which
    /// waits for `looping` to turn false.
    pub async fn run(&self) -> Result<(), RuntimeError> {
        self.inner.looping.store(true, Ordering::SeqCst);
        // `Exit` ends the run the same way on every path. It silences the
        // heartbeat unless the loop ended for a drain: a failed process, or
        // one whose `run` future was dropped (its task aborted) or panicked,
        // stops renewing, so it becomes `Dead` and its partitions and calls
        // move on. After a drain the heartbeat keeps going until the drain
        // retires the registration.
        let mut exit = Exit { inner: &self.inner, armed: true };
        let result = self.run_loop().await;
        exit.armed = result.is_err();
        drop(exit);
        result
    }

    /// The main loop. Start: register membership, open the command feed,
    /// mark ready. Then each pass:
    /// 1. stop if draining; end with `membership_lost` if the heartbeat
    ///    found the registration lost (it renews on its own task, so slow
    ///    store or peer I/O here never lets the registration expire);
    /// 2. every `ownership_interval` (and on the first pass): the ownership
    ///    round, then point the feed at the owned partitions if they changed;
    /// 3. `reap` finished and dead actors and retry orphans;
    /// 4. at capacity, read no hints: wait for an actor to finish (or
    ///    `receive_timeout`) and start over. The hints of this process's
    ///    partitions wait in their streams meanwhile: only the owner reads a
    ///    partition, and a live owner is not displaced (no rebalancing);
    /// 5. otherwise read up to `free` hints, interruptible by `stop`;
    /// 6. handle each hint (claim, act, ack); on a read error, back off.
    ///
    /// A `?` here (register, open feed) returns the error from `run`: the
    /// process does not claim anything then.
    async fn run_loop(&self) -> Result<(), RuntimeError> {
        let inner = &self.inner;
        let config = inner.config.clone();
        // Returns `Ok` without a lease for the stub membership, and `Ok` when
        // a drain started meanwhile (the draining check below then stops).
        self.register().await?;
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
        self.start_heartbeat();
        let mut owned_at: Option<Instant> = None;
        let mut assigned: Option<u64> = None;
        let mut backoff = Backoff::new(&config);
        loop {
            if inner.draining.load(Ordering::SeqCst) {
                return Ok(());
            }
            if inner.lost.load(Ordering::SeqCst) {
                return Err(failed("membership_lost"));
            }
            if owned_at.is_none_or(|at| at.elapsed() >= config.ownership_interval) {
                owned_at = Some(Instant::now());
                self.own_partitions().await;
            }
            // Also after an accepted handoff, which changes ownership from
            // the control path between rounds.
            let (version, owned) = self.owned_partitions();
            if assigned != Some(version) {
                assigned = Some(version);
                feed.assign(&owned);
            }
            self.reap().await;
            let free = config.max_active_calls.saturating_sub(self.live());
            // `let … else`: when `free` is zero (or does not fit a u32) the
            // `else` block runs and must leave this pass with `continue`.
            let Some(free) = u32::try_from(free).ok().and_then(NonZeroU32::new) else {
                // At capacity: read no hints; they wait in the streams.
                // An actor that ends sends `wake`; the timeout covers a missed
                // wake-up and keeps the drain and membership checks going.
                let _ = tokio::time::timeout(config.receive_timeout, inner.wake.notified()).await;
                continue;
            };
            // `select!` runs both futures and drops the one that did not
            // finish. When `stop` wins, the `receive` future is dropped
            // mid-way and `continue` goes back to the draining check.
            // Dropping the wait is safe: the feed keeps its in-flight reads.
            let received = tokio::select! {
                received = feed.receive(free, config.receive_timeout) => received,
                _ = inner.stop.notified() => continue,
            };
            match received {
                Ok(deliveries) => {
                    // A good read ends an outage: the next error starts again
                    // from the shortest backoff.
                    backoff = Backoff::new(&config);
                    for delivery in deliveries {
                        if inner.draining.load(Ordering::SeqCst) {
                            // Not acked: another Connector takes it over.
                            // The unhandled entries stay pending under this
                            // consumer until their idle takeover.
                            break;
                        }
                        self.handle(feed.as_mut(), delivery).await;
                    }
                }
                Err(error) => {
                    tracing::warn!(?error, "command feed read failed");
                    // The heartbeat keeps the registration alive meanwhile;
                    // a drain or a lost registration cuts the wait short.
                    // The backoff doubles (200 ms up to 5 s by default).
                    tokio::select! {
                        () = backoff.wait() => {}
                        () = inner.stop.notified() => {}
                    }
                }
            }
        }
    }

    /// The claim decision for one hint, then the ack.
    ///
    /// Claim → act → ack, in that order (B2):
    /// - `Acquired`: this process now holds the call; start an actor.
    /// - `OwnedByMe`: this process already holds it. Poke the live actor (the
    ///   hint usually means a reply or a cancel was written); with no live
    ///   actor, resume the call under a new generation instead.
    /// - `HeldByOther`, `Ended`, `NotFound`: nothing to do here. A hint can
    ///   land on a Connector that does not hold the call, because the
    ///   consumer group hands each entry to whichever consumer reads first.
    ///   The owner still sees the reply or cancel at its next tick (B14).
    ///
    /// Every `Ok` outcome is acked. An `Err` is not: the hint stays pending
    /// and comes back through idle takeover, here or on another Connector.
    /// A hint that fails every time is therefore retried forever.
    async fn handle(&self, feed: &mut dyn CommandFeedPort, delivery: CommandDelivery) {
        let did = delivery.did.clone();
        if let Err(error) = self.decide(&did).await {
            // No decision, no ack: the hint stays pending and comes back.
            tracing::warn!(did = %did.0, ?error, "claim failed; hint left pending");
            return;
        }
        // A failed ack only means the hint comes back later; the claim
        // decision is in the dispatch document, so handling it again is
        // harmless (B19).
        if let Err(error) = feed.ack(&delivery).await {
            tracing::warn!(did = %did.0, ?error, "command ack failed; the hint will be redelivered");
        }
    }

    /// Claims `did` as the owner of its partition and acts on the outcome
    /// (see `handle`). A call of a partition this process does not own is
    /// left alone: its owner reads its hints (a read started before the
    /// partition moved can still deliver one here). `NotOwner` means the
    /// partition moved meanwhile: it is dropped until the next round.
    async fn decide(&self, did: &Did) -> Result<(), StoreError> {
        let inner = &self.inner;
        // Read after the gate, so a drain that handed the partition over
        // (holding the gate) leaves no proof behind for this claim.
        let _claiming = inner.claim_gate.read().await;
        if inner.draining.load(Ordering::SeqCst) {
            // Nothing new is taken on while draining; the partition's next
            // owner gets the re-posted hint.
            return Ok(());
        }
        let Some(proof) = self.proof_for(did) else {
            return Ok(());
        };
        let partition = proof.shard.0;
        let request = DispatchClaimRequest {
            did: did.clone(),
            me: inner.config.me.clone(),
            request_id: self.claim_request_id(did),
            partition: Some(proof),
        };
        match inner.ports.queue.claim(request).await? {
            ClaimOutcome::Acquired(held) => self.spawn(held).await,
            ClaimOutcome::OwnedByMe(holder) => {
                if !self.poke(did) {
                    // Held by this process but no actor runs it (it died, or
                    // a claim answer was lost): take a new generation.
                    self.resume(did.clone(), holder).await;
                }
            }
            ClaimOutcome::NotOwner => lock(&inner.ownership).lose(partition),
            ClaimOutcome::HeldByOther(_) | ClaimOutcome::Ended | ClaimOutcome::NotFound => {}
        }
        Ok(())
    }

    /// A request id unique to one claim or resume attempt of this process:
    /// `<node>:<incarnation>:<did>:<n>`, for example `c01-7:1794370950:d-42:17`,
    /// where `n` counts the attempts of this process. It names the attempt
    /// in the claim/resume request; the store in this repository does not
    /// read it today.
    fn claim_request_id(&self, did: &Did) -> RequestId {
        let me = &self.inner.config.me;
        let n = self.inner.claims.fetch_add(1, Ordering::Relaxed);
        RequestId(format!("{}:{}:{}:{n}", me.node, me.incarnation, did.0))
    }

    /// Refreshes the actor of `did` now; `false` when no live actor runs it.
    ///
    /// `notify_one` stores one permit if the actor is busy, so several pokes
    /// before it looks collapse into one extra tick. The caller resumes the
    /// call when this returns `false`.
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

    /// An actor for `did` exists and its task has not finished.
    fn running(&self, did: &Did) -> bool {
        lock(&self.inner.actors).get(did).is_some_and(|actor| !actor.task.is_finished())
    }

    /// Resumes a held call that has no actor. A failure is remembered and
    /// retried by the main loop, so the call is never silently left behind.
    ///
    /// `resume_owned` re-claims the call at holder epoch + 1, so any late
    /// write of the old actor is fenced. Outcomes:
    /// - `Acquired`: start an actor for the new generation;
    /// - `OwnedByMe`: an earlier resume already landed (its answer was lost);
    ///   remember that generation as an orphan, `reap` resumes it next pass;
    /// - anything else (ended, missing, held by another instance): nothing
    ///   left to do here;
    /// - a permanent store error: give up, logged;
    /// - any other error: remember `previous` as an orphan and retry.
    async fn resume(&self, did: Did, previous: Holder) {
        // Only after the old actor is gone: stop and join it first.
        // `abort` only requests cancellation (at the task's next `.await`);
        // awaiting the handle waits until the task is really gone, so the old
        // actor cannot call the agent or append to the inbox after this.
        // The `Err` the join returns for an aborted task is expected.
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

    /// Starts the actor of a freshly claimed call (`held` proves the claim)
    /// and records it in `actors`.
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
            inner.yielding.clone(),
        );
        let wake = self.inner.clone();
        // Checked under the `actors` lock, in the same critical section as
        // the insert: `drain` sets `draining` before it collects the actors
        // under this lock, so either it sees this actor and stops it, or
        // this sees `draining` and starts nothing. A claim or resume that
        // wins after the drain began therefore never runs here; its call
        // stays held and the partition's next owner takes it over, like the
        // calls of the actors `drain` stops.
        let mut actors = lock(&self.inner.actors);
        if self.inner.draining.load(Ordering::SeqCst) {
            tracing::info!(did = %did.0, "draining; the call is left held for another Connector");
            return;
        }
        // The `wake` is sent inside the task, after `run` returned: it tells a
        // main loop waiting at capacity, or a drain, that an actor ended. A
        // task that is aborted or panics never reaches it; those waiters time
        // out instead.
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
        actors.insert(did.clone(), actor);
        drop(actors);
        // Whatever resume was still owed for this call is settled by this actor.
        // (`retain` keeps the entries for which the test is true, i.e. it
        // drops this call's orphan entries.)
        lock(&self.inner.orphans).retain(|(orphan, _)| orphan != &did);
    }

    /// Forgets finished actors; one that died without finishing its run is
    /// resumed under a new holder generation.
    ///
    /// Runs on every main-loop pass. "Died" = the task finished but
    /// `CallStatus.done` is false: it panicked or was aborted, so the call is
    /// still held with nobody running it. Earlier failed resumes (`orphans`)
    /// are retried in the same pass.
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
            // No effect: a leftover that only marks `holder` as used.
            let _ = holder;
        }
        // Earlier failed resumes are tried again together with the new ones.
        let mut pending = std::mem::take(&mut *lock(&self.inner.orphans));
        pending.extend(crashed);
        for (did, holder) in pending {
            // Stale: the call was resumed meanwhile (a hint, or an earlier
            // entry of this list). A running actor is never replaced here.
            if self.running(&did) {
                continue;
            }
            self.resume(did, holder).await;
        }
    }

    /// Number of actors whose task has not finished: the calls that count
    /// against `max_active_calls`. Orphans do not count here.
    fn live(&self) -> usize {
        lock(&self.inner.actors)
            .values()
            .filter(|actor| !actor.task.is_finished())
            .count()
    }

    /// Registers this instance before anything is claimed.
    ///
    /// Only the answer of the "no provider wired" stub lets the Connector run
    /// unregistered. A refusal (for example an invalid control address) fails
    /// the start. Any other failure may have applied the registration, so it
    /// is asked again (an identical registration is idempotent) until it
    /// answers: claiming without a lease that is renewed would let the
    /// registration expire under running calls. The retries end with the
    /// registration's lifetime: an applied registration nobody renewed has
    /// expired by then and this incarnation cannot register again.
    ///
    /// Outcomes, in short:
    /// - `Ok`, lease stored: registered (renewed at once if it took retries);
    /// - `Ok`, no lease: stub membership (`need_contract_not_implemented`),
    ///   or a drain started before the registration answered;
    /// - `Err(membership_registration_refused)`: a permanent refusal;
    /// - `Err(membership_lost)`: not confirmed within `membership_ttl`, or the
    ///   catch-up renewal found it lost.
    ///
    /// A drain interrupts the retry wait through `stop`.
    async fn register(&self) -> Result<(), RuntimeError> {
        let inner = &self.inner;
        let config = &inner.config;
        let mut backoff = Backoff::new(config);
        let started = Instant::now();
        let mut retried = false;
        loop {
            if inner.draining.load(Ordering::SeqCst) {
                return Ok(());
            }
            let registered = inner
                .ports
                .membership
                .register(
                    config.me.clone(),
                    RuntimeRole::Connector,
                    config.control_address.clone(),
                    config.membership_ttl,
                )
                .await;
            match registered {
                Ok(lease) => {
                    *lock(&inner.lease) = Some(lease.clone());
                    // A drain that arrived while this call was in flight saw
                    // no lease to mark: mark it now. Checked after the lease
                    // is stored, so either `drain` sees the lease or this
                    // check sees `draining`; the registration cannot miss
                    // the mark.
                    if inner.draining.load(Ordering::SeqCst) {
                        if let Err(error) = inner.ports.membership.set_draining(&lease).await {
                            tracing::warn!(?error, "could not mark the registration as draining");
                        }
                        return Ok(());
                    }
                    // A registration confirmed late keeps the lifetime of its
                    // first attempt: renew at once instead of a period later.
                    // (An identical retry is idempotent and does not restart
                    // the lifetime of an earlier attempt that landed.)
                    if retried && !self.renew().await {
                        return Err(failed("membership_lost"));
                    }
                    return Ok(());
                }
                Err(StoreError::NotApplied { code }) if code == crate::delayed::NEED_CONTRACT => {
                    tracing::warn!("no membership provider wired; running without a registration");
                    return Ok(());
                }
                Err(error @ (StoreError::Invalid { .. } | StoreError::UnsupportedFormat { .. })) => {
                    tracing::error!(?error, "membership registration refused");
                    return Err(failed("membership_registration_refused"));
                }
                Err(error) => {
                    if started.elapsed() >= config.membership_ttl {
                        tracing::error!(?error, "membership registration not confirmed within its lifetime");
                        return Err(failed("membership_lost"));
                    }
                    tracing::warn!(?error, "membership registration failed; retrying before any claim");
                    retried = true;
                }
            }
            // Retry wait; a drain's `stop` cuts it short so the draining
            // check at the top of the loop returns promptly.
            tokio::select! {
                () = backoff.wait() => {}
                () = inner.stop.notified() => {}
            }
        }
    }

    /// Renews the registration every `membership_renew` on its own task, so
    /// no I/O of the main loop or of a drain (ownership rounds, recovery,
    /// handoffs) can delay a renewal. Ends when the registration is retired,
    /// when `run` failed, or when a renewal finds it lost (then the main loop
    /// is told through `lost` and `stop`). Without a registration (stub
    /// membership) there is nothing to renew.
    fn start_heartbeat(&self) {
        if lock(&self.inner.lease).is_none() {
            return;
        }
        let engine = self.clone();
        let task = tokio::spawn(async move {
            let inner = &engine.inner;
            loop {
                tokio::time::sleep(inner.config.membership_renew).await;
                if inner.quiet.load(Ordering::SeqCst) {
                    return;
                }
                if !engine.renew().await {
                    inner.lost.store(true, Ordering::SeqCst);
                    inner.stop.notify_one();
                    inner.wake.notify_waiters();
                    return;
                }
            }
        });
        *lock(&self.inner.heartbeat) = Some(task);
        // Silenced before it was stored (a racing `retire`): stop it now.
        if self.inner.quiet.load(Ordering::SeqCst) {
            silence_heartbeat(&self.inner);
        }
    }

    /// `false` when the registration is definitely lost: the process must
    /// stop claiming and restart under a new incarnation.
    ///
    /// `true` means "no reason to stop", not "renewed": it is also the answer
    /// for a temporary error (the next renewal tries again) and when there is
    /// no lease to renew (`None`: stub membership or never registered).
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

    /// Ends this instance's registration for good (drain only). Afterwards
    /// liveness answers `Dead` for it at once, instead of after the lifetime
    /// runs out. No registration (stub membership, or a drain before
    /// registering): nothing to do. A failure is only logged: the
    /// registration then expires on its own, because nothing renews it.
    async fn retire(&self) {
        silence_heartbeat(&self.inner);
        let Some(lease) = lock(&self.inner.lease).clone() else {
            return;
        };
        if let Err(error) = self.inner.ports.membership.retire(&lease).await {
            tracing::warn!(?error, "membership retire failed; the registration expires on its own");
        }
    }
}

/// Locks a `std` mutex, ignoring poisoning. A mutex is poisoned when a thread
/// panicked while holding it; the data here (maps and vectors changed in one
/// step) stays usable, so the engine keeps going instead of panicking too.
fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[async_trait]
impl DistRuntimeControlPort for ConnectorEngine {
    /// A draining peer offers one of its partitions (see
    /// `partitions::accept`). The caller is bound by the application layer.
    async fn accept_handoff(&self, request: HandoffRequest) -> Result<HandoffOutcome, RuntimeError> {
        self.accept(request).await
    }

    /// Health of this process: `active_work` = live actors plus orphans,
    /// `pending_effects` = live actors with `pending_effect` set plus orphans.
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        let inner = &self.inner;
        // One pass over the live actors counts both numbers: `fold` carries
        // the pair `(active, pending)` and adds 1 and 0/1 per actor.
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

    /// Stops claiming, lets held calls reach a safe point, hands the owned
    /// partitions to peers, then retires the registration (B20).
    ///
    /// Steps: mark draining, interrupt the main loop (`stop`), mark the
    /// registration as draining if there is one, and ask every actor to stop
    /// at its next safe point (`yielding`, plus a poke). Then wait (the
    /// heartbeat keeps the registration alive) until the main loop has left
    /// and no actor is live, but only until `handoff_reserve` (at most half
    /// the grace) before the deadline.
    ///
    /// Then every actor still running is aborted and awaited, so no call of
    /// this process talks to its agent or session any more. No new actor
    /// starts once draining (`spawn` refuses), so this also holds for a claim
    /// or resume still in flight. Claims still in flight are waited for
    /// (`claim_gate`); only then is each owned partition offered
    /// to a peer: the peer that takes it owns it at once and takes its calls
    /// over (they stay held by this process until then). Finally the
    /// registration is retired: this instance is `Dead` at once, and
    /// partitions not handed over are claimed by others.
    ///
    /// Result: `Drained` when every call stopped at a safe point, every
    /// partition was handed over and the main loop has stopped; otherwise
    /// `DeadlineReached { remaining }` with the actors aborted, orphans and
    /// partitions not handed over (`remaining` can be 0 when only the main
    /// loop had not stopped yet, for example while it waits on a claim).
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        let inner = &self.inner;
        // A drain cancelled before it retires must not leave the heartbeat
        // keeping a process alive that reads nothing (see `DrainExit`).
        let mut exit = DrainExit { engine: self.clone(), done: false };
        inner.draining.store(true, Ordering::SeqCst);
        inner.stop.notify_one();
        inner.wake.notify_waiters();
        let lease = lock(&inner.lease).clone();
        if let Some(lease) = lease {
            if let Err(error) = inner.ports.membership.set_draining(&lease).await {
                tracing::warn!(?error, "membership drain mark failed");
            }
        }
        inner.yielding.store(true, Ordering::SeqCst);
        for actor in lock(&inner.actors).values() {
            actor.poke.notify_one();
        }
        let deadline = Instant::now() + grace;
        // Renewals already stopped (the main loop failed or was dropped
        // before this drain): this instance is about to read `Dead`, so its
        // calls are stopped at once and nothing is offered (a peer's store
        // needs a live source).
        let silenced = inner.quiet.load(Ordering::SeqCst);
        let calls_until = if silenced {
            Instant::now()
        } else {
            deadline - inner.config.handoff_reserve.min(grace / 2)
        };
        // First the main loop must leave its iteration: a claim in flight may
        // still take a call. Then the held calls get the rest of the grace.
        // Short waits instead of one long one: a wake-up can be missed.
        // The heartbeat keeps renewing meanwhile (until `retire`), so other
        // Connectors cannot take over calls whose actors still run here.
        let step = Duration::from_millis(20);
        while (inner.looping.load(Ordering::SeqCst) || self.live() > 0) && Instant::now() < calls_until {
            let _ = tokio::time::timeout(step, inner.wake.notified()).await;
        }
        // Whatever still runs is stopped here, not left behind: once the
        // registration is gone, another Connector may take these calls over,
        // and the dispatch fences only this actor's `disp` writes, not its
        // agent calls or inbox writes.
        // One drain at a time from here (see `stopping`).
        let _stopping = inner.stopping.lock().await;
        // The set collected below is complete: `draining` is already set,
        // and `spawn` starts no actor once it is (it checks under the same
        // lock). So retiring after stopping them is safe even if the main
        // loop is still inside a claim; that claim's call is left held.
        let stopped = self.stop_actors().await;
        // No claim made under the current ownership may still be running
        // when a partition moves: it could take a call back from the new
        // owner. Wait for claims in flight (the main loop may be inside one);
        // if one does not end in time, hand nothing over: the partitions
        // then move only once this instance is `Dead`.
        let gate_until = deadline.checked_sub(inner.config.peer_timeout).unwrap_or(deadline);
        let left = match tokio::time::timeout_at(gate_until, inner.claim_gate.write()).await {
            Ok(_) if silenced => self.owned_partitions().1.len(),
            Ok(_quiet_claims) => self.hand_off(deadline).await,
            Err(_) => {
                tracing::warn!("a claim is still running; partitions are not handed over");
                self.owned_partitions().1.len()
            }
        };
        let remaining = stopped + lock(&inner.orphans).len() + left;
        self.retire().await;
        exit.done = true;
        if remaining == 0 && !inner.looping.load(Ordering::SeqCst) {
            return Ok(DrainOutcome::Drained);
        }
        Ok(DrainOutcome::DeadlineReached {
            remaining: remaining as u64,
        })
    }
}
