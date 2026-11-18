use super::{StoreInboxProducer, state::*, storage::*};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    admission::*, common::*, inbox::*, session::CheckpointEnvelope,
};
use bcs_store_api::zset::{ZSetEntry, ZSetRange};
use std::ops::Bound;

impl StoreInboxProducer {
    pub(super) async fn sealed(&self, owner: &SessionOwnership) -> Result<bool, StoreError> {
        if crate::layout::KeyLayout::shard_of(&owner.sid)? != owner.shard {
            return Ok(false);
        }
        Ok(self
            .load::<CheckpointEnvelope>(&self.inner.layout.ckpt(&owner.sid)?)
            .await?
            .is_some_and(|(_, c)| c.owner == owner.owner))
    }
    pub(super) async fn activate(&self, sid: &Sid, op: &str) -> Result<(), StoreError> {
        let key = self.inner.layout.schedule(sid)?;
        let mut budget = EqBudget::new();
        loop {
            budget.attempt(op).await?;
            let (v, mut s) = budget.io(op, self.schedule(sid)).await?;
            s.activation = s
                .activation
                .checked_add(1)
                .ok_or_else(|| invalid("activation_exhausted"))?;
            s.next_check = Some(self.inner.clock.now());
            if budget.io(op, self.save(&key, v, &s, op)).await? {
                let _ = self.hint(sid, s.next_check).await;
                return Ok(());
            }
        }
    }
}
#[async_trait]
impl InboxSchedulingPort for StoreInboxProducer {
    async fn observe(&self, owner: &SessionOwnership) -> Result<ScheduleObservation, StoreError> {
        let key = self.inner.layout.schedule(&owner.sid)?;
        let mut budget = EqBudget::new();
        loop {
            budget.attempt("schedule_observe").await?;
            if !budget.io("schedule_observe", self.sealed(owner)).await? {
                return Ok(ScheduleObservation::Fenced);
            }
            let (v, mut s) = budget
                .io("schedule_observe", self.schedule(&owner.sid))
                .await?;
            if s.owner.as_ref().is_some_and(|o| {
                o.epoch > owner.owner.epoch || (o.epoch == owner.owner.epoch && o != &owner.owner)
            }) {
                return Ok(ScheduleObservation::Fenced);
            }
            let token = ScheduleToken {
                sid: owner.sid.clone(),
                owner_epoch: owner.owner.epoch,
                opaque: s.activation.to_string(),
            };
            if s.owner.as_ref() == Some(&owner.owner) && s.listening {
                return Ok(ScheduleObservation::Observed(token));
            }
            s.owner = Some(owner.owner.clone());
            s.listening = true;
            if budget
                .io(
                    "schedule_observe",
                    self.save(&key, v, &s, "schedule_observe"),
                )
                .await?
            {
                return Ok(ScheduleObservation::Observed(token));
            }
        }
    }
    async fn settle(&self, r: SettleSession) -> Result<SettleOutcome, StoreError> {
        if r.observed.sid != r.owner.sid || r.observed.owner_epoch != r.owner.owner.epoch {
            return Ok(SettleOutcome::Fenced);
        }
        let expected = r
            .observed
            .opaque
            .parse::<u64>()
            .map_err(|_| invalid("schedule_token"))?;
        let key = self.inner.layout.schedule(&r.owner.sid)?;
        let mut budget = EqBudget::new();
        budget.attempt("schedule_settle").await?;
        if !budget.io("schedule_settle", self.sealed(&r.owner)).await? {
            return Ok(SettleOutcome::Fenced);
        }
        let (v, mut s) = budget
            .io("schedule_settle", self.schedule(&r.owner.sid))
            .await?;
        if s.owner.as_ref() != Some(&r.owner.owner) {
            return Ok(SettleOutcome::Fenced);
        }
        if s.activation != expected {
            return Ok(SettleOutcome::NeedsRecheck);
        }
        s.next_check = r.next_check;
        s.listening = false;
        if budget
            .io("schedule_settle", self.save(&key, v, &s, "schedule_settle"))
            .await?
        {
            let _ = self.hint(&r.owner.sid, r.next_check).await;
            return Ok(SettleOutcome::Applied);
        }
        // An EQ conflict invalidates the work decision. Never refresh a token
        // and retry the caller's old result without checking obligations again.
        Ok(SettleOutcome::NeedsRecheck)
    }
    async fn due(
        &self,
        shard: ShardId,
        now: TimestampMillis,
        page: PageRequest,
    ) -> Result<Page<Sid>, StoreError> {
        let work = async {
            if page.limit.get() > 128 {
                return Err(invalid("page_limit"));
            }
            let after = page
                .after
                .map(|p| {
                    serde_json::from_str::<(f64, String)>(&p.0)
                        .map(|(score, member)| ZSetEntry { score, member })
                        .map_err(|_| invalid("due_cursor"))
                })
                .transpose()?;
            let _permit = self.io_permit().await?;
            let entries = tokio::time::timeout(
                std::time::Duration::from_millis(100),
                self.inner.zset.range(
                    &self.inner.layout.active(shard),
                    &ZSetRange {
                        min: Bound::Unbounded,
                        max: Bound::Included(now.0 as f64),
                        after,
                        limit: page.limit,
                    },
                ),
            )
            .await
            .map_err(|_| unknown("due_scan"))?
            .map_err(|e| crate::error::from_read(&e))?;
            drop(_permit);
            let next = entries
                .last()
                .filter(|_| entries.len() == page.limit.get() as usize)
                .map(|e| {
                    serde_json::to_string(&(e.score, &e.member))
                        .map(PageToken)
                        .map_err(|_| invalid("due_cursor"))
                })
                .transpose()?;
            // This is an accelerator page. The caller's observe + final obligation
            // check consults the authority document before settling or running work.
            let items = entries
                .into_iter()
                .filter_map(|entry| {
                    let sid = Sid(entry.member);
                    (crate::layout::KeyLayout::shard_of(&sid).ok() == Some(shard)).then_some(sid)
                })
                .collect();
            Ok(Page { items, next })
        };
        tokio::time::timeout(std::time::Duration::from_millis(100), work)
            .await
            .map_err(|_| unknown("due_page"))?
    }
}
#[async_trait]
impl AdmissionControlPort for StoreInboxProducer {
    async fn close_gate(
        &self,
        owner: &SessionOwnership,
        generation: u64,
        operation: RequestId,
    ) -> Result<GateCloseOutcome, StoreError> {
        if operation.0.is_empty() || operation.0.len() > 256 {
            return Err(invalid("closure_identity"));
        }
        let key = self.inner.layout.schedule(&owner.sid)?;
        let mut budget = EqBudget::new();
        loop {
            budget.attempt(&operation.0).await?;
            if !budget.io(&operation.0, self.sealed(owner)).await? {
                return Ok(GateCloseOutcome::Fenced);
            }
            let (v, mut s) = budget.io(&operation.0, self.schedule(&owner.sid)).await?;
            if s.owner.as_ref() != Some(&owner.owner) {
                return Ok(GateCloseOutcome::Fenced);
            }
            if s.generation != generation {
                return Ok(GateCloseOutcome::Conflict);
            }
            if let Some(closed) = &s.closed {
                if closed.operation != operation || closed.generation != generation {
                    return Ok(GateCloseOutcome::Conflict);
                }
                if let Some(cut) = closed.cut {
                    return Ok(GateCloseOutcome::Closed(AdmissionClosure {
                        sid: owner.sid.clone(),
                        generation,
                        operation_id: operation,
                        journal_cut: cut,
                    }));
                }
                let cut = budget
                    .io(
                        &operation.0,
                        self.load::<JournalRoot>(&self.inner.layout.admission_root(owner.shard)),
                    )
                    .await?
                    .map_or(0, |(_, r)| r.next);
                if let Some(closed) = s.closed.as_mut() {
                    closed.cut = Some(cut)
                }
            } else {
                s.closed = Some(ClosedGate {
                    operation: operation.clone(),
                    generation,
                    cut: None,
                });
                s.activation = s
                    .activation
                    .checked_add(1)
                    .ok_or_else(|| invalid("activation_exhausted"))?;
                s.next_check = Some(self.inner.clock.now());
            }
            if budget
                .io(&operation.0, self.save(&key, v, &s, &operation.0))
                .await?
            {
                // The cut is read only after close is durably confirmed. A lost
                // response repeats this identity and cannot reopen the gate.
                budget = EqBudget::new();
            }
        }
    }
    async fn closure_obligations(
        &self,
        closure: &AdmissionClosure,
        page: PageRequest,
    ) -> Result<Page<AdmissionObligation>, StoreError> {
        let work = async {
            if page.limit.get() > 128 {
                return Err(invalid("page_limit"));
            }
            let (_, s) = self.schedule(&closure.sid).await?;
            if !s.closed.as_ref().is_some_and(|c| {
                c.operation == closure.operation_id
                    && c.generation == closure.generation
                    && c.cut == Some(closure.journal_cut)
            }) {
                return Err(invalid("stale_closure"));
            }
            let shard = crate::layout::KeyLayout::shard_of(&closure.sid)?;
            let mut cursor = page
                .after
                .map(|p| p.0.parse::<u64>().map_err(|_| invalid("closure_cursor")))
                .transpose()?
                .unwrap_or(0);
            if cursor > closure.journal_cut {
                return Err(invalid("closure_cursor"));
            }
            let mut items = Vec::new();
            let initial_cursor = cursor;
            let started = std::time::Instant::now();
            let mut bytes = 0;
            let end = cursor
                .saturating_add(u64::from(page.limit.get()))
                .min(closure.journal_cut);
            while cursor < end
                && started.elapsed() < std::time::Duration::from_millis(80)
                && bytes + self.inner.config.max_input_bytes + 512 <= 1024 * 1024
            {
                let next = cursor + 1;
                let deadline = started + std::time::Duration::from_millis(80);
                let item = tokio::time::timeout_at(
                    tokio::time::Instant::from_std(deadline),
                    self.closure_item(closure, shard, next),
                )
                .await;
                match item {
                    Ok(Ok(Some((obligation, size)))) => {
                        bytes += size;
                        cursor = next;
                        if let Some(obligation) = obligation {
                            items.push(obligation);
                        }
                    }
                    Ok(Ok(None)) | Err(_) => break,
                    Ok(Err(error)) if cursor == initial_cursor => return Err(error),
                    Ok(Err(_)) => break,
                }
            }
            Ok(Page {
                items,
                next: (cursor < closure.journal_cut).then(|| PageToken(cursor.to_string())),
            })
        };
        tokio::time::timeout(std::time::Duration::from_millis(100), work)
            .await
            .map_err(|_| unknown("closure_obligations_page"))?
    }
}

