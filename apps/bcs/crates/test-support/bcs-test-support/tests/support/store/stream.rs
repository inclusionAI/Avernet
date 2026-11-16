use super::*;
use async_trait::async_trait;
use bcs_store_api::stream::*;
use std::{num::NonZeroU32, ops::Bound};
fn in_range(id: StreamId, start: &Bound<StreamId>, end: &Bound<StreamId>) -> bool {
    (match start {
        Bound::Unbounded => true,
        Bound::Included(v) => id >= *v,
        Bound::Excluded(v) => id > *v,
    }) && (match end {
        Bound::Unbounded => true,
        Bound::Included(v) => id <= *v,
        Bound::Excluded(v) => id < *v,
    })
}
fn page(log: Option<&Log>, entries: Vec<StreamEntry>) -> StreamPage {
    StreamPage {
        entries,
        last_id: log.and_then(|x| x.last),
        removed_through: log.and_then(|x| x.removed),
    }
}
fn validate_read(options: ReadOptions) -> StoreResult<()> {
    if options.block.is_some_and(|x| x.is_zero()) {
        Err(invalid("zero block duration"))
    } else {
        Ok(())
    }
}
#[async_trait]
impl StreamStorePlugin for Model {
    async fn append(
        &self,
        key: &str,
        id: AppendId,
        fields: &BTreeMap<String, Vec<u8>>,
    ) -> StoreResult<StreamId> {
        if fields.is_empty() {
            return Err(invalid("empty fields"));
        }
        let mut s = self.state.lock().unwrap();
        s.kind(key, "stream")?;
        let log = s.streams.entry(key.into()).or_default();
        let next = match id {
            AppendId::Auto => match log.last {
                None => StreamId { major: 1, minor: 0 },
                Some(v) => StreamId {
                    major: v.major,
                    minor: v.minor.checked_add(1).ok_or(StoreError::VersionExhausted)?,
                },
            },
            AppendId::Explicit(v) => v,
        };
        if next == (StreamId { major: 0, minor: 0 }) {
            return Err(invalid("zero stream id"));
        }
        if log.last.is_some_and(|x| next <= x) {
            return Err(StoreError::IdNotIncreasing);
        }
        log.entries.insert(
            next,
            StreamEntry {
                id: next,
                fields: fields.clone(),
            },
        );
        log.last = Some(next);
        self.finish(next)
    }
    async fn read(
        &self,
        reads: &[StreamRead],
        options: ReadOptions,
    ) -> StoreResult<Vec<StreamBatch>> {
        validate_read(options)?;
        let mut seen = std::collections::HashSet::new();
        if reads.is_empty() || reads.iter().any(|x| !seen.insert(&x.key)) {
            return Err(invalid("invalid read keys"));
        }
        let started = Instant::now();
        loop {
            let result = {
                let mut s = self.state.lock().unwrap();
                let mut out = Vec::new();
                for r in reads {
                    s.kind(&r.key, "stream")?;
                    let log = s.streams.get(&r.key);
                    let entries = log
                        .into_iter()
                        .flat_map(|l| l.entries.values())
                        .filter(|e| r.after.is_none_or(|id| e.id > id))
                        .cloned()
                        .collect();
                    out.push(StreamBatch {
                        key: r.key.clone(),
                        page: page(log, entries),
                    });
                }
                let mut remaining = options.max_entries.get() as usize;
                for n in 0..reads.len() {
                    let i = (s.read_turn + n) % reads.len();
                    out[i].page.entries.truncate(remaining);
                    remaining -= out[i].page.entries.len();
                }
                s.read_turn = (s.read_turn + 1) % reads.len();
                out
            };
            if result.iter().any(|x| !x.page.entries.is_empty())
                || options.block.is_none_or(|d| started.elapsed() >= d)
            {
                return Ok(result);
            }
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    }
    async fn range(
        &self,
        key: &str,
        start: Bound<StreamId>,
        end: Bound<StreamId>,
        limit: NonZeroU32,
        reverse: bool,
    ) -> StoreResult<StreamPage> {
        let s = self.state.lock().unwrap();
        s.kind(key, "stream")?;
        let log = s.streams.get(key);
        let mut entries: Vec<_> = log
            .into_iter()
            .flat_map(|l| l.entries.values())
            .filter(|e| in_range(e.id, &start, &end))
            .cloned()
            .collect();
        if reverse {
            entries.reverse();
        }
        entries.truncate(limit.get() as usize);
        Ok(page(log, entries))
    }
    async fn trim(&self, key: &str, policy: TrimPolicy) -> StoreResult<TrimResult> {
        let mut s = self.state.lock().unwrap();
        s.kind(key, "stream")?;
        let Some(log) = s.streams.get_mut(key) else {
            return Ok(TrimResult {
                removed: 0,
                removed_through: None,
            });
        };
        let ids: Vec<_> = match policy {
            TrimPolicy::Before(v) => log
                .entries
                .keys()
                .take_while(|id| **id < v)
                .copied()
                .collect(),
            TrimPolicy::MaxLen(n) => log
                .entries
                .keys()
                .take(log.entries.len().saturating_sub(n as usize))
                .copied()
                .collect(),
        };
        for id in &ids {
            log.entries.remove(id);
            log.removed = Some(log.removed.map_or(*id, |old| old.max(*id)));
        }
        Ok(TrimResult {
            removed: ids.len() as u64,
            removed_through: log.removed,
        })
    }
    async fn create_group(
        &self,
        key: &str,
        group: &str,
        start: GroupStart,
    ) -> StoreResult<GroupCreateOutcome> {
        let mut s = self.state.lock().unwrap();
        s.kind(key, "stream")?;
        let log = s.streams.entry(key.into()).or_default();
        if log.groups.contains_key(group) {
            return Ok(GroupCreateOutcome::AlreadyExists);
        }
        let cursor = match start {
            GroupStart::Beginning => None,
            GroupStart::After(v) => Some(v),
            GroupStart::Tail => log.last,
        };
        log.groups.insert(
            group.into(),
            Group {
                cursor,
                pending: BTreeMap::new(),
            },
        );
        Ok(GroupCreateOutcome::Created)
    }
    async fn read_group(
        &self,
        key: &str,
        group: &str,
        consumer: &str,
        options: ReadOptions,
    ) -> StoreResult<Vec<StreamEntry>> {
        validate_read(options)?;
        let started = Instant::now();
        loop {
            let entries = {
                let mut s = self.state.lock().unwrap();
                s.kind(key, "stream")?;
                let log = s.streams.get_mut(key).ok_or(StoreError::GroupMissing)?;
                let g = log.groups.get_mut(group).ok_or(StoreError::GroupMissing)?;
                let entries: Vec<_> = log
                    .entries
                    .values()
                    .filter(|e| g.cursor.is_none_or(|c| e.id > c))
                    .take(options.max_entries.get() as usize)
                    .cloned()
                    .collect();
                for e in &entries {
                    g.pending.insert(
                        e.id,
                        Pending {
                            consumer: consumer.into(),
                            delivered: Instant::now(),
                        },
                    );
                    g.cursor = Some(e.id);
                }
                entries
            };
            if !entries.is_empty() || options.block.is_none_or(|d| started.elapsed() >= d) {
                return self.finish(entries);
            }
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    }
    async fn ack(&self, key: &str, group: &str, ids: &[StreamId]) -> StoreResult<u64> {
        let mut s = self.state.lock().unwrap();
        s.kind(key, "stream")?;
        let g = s
            .streams
            .get_mut(key)
            .and_then(|l| l.groups.get_mut(group))
            .ok_or(StoreError::GroupMissing)?;
        Ok(ids
            .iter()
            .filter(|id| g.pending.remove(id).is_some())
            .count() as u64)
    }
    async fn claim_idle(
        &self,
        key: &str,
        group: &str,
        consumer: &str,
        min_idle: Duration,
        start: Option<StreamId>,
        limit: NonZeroU32,
    ) -> StoreResult<ClaimPage> {
        let mut s = self.state.lock().unwrap();
        s.kind(key, "stream")?;
        let log = s.streams.get_mut(key).ok_or(StoreError::GroupMissing)?;
        let g = log.groups.get_mut(group).ok_or(StoreError::GroupMissing)?;
        let ids: Vec<_> = g
            .pending
            .keys()
            .filter(|id| start.is_none_or(|v| **id >= v))
            .take(limit.get() as usize + 1)
            .copied()
            .collect();
        let next = ids.get(limit.get() as usize).copied();
        let mut entries = Vec::new();
        let mut deleted = Vec::new();
        for id in ids.into_iter().take(limit.get() as usize) {
            if !log.entries.contains_key(&id) {
                g.pending.remove(&id);
                deleted.push(id);
                continue;
            }
            let pending = g.pending.get_mut(&id).unwrap();
            if pending.delivered.elapsed() >= min_idle {
                pending.consumer = consumer.into();
                pending.delivered = Instant::now();
                entries.push(log.entries[&id].clone());
            }
        }
        Ok(ClaimPage {
            entries,
            deleted,
            next,
        })
    }
}
