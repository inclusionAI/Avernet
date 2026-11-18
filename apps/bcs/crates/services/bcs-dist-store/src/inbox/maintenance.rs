//! Forward scanning and the continuous repair watermark are different state.
//! Fixed cuts and independent discovery rounds prevent a hot head or one pending
//! intent from hiding retained sessions. No scan cursor authorizes deletion.
use super::{StoreInboxProducer, state::*, storage::*};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{admission::*, common::*, inbox::InboxConsumerPort};
use std::{
    collections::BTreeSet,
    time::{Duration, Instant},
};

pub(super) struct Rate {
    since: Instant,
    logs: [u32; 2],
    sessions: [u32; 2],
}
impl Default for Rate {
    fn default() -> Self {
        Self {
            since: Instant::now(),
            logs: [0; 2],
            sessions: [0; 2],
        }
    }
}
impl Rate {
    fn claim(&mut self, kind: usize) -> bool {
        if self.since.elapsed() >= Duration::from_secs(1) {
            *self = Self::default();
        }
        // At most 128 session checks/s here: 72 remain reserved for dispatch
        // supervision. Each class has its own share; neither borrows the other.
        if self.logs[kind] >= 375 || self.sessions[kind] >= 64 {
            return false;
        }
        self.logs[kind] += 1;
        self.sessions[kind] += 1;
        true
    }
}
impl StoreInboxProducer {
    pub(super) async fn history_page(
        &self,
        shard: ShardId,
        page: PageRequest,
    ) -> Result<Page<Sid>, StoreError> {
        let work = async {
            if page.limit.get() > 128 {
                return Err(invalid("page_limit"));
            }
            let root = self
                .load::<JournalRoot>(&self.inner.layout.admission_root(shard))
                .await?
                .map(|(_, r)| r)
                .unwrap_or_default();
            let (cut, mut cursor) = match page.after {
                Some(p) => serde_json::from_str::<(u64, u64)>(&p.0)
                    .map_err(|_| invalid("history_cursor"))?,
                None => (root.next, 0),
            };
            if cut > root.next || cursor > cut {
                return Err(invalid("history_cursor"));
            }
            let started = Instant::now();
            let mut bytes = 0;
            let mut items = BTreeSet::new();
            let end = cursor.saturating_add(u64::from(page.limit.get())).min(cut);
            while cursor < end
                && started.elapsed() < Duration::from_millis(80)
                && bytes + self.inner.config.max_input_bytes + 512 <= 1024 * 1024
            {
                let intent = match tokio::time::timeout(
                    Duration::from_millis(20),
                    self.intent(shard, cursor + 1),
                )
                .await
                {
                    Ok(Ok(intent)) => intent,
                    _ => {
                        // This enumeration cursor is not a completion watermark.
                        // The root's retained sequence still obliges repair; report
                        // the failed position and continue other sessions in-page.
                        cursor += 1;
                        tracing::warn!(
                            shard = shard.0,
                            sequence = cursor,
                            "admission discovery entry remains pending repair"
                        );
                        bytes += self.inner.config.max_input_bytes + 512;
                        continue;
                    }
                };
                bytes += serde_json::to_vec(&intent)
                    .map_err(|_| invalid("intent_encoding"))?
                    .len();
                if bytes > 1024 * 1024 {
                    break;
                }
                cursor += 1;
                // Return candidates from the immutable discovery journal. A bad
                // canonical/checkpoint belongs to this session's recovery, not to
                // the shard-wide page. Worker already acquires candidates separately.
                items.insert(intent.request.input.sid);
            }
            let next = if cursor < cut {
                Some(PageToken(
                    serde_json::to_string(&(cut, cursor)).map_err(|_| invalid("history_cursor"))?,
                ))
            } else {
                None
            };
            Ok(Page {
                items: items.into_iter().collect(),
                next,
            })
        };
        tokio::time::timeout(Duration::from_millis(100), work)
            .await
            .map_err(|_| unknown("history_page"))?
    }
    async fn discover_intent(&self, intent: &Intent) -> Result<bool, StoreError> {
        let key = self
            .inner
            .layout
            .canonical_admission(&intent.request.scope, &intent.request.input.input_id)?;
        let Some((_, c)) = self.canonical(&key).await? else {
            return Ok(false);
        };
        let sid = c.intent.sid();
        let (version, schedule) = self.schedule(sid).await?;
        let checkpoint = self.checkpoint(sid).await?;
        let inputs = self
            .has_unconsumed(
                sid,
                checkpoint
                    .as_ref()
                    .and_then(|c| c.checkpoint.consumed.as_ref()),
            )
            .await?;
        if inputs || !schedule.slots.is_empty() || schedule.closed.is_some() {
            self.activate_discovered(sid, version, schedule).await?;
            return Ok(true);
        }
        if let Some(due) = schedule.next_check {
            self.hint(sid, Some(due)).await?;
            return Ok(true);
        }
        if checkpoint.as_ref().is_some_and(|c| {
            !c.checkpoint.tasks.is_empty()
                || c.checkpoint.outbox.is_some()
                || !c.checkpoint.deadlines.is_empty()
        }) {
            self.activate_discovered(sid, version, schedule).await?;
            return Ok(true);
        }
        Ok(false)
    }
    async fn activate_discovered(
        &self,
        sid: &Sid,
        version: Option<bcs_store_api::Version>,
        mut schedule: Schedule,
    ) -> Result<(), StoreError> {
        // Reuse the observed schedule version. A racing owner/activation makes
        // this exact CAS fail and requeues the item; it never forces stale state.
        schedule.activation = schedule
            .activation
            .checked_add(1)
            .ok_or_else(|| invalid("activation_exhausted"))?;
        schedule.next_check = Some(self.inner.clock.now());
        if !self
            .save(
                &self.inner.layout.schedule(sid)?,
                version,
                &schedule,
                "historical_discovery",
            )
            .await?
        {
            return Err(unknown("historical_discovery"));
        }
        self.hint(sid, schedule.next_check).await
    }
    async fn maintenance(
        &self,
        shard: ShardId,
        discovery: bool,
    ) -> Result<AdmissionMaintenance, StoreError> {
        tokio::time::timeout(
            Duration::from_millis(100),
            self.maintenance_round(shard, discovery),
        )
        .await
        .map_err(|_| unknown("maintenance_round"))?
    }
    async fn maintenance_round(
        &self,
        shard: ShardId,
        discovery: bool,
    ) -> Result<AdmissionMaintenance, StoreError> {
        // Includes root/scan initialization, bounded item work, and the final
        // cursor write; reserve the last 20ms of this turn for that write.
        let started = Instant::now();
        let kind = if discovery { "discovery" } else { "repair" };
        let kind_index = usize::from(discovery);
        let key = self.inner.layout.admission_scan(shard, kind)?;
        let root_key = self.inner.layout.admission_root(shard);
        let (root, stored) =
            tokio::try_join!(self.load::<JournalRoot>(&root_key), self.load::<Scan>(&key))?;
        let root = root.map(|(_, r)| r).unwrap_or_default();
        let (version, mut scan) = stored.map_or((None, Scan::default()), |(v, s)| (Some(v), s));
        if scan.cursor >= scan.cut && scan.pending.is_empty() && scan.round > 0 {
            // A previous turn may have persisted its completed cut with too little
            // time left to publish health. Finish that proof before a new round.
            self.update_lag(shard, discovery, &scan).await?;
        }
        if scan.cursor >= scan.cut {
            scan.cut = root.next;
            scan.cursor = if discovery { 0 } else { scan.completed };
            scan.started_at = self.inner.clock.now().0;
            scan.round = scan
                .round
                .checked_add(1)
                .ok_or_else(|| invalid("scan_round_exhausted"))?;
            self.begin_health_round(shard, &root, discovery, scan.round)?;
        }
        let mut report = AdmissionMaintenance::default();
        let mut bytes = 0;
        let retry = scan
            .pending
            .range((
                std::ops::Bound::Excluded(scan.retry_after),
                std::ops::Bound::Unbounded,
            ))
            .next()
            .copied()
            .or_else(|| scan.pending.first().copied());
        let mut retry = retry.into_iter();
        // A slow unresolved item may consume its whole time slice. Alternate
        // which lane goes first durably, so it cannot starve forward discovery.
        let mut forward_first = !scan.retry_first;
        scan.retry_first = !scan.retry_first;
        while report.examined < 128
            // Avoid starting a fresh multi-IO item in the final few milliseconds;
            // returning a shorter page preserves already completed forward work.
            && started.elapsed() < Duration::from_millis(50)
            && bytes + self.inner.config.max_input_bytes + 512 <= 1024 * 1024
        {
            let pending = if forward_first && scan.cursor < scan.cut {
                forward_first = false;
                None
            } else {
                retry.next()
            };
            let seq = match pending {
                Some(seq) => {
                    scan.retry_after = seq;
                    seq
                }
                None if scan.cursor < scan.cut => scan.cursor + 1,
                None => break,
            };
            if !self
                .inner
                .scan_rate
                .lock()
                .map_err(|_| invalid("scan_budget_lock"))?
                .claim(kind_index)
            {
                break;
            }
            let left = Duration::from_millis(80).saturating_sub(started.elapsed());
            // Reserve before IO: decoding/validation failures still consumed the
            // physical log bytes and may not reset this round's read budget.
            let reserved = self.inner.config.max_input_bytes + 512;
            bytes += reserved;
            let action = async {
                let intent = self.intent(shard, seq).await?;
                let size = serde_json::to_vec(&intent)
                    .map_err(|_| invalid("intent_encoding"))?
                    .len();
                if size > self.inner.config.max_input_bytes + 512 {
                    return Err(invalid("intent_byte_budget"));
                }
                bytes -= reserved - size;
                if discovery {
                    Ok((true, self.discover_intent(&intent).await?))
                } else {
                    Ok((self.repair_one(&intent).await?, false))
                }
            };
            let result = tokio::time::timeout(left, action).await;
            report.examined += 1;
            if pending.is_none() {
                scan.cursor = seq;
            }
            match result {
                Ok(Ok((true, activated))) => {
                    scan.pending.remove(&seq);
                    report.completed += 1;
                    if activated {
                        report.activated += 1;
                    }
                }
                _ => {
                    scan.pending.insert(seq);
                    report.pending += 1;
                }
            }
            if bytes >= 1024 * 1024 {
                break;
            }
        }
        let completed = scan
            .pending
            .first()
            .map_or(scan.cursor, |gap| scan.cursor.min(gap.saturating_sub(1)));
        scan.completed = scan.completed.max(completed);
        report.pending = scan.pending.len() as u32;
        report.completed_through = scan.completed;
        report.round_complete = scan.cursor >= scan.cut;
        // CAS prevents concurrent workers from erasing each other's unresolved
        // items. Conflict discards this local progress; operations remain durable.
        if !self.save(&key, version, &scan, kind).await? {
            return Err(unknown(kind));
        }
        if started.elapsed() < Duration::from_millis(70) {
            self.update_lag(shard, discovery, &scan).await?;
        }
        Ok(report)
    }
    async fn update_lag(
        &self,
        shard: ShardId,
        discovery: bool,
        scan: &Scan,
    ) -> Result<(), StoreError> {
        if scan.cursor < scan.cut || !scan.pending.is_empty() {
            return Ok(());
        }
        let Some(started) = self.health_round_start(shard, discovery, scan.round)? else {
            return Ok(());
        };
        let key = self.inner.layout.admission_root(shard);
        let mut budget = EqBudget::new();
        loop {
            budget.attempt("maintenance_health").await?;
            let Some((v, mut root)) = budget
                .io("maintenance_health", self.load::<JournalRoot>(&key))
                .await?
            else {
                return Ok(());
            };
            let completed_round = if discovery {
                root.discovery_healthy_round
            } else {
                root.repair_healthy_round
            };
            if completed_round >= scan.round {
                return Ok(());
            }
            // Include an upper bound on publication IO: a different producer
            // may observe the previous counter while this CAS is in flight.
            let age = u64::try_from(started.elapsed().as_millis())
                .map_err(|_| invalid("maintenance_age"))?
                .saturating_add(500);
            if discovery {
                root.discovery_healthy_round = scan.round;
                root.discovery_health_age_millis = age;
            } else {
                root.repair_healthy_round = scan.round;
                root.repair_health_age_millis = age;
            }
            let generation = if discovery {
                &mut root.discovery_health
            } else {
                &mut root.repair_health
            };
            *generation = generation
                .checked_add(1)
                .ok_or_else(|| invalid("maintenance_generation_exhausted"))?;
            if budget
                .io(
                    "maintenance_health",
                    self.save(&key, Some(v), &root, "maintenance_health"),
                )
                .await?
            {
                return self.confirm_health(shard, &root, discovery, started);
            }
        }
    }
}
#[async_trait]
impl AdmissionMaintenancePort for StoreInboxProducer {
    async fn repair(&self, shard: ShardId) -> Result<AdmissionMaintenance, StoreError> {
        self.maintenance(shard, false).await
    }
    async fn discover(&self, shard: ShardId) -> Result<AdmissionMaintenance, StoreError> {
        self.maintenance(shard, true).await
    }
}
