//! Engine settings and the ports it runs on.
//!
//! [`ConnectorConfig`] is plain data built by the composition root (nothing
//! is read from the environment here). [`ConnectorPorts`] is every outside
//! system the engine and its actors talk to, as trait objects, so tests and
//! the local stack swap in other implementations without code changes.
//! `Backoff` is the "sleep, then retry in place" helper used for store
//! writes that must complete before an actor can go on.
//!
//! Defaults at a glance (see [`ConnectorConfig::new`]):
//!
//! ```text
//! max_active_calls   1000       poll_interval     1.5 s (±20 % per call)
//! receive_timeout    1 s        max_inline_bytes  64 KiB
//! backoff_min/max    200 ms / 5 s (doubling)
//! not_sent_retries   3          membership_ttl/renew  3 s / 1 s
//! control_address    "" (no control endpoint yet)
//! ```

use bcs_dist_model::{Digest, Did, DownstreamCapabilities, Instance, PoolId};
use bcs_service_api::core::dist_connector::DistConnectorCoreService;
use bcs_service_api::port::dist_agent::DistAgentClientPort;
use bcs_service_api::port::repo::distributed::dispatch::DispatchQueuePort;
use bcs_service_api::port::repo::distributed::inbox::InboxProducerPort;
use bcs_service_api::port::repo::distributed::ownership::MembershipPort;
use bcs_service_api::port::repo::distributed::result::BlobStorePort;
use std::sync::Arc;
use std::time::Duration;

/// Wall-clock source for deadline and window decisions. Injected so tests
/// stay deterministic; production reads the system clock.
pub trait WallClock: Send + Sync {
    fn now(&self) -> i64;
}

/// The production clock.
pub struct SystemWallClock;

impl WallClock for SystemWallClock {
    fn now(&self) -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|since| since.as_millis() as i64)
            .unwrap_or_default()
    }
}

/// Settings of one Connector process. Nothing is read from the environment.
#[derive(Clone)]
pub struct ConnectorConfig {
    /// This process: node name and incarnation (process start time, epoch ms).
    /// Together they are the holder `instance` written into every claim, and
    /// the consumer name `<node>:<incarnation>` in the command-stream group.
    pub me: Instance,
    /// The command-stream pool this Connector serves.
    pub pool: PoolId,
    /// Calls held at once (live actors). At the limit the engine reads no
    /// hints at all, so other Connectors take them: pull-based load balancing.
    pub max_active_calls: usize,
    /// How often a held call's dispatch is re-read for replies and cancels
    /// (the actor's tick, B14). Each call uses its own value spread over
    /// ±20 % (`ConnectorConfig::poll_interval_of`). Must not be zero.
    pub poll_interval: Duration,
    /// Longest wait of one read of the command feed. It also bounds how long
    /// the main loop waits at capacity before it looks again.
    pub receive_timeout: Duration,
    /// Event payloads above this size (serialized JSON bytes) are stored as
    /// blobs and reported by reference.
    pub max_inline_bytes: usize,
    /// Backoff of safe retries: starts here, doubles up to `backoff_max`.
    /// Used both by `Backoff` (sleep and retry in place) and by an actor's
    /// tick backoff ("not before time X"). Must not be zero.
    pub backoff_min: Duration,
    /// Upper bound of one backoff wait (default 5 s).
    pub backoff_max: Duration,
    /// Bound of repeated attempts whose outcome stays bad: a send that
    /// definitely did not leave the process (B16), a resend of an idempotent
    /// start or input whose outcome stays unknown, queries by request id, and
    /// reads of a task whose cancel answered "already finished". After it a
    /// send is left unresolved, and a finished task is reported from the
    /// cancel answer alone.
    pub not_sent_retries: u32,
    /// Verifier concurrency: handed-off calls this process verifies at
    /// once (plan T7). Independent of `max_active_calls`: a handed-off
    /// call frees its normal slot, and verification slots never block a
    /// normal claim.
    pub max_verifications: usize,
    /// How often the engine looks for due verification work (plan T7):
    /// the lane runs at its own low frequency.
    pub verification_interval: Duration,
    /// First backoff between two verification attempts of one call.
    pub verification_backoff_min: Duration,
    /// Ceiling of the verification backoff.
    pub verification_backoff_max: Duration,
    /// Lifetime of this process's membership record: without a renewal
    /// within it the record expires. A registration that
    /// is not confirmed within it fails the start-up.
    pub membership_ttl: Duration,
    /// How often the membership record is renewed; well below the TTL.
    pub membership_renew: Duration,
    /// URL where other processes reach this process's control endpoint;
    /// stored in the member record at registration. Empty until a control
    /// endpoint exists; the real membership provider refuses an empty or
    /// non-http value (today the Connector runs with the stub membership).
    pub control_address: String,
    /// Wall-clock source for deadline and dedupe-window decisions.
    pub clock: Arc<dyn WallClock>,
}

