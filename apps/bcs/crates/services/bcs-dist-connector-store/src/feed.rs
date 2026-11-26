//! The `CommandFeedPort` provider: command-stream partitions read through the
//! consumer group `connectors`.
//!
//! - Every Connector reads every partition, one blocking group read per
//!   partition (one key per call). The group hands each entry to one consumer.
//! - A group read that was started is never dropped: it may already have
//!   registered deliveries. Its future is kept across `receive` calls.
//! - Entries a dead Connector left pending are taken over after an idle time.
//! - The group is created from the beginning, so a hint posted before any
//!   Connector existed is not skipped. Re-reading an old hint is harmless: the
//!   claim checks the dispatch document.
//!
//! Keys and values:
//!
//! ```text
//! partition key: bcn:v2:cmd:<cluster>:<ns>:<pool>:p<n>
//!                bcn:v2:cmd:c01:main:a2a:p3
//! entry:         id 1794370950123-0, fields {"did": "s0042.7f3a9c.b21e.n2.a1"}
//! group:         connectors      consumer: conn-03:1794370950
//! ```
//!
//! Glossary:
//! - **Hint**: one entry, "go look at `disp:<did>`"; never an instruction.
//! - **Consumer group** `connectors`: shared by every Connector. The store
//!   hands each new entry to exactly one consumer: the first group read
//!   served wins. There is no routing to the Connector that holds the call.
//! - **Pending list**: per partition, the entries delivered to some consumer
//!   and not yet acknowledged. A group read for new entries never returns
//!   them again, and nothing waits behind them: later hints are delivered as
//!   usual.
//! - **Ack**: removes an entry from the pending list. It means "the claim
//!   decision was made" (spec rule B2), not "the call finished". A hint that
//!   lands on a Connector that does not hold the call gets `HeldByOther` and
//!   is acknowledged; the holder learns of the reply or cancel from its own
//!   tick (B14).
//! - **Idle takeover** (`claim_idle`): every `reclaim_every` (10 s), move
//!   entries pending for at least `reclaim_min_idle` (30 s) under any
//!   consumer to this one. It is not an ack: the entry stays pending, now
//!   under this consumer, and its idle time restarts, so of several
//!   Connectors doing it at once only one gets it. It covers crashed
//!   Connectors and claim errors (the engine leaves a hint unacknowledged on
//!   purpose when its claim failed). Known gap: a hint whose claim fails
//!   every time is retried this way forever.

use crate::shared::Shared;
use async_trait::async_trait;
use bcs_dist_model::{Did, Instance, PoolId};
use bcs_dist_store::command::{DID_FIELD, GROUP};
use bcs_dist_store::error::{from_read, from_write};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::dispatch::{CommandDelivery, CommandFeedPort};
use bcs_store_api::StoreError as PluginError;
use bcs_store_api::stream::{GroupStart, ReadOptions, StreamEntry, StreamId};
use futures::StreamExt;
use futures::future::BoxFuture;
use futures::stream::FuturesUnordered;
use std::collections::VecDeque;
use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::Duration;
use tokio::time::Instant;

type GroupRead = BoxFuture<'static, (usize, Result<Vec<StreamEntry>, PluginError>)>;

/// The `CommandFeedPort` provider: one per Connector process run, opened by
/// `StoreDispatchQueue::notifications`. Used by one task at a time (`&mut`).
pub struct StoreCommandFeed {
    shared: Arc<Shared>,
    /// `<node>:<incarnation>`: one consumer per process run,
    /// e.g. `conn-03:1794370950`.
    consumer: String,
    /// One stream key per partition, indexed by partition number,
    /// e.g. `["bcn:v2:cmd:c01:main:a2a:p0", ..., "...:p7"]`.
    keys: Vec<String>,
    /// At most one in-flight group read per partition.
    ///
    /// These reads are kept across `receive` calls on purpose. A group read
    /// that the store already served has registered its entries as pending
    /// under this consumer; dropping its future before the answer is read
    /// would lose those entries until idle takeover brings them back. So a
    /// `receive` that hits its deadline leaves the reads running, and the
    /// next `receive` collects them. Each finished read yields
    /// `(partition, entries)`, e.g. `(3, [entry 1794370950123-0])`.
    reads: FuturesUnordered<GroupRead>,
    /// `reading[p]` is true while partition `p` has a read in `reads`,
    /// e.g. `[true, true, false, true]` right after partition 2 answered.
    reading: Vec<bool>,
    /// Deliveries read but not yet returned by `receive` (it returns at most
    /// `max`). They are already pending under this consumer.
    /// E.g. `[CommandDelivery { did: "s0042.7f3a9c.b21e.n2.a1", opaque:
    /// "p3:1794370950123-0", .. }]`.
    buffer: VecDeque<CommandDelivery>,
    /// When the last idle-takeover round ran; `None` until the first one, so
    /// the first `receive` runs one at once.
    reclaimed_at: Option<Instant>,
    /// Where the next idle-takeover round starts, and how far each
    /// partition's pending list was scanned.
    ///
    /// `reclaim_from`: the partition the next round starts at, e.g. `2`.
    /// It rotates by one per round, because a round stops early once the
    /// buffer is full and would otherwise always favour the first partitions.
    reclaim_from: usize,
    /// `reclaim_cursor[p]`: the bookmark in partition `p`'s pending list where
    /// the next page starts, e.g. `Some(1794370950123-0)`; `None` = from the
    /// start (a full pass ended, or none began).
    reclaim_cursor: Vec<Option<StreamId>>,
}

