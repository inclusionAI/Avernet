//! Each repair visit advances one durable phase. Normal RTT cannot force every
//! retry to repeat an unbounded prefix before reaching its next write.
use super::{StoreInboxProducer, state::*, storage::*};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::AdmitOutcome};
use bcs_store_api::{
    Version,
    zset::{ZAddMode, ZSetEntry},
};
use std::time::Duration;

pub(super) enum Progress {
    Changed,
    Retry,
    Done(AdmitOutcome),
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
            self.release_slot(key, &c).await?;
            return Ok(Progress::Done(self.outcome(&c, decision, new)));
        }
        match c.phase {
            Phase::Created => {
                if let Some(decision) = self.register(key, &c).await? {
                    c.decision = Some(decision);
                } else {
                    c.registered = true;
                    c.phase = Phase::Registered;
                }
            }
            Phase::Registered => {
                if let Some(spec) = &c.request.create_session {
                    let spec_key = self.inner.layout.spec(&spec.sid)?;
                    if !self.save(&spec_key, None, spec, key).await? {
                        let (_, stored) = self
                            .load::<SessionSpec>(&spec_key)
                            .await?
                            .ok_or_else(|| unknown(key))?;
                        if stored.scope != spec.scope || stored.group != spec.group {
                            return Err(invalid("frozen_spec_conflict"));
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
                        return Err(invalid("frozen_task_conflict"));
                    }
                }
                c.phase = Phase::TaskReady;
            }
            Phase::TaskReady if c.position.is_some() => {
                c.decision = Some(self.activate_slot(key, &c).await?);
            }
            Phase::TaskReady => return self.append_step(v, c, key).await,
        }
        Ok(if self.save(key, Some(v), &c, key).await? {
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