impl ConnectorConfig {
    /// The defaults listed in the module docs.
    pub fn new(me: Instance, pool: PoolId) -> Self {
        Self {
            me,
            pool,
            max_active_calls: 1000,
            poll_interval: Duration::from_millis(1500),
            receive_timeout: Duration::from_secs(1),
            max_inline_bytes: 65_536,
            backoff_min: Duration::from_millis(200),
            backoff_max: Duration::from_secs(5),
            not_sent_retries: 3,
            max_verifications: 8,
            verification_interval: Duration::from_secs(2),
            verification_backoff_min: Duration::from_secs(2),
            verification_backoff_max: Duration::from_secs(60),
            membership_ttl: Duration::from_secs(3),
            membership_renew: Duration::from_secs(1),
            control_address: String::new(),
            clock: Arc::new(SystemWallClock),
        }
    }

    /// The digest of the send configuration in force for one dispatch
    /// (plan T6): the collision of pool, retry policy, inline budget and
    /// the target's declared capabilities that shaped every attempt of
    /// the call. A changed valid configuration voids a dedupe proof.
    pub fn send_fingerprint(&self, capabilities: &DownstreamCapabilities) -> Digest {
        let flags = [
            capabilities.idempotent_start,
            capabilities.idempotent_input,
            capabilities.query_by_request,
            capabilities.query_by_task,
            capabilities.subscribe,
            capabilities.resume_events,
            capabilities.cancel,
            capabilities.isolated_memory,
            capabilities.persistent_dedupe,
        ]
        .iter()
        .map(|flag| if *flag { b'1' } else { b'0' } as u8)
        .collect::<Vec<_>>();
        bcs_dist_encoding::digest_fields(&[
            b"send-config",
            self.pool.0.as_bytes(),
            &self.not_sent_retries.to_be_bytes(),
            &self.max_inline_bytes.to_be_bytes(),
            &flags,
        ])
    }

    /// The poll interval of one call, spread over ±20 % by its id so that
    /// calls claimed together do not all poll at the same instant. The spread
    /// is a fixed hash of the dispatch id, so one call always gets the same
    /// interval, on every holder.
    pub(crate) fn poll_interval_of(&self, did: &Did) -> Duration {
        let spread = did.0.bytes().fold(0u32, |acc, b| acc.wrapping_mul(31).wrapping_add(u32::from(b))) % 401;
        // 800..=1200 thousandths of the configured interval.
        self.poll_interval.mul_f64(f64::from(800 + spread) / 1000.0)
    }
}

/// Everything the engine reaches the outside through. Cloning copies the
/// `Arc`s only; every actor gets its own clone.
#[derive(Clone)]
pub struct ConnectorPorts {
    /// The command stream (hints) and the `disp` documents: claim, resume,
    /// ack, and the per-call holder handle.
    pub queue: Arc<dyn DispatchQueuePort>,
    /// The session inbox: key events are admitted, intermediate events
    /// appended.
    pub inbox: Arc<dyn InboxProducerPort>,
    /// The downstream agents (A2A): start, send input, query, subscribe,
    /// cancel.
    pub agent: Arc<dyn DistAgentClientPort>,
    /// Registration of this process and proof of death of others: the
    /// composition injects the real provider (`CoordinationStore`); without
    /// one, the stub `PendingMembership` answers `need_contract_not_implemented`.
    pub membership: Arc<dyn MembershipPort>,
    /// Immutable blobs: large event payloads, and requests or replies the
    /// Worker stored by reference.
    pub blobs: Arc<dyn BlobStorePort>,
    /// The pure rules; normally [`crate::rules::DistConnectorRules`].
    pub rules: Arc<dyn DistConnectorCoreService>,
}

/// Exponential backoff between safe retries: the caller sleeps and retries
/// the same operation in place (200 ms, 400 ms, … up to 5 s by default). Used
/// where nothing else may happen until the operation succeeds, such as a
/// holder write. A fresh value starts again at `backoff_min`.
///
/// Not to be confused with an actor's tick backoff (`Call::retry_after`),
/// which never sleeps: it only makes later ticks skip a remote retry until a
/// point in time, so the actor keeps serving its call meanwhile.
pub(crate) struct Backoff {
    /// The next wait.
    next: Duration,
    /// Cap of `next`.
    max: Duration,
}

impl Backoff {
    pub fn new(config: &ConnectorConfig) -> Self {
        Self {
            next: config.backoff_min,
            max: config.backoff_max,
        }
    }

    /// Sleeps the current wait, then doubles it (up to the cap).
    pub async fn wait(&mut self) {
        tokio::time::sleep(self.next).await;
        self.next = self.next.saturating_mul(2).min(self.max);
    }

    /// Like `wait`, but never sleeps longer than `limit` in one go, so the
    /// caller can do periodic work (a heartbeat) between slices.
    pub async fn wait_at_most(&mut self, limit: Duration) {
        tokio::time::sleep(self.next.min(limit)).await;
        self.next = self.next.saturating_mul(2).min(self.max);
    }
}