impl StoreCommandFeed {
    /// Builds the partition keys and joins (creates if needed) the group on
    /// every partition. Fails if a key is invalid or a group cannot be created.
    pub(crate) async fn open(shared: Arc<Shared>, pool: PoolId, me: &Instance) -> Result<Self, StoreError> {
        // The held-index key validates the instance (no ':' inside its parts).
        shared.layout.held(me)?;
        let mut keys = Vec::new();
        for partition in 0..shared.config.streams.partitions() {
            keys.push(shared.layout.command(&pool, partition)?);
        }
        let feed = Self {
            consumer: format!("{}:{}", me.node, me.incarnation),
            reading: vec![false; keys.len()],
            reads: FuturesUnordered::new(),
            buffer: VecDeque::new(),
            reclaimed_at: None,
            reclaim_from: 0,
            reclaim_cursor: vec![None; keys.len()],
            keys,
            shared,
        };
        for key in &feed.keys {
            create_group(&feed.shared, key).await?;
        }
        Ok(feed)
    }

    /// Starts a group read on every partition that has none in flight.
    /// Only queues the futures; they make progress while `receive` polls
    /// `reads`.
    fn start_reads(&mut self) {
        for (partition, key) in self.keys.iter().enumerate() {
            if self.reading[partition] {
                continue;
            }
            self.reading[partition] = true;
            let (shared, key, consumer) = (self.shared.clone(), key.clone(), self.consumer.clone());
            let options = ReadOptions {
                max_entries: shared.config.read_batch,
                block: Some(shared.config.read_block),
            };
            self.reads.push(Box::pin(async move {
                let entries = shared.stream.read_group(&key, GROUP, &consumer, options).await;
                (partition, entries)
            }));
        }
    }

    /// Buffers entries; one without a readable dispatch id is acked and
    /// dropped. Valid entries are buffered before any await, so dropping the
    /// caller's future cannot lose a delivery that was already registered.
    ///
    /// `opaque` encodes where to ack: `p<partition>:<stream id>`, e.g.
    /// `p3:1794370950123-0`. `consumer_generation` is the consumer name
    /// (`node:incarnation`), diagnostic and checked by `ack`; it is not the
    /// holder epoch.
    async fn accept(&mut self, partition: usize, entries: Vec<StreamEntry>) {
        let mut malformed = Vec::new();
        for entry in entries {
            let did = entry
                .fields
                .get(DID_FIELD)
                .and_then(|bytes| std::str::from_utf8(bytes).ok())
                .filter(|did| !did.is_empty());
            match did {
                Some(did) => self.buffer.push_back(CommandDelivery {
                    did: Did(did.to_owned()),
                    backend: self.shared.config.backend.clone(),
                    consumer_generation: self.consumer.clone(),
                    opaque: format!("p{partition}:{}-{}", entry.id.major, entry.id.minor),
                }),
                None => malformed.push(entry.id),
            }
        }
        if !malformed.is_empty() {
            tracing::warn!(partition, count = malformed.len(), "command entries without a dispatch id; dropped");
            let acked = self.shared.stream.ack(&self.keys[partition], GROUP, &malformed).await;
            if let Err(error) = acked {
                tracing::debug!(?error, "ack of malformed command entries failed");
            }
        }
    }

    /// Takes over hints other consumers left pending for too long: at most
    /// one page per partition per round, continuing where the last round
    /// stopped and starting at a different partition each time, so no
    /// partition is starved and a long pending list is not rescanned at once.
    ///
    /// Stops early once `max` deliveries are buffered. A failed page is only
    /// logged; that partition is tried again next round. Entries the stream
    /// already trimmed (`deleted`) are dropped from the pending list by the
    /// store and only logged here.
    async fn reclaim(&mut self, max: usize) {
        let limit = self.shared.config.read_batch;
        let partitions = self.keys.len();
        for step in 0..partitions {
            if self.buffer.len() >= max {
                break;
            }
            let partition = (self.reclaim_from + step) % partitions;
            let page = self
                .shared
                .stream
                .claim_idle(
                    &self.keys[partition],
                    GROUP,
                    &self.consumer,
                    self.shared.config.reclaim_min_idle,
                    self.reclaim_cursor[partition],
                    limit,
                )
                .await;
            match page {
                Ok(page) => {
                    self.reclaim_cursor[partition] = page.next;
                    if !page.deleted.is_empty() {
                        tracing::debug!(partition, count = page.deleted.len(), "pending hints already trimmed");
                    }
                    self.accept(partition, page.entries).await;
                }
                Err(error) => tracing::debug!(?error, partition, "idle takeover failed"),
            }
        }
        self.reclaim_from = (self.reclaim_from + 1) % partitions;
    }

