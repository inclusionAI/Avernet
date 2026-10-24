//! Per-operation output permits for Workbench delivery
//! (bot-authority spec §8.2/§8.3 — 已准入输出不追溯).
//!
//! One accepted business operation installs, through
//! [`install_permits`], the read-only [`AdmittedRecipient`] view of
//! every candidate connection admitted AT ACCEPT TIME. Every later
//! frame of that operation is checked against the permit with a
//! pure predicate — no database read, no authority re-query, and
//! crucially NO retroactive revocation: a mid-operation transfer,
//! manager-edge removal, membership or scope change leaves the
//! already-admitted output flowing to its still-live connections.
//!
//! ## Stream lifecycle (Run terminal ≠ operation complete)
//!
//! One operation may drive SEVERAL output streams (a group send that
//! @-mentions multiple Bots produces one Run per target). Each
//! stream terminates on its own: its terminal frame stops THAT
//! stream's new frames; sibling streams of the same operation keep
//! flowing. The operation as a whole turns `Draining` only when
//! EVERY registered stream is terminal — the full stream set is
//! registered up front ([`register_operation_runs`], and run sinks
//! self-register via [`bind_run`]) BEFORE any downstream callback
//! can reference it, so a fast first terminal never retires an
//! operation whose siblings are still running. Registration is
//! idempotent and tolerates terminals observed BEFORE registration
//! (a managed worker may answer faster than the sender returns).
//!
//! ## Store lifecycle (prepared vs accepted)
//!
//! `Prepared` → `Accepted` → `Draining` → swept. A freshly installed
//! operation has neither frames nor streams; it becomes ACCEPTED on
//! its first frame or stream registration. Only prepared-but-never-
//! accepted entries (idempotent duplicates, preparations rolled back
//! by a later side-effect failure) expire on the bounded prepare-
//! timeout; an accepted operation is NEVER retired by a clock, full
//! stop — there is no age beyond which a queued or running
//! operation's permit becomes collectible, because the delivery
//! queue has no default expiry and pauses are legal (a legitimate
//! wait may outlast any fixed bound). Reclamation uses explicit
//! state only:
//!
//! - every registered stream terminal (`Draining` + drain grace);
//! - every recipient connection closed — permits keyed by dead
//!   instances can never deliver again and are swept immediately;
//! - every registered stream explicitly RELEASED by the application
//!   ([`release_operation_runs`]) after a delivery failure/rejection
//!   proved the stream will never produce output.
//!
//! A cancelled or crashed producer leaves runs that either complete
//! through the normal terminal machinery (provider run timeouts,
//! queue expiry when configured) or stay registered while their
//! recipients stay connected — bounded by `MAX_LIVE_OPERATIONS`
//! capacity refusal, which fails loudly and never evicts live
//! permits to make room.
//!
//! The store is process-local by design (spec §8.4): restarts and
//! leadership changes invalidate connections, and fresh connections
//! re-admit against current facts.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use bcs_service_api::application::v1::{AdmittedRecipient, DeliveryVisibility};
use bcs_service_api::port::OutputOperationAdmission;
use tokio::sync::RwLock;

