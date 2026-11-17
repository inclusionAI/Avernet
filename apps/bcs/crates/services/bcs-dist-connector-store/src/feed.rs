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

pub struct StoreCommandFeed {
    shared: Arc<Shared>,
    /// `<node>:<incarnation>`: one consumer per process run.
    consumer: String,
    keys: Vec<String>,
    /// At most one in-flight group read per partition.
    reads: FuturesUnordered<GroupRead>,
    reading: Vec<bool>,
    buffer: VecDeque<CommandDelivery>,
    reclaimed_at: Option<Instant>,
    /// Where the next idle-takeover round starts, and how far each
    /// partition's pending list was scanned.
    reclaim_from: usize,
    reclaim_cursor: Vec<Option<StreamId>>,
}

impl StoreCommandFeed {
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

    async fn ack(&mut self, delivery: &CommandDelivery) -> Result<(), StoreError> {
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
