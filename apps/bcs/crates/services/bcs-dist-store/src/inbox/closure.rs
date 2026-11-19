//! Recoverable close/cut/terminal-proof scan and conditional gate recovery.
mod progress;
mod recovery;
use super::{StoreInboxProducer, state::*, storage::*};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{admission::*, common::*, control::*, session::*};
use bcs_store_api::zset::{ZAddMode, ZSetEntry};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

#[derive(Clone)]
pub struct StoreAdmissionControl {
    store: StoreInboxProducer,
    terminal: Arc<dyn TerminalTaskEvidencePort>,
}
impl StoreAdmissionControl {
    pub fn new(store: StoreInboxProducer, terminal: Arc<dyn TerminalTaskEvidencePort>) -> Self {
        Self { store, terminal }
    }
    async fn authorize(
        &self,
        owner: &SessionOwnership,
        operation: &RequestId,
    ) -> Result<SessionControl, StoreError> {
        let Some((_, raw)) = self
            .store
            .load::<serde_json::Value>(&self.store.inner.layout.ckpt(&owner.sid)?)
            .await?
        else {
            return Err(invalid("closure_owner_fenced"));
        };
        let stored: ShardOwner = crate::codec::decode_json(raw["owner"].clone())?;
        if stored != owner.owner || crate::layout::KeyLayout::shard_of(&owner.sid)? != owner.shard {
            return Err(invalid("closure_owner_fenced"));
        }
        let control: SessionControl = crate::codec::decode_json(raw["control"].clone())?;
        if !matches!(&control.mode,ControlMode::Quarantined { isolation } | ControlMode::Blocked { isolation } | ControlMode::Recovering { isolation,.. } if isolation.operation_id==*operation)
        {
            return Err(invalid("closure_control_changed"));
        }
        Ok(control)
    }
    async fn checked_schedule(
        &self,
        owner: &SessionOwnership,
        closure: &AdmissionClosure,
    ) -> Result<(Option<bcs_store_api::Version>, Schedule), StoreError> {
        self.authorize(owner, &closure.operation_id).await?;
        let (version, schedule) = self.store.schedule(&owner.sid).await?;
        if schedule.owner.as_ref() != Some(&owner.owner)
            || closure.sid != owner.sid
            || !schedule.closed.as_ref().is_some_and(|c| {
                c.operation == closure.operation_id
                    && c.generation == closure.generation
                    && c.cut == Some(closure.journal_cut)
            })
        {
            return Err(invalid("stale_closure"));
        }
        Ok((version, schedule))
    }
    async fn evidence(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<TerminalTaskEvidence>, StoreError> {
        let evidence = tokio::time::timeout(
            std::time::Duration::from_secs(2),
            self.terminal.terminal_evidence(scope, tid),
        )
        .await
        .map_err(|_| unknown("closure_terminal_evidence"))??;
        if let Some(e) = &evidence
            && (e.scope != *scope
                || e.tid != *tid
                || e.sid.0.is_empty()
                || e.source_commit.0 == 0
                || e.terminal_event.commit > e.source_commit)
        {
            return Err(invalid("closure_terminal_identity"));
        }
        Ok(evidence)
    }
}
#[async_trait]
impl AdmissionControlPort for StoreAdmissionControl {
    async fn gate_state(&self, owner: &SessionOwnership) -> Result<AdmissionGateState, StoreError> {
        self.store.gate_state(owner).await
    }
    async fn close_gate(
        &self,
        guard: ControlGuard,
        generation: u64,
        operation: RequestId,
    ) -> Result<GateCloseOutcome, StoreError> {
        self.store.close_gate(guard, generation, operation).await
    }
    async fn closure_obligations(
        &self,
        closure: &AdmissionClosure,
        page: PageRequest,
    ) -> Result<Page<AdmissionObligation>, StoreError> {
        self.store.closure_obligations(closure, page).await
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
enum Materialize {
    Spec,
    ScopeIndex,
    SessionIndex,
    Task,
    Verify,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Pending {
    sequence: u64,
    obligation: AdmissionObligation,
    phase: Materialize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Progress {
    closure: AdmissionClosure,
    cursor: u64,
    pending: Option<Pending>,
    checkpoint_tasks: Vec<Tid>,
    checkpoint_cursor: usize,
    terminal_tasks: u64,
    evidence_digest: Digest,
}
impl Progress {
    fn validate(&self) -> Result<(), StoreError> {
        if self.cursor > self.closure.journal_cut
            || self.checkpoint_cursor > self.checkpoint_tasks.len()
            || self.checkpoint_tasks.len() > 128
            || self.evidence_digest.0.is_empty()
            || self.evidence_digest.0.len() > 256
            || self.pending.as_ref().is_some_and(|pending| {
                self.cursor.checked_add(1) != Some(pending.sequence)
                    || pending.sequence > self.closure.journal_cut
            })
            || self
                .checkpoint_tasks
                .iter()
                .collect::<std::collections::BTreeSet<_>>()
                .len()
                != self.checkpoint_tasks.len()
        {
            return Err(invalid("closure_progress_invalid"));
        }
        Ok(())
    }
    fn work(&self, obligation: Option<AdmissionObligation>) -> ClosureWork {
        ClosureWork {
            closure: self.closure.clone(),
            completed_through: self.cursor,
            obligation,
            certificate: None,
        }
    }
    fn confirmed(&mut self, evidence: &TerminalTaskEvidence) -> Result<(), StoreError> {
        let encoded =
            serde_json::to_vec(evidence).map_err(|_| invalid("closure_evidence_encoding"))?;
        self.evidence_digest = crate::identity::digest_fields(&[
            b"closure-progress:v1",
            self.evidence_digest.0.as_bytes(),
            &encoded,
        ]);
        self.terminal_tasks = self
            .terminal_tasks
            .checked_add(1)
            .ok_or_else(|| invalid("closure_count"))?;
        Ok(())
    }
}