/// A prepared admission that never accepted (no frame, no stream)
/// within this window is released (idempotent duplicates,
/// preparations rolled back by a later side-effect failure).
const PREPARED_PERMIT_TTL: Duration = Duration::from_secs(120);
/// After the operation completed (all streams terminal/released),
/// permits stay drainable for this grace and are then released.
const DRAINED_PERMIT_GRACE: Duration = Duration::from_secs(60);
/// Maximum simultaneously live operations holding permits. A full
/// store REFUSES new admissions (the operation proceeds permit-less,
/// logged); it never evicts running permits to make room.
const MAX_LIVE_OPERATIONS: usize = 10_000;
/// Sweep runs at most this often, driven lazily by store activity.
const SWEEP_INTERVAL: Duration = Duration::from_secs(15);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PermitInstallOutcome {
    Installed,
    /// The store is at capacity: the operation proceeds without
    /// permits (fail visible through the metric/log, never a silent
    /// eviction of running permits).
    RefusedCapacity,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum OperationState {
    Running,
    Draining,
}

#[derive(Debug)]
struct OperationEntry {
    recipients: HashMap<u64, AdmittedRecipient>,
    state: OperationState,
    /// The operation's expected output streams: union of the
    /// up-front [`OutputPermitStore::register_operation_runs`]
    /// registration and self-registered run sinks, minus streams
    /// explicitly released after a delivery failure. The operation
    /// completes when every remaining stream is terminal — and the
    /// moment a release empties the set (no stream can still
    /// produce output).
    registered_runs: HashSet<String>,
    /// Streams whose terminal frame was recorded (possibly BEFORE
    /// their registration — a fast callback may precede the sender's
    /// own bookkeeping) or which were explicitly released: both
    /// refuse any later frame for that stream.
    terminal_runs: HashSet<String>,
    /// Whether the operation accepted (produced a frame or owns a
    /// registered stream). Only NOT-yet-accepted preparations are
    /// subject to the prepare-timeout; accepted operations retire
    /// exclusively by explicit state (completion, recipient loss).
    accepted: bool,
    /// Whether ANY stream registration ever landed (bind or batch):
    /// completion may only be claimed after this, so a terminal
    /// tombstone arriving before ANY registration cannot drain an
    /// operation whose streams are still unknown.
    registered_any: bool,
    created_at: Instant,
    touched_at: Instant,
}

impl OperationEntry {
    /// The operation's output closes once SOME registration happened
    /// and every registered stream is terminal/released — an
    /// all-terminal set, or a set emptied BY RELEASES (nothing can
    /// still produce output). An operation with NO streams ever
    /// registered keeps accepting frames (its producer may still
    /// bind some), even when a terminal tombstone arrived early.
    fn completed(&self) -> bool {
        self.registered_any
            && self
                .registered_runs
                .iter()
                .all(|run| self.terminal_runs.contains(run))
    }

    fn into_draining(&mut self) {
        self.state = OperationState::Draining;
        self.touched_at = Instant::now();
    }
}

/// The shared per-operation permit store consumed by the fanout
/// registry, the run-channel manager and the output-admission port.
#[derive(Debug)]
pub struct OutputPermitStore {
    operations: RwLock<HashMap<String, OperationEntry>>,
    runs: RwLock<HashMap<String, String>>,
    last_sweep: RwLock<Instant>,
    frames_dropped_no_permit: AtomicU64,
    frames_dropped_after_terminal: AtomicU64,
    admissions_refused_capacity: AtomicU64,
    operations_reaped_stale: AtomicU64,
}

impl Default for OutputPermitStore {
    fn default() -> Self {
        Self::new()
    }
}

impl OutputPermitStore {
    pub fn new() -> Self {
        Self {
            operations: RwLock::new(HashMap::new()),
            runs: RwLock::new(HashMap::new()),
            last_sweep: RwLock::new(Instant::now()),
            frames_dropped_no_permit: AtomicU64::new(0),
            frames_dropped_after_terminal: AtomicU64::new(0),
            admissions_refused_capacity: AtomicU64::new(0),
            operations_reaped_stale: AtomicU64::new(0),
        }
    }

    pub fn frames_dropped_no_permit(&self) -> u64 {
        self.frames_dropped_no_permit.load(Ordering::Relaxed)
    }

    pub fn frames_dropped_after_terminal(&self) -> u64 {
        self.frames_dropped_after_terminal.load(Ordering::Relaxed)
    }

    pub fn admissions_refused_capacity(&self) -> u64 {
        self.admissions_refused_capacity.load(Ordering::Relaxed)
    }

    /// Accepted entries reclaimed because EVERY recipient was gone
    /// — an explicit end-state, never a clock and never live
    /// permits evicted for capacity.
    pub fn operations_reaped_stale(&self) -> u64 {
        self.operations_reaped_stale.load(Ordering::Relaxed)
    }

    pub async fn live_operation_count(&self) -> usize {
        self.operations.read().await.len()
    }

    /// Install the frozen per-connection permits of ONE accepted
    /// operation. Overwrites a same-id preparation (a retried
    /// producer creating the same operation identity); never merges
    /// recipients with a different operation id.
    pub async fn install_permits(
        &self,
        admission: &OutputOperationAdmission,
        recipients: Vec<AdmittedRecipient>,
    ) -> PermitInstallOutcome {
        self.sweep_if_due().await;
        let recipients: HashMap<u64, AdmittedRecipient> = recipients
            .into_iter()
            .map(|recipient| (recipient.connection_instance_id, recipient))
            .collect();
        let mut operations = self.operations.write().await;
        if !operations.contains_key(&admission.operation_id)
            && operations.len() >= MAX_LIVE_OPERATIONS
        {
            self.admissions_refused_capacity.fetch_add(1, Ordering::Relaxed);
            return PermitInstallOutcome::RefusedCapacity;
        }
        operations.insert(
            admission.operation_id.clone(),
            OperationEntry {
                recipients,
                state: OperationState::Running,
                registered_runs: HashSet::new(),
                terminal_runs: HashSet::new(),
                accepted: false,
                registered_any: false,
                created_at: Instant::now(),
                touched_at: Instant::now(),
            },
        );
        PermitInstallOutcome::Installed
    }

    /// Bind a concrete downstream Run to its admitted operation
    /// (called when the run is registered, before downstream I/O).
    /// Aliases resolve to the canonical run id upstream of this map.
    /// The binding also self-registers the stream: the operation
    /// completes when every registered stream reported terminal.
    pub async fn bind_run(&self, run_id: &str, operation_id: &str) {
        self.runs
            .write()
            .await
            .entry(run_id.to_string())
            .or_insert_with(|| operation_id.to_string());
        if let Some(entry) = self.operations.write().await.get_mut(operation_id) {
            self.register_stream_locked(entry, run_id);
        }
    }

    /// Register the operation's complete output-stream set (the
    /// application calls this once per operation, BEFORE any
    /// downstream callback can reference the runs). Idempotent union
    /// semantics; a terminal that already arrived for one of these
    /// streams (fast callback) is subtracted immediately, and the
    /// completion check runs ONCE against the whole set — never
    /// per entry, so an early terminal inside the batch cannot
    /// drain the operation prematurely.
    pub async fn register_operation_runs(&self, operation_id: &str, run_ids: &[String]) {
        let mut operations = self.operations.write().await;
        let Some(entry) = operations.get_mut(operation_id) else {
            return;
        };
        entry.accepted = true;
        entry.registered_any |= !run_ids.is_empty();
        entry.touched_at = Instant::now();
        for run_id in run_ids {
            entry.registered_runs.insert(run_id.clone());
        }
        if entry.state == OperationState::Running && entry.completed() {
            entry.into_draining();
        }
    }

    fn register_stream_locked(&self, entry: &mut OperationEntry, run_id: &str) {
        entry.accepted = true;
        entry.registered_any = true;
        entry.touched_at = Instant::now();
        entry.registered_runs.insert(run_id.to_string());
        if entry.state == OperationState::Running && entry.completed() {
            entry.into_draining();
        }
    }

    /// The operation a run was bound to, if any.
    pub async fn operation_of_run(&self, run_id: &str) -> Option<String> {
        self.runs.read().await.get(run_id).cloned()
    }

    /// Drop the run → operation binding (run unregistered). The
    /// operation's stream bookkeeping survives: a late terminal for
    /// an unbound run is still attributed (frames carry canonical
    /// run identity), and the operation's own permits survive for
    /// already-queued frames.
    pub async fn unbind_run(&self, run_id: &str) {
        self.runs.write().await.remove(run_id);
    }

    /// The per-connection permit of one operation, with the frame
    /// predicate already applied. Frames whose own stream is
    /// terminal (or whose operation is `Draining`) are rejected —
    /// post-terminal leakage for THAT stream only; sibling streams
    /// are undisturbed.
    pub async fn check_frame(
        &self,
        operation_id: &str,
        connection_instance_id: u64,
        visibility: &DeliveryVisibility,
        run_id: Option<&str>,
    ) -> bool {
        let mut operations = self.operations.write().await;
        let Some(entry) = operations.get_mut(operation_id) else {
            self.frames_dropped_no_permit.fetch_add(1, Ordering::Relaxed);
            return false;
        };
        if entry.state == OperationState::Draining {
            self.frames_dropped_after_terminal
                .fetch_add(1, Ordering::Relaxed);
            return false;
        }
        if run_id.is_some_and(|run| entry.terminal_runs.contains(run)) {
            self.frames_dropped_after_terminal
                .fetch_add(1, Ordering::Relaxed);
            return false;
        }
        let Some(permit) = entry.recipients.get(&connection_instance_id) else {
            self.frames_dropped_no_permit.fetch_add(1, Ordering::Relaxed);
            return false;
        };
        if !permit.allows(visibility) {
            return false;
        }
        entry.accepted = true;
        entry.touched_at = Instant::now();
        true
    }

    /// Record one STREAM's terminal frame: that stream stops
    /// accepting new frames; the operation turns `Draining` only
    /// when every registered stream is terminal. Idempotent, and
    /// valid before the stream's registration (fast callbacks).
    pub async fn mark_run_terminal(&self, operation_id: &str, run_id: &str) {
        let mut operations = self.operations.write().await;
        let Some(entry) = operations.get_mut(operation_id) else {
            return;
        };
        entry.touched_at = Instant::now();
        if entry.state == OperationState::Draining {
            return;
        }
        entry.terminal_runs.insert(run_id.to_string());
        if entry.completed() {
            entry.into_draining();
        }
    }

    /// Explicitly RELEASE streams the application proved will never
    /// produce output (a delivery failure or an admission-time
    /// rejection): the streams leave the registered set, keep a
    /// tombstone refusing any late frame, and the operation
    /// completes once nothing registered can still produce. This is
    /// an explicit end-state — never a time-based reclamation.
    pub async fn release_operation_runs(&self, operation_id: &str, run_ids: &[String]) {
        let mut operations = self.operations.write().await;
        let Some(entry) = operations.get_mut(operation_id) else {
            return;
        };
        entry.touched_at = Instant::now();
        if entry.state == OperationState::Draining {
            return;
        }
        let mut removed_stream = false;
        for run_id in run_ids {
            removed_stream |= entry.registered_runs.remove(run_id);
            entry.terminal_runs.insert(run_id.clone());
        }
        if removed_stream && entry.completed() {
            entry.into_draining();
        }
    }

    /// Legacy whole-operation close for a terminal frame that
    /// carries NO canonical stream identity. Production producers
    /// always tag their run; this path only exists so a
    /// stream-less terminal still closes its operation instead of
    /// leaking permits.
    pub async fn mark_terminal(&self, operation_id: &str) {
        if let Some(entry) = self.operations.write().await.get_mut(operation_id) {
            entry.into_draining();
        }
    }

    /// A closed/replaced connection loses every permit keyed to its
    /// instance; permits are never inherited across instances.
    pub async fn remove_connection(&self, connection_instance_id: u64) {
        let mut operations = self.operations.write().await;
        for entry in operations.values_mut() {
            entry.recipients.remove(&connection_instance_id);
        }
    }

    /// Lazily release entries by explicit end-state — never by any
    /// age or frame-activity clock:
    /// - completed (`Draining`) entries outlive the drain grace;
    /// - prepared-but-never-accepted entries outlive the prepare
    ///   timeout (duplicates, rolled-back sends);
    /// - accepted entries whose recipients are ALL gone can never
    ///   deliver again and are reclaimed immediately. Accepted
    ///   entries with live recipients are KEPT as long as it takes
    ///   (a legitimate queue wait or provider run has no bound the
    ///   permit layer may assume).
    async fn sweep_if_due(&self) {
        {
            let last_sweep = self.last_sweep.read().await;
            if last_sweep.elapsed() < SWEEP_INTERVAL {
                return;
            }
        }
        let mut last_sweep = self.last_sweep.write().await;
        if last_sweep.elapsed() < SWEEP_INTERVAL {
            return;
        }
        *last_sweep = Instant::now();
        drop(last_sweep);
        let mut operations = self.operations.write().await;
        let mut reaped_stale = 0u64;
        let mut removed: Vec<String> = Vec::new();
        operations.retain(|operation_id, entry| {
            let (keep, stale) = match entry.state {
                OperationState::Draining => {
                    (entry.touched_at.elapsed() < DRAINED_PERMIT_GRACE, false)
                }
                OperationState::Running if !entry.accepted => {
                    (entry.created_at.elapsed() < PREPARED_PERMIT_TTL, false)
                }
                OperationState::Running => (!entry.recipients.is_empty(), true),
            };
            if !keep {
                removed.push(operation_id.clone());
                reaped_stale += u64::from(stale);
            }
            keep
        });
        drop(operations);
        if reaped_stale > 0 {
            self.operations_reaped_stale
                .fetch_add(reaped_stale, Ordering::Relaxed);
        }
        if !removed.is_empty() {
            // A removed operation's run bindings die with it.
            let mut runs = self.runs.write().await;
            runs.retain(|_, operation_id| !removed.contains(operation_id));
        }
    }

    /// Test/diagnostic accessor: whether an operation entry exists.
    #[cfg(test)]
    pub async fn has_operation(&self, operation_id: &str) -> bool {
        self.operations.read().await.contains_key(operation_id)
    }
}

#[cfg(test)]
mod lifecycle_tests {
    use super::*;
    use bcs_domain::MessageVisibilityDomain;

    fn admission(operation_id: &str) -> OutputOperationAdmission {
        OutputOperationAdmission {
            operation_id: operation_id.to_string(),
            group_id: "group".to_string(),
            session_id: Some("session".to_string()),
        }
    }

    fn recipient(conn: u64) -> AdmittedRecipient {
        AdmittedRecipient {
            connection_instance_id: conn,
            view_actor_id: "human_user-a".to_string(),
            scope: bcs_service_api::types::MessageViewScope::Full,
            allow_legacy_unclassified_chat: true,
        }
    }

    fn chat_visibility() -> DeliveryVisibility {
        DeliveryVisibility {
            domain: MessageVisibilityDomain::Chat,
            audience: None,
        }
    }

    /// R4 probe: an admitted Run still waiting for its first frame
    /// (queue wait / slow provider) keeps its permit past the prepare
    /// timeout — acceptance, not first-frame age, gates reclamation.
    #[tokio::test]
    async fn admitted_waiting_run_survives_beyond_the_prepare_timeout() {
        let store = OutputPermitStore::new();
        store
            .install_permits(&admission("accepted-queued"), vec![recipient(1)])
            .await;
        store.bind_run("already-accepted-run", "accepted-queued").await;
        // Model 121 seconds of valid queue wait without sleeping.
        store
            .operations
            .write()
            .await
            .get_mut("accepted-queued")
            .expect("operation")
            .created_at -= Duration::from_secs(121);
        *store.last_sweep.write().await -= Duration::from_secs(16);
        // Trigger a real sweep via a second installation.
        store
            .install_permits(&admission("next-operation"), vec![recipient(2)])
            .await;
        assert!(
            store.has_operation("accepted-queued").await,
            "the accepted operation must not be swept by the prepare timeout"
        );
        assert!(
            store
                .check_frame("accepted-queued", 1, &chat_visibility(), Some("already-accepted-run"))
                .await,
            "the first frame of an admitted waiting Run must still be allowed"
        );
    }

    /// A prepared-but-never-accepted operation (idempotent duplicate,
    /// rolled-back preparation) still expires on the prepare timeout.
    #[tokio::test]
    async fn prepared_but_never_accepted_expires_on_the_prepare_timeout() {
        let store = OutputPermitStore::new();
        store
            .install_permits(&admission("duplicate-preparation"), vec![recipient(1)])
            .await;
        store
            .operations
            .write()
            .await
            .get_mut("duplicate-preparation")
            .expect("operation")
            .created_at -= Duration::from_secs(121);
        *store.last_sweep.write().await -= Duration::from_secs(16);
        store
            .install_permits(&admission("next-operation"), vec![recipient(2)])
            .await;
        assert!(
            !store.has_operation("duplicate-preparation").await,
            "an unused preparation expires on the prepare timeout"
        );
    }

    /// An operation whose recipients are ALL gone (every connection
    /// of the frozen set closed) can never deliver again and is
    /// reclaimed immediately — dead ends never occupy capacity.
    #[tokio::test]
    async fn accepted_operation_with_no_live_recipients_is_reclaimed() {
        let store = OutputPermitStore::new();
        store
            .install_permits(&admission("orphaned"), vec![recipient(7)])
            .await;
        store.bind_run("run-1", "orphaned").await;
        store.remove_connection(7).await;
        *store.last_sweep.write().await -= Duration::from_secs(16);
        store
            .install_permits(&admission("next-operation"), vec![recipient(8)])
            .await;
        assert!(
            !store.has_operation("orphaned").await,
            "an operation with zero live recipients is reclaimed even while Running"
        );
    }

    /// R1 probe (run sink shape): one stream's terminal stops THAT
    /// stream only; sibling streams continue until every registered
    /// stream reported terminal — THEN the operation closes.
    #[tokio::test]
    async fn one_stream_terminal_never_closes_sibling_streams() {
        let store = OutputPermitStore::new();
        store
            .install_permits(&admission("multi-run"), vec![recipient(1)])
            .await;
        store
            .register_operation_runs(
                "multi-run",
                &["run-a".to_string(), "run-b".to_string()],
            )
            .await;
        store.bind_run("run-a", "multi-run").await;
        store.bind_run("run-b", "multi-run").await;

        store.mark_run_terminal("multi-run", "run-a").await;
        assert!(
            !store
                .check_frame("multi-run", 1, &chat_visibility(), Some("run-a"))
                .await,
            "the terminal stream itself refuses further frames"
        );
        assert!(
            store
                .check_frame("multi-run", 1, &chat_visibility(), Some("run-b"))
                .await,
            "the sibling stream keeps flowing after run-a's terminal"
        );

        // A duplicate terminal is counted and refused gracefully.
        store.mark_run_terminal("multi-run", "run-b").await;
        store.mark_run_terminal("multi-run", "run-b").await;
        assert!(
            !store
                .check_frame("multi-run", 1, &chat_visibility(), Some("run-b"))
                .await,
            "with every registered stream terminal the operation is closed"
        );
        assert!(
            !store
                .check_frame("multi-run", 1, &chat_visibility(), None)
                .await,
            "a completed operation refuses even untagged frames"
        );
        assert!(store.frames_dropped_after_terminal() >= 1);
    }

    /// A terminal observed BEFORE the stream's registration (a fast
    /// managed callback) is subtracted at registration: completion is
    /// computed against the union, never against "currently nothing".
    #[tokio::test]
    async fn fast_terminal_before_registration_still_completes_correctly() {
        let store = OutputPermitStore::new();
        store
            .install_permits(&admission("fast-callback"), vec![recipient(1)])
            .await;
        // The worker's terminal lands BEFORE the sender's bookkeeping.
        store.mark_run_terminal("fast-callback", "run-a").await;
        assert!(
            store
                .check_frame("fast-callback", 1, &chat_visibility(), Some("run-b"))
                .await,
            "a terminal for one stream alone cannot complete the operation"
        );
        store
            .register_operation_runs(
                "fast-callback",
                &["run-a".to_string(), "run-b".to_string()],
            )
            .await;
        assert!(
            store.has_operation("fast-callback").await
                && store
                    .check_frame("fast-callback", 1, &chat_visibility(), Some("run-b"))
                    .await,
            "run-b is still open after registration"
        );
        store.mark_run_terminal("fast-callback", "run-b").await;
        assert!(
            !store
                .check_frame("fast-callback", 1, &chat_visibility(), Some("run-b"))
                .await,
            "the operation closes when the last registered stream terminates"
        );
    }

    /// R7 (recheck): an accepted operation with live recipients is
    /// NEVER reclaimed by age — a legitimate queue pause or slow
    /// first token has no bound the permit layer may assume.
    #[tokio::test]
    async fn live_accepted_operation_survives_beyond_any_age() {
        let store = OutputPermitStore::new();
        store
            .install_permits(&admission("long-queued"), vec![recipient(1)])
            .await;
        store.bind_run("queued-run", "long-queued").await;
        // Model >24h of legitimate queue wait without sleeping.
        store
            .operations
            .write()
            .await
            .get_mut("long-queued")
            .expect("operation")
            .created_at -= Duration::from_secs(86_401);
        // A frame lands successfully right before the sweep.
        assert!(
            store
                .check_frame("long-queued", 1, &chat_visibility(), Some("queued-run"))
                .await,
            "the operation is actively producing"
        );
        *store.last_sweep.write().await -= Duration::from_secs(16);
        store
            .install_permits(&admission("next-operation"), vec![recipient(2)])
            .await;
        assert!(
            store.has_operation("long-queued").await,
            "no age reclaims a live accepted operation"
        );
        assert!(
            store
                .check_frame("long-queued", 1, &chat_visibility(), Some("queued-run"))
                .await,
            "the next frame is still allowed"
        );
    }

    /// Explicit release after a delivery failure: the released
    /// stream is tombstoned at once; the operation closes when no
    /// registered stream can still produce output.
    #[tokio::test]
    async fn released_streams_are_tombstoned_and_close_the_operation() {
        let store = OutputPermitStore::new();
        store
            .install_permits(&admission("releasing"), vec![recipient(1)])
            .await;
        store
            .register_operation_runs(
                "releasing",
                &["run-a".to_string(), "run-b".to_string()],
            )
            .await;
        store
            .release_operation_runs("releasing", &["run-a".to_string()])
            .await;
        assert!(
            !store
                .check_frame("releasing", 1, &chat_visibility(), Some("run-a"))
                .await,
            "the released stream refuses frames immediately"
        );
        assert!(
            store
                .check_frame("releasing", 1, &chat_visibility(), Some("run-b"))
                .await,
            "the surviving stream is undisturbed"
        );
        // Releasing the last stream completes the operation.
        store
            .release_operation_runs("releasing", &["run-b".to_string()])
            .await;
        assert!(
            !store
                .check_frame("releasing", 1, &chat_visibility(), Some("run-b"))
                .await,
            "with nothing able to produce output the operation is closed"
        );
        assert!(
            !store
                .check_frame("releasing", 1, &chat_visibility(), None)
                .await,
            "even untagged frames stop once every stream is over"
        );
    }

    /// Completed operations outlive the drain grace and are then
    /// reclaimed — terminal drains never leak permits permanently.
    #[tokio::test]
    async fn completed_operations_expire_after_the_drain_grace() {
        let store = OutputPermitStore::new();
        store
            .install_permits(&admission("done"), vec![recipient(1)])
            .await;
        store.bind_run("run-1", "done").await;
        store.mark_run_terminal("done", "run-1").await;
        store
            .operations
            .write()
            .await
            .get_mut("done")
            .expect("operation")
            .touched_at -= Duration::from_secs(61);
        *store.last_sweep.write().await -= Duration::from_secs(16);
        store
            .install_permits(&admission("next-operation"), vec![recipient(2)])
            .await;
        assert!(
            !store.has_operation("done").await,
            "a completed operation is reclaimed after the drain grace"
        );
        assert!(
            store.operation_of_run("run-1").await.is_none(),
            "run bindings are purged with their operation"
        );
    }
}
