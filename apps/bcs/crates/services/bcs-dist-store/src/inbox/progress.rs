//! Each repair visit advances one durable phase. Normal RTT cannot force every
//! retry to repeat an unbounded prefix before reaching its next write.
use super::{
    StoreInboxProducer,
    admission::{OccupiedSlot, Registration},
    state::*,
    storage::*,
};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::StoreError,
    inbox::{AdmitOutcome, AdmitRequest},
    session::TaskPhase,
};
use bcs_store_api::{
    Version,
    zset::{ZAddMode, ZSetEntry},
};
use std::time::Duration;

pub(super) enum Progress {
    Changed,
    Retry,
    Done(AdmitOutcome),
    /// Staged strict writes are durable repairs only: without the checkpoint
    /// license no Start entry is appended and no schedule activation happens.
    Staged,
    Full(OccupiedSlot),
}
impl StoreInboxProducer {
    /// One checkpoint read verifies the exact cancel target and that it
    /// still takes controls. The Worker validates the creation namespace.
    pub(super) async fn strict_cancel_round(
        &self,
        request: &AdmitRequest,
    ) -> Result<Option<StrictRound>, StoreError> {
        let Some(envelope) = self.checkpoint(&request.input.sid).await? else {
            return Ok(None);
        };
        let checkpoint = envelope.checkpoint;
        let Some(license) = checkpoint.runner_state.as_ref()
            .filter(|runner| runner.tid == request.input.tid)
            .and_then(|runner| crate::group_admission::decode_strict_license(&runner.state.value))
        else {
            return Ok(None);
        };
        Ok((license.schema_version == 1
            && license.scope == request.scope
            && license.sid == request.input.sid
            && license.tid == request.input.tid
            && checkpoint.tasks.iter().any(|task| {
                task.tid == license.tid && matches!(task.phase, TaskPhase::Queued | TaskPhase::Running)
            })).then_some(StrictRound {
            request_id: license.request_id,
            generation: license.generation.get(),
        }))
    }

