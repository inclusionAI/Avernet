//! In-memory speaking-lease repository (spec §6.3/§6.3.1). One mutex per
//! process decides acquire/preempt/release/admission atomically — the
//! reference semantics for the SQL implementation to reproduce under
//! database transactions.
use std::collections::HashMap;
use std::sync::Mutex;

use async_trait::async_trait;
use bcs_domain::speaking_lease::SessionSpeakingLease;
use bcs_service_api::port::repo::session_speaking_lease::{
    AcquireLease, AdmitMessage, LeaseAcquire, LeaseDecision, LeaseRelease, PreemptLease,
    ReleaseLease, SpeakingLeaseRepoPort,
};
use bcs_service_api::{ServiceError, ServiceResult};

#[derive(Default)]
struct Inner {
    leases: HashMap<String, SessionSpeakingLease>,
    /// Highest epoch ever reached per session: fencing stays monotonic
    /// across release/reacquire cycles (重新获取/抢占生成新的 lease_id
    /// 和递增 epoch).
    epochs: HashMap<String, u64>,
}

/// Session-keyed in-memory lease store.
#[derive(Default)]
pub struct MemorySpeakingLeaseRepo {
    inner: Mutex<Inner>,
}

impl MemorySpeakingLeaseRepo {
    pub fn new() -> Self {
        Self::default()
    }
}

fn new_lease(session_id: &str, holder: &str, epoch: u64, now_ms: i64, idle_timeout_ms: i64) -> SessionSpeakingLease {
    SessionSpeakingLease {
        session_id: session_id.to_string(),
        holder_actor_id: holder.to_string(),
        lease_id: uuid::Uuid::new_v4().to_string(),
        epoch,
        expires_at_ms: now_ms + idle_timeout_ms,
    }
}

fn validate(command_now_ms: i64, idle_timeout_ms: i64) -> Result<(), ServiceError> {
    // Closed-input rejects land before any state read: repositories must
    // never fabricate a lease from a non-positive timeout or clock.
    if idle_timeout_ms <= 0 {
        return Err(ServiceError::InvalidOperation {
            message: "speaking lease idle timeout must be positive".to_string(),
            request_id: None,
        });
    }
    if command_now_ms <= 0 {
        return Err(ServiceError::InvalidOperation {
            message: "speaking lease requires a positive server clock".to_string(),
            request_id: None,
        });
    }
    Ok(())
}

#[async_trait]
impl SpeakingLeaseRepoPort for MemorySpeakingLeaseRepo {
    async fn acquire(&self, command: AcquireLease<'_>) -> ServiceResult<LeaseAcquire> {
        validate(command.now_ms, command.idle_timeout_ms)?;
        let mut inner = self.inner.lock().expect("lease store poisoned");
        match inner.leases.get(command.session_id) {
            Some(current) if !current.is_expired(command.now_ms) => {
                if current.holder_actor_id == command.requester_actor_id {
                    // Idempotent re-entry: never renews, never rotates.
                    Ok(LeaseAcquire::SelfHeld(current.clone()))
                } else {
                    Ok(LeaseAcquire::Occupied {
                        holder_actor_id: current.holder_actor_id.clone(),
                        epoch: current.epoch,
                        expires_at_ms: current.expires_at_ms,
                    })
                }
            }
            _ => {
                // Absent or lapsed: a fresh grant rotates the lease id and
                // takes the next epoch — an expired lease never revives.
                let epoch = inner
                    .epochs
                    .get(command.session_id)
                    .copied()
                    .map(|watermark| watermark + 1)
                    .unwrap_or(1);
                let lease = new_lease(
                    command.session_id,
                    command.requester_actor_id,
                    epoch,
                    command.now_ms,
                    command.idle_timeout_ms,
                );
                inner.epochs.insert(command.session_id.to_string(), epoch);
                inner.leases.insert(command.session_id.to_string(), lease.clone());
                Ok(LeaseAcquire::Granted(lease))
            }
        }
    }

    async fn preempt(&self, command: PreemptLease<'_>) -> ServiceResult<SessionSpeakingLease> {
        validate(command.now_ms, command.idle_timeout_ms)?;
        let mut inner = self.inner.lock().expect("lease store poisoned");
        // Preemption always transfers: new lease id, strictly next epoch,
        // regardless of the previous holder or its expiry. Only moving
        // submission eligibility — old turns/runs keep their own fencing.
        let epoch = inner
            .epochs
            .get(command.session_id)
            .copied()
            .map(|watermark| watermark + 1)
            .unwrap_or(1);
        let lease = new_lease(
            command.session_id,
            command.requester_actor_id,
            epoch,
            command.now_ms,
            command.idle_timeout_ms,
        );
        inner.epochs.insert(command.session_id.to_string(), epoch);
        inner.leases.insert(command.session_id.to_string(), lease.clone());
        Ok(lease)
    }

