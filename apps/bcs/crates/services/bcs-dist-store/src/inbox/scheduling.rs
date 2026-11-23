use super::{StoreInboxProducer, state::*, storage::*};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{admission::*, common::*, control::*, inbox::*};
use bcs_store_api::zset::{ZSetEntry, ZSetRange};
use std::ops::Bound;

impl StoreInboxProducer {
    pub(super) async fn sealed(&self, owner: &SessionOwnership) -> Result<bool, StoreError> {
        if crate::layout::KeyLayout::shard_of(&owner.sid)? != owner.shard {
            return Ok(false);
        }
        Ok(self
            .load::<serde_json::Value>(&self.inner.layout.ckpt(&owner.sid)?)
            .await?
            .is_some_and(|(_, c)| {
                serde_json::from_value::<ShardOwner>(c["owner"].clone())
                    .ok()
                    .as_ref()
                    == Some(&owner.owner)
            }))
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
    fn scan_budget_profile(
        &self,
    ) -> Option<bcs_service_api::port::repo::distributed::budget::ScanBudgetProfile> {
        Some(self.inner.scan_budget.profile())
    }
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
            let observed = ScheduleToken {
                sid: owner.sid.clone(),
                owner_epoch: owner.owner.epoch,
                opaque: s.activation.to_string(),
            };
            if s.owner.as_ref() == Some(&owner.owner) && s.listening {
                return Ok(ScheduleObservation::Observed(observed));
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
                return Ok(ScheduleObservation::Observed(observed));
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
            let _scan_io =
                self.inner
                    .scan_budget
                    .try_io()
                    .ok_or_else(|| StoreError::NotApplied {
                        code: "scan_io_busy".into(),
                    })?;
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
    async fn gate_state(&self, owner: &SessionOwnership) -> Result<AdmissionGateState, StoreError> {
        if !self.sealed(owner).await? {
            return Err(invalid("gate_owner_fenced"));
        }
        let (_, s) = self.schedule(&owner.sid).await?;
        if s.owner.as_ref() != Some(&owner.owner) {
            return Err(invalid("gate_owner_fenced"));
        }
        Ok(AdmissionGateState {
            generation: s.generation,
            closed_operation: s.closed.as_ref().map(|c| c.operation.clone()),
            closure: s.closed.map(|c| AdmissionClosure {
                sid: owner.sid.clone(),
                generation: c.generation,
                operation_id: c.operation,
                // Kept as a compatibility field; MVP closure coverage is
                // session-local and no longer derives a shard-wide cut.
                journal_cut: 0,
            }),
        })
    }
    async fn close_gate(
        &self,
        guard: ControlGuard,
        generation: u64,
        operation: RequestId,
    ) -> Result<GateCloseOutcome, StoreError> {
        if operation.0.is_empty() || operation.0.len() > 256 {
            return Err(invalid("closure_identity"));
        }
        let owner = &guard.owner;
        let key = self.inner.layout.schedule(&owner.sid)?;
        let mut budget = EqBudget::new();
        loop {
            budget.attempt(&operation.0).await?;
            if !budget.io(&operation.0, self.sealed(owner)).await? {
                return Ok(GateCloseOutcome::Fenced);
            }
            if !budget
                .io(&operation.0, self.authorized_close(&guard, &operation))
                .await?
            {
                return Ok(GateCloseOutcome::Conflict);
            }
            let (v, mut s) = budget.io(&operation.0, self.schedule(&owner.sid)).await?;
            if s.owner.as_ref() != Some(&owner.owner) {
                return Ok(GateCloseOutcome::Fenced);
            }
            if s.generation != generation || s.control_revision > guard.expected_revision {
                return Ok(GateCloseOutcome::Conflict);
            }
            s.control_revision = guard.expected_revision;
            if s.closed
                .as_ref()
                .is_some_and(|closed| closed.operation != operation)
            {
                // A freshly authorized isolation supersedes an older close.
                // Session-local closure work is re-evaluated for the new operation.
                s.closed = None;
            }
            if let Some(closed) = &s.closed {
                if closed.operation != operation || closed.generation != generation {
                    return Ok(GateCloseOutcome::Conflict);
                }
                if budget
                    .io(&operation.0, self.save(&key, v, &s, &operation.0))
                    .await?
                {
                    return Ok(GateCloseOutcome::Closed(AdmissionClosure {
                        sid: owner.sid.clone(),
                        generation,
                        operation_id: operation,
                        journal_cut: 0,
                    }));
                }
                continue;
            }
            s.closed = Some(ClosedGate {
                operation: operation.clone(),
                generation,
            });
            s.activation = s
                .activation
                .checked_add(1)
                .ok_or_else(|| invalid("activation_exhausted"))?;
            s.next_check = Some(self.inner.clock.now());
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
        if page.limit.get() > 128 || page.after.is_some() {
            return Err(invalid("closure_cursor"));
        }
        let (_, schedule) = self.schedule(&closure.sid).await?;
        if !schedule.closed.as_ref().is_some_and(|c| {
            c.operation == closure.operation_id && c.generation == closure.generation
        }) {
            return Err(invalid("stale_closure"));
        }
        // There is no shard-wide admission journal in the MVP.  Admission
        // documents are repaired by the caller's InputId retry; closure work
        // covers the session checkpoint and the current schedule slots.
        Ok(Page { items: Vec::new(), next: None })
    }

}

impl StoreInboxProducer {
    pub(super) async fn authorized_close(
        &self,
        guard: &ControlGuard,
        operation: &RequestId,
    ) -> Result<bool, StoreError> {
        let Some((_, raw)) = self
            .load::<serde_json::Value>(&self.inner.layout.ckpt(&guard.owner.sid)?)
            .await?
        else {
            return Ok(false);
        };
        let control: SessionControl = crate::codec::decode_json(
            raw.get("control").cloned().unwrap_or(
                serde_json::to_value(SessionControl::default())
                    .map_err(|_| invalid("control_encoding"))?,
            ),
        )?;
        let matching = matches!(&control.mode,ControlMode::Quarantined { isolation } | ControlMode::Blocked { isolation } if isolation.operation_id==*operation);
        Ok(matching
            && control.revision == guard.expected_revision
            && raw["owner"]
                == serde_json::to_value(&guard.owner.owner)
                    .map_err(|_| invalid("owner_encoding"))?
            && raw["checkpoint"]["commit_seq"] == guard.expected_head.0)
    }
}