    /// License gate for staged strict rounds: the Start feed entry is
    /// delivered only when the checkpoint license names this exact request
    /// id, round generation and Tid. One bounded document read.
    pub(super) async fn round_licensed(&self, c: &AdmissionRecord) -> Result<bool, StoreError> {
        let Some(strict) = &c.strict else {
            return Ok(true);
        };
        let Some(license) = crate::group_admission::probe_strict_license(
            &self.inner.documents,
            &self.inner.layout,
            c.sid(),
        )
        .await?
        else {
            return Ok(false);
        };
        Ok(license.request_id == strict.request_id
            && license.generation.get() == strict.generation
            && license.tid == c.request.input.tid)
    }
}
impl StoreInboxProducer {
    pub(super) async fn progress(
        &self,
        v: Version,
        mut c: AdmissionRecord,
        key: &str,
        new: bool,
    ) -> Result<Progress, StoreError> {
        if let Some(decision) = &c.decision {
            // Strict round slots are session claim marks: they stay until
            // the strict lifecycle (T4/T7) frees them, not until this record
            // completes, so generic entries keep seeing the claim.
            if !c.start() || c.strict.is_none() {
                self.release_slot(key, &c).await?;
            }
            return Ok(Progress::Done(self.outcome(&c, decision, new)));
        }
        match c.phase {
            Phase::Created => match self.register(key, &c).await? {
                Registration::Full(slot) => return Ok(Progress::Full(slot)),
                Registration::Ready(Some(decision)) => c.decision = Some(decision),
                Registration::Ready(None) => {
                    c.registered = true;
                    c.phase = Phase::Registered;
                }
            },
            Phase::Registered => {
                if let Some(spec) = &c.request.create_session {
                    let spec_key = self.inner.layout.spec(&spec.sid)?;
                    if !self.save(&spec_key, None, spec, key).await? {
                        let (_, stored) = self
                            .load::<SessionSpec>(&spec_key)
                            .await?
                            .ok_or_else(|| unknown(key))?;
                        if stored.scope != spec.scope || stored.group != spec.group {
                            return self.finish_content_conflict(v, c, key).await;
                        }
                    }
                }
                c.phase = if c.request.create_task.is_some() {
                    Phase::SpecReady
                } else {
                    Phase::TaskReady
                };
            }
            Phase::SpecReady | Phase::ScopeIndexed => {
                let task = c
                    .request
                    .create_task
                    .as_ref()
                    .ok_or_else(|| invalid("initial_task_missing"))?;
                let index = if c.phase == Phase::SpecReady {
                    self.inner.layout.tasks(&task.scope)?
                } else {
                    self.inner.layout.session_tasks(&task.sid)?
                };
                let _permit = self.io_permit().await?;
                tokio::time::timeout(
                    Duration::from_millis(500),
                    self.inner.zset.add(
                        &index,
                        &[ZSetEntry {
                            member: task.tid.0.clone(),
                            score: 0.0,
                        }],
                        ZAddMode::InsertOnly,
                    ),
                )
                .await
                .map_err(|_| unknown(key))?
                .map_err(|e| crate::error::from_write(&e, key))?;
                c.phase = if c.phase == Phase::SpecReady {
                    Phase::ScopeIndexed
                } else {
                    Phase::SessionIndexed
                };
            }
            Phase::SessionIndexed => {
                let task = c
                    .request
                    .create_task
                    .as_ref()
                    .ok_or_else(|| invalid("initial_task_missing"))?;
                let task_key = self.inner.layout.task(&task.tid)?;
                if !self.save(&task_key, None, task, key).await? {
                    let (_, stored) = self
                        .load::<TaskSnapshot>(&task_key)
                        .await?
                        .ok_or_else(|| unknown(key))?;
                    if stored.scope != task.scope
                        || stored.sid != task.sid
                        || stored.tid != task.tid
                    {
                        return self.finish_content_conflict(v, c, key).await;
                    }
                }
                c.phase = Phase::TaskReady;
            }
            Phase::TaskReady if c.start() && c.strict.is_some() && !self.round_licensed(&c).await? => {
                // The snapshot/index writes above are repairable scaffolding.
                // Visibility and execution ride the checkpoint license alone.
                return Ok(Progress::Staged);
            }
            Phase::TaskReady if !c.start() && c.strict.is_some()
                && self.strict_cancel_round(&c.request).await? != c.strict => {
                // Recheck before both append and decision, including when a
                // reader repairs an observed append. A stale row is skipped,
                // never left pending in front of a successor's entries.
                c.decision = Some(self.decide_slot(key, &c, Decision::Rejected).await?);
            }
            Phase::TaskReady if c.position.is_some() => {
                c.decision = Some(self.decide_slot(key, &c, Decision::Accepted).await?);
            }
            Phase::TaskReady => return self.append_step(v, c, key).await,
        }
        self.save_progress(v, &c, key).await
    }
    async fn finish_content_conflict(
        &self,
        v: Version,
        mut c: AdmissionRecord,
        key: &str,
    ) -> Result<Progress, StoreError> {
        // These identity fields are frozen. After an interrupted write, the
        // same phase finds the conflict again and copies the existing slot decision.
        c.decision = Some(self.decide_slot(key, &c, Decision::ContentConflict).await?);
        self.save_progress(v, &c, key).await
    }
    async fn save_progress(
        &self,
        v: Version,
        c: &AdmissionRecord,
        key: &str,
    ) -> Result<Progress, StoreError> {
        Ok(if self.save(key, Some(v), c, key).await? {
            Progress::Changed
        } else {
            Progress::Retry
        })
    }
    async fn append_step(
        &self,
        v: Version,
        mut c: AdmissionRecord,
        key: &str,
    ) -> Result<Progress, StoreError> {
        let mut fields = crate::codec::encode_inbox_entry(&c.request.input)?;
        fields.insert("admission".into(), key.as_bytes().to_vec());
        if c.append_credits == 0 {
            self.reserve_append(
                c.sid(),
                self.reliable_bytes(&c.request)?,
                c.start(),
                true,
                key,
            )
            .await?;
            c.append_credits = 1;
            return Ok(if self.save(key, Some(v), &c, key).await? {
                Progress::Changed
            } else {
                Progress::Retry
            });
        }
        // Consume exactly one durable credit before append. A lost claim reply
        // cannot authorize an uncharged append; a later visit uses repair reserve.
        c.append_credits -= 1;
        c.append_attempts = c
            .append_attempts
            .checked_add(1)
            .ok_or_else(|| invalid("append_attempt_exhausted"))?;
        let Some(claimed) = self.save_version(key, Some(v), &c, key).await? else {
            return Ok(Progress::Retry);
        };
        let id = self.append(c.sid(), &fields, key).await?;
        c.position = Some(self.position(c.sid(), id));
        // A racing claim/decision may win. Its position/decision is authoritative;
        // this append remains a physical duplicate with durable responsibility.
        self.save(key, Some(claimed), &c, key).await?;
        Ok(Progress::Changed)
    }
}