    async fn release(&self, command: ReleaseLease<'_>) -> ServiceResult<LeaseRelease> {
        if command.now_ms <= 0 {
            return Err(ServiceError::InvalidOperation {
                message: "speaking lease requires a positive server clock".to_string(),
                request_id: None,
            });
        }
        let mut inner = self.inner.lock().expect("lease store poisoned");
        let Some(current) = inner.leases.get(command.session_id) else {
            return Ok(LeaseRelease::NotHeld);
        };
        // Release is guarded by lease id AND epoch: a stale instruction from
        // a pre-emption boundary never clears the successor's lease.
        // Expiry does not matter for release — a lapsed-but-present lease
        // can still be explicitly given back, which simply drops the row.
        let matches = current.holder_actor_id == command.holder_actor_id
            && current.lease_id == command.lease_id
            && current.epoch == command.epoch;
        if !matches {
            return Ok(LeaseRelease::NotHeld);
        }
        let released_epoch = current.epoch;
        inner.leases.remove(command.session_id);
        // Keep the epoch watermark: the next grant for this session takes
        // the strictly next epoch, never a reused one.
        inner
            .epochs
            .entry(command.session_id.to_string())
            .and_modify(|epoch| *epoch = (*epoch).max(released_epoch))
            .or_insert(released_epoch);
        Ok(LeaseRelease::Released)
    }

    async fn admit_message(&self, command: AdmitMessage<'_>) -> ServiceResult<LeaseDecision> {
        validate(command.now_ms, command.idle_timeout_ms)?;
        let mut inner = self.inner.lock().expect("lease store poisoned");
        let Some(current) = inner.leases.get(command.session_id) else {
            return Ok(LeaseDecision::Lost);
        };
        if current.is_expired(command.now_ms) {
            // A lapsed lease admits nothing and never revives via a late
            // message; the row stays until reacquired with a fresh epoch.
            return Ok(LeaseDecision::Lost);
        }
        // Fenced by lease identity first: a submission quoting a lease id
        // that is no longer the current one is superseded regardless of
        // which holder signed it.
        if current.lease_id != command.expected_lease_id {
            return Ok(LeaseDecision::Superseded { current_epoch: current.epoch });
        }
        if current.holder_actor_id != command.expected_holder {
            return Ok(LeaseDecision::WrongHolder {
                holder_actor_id: current.holder_actor_id.clone(),
            });
        }
        if current.epoch > command.expected_epoch {
            // Same lease id but the caller's epoch lost the preemption
            // race: the known lease out-epochs their claim.
            return Ok(LeaseDecision::Superseded { current_epoch: current.epoch });
        }
        if current.epoch < command.expected_epoch {
            // The caller claims a lease state that never existed.
            return Ok(LeaseDecision::Lost);
        }
        // Accepted: the only renewal event under the same id and epoch.
        let mut renewed = current.clone();
        renewed.expires_at_ms = command.now_ms + command.idle_timeout_ms;
        inner.leases.insert(command.session_id.to_string(), renewed.clone());
        Ok(LeaseDecision::Admitted(renewed))
    }

    async fn check_submission(&self, command: AdmitMessage<'_>) -> ServiceResult<LeaseDecision> {
        validate(command.now_ms, command.idle_timeout_ms)?;
        let inner = self.inner.lock().expect("lease store poisoned");
        let Some(current) = inner.leases.get(command.session_id) else {
            return Ok(LeaseDecision::Lost);
        };
        if current.is_expired(command.now_ms) {
            return Ok(LeaseDecision::Lost);
        }
        if current.lease_id != command.expected_lease_id {
            return Ok(LeaseDecision::Superseded { current_epoch: current.epoch });
        }
        if current.holder_actor_id != command.expected_holder {
            return Ok(LeaseDecision::WrongHolder {
                holder_actor_id: current.holder_actor_id.clone(),
            });
        }
        if current.epoch > command.expected_epoch {
            return Ok(LeaseDecision::Superseded { current_epoch: current.epoch });
        }
        if current.epoch < command.expected_epoch {
            return Ok(LeaseDecision::Lost);
        }
        // Eligible — but this call renews nothing: the acceptance-time
        // `admit_message` is the only renewal event.
        Ok(LeaseDecision::Admitted(current.clone()))
    }

    async fn current(&self, session_id: &str) -> ServiceResult<Option<SessionSpeakingLease>> {
        let inner = self.inner.lock().expect("lease store poisoned");
        Ok(inner.leases.get(session_id).cloned())
    }
}