use super::{StoreInboxProducer, state::*, storage::*, validation::critical};
use crate::{
    codec::{decode_inbox_entry, encode_inbox_entry, encode_json},
    identity::{digest_fields, digest_json, hex_of},
    layout::KeyLayout,
};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, inbox::*, session::CheckpointEnvelope};
use bcs_store_api::stream::*;
use std::{
    collections::VecDeque,
    num::NonZeroU32,
    ops::Bound,
    sync::atomic::Ordering,
    time::{Duration, Instant},
};

#[derive(Clone)]
pub(super) struct Delivery {
    generation: String,
    sid: Sid,
    through: InboxPos,
    confirmed: bool,
}
struct Feed {
    store: StoreInboxProducer,
    shard: ShardId,
    generation: String,
    watches: VecDeque<Watch>,
    _reader: tokio::sync::OwnedSemaphorePermit,
    failed: bool,
}
struct Watch {
    sid: Sid,
    after: Option<InboxPos>,
    _permit: tokio::sync::OwnedSemaphorePermit,
    failure: Option<StoreError>,
}
impl Drop for Feed {
    fn drop(&mut self) {
        if let Ok(mut receipts) = self.store.inner.receipts.lock() {
            receipts.retain(|_, d| d.generation != self.generation);
        }
    }
}
impl StoreInboxProducer {
    pub(super) fn cursor(&self, sid: &Sid, pos: &InboxPos) -> Result<StreamId, StoreError> {
        if &pos.sid != sid
            || pos.token.backend != self.inner.config.backend
            || pos.token.generation.0 != super::producer::LOG_GENERATION
        {
            return Err(invalid("inbox_position_scope"));
        }
        let (a, b) = pos
            .token
            .value
            .split_once('-')
            .ok_or_else(|| invalid("inbox_position"))?;
        Ok(StreamId {
            major: a.parse().map_err(|_| invalid("inbox_position"))?,
            minor: b.parse().map_err(|_| invalid("inbox_position"))?,
        })
    }
    async fn range_inputs(
        &self,
        sid: &Sid,
        after: Option<&InboxPos>,
        limit: NonZeroU32,
    ) -> Result<Vec<StreamEntry>, StoreError> {
        let start = after
            .map(|p| self.cursor(sid, p).map(Bound::Excluded))
            .transpose()?
            .unwrap_or(Bound::Unbounded);
        self.read_inputs(sid, start, Bound::Unbounded, limit, false).await
    }
    pub(super) async fn read_inputs(
        &self,
        sid: &Sid,
        start: Bound<StreamId>,
        end: Bound<StreamId>,
        limit: NonZeroU32,
        reverse: bool,
    ) -> Result<Vec<StreamEntry>, StoreError> {
        let _permit = self.io_permit().await?;
        Ok(tokio::time::timeout(
            Duration::from_secs(2),
            self.inner.stream.range(&self.inner.layout.inbox(sid)?, start, end, limit, reverse),
        )
        .await
        .map_err(|_| unknown("inbox_read"))?
        .map_err(|e| crate::error::from_read(&e))?
        .entries)
    }
    pub(super) async fn checkpoint(
        &self,
        sid: &Sid,
    ) -> Result<Option<CheckpointEnvelope>, StoreError> {
        Ok(self
            .load(&self.inner.layout.ckpt(sid)?)
            .await?
            .map(|(_, c)| c))
    }
    async fn validate_proof(
        &self,
        proof: &CommitReceipt,
    ) -> Result<CheckpointEnvelope, StoreError> {
        let c = self
            .checkpoint(&proof.sid)
            .await?
            .ok_or_else(|| invalid("commit_proof_missing"))?;
        let b = c
            .checkpoint
            .outbox
            .as_ref()
            .ok_or_else(|| invalid("commit_proof_missing"))?;
        if b.commit_id != proof.commit_id
            || b.content_digest != proof.content_digest
            || b.source_commit != proof.sequence
            || c.checkpoint.commit_seq != proof.sequence
            || c.checkpoint.consumed != proof.consumed
        {
            return Err(invalid("commit_proof_mismatch"));
        }
        Ok(c)
    }
    pub(super) async fn append_intermediate(
        &self,
        r: AppendAgentEvent,
    ) -> Result<InboxPos, StoreError> {
        if critical(&r.event.kind) {
            return Err(invalid("critical_requires_admission"));
        }
        if !r.tid.0.starts_with(&format!("{}.", r.sid.0))
            || !r.event.did.0.starts_with(&format!("{}.", r.tid.0))
        {
            return Err(invalid("dispatch_task_mismatch"));
        }
        if self
            .load::<SessionSpec>(&self.inner.layout.spec(&r.sid)?)
            .await?
            .is_none()
        {
            return Err(invalid("session_spec_missing"));
        }
        let identity = digest_fields(&[
            b"agent_stream",
            r.event.did.0.as_bytes(),
            &r.event.holder_epoch.0.to_be_bytes(),
            &r.event.seq.0.to_be_bytes(),
        ]);
        let body = InputBody::Agent { event: r.event };
        let input = InputEnvelope {
            schema_version: 1,
            sid: r.sid.clone(),
            tid: r.tid,
            input_id: InputId(format!("as-{}", hex_of(&identity))),
            business_digest: digest_json(&encode_json(&body)?),
            body,
        };
        let mut fields = encode_inbox_entry(&input)?;
        fields.insert("intermediate".into(), b"1".to_vec());
        let bytes = fields.iter().map(|(k, v)| k.len() + v.len()).sum::<usize>();
        if bytes + 512 > self.inner.config.max_input_bytes {
            return Err(invalid("input_byte_budget"));
        }
        self.reserve_append(&r.sid, bytes as u64 + 512, true, false, &input.input_id.0)
            .await?;
        let id = self.append(&r.sid, &fields, &input.input_id.0).await?;
        self.activate(&r.sid, &input.input_id.0)
            .await
            .map_err(|_| unknown(&input.input_id.0))?;
        Ok(self.position(&r.sid, id))
    }
    pub(super) async fn admission_for_input(
        &self,
        sid: &Sid,
        input: &InputEnvelope,
        key: &[u8],
    ) -> Result<Option<AdmissionRecord>, StoreError> {
        let key = std::str::from_utf8(key).map_err(|_| invalid("admission_key"))?;
        if !key.starts_with(&format!(
            "{}:admission:",
            self.inner.layout.namespace_identity()
        )) {
            return Err(invalid("admission_namespace"));
        }
        let Some((_, c)) = self.admission_record(key).await? else {
            return Ok(None);
        };
        if c.sid() != sid
            || c.request.input.input_id != input.input_id
            || c.request.input.business_digest != input.business_digest
        {
            return Err(invalid("admission_input_mismatch"));
        }
        Ok(Some(c))
    }
    /// None: unresolved admission position, stop only this session. Some(None):
    /// durable duplicate/rejection, advance coverage without invoking the Runner.
    async fn classify(
        &self,
        sid: &Sid,
        row: &StreamEntry,
    ) -> Result<Option<Option<InputEnvelope>>, StoreError> {
        let input = decode_inbox_entry(sid, &row.fields)?;
        if let Some(key) = row.fields.get("admission") {
            let Some(c) = self.admission_for_input(sid, &input, key).await? else {
                return Ok(None);
            };
            match c.decision {
                None => Ok(None),
                Some(Decision::Rejected) => Ok(Some(None)),
                Some(Decision::Accepted) => {
                    let pos = c
                        .position
                        .as_ref()
                        .ok_or_else(|| invalid("accepted_without_position"))?;
                    if self.cursor(sid, pos)? == row.id {
                        Ok(Some(Some(c.request.input)))
                    } else {
                        Ok(Some(None))
                    }
                }
            }
        } else if row.fields.get("intermediate").is_some_and(|v| v == b"1") {
            if !matches!(&input.body,InputBody::Agent{event} if !critical(&event.kind)) {
                return Err(invalid("invalid_intermediate"));
            }
            Ok(Some(Some(input)))
        } else {
            Err(invalid("unclassified_inbox_entry"))
        }
    }
}
#[async_trait]
impl ShardFeedPort for Feed {
    async fn watch(&mut self, sid: Sid, after: Option<InboxPos>) -> Result<(), StoreError> {
        if self.failed {
            return Err(invalid("reader_restart_required"));
        }
        if KeyLayout::shard_of(&sid)? != self.shard {
            return Err(invalid("watch_shard"));
        }
        if !self.watches.iter().any(|w| w.sid == sid)
            && self.watches.len() >= self.store.inner.config.max_watches
        {
            return Err(pressure());
        }
        let committed = self
            .store
            .checkpoint(&sid)
            .await?
            .and_then(|c| c.checkpoint.consumed);
        if let Some(after) = after {
            let requested = self.store.cursor(&sid, &after)?;
            let actual = committed
                .as_ref()
                .map(|p| self.store.cursor(&sid, p))
                .transpose()?;
            if actual.is_none_or(|a| requested > a) {
                return Err(invalid("watch_uncommitted_cursor"));
            }
        }
        if let Some(watch) = self.watches.iter_mut().find(|w| w.sid == sid) {
            watch.after = committed;
            watch.failure = None;
            return Ok(());
        }
        let permit = self
            .store
            .inner
            .watches
            .clone()
            .try_acquire_owned()
            .map_err(|_| pressure())?;
        self.watches.push_back(Watch {
            sid,
            after: committed,
            _permit: permit,
            failure: None,
        });
        Ok(())
    }
    async fn unwatch(&mut self, sid: &Sid) -> Result<(), StoreError> {
        self.watches.retain(|w| &w.sid != sid);
        let mut receipts = self
            .store
            .inner
            .receipts
            .lock()
            .map_err(|_| invalid("receipt_lock"))?;
        receipts.retain(|_, d| d.generation != self.generation || &d.sid != sid);
        Ok(())
    }
    async fn next(
        &mut self,
        limits: ReadLimits,
        timeout: Duration,
    ) -> Result<FeedBatch, StoreError> {
        if self.failed {
            return Err(invalid("reader_restart_required"));
        }
        if limits.max_sessions.get() > 128
            || limits.max_entries.get() > 128
            || limits.max_bytes.get() > 1024 * 1024
        {
            return Err(invalid("read_limits"));
        }
        let result = tokio::time::timeout(Duration::from_secs(2), self.poll(limits))
            .await
            .unwrap_or_else(|_| Err(unknown("reader_turn")));
        if result.is_err() {
            self.failed = true;
        }
        if result
            .as_ref()
            .is_ok_and(|b| b.sessions.is_empty() && b.failures.is_empty())
            && !timeout.is_zero()
        {
            tokio::time::sleep(timeout.min(Duration::from_millis(100))).await;
        }
        result
    }
}
struct ReadSession {
    entries: Vec<InputEnvelope>,
    through: Option<InboxPos>,
    physical: u32,
    bytes: usize,
    pending: bool,
}
impl Feed {
    async fn read_session(
        &self,
        watch: &Watch,
        max_entries: u32,
        max_bytes: usize,
        deadline: Instant,
    ) -> Result<ReadSession, StoreError> {
        let fetch =
            max_entries.min((max_bytes / self.store.inner.config.max_input_bytes).max(1) as u32);
        // Single-key ranges deliberately avoid cross-partition multi-XREAD.
        let rows = tokio::time::timeout_at(
            tokio::time::Instant::from_std(deadline),
            self.store.range_inputs(
                &watch.sid,
                watch.after.as_ref(),
                NonZeroU32::new(fetch).ok_or_else(|| invalid("read_limit"))?,
            ),
        )
        .await
        .map_err(|_| unknown("session_read_timeout"))??;
        let mut result = ReadSession {
            entries: vec![],
            through: watch.after.clone(),
            physical: 0,
            bytes: 0,
            pending: false,
        };
        for row in rows {
            if Instant::now() >= deadline {
                break;
            }
            let bytes = row
                .fields
                .iter()
                .map(|(k, v)| k.len() + v.len())
                .sum::<usize>()
                + 512;
            if bytes > max_bytes - result.bytes {
                if result.physical == 0 {
                    return Err(invalid("single_input_exceeds_read_budget"));
                }
                break;
            }
            let classified = match tokio::time::timeout_at(
                tokio::time::Instant::from_std(deadline),
                self.store.classify(&watch.sid, &row),
            )
            .await
            {
                Ok(Ok(value)) => value,
                // Retain a confirmed continuous prefix; retry the unfinished row
                // next time. A failure at the first row still suspends the watch.
                _ if result.physical > 0 => break,
                Ok(Err(error)) => return Err(error),
                Err(_) => return Err(unknown("session_read_timeout")),
            };
            let Some(classified) = classified else {
                result.pending = true;
                break;
            };
            result.physical += 1;
            result.bytes += bytes;
            result.through = Some(self.store.position(&watch.sid, row.id));
            if let Some(input) = classified {
                result.entries.push(input)
            }
        }
        Ok(result)
    }
    async fn poll(&mut self, limits: ReadLimits) -> Result<FeedBatch, StoreError> {
        let mut sessions = Vec::new();
        let mut hints = Vec::new();
        let mut failures = Vec::new();
        let mut staged = Vec::new();
        let mut entries_left = limits.max_entries.get();
        let mut bytes_left = limits.max_bytes.get() as usize;
        let round_deadline = Instant::now() + Duration::from_millis(1900);
        let count = self.watches.len().min(limits.max_sessions.get() as usize);
        for _ in 0..count {
            if entries_left == 0 || bytes_left == 0 || Instant::now() >= round_deadline {
                break;
            }
            let Some(watch) = self.watches.front() else {
                break;
            };
            if let Some(error) = watch.failure.clone() {
                failures.push(SessionFeedFailure {
                    sid: watch.sid.clone(),
                    error,
                });
                self.watches.rotate_left(1);
                continue;
            }
            let deadline = round_deadline.min(Instant::now() + Duration::from_millis(500));
            // The watch stays owned by Feed during IO. Canceling next() must not
            // drop its permit or silently forget a Session with unread inputs.
            let read = self
                .read_session(watch, entries_left, bytes_left, deadline)
                .await;
            let watch = self
                .watches
                .front_mut()
                .ok_or_else(|| invalid("reader_watch_missing"))?;
            match read {
                Ok(read) => {
                    entries_left -= read.physical;
                    bytes_left -= read.bytes;
                    if read.pending {
                        hints.push(watch.sid.clone());
                    }
                    if read.through != watch.after {
                        staged.push((watch.sid.clone(), read));
                    }
                }
                Err(error) => {
                    watch.failure = Some(error.clone());
                    self.store
                        .inner
                        .receipts
                        .lock()
                        .map_err(|_| invalid("receipt_lock"))?
                        .retain(|_, d| d.generation != self.generation || d.sid != watch.sid);
                    failures.push(SessionFeedFailure {
                        sid: watch.sid.clone(),
                        error,
                    });
                }
            }
            self.watches.rotate_left(1);
        }
        // No await below this point: either the full returned batch owns its
        // receipts/cursors, or cancellation before here leaves all input unread.
        let mut receipts = self
            .store
            .inner
            .receipts
            .lock()
            .map_err(|_| invalid("receipt_lock"))?;
        if receipts.len() + staged.len() > self.store.inner.config.max_receipts {
            receipts.retain(|_, d| !d.confirmed);
        }
        if receipts.len() + staged.len() > self.store.inner.config.max_receipts {
            return Err(pressure());
        }
        for (sid, read) in staged {
            let through = read.through.ok_or_else(|| invalid("reader_coverage"))?;
            let opaque = self
                .store
                .inner
                .receipt_sequence
                .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |v| v.checked_add(1))
                .map_err(|_| invalid("receipt_sequence_exhausted"))?
                .to_string();
            let watch = self
                .watches
                .iter_mut()
                .find(|w| w.sid == sid)
                .ok_or_else(|| invalid("reader_watch_missing"))?;
            receipts.insert(
                opaque.clone(),
                Delivery {
                    generation: self.generation.clone(),
                    sid: sid.clone(),
                    through: through.clone(),
                    confirmed: false,
                },
            );
            sessions.push(SessionBatch {
                sid,
                entries: read.entries,
                read_through: through.clone(),
                delivery: DeliveryReceipt {
                    backend: self.store.inner.config.backend.clone(),
                    consumer_generation: self.generation.clone(),
                    opaque,
                },
            });
            watch.after = Some(through);
        }
        Ok(FeedBatch {
            sessions,
            hints,
            failures,
        })
    }
}
#[async_trait]
impl InboxConsumerPort for StoreInboxProducer {
    fn reader_resources(&self) -> Option<InboxReaderResources> {
        Some(InboxReaderResources {
            max_readers: self.inner.config.max_readers,
            max_watches: self.inner.config.max_watches,
            max_receipts: self.inner.config.max_receipts,
            max_document_bytes: self.inner.config.max_document_bytes,
        })
    }
    fn scan_budget_profile(
        &self,
    ) -> Option<bcs_service_api::port::repo::distributed::budget::ScanBudgetProfile> {
        Some(self.inner.scan_budget.profile())
    }
    async fn open(&self, shard: ShardId) -> Result<Box<dyn ShardFeedPort>, StoreError> {
        let reader = self
            .inner
            .readers
            .clone()
            .try_acquire_owned()
            .map_err(|_| pressure())?;
        let generation = uuid::Uuid::new_v4().to_string();
        Ok(Box::new(Feed {
            store: self.clone(),
            shard,
            generation,
            _reader: reader,
            watches: VecDeque::new(),
            failed: false,
        }))
    }
    async fn has_unconsumed(
        &self,
        sid: &Sid,
        after: Option<&InboxPos>,
    ) -> Result<bool, StoreError> {
        Ok(!self
            .range_inputs(sid, after, NonZeroU32::MIN)
            .await?
            .is_empty())
    }
    async fn confirm_committed(
        &self,
        r: &DeliveryReceipt,
        proof: &CommitReceipt,
    ) -> Result<(), StoreError> {
        if r.backend != self.inner.config.backend {
            return Err(invalid("receipt_backend"));
        }
        let delivery = self
            .inner
            .receipts
            .lock()
            .map_err(|_| invalid("receipt_lock"))?
            .get(&r.opaque)
            .cloned()
            .ok_or_else(|| invalid("receipt_unknown"))?;
        if delivery.generation != r.consumer_generation || delivery.sid != proof.sid {
            return Err(invalid("receipt_scope"));
        }
        let c = self.validate_proof(proof).await?;
        let consumed = c
            .checkpoint
            .consumed
            .as_ref()
            .ok_or_else(|| invalid("commit_without_coverage"))?;
        if self.cursor(&delivery.sid, consumed)? < self.cursor(&delivery.sid, &delivery.through)? {
            return Err(invalid("insufficient_committed_coverage"));
        }
        let mut receipts = self
            .inner
            .receipts
            .lock()
            .map_err(|_| invalid("receipt_lock"))?;
        let delivery = receipts
            .get_mut(&r.opaque)
            .ok_or_else(|| invalid("receipt_expired"))?;
        delivery.confirmed = true;
        Ok(())
    }
    async fn trim_committed(&self, proof: &CommitReceipt) -> Result<(), StoreError> {
        self.validate_proof(proof).await?;
        // Phase one deliberately retains Inbox and all admission evidence.
        Ok(())
    }
    async fn enumerate_pending(
        &self,
        shard: ShardId,
        page: PageRequest,
    ) -> Result<Page<Sid>, StoreError> {
        self.history_page(shard, page).await
    }
}