impl StoreInboxProducer {
    async fn closure_item(
        &self,
        closure: &AdmissionClosure,
        shard: ShardId,
        sequence: u64,
    ) -> Result<Option<(Option<AdmissionObligation>, usize)>, StoreError> {
        let intent = self.intent(shard, sequence).await?;
        let bytes = encoded_size(&intent, self.inner.config.max_input_bytes + 512)?;
        if intent.sid() != &closure.sid {
            return Ok(Some((None, bytes)));
        }
        let key = self
            .inner
            .layout
            .canonical_admission(&intent.request.scope, &intent.request.input.input_id)?;
        let Some((v, mut c)) = self.canonical(&key).await? else {
            self.save(&key, None, &self.initial_canonical(&intent), &key)
                .await?;
            return Ok(None);
        };
        if c.intent.sid() != &closure.sid
            || !c.intent.start()
            || c.intent.generation != closure.generation
            || c.intent.sequence != sequence
        {
            return Ok(Some((None, bytes)));
        }
        let decision = if let Some(decision) = c.decision.clone() {
            decision
        } else {
            // Closing does not replay initial creation. The same authoritative
            // slot preserves an earlier Accepted or fixes the old generation's
            // Rejected decision; creators already authorized remain obligations.
            let Some(decision) = self.register(&key, &c).await? else {
                return Ok(None);
            };
            c.decision = Some(decision.clone());
            if !self.save(&key, Some(v), &c, &key).await? {
                return Ok(None);
            }
            decision
        };
        Ok(Some((
            Some(AdmissionObligation {
                request: c.intent.request,
                accepted: decision == Decision::Accepted,
                may_have_created_task: c.registered,
            }),
            bytes,
        )))
    }
}