    /// Removes and returns up to `max` deliveries from the front of `buffer`.
    fn take(&mut self, max: usize) -> Vec<CommandDelivery> {
        let count = self.buffer.len().min(max);
        self.buffer.drain(..count).collect()
    }
}

/// Creates the group from the beginning; an existing group is untouched.
async fn create_group(shared: &Shared, key: &str) -> Result<(), StoreError> {
    shared
        .stream
        .create_group(key, GROUP, GroupStart::Beginning)
        .await
        .map(|_| ())
        .map_err(|e| from_write(&e, key))
}

/// Reverses the `opaque` of a delivery: `p3:1794370950123-0` ->
/// `(3, StreamId { major: 1794370950123, minor: 0 })`.
fn parse_opaque(opaque: &str) -> Option<(usize, StreamId)> {
    let (partition, id) = opaque.strip_prefix('p')?.split_once(':')?;
    let (major, minor) = id.split_once('-')?;
    Some((
        partition.parse().ok()?,
        StreamId {
            major: major.parse().ok()?,
            minor: minor.parse().ok()?,
        },
    ))
}

#[async_trait]
impl CommandFeedPort for StoreCommandFeed {
    /// Returns up to `max` hints, waiting at most `timeout`.
    ///
    /// First, if an idle-takeover round is due, it runs it. Then the inner
    /// loop: return as soon as anything is buffered; return empty only at the
    /// deadline; otherwise make sure every partition has a read in flight and
    /// wait for the next one to answer. Without the loop, the first empty
    /// partition read (a blocking read ends empty after `read_block`) would
    /// return early with nothing while other partitions are still waiting.
    ///
    /// Dropping this future (e.g. a `select!` picked another branch) while it
    /// waits for reads loses nothing: buffered deliveries stay in `buffer`,
    /// and reads in flight stay in `reads` for the next call. A drop during an
    /// idle-takeover page can only delay those entries to a later takeover.
    async fn receive(
        &mut self,
        max: NonZeroU32,
        timeout: Duration,
    ) -> Result<Vec<CommandDelivery>, StoreError> {
        let max = max.get() as usize;
        let deadline = Instant::now() + timeout;
        let due = self
            .reclaimed_at
            .is_none_or(|at| at.elapsed() >= self.shared.config.reclaim_every);
        // A round skipped because the buffer is full stays due.
        if due && self.buffer.len() < max {
            self.reclaimed_at = Some(Instant::now());
            self.reclaim(max).await;
        }
        loop {
            if !self.buffer.is_empty() {
                return Ok(self.take(max));
            }
            if Instant::now() >= deadline {
                return Ok(Vec::new());
            }
            self.start_reads();
            // Deadline passed (`Err`) or no read in flight (`None`, not
            // expected after `start_reads`): return empty; the reads stay.
            let Ok(Some((partition, result))) =
                tokio::time::timeout_at(deadline, self.reads.next()).await
            else {
                return Ok(Vec::new());
            };
            self.reading[partition] = false;
            match result {
                Ok(entries) => self.accept(partition, entries).await,
                Err(PluginError::GroupMissing) => {
                    // The stream was recreated: join again from the beginning.
                    let (shared, key) = (self.shared.clone(), self.keys[partition].clone());
                    create_group(&shared, &key).await?;
                }
                // A delivery may have been registered; it comes back through
                // the idle takeover. Report the failure so the caller backs off.
                Err(error) => return Err(from_read(&error)),
            }
        }
    }

    /// Acknowledges one delivery: "the claim decision was made" (B2), not
    /// "the call finished". Removes the entry from the pending list so idle
    /// takeover will not bring it back. Refuses a delivery from another
    /// backend or another consumer (`command_delivery_scope`), or with an
    /// unreadable `opaque` (`command_delivery_opaque`). A failed ack only
    /// means the hint comes back later, which is harmless (B19).
    async fn ack(&mut self, delivery: &CommandDelivery) -> Result<(), StoreError> {
        if delivery.backend != self.shared.config.backend
            || delivery.consumer_generation != self.consumer
        {
            return Err(StoreError::Invalid {
                code: "command_delivery_scope".into(),
            });
        }
        let (partition, id) = parse_opaque(&delivery.opaque)
            .filter(|(partition, _)| *partition < self.keys.len())
            .ok_or_else(|| StoreError::Invalid {
                code: "command_delivery_opaque".into(),
            })?;
        // Zero newly acknowledged (a repeat, or taken over meanwhile) is fine.
        self.shared
            .stream
            .ack(&self.keys[partition], GROUP, &[id])
            .await
            .map(|_| ())
            .map_err(|e| from_write(&e, &delivery.opaque))
    }
}
