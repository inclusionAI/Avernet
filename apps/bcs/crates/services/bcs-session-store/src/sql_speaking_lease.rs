//! SQL-backed speaking-lease repository (spec §6.3/§6.3.1 B7): one row per
//! (env, session); every mutator is a single conditional UPDATE (or
//! INSERT-OR-IGNORE for the first grant) whose affected-row count IS the
//! decision — no read-then-write windows. A released row stays with its
//! epoch as a watermark, so grants never reuse an epoch.
use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::speaking_lease::SessionSpeakingLease;
use bcs_service_api::port::repo::session_speaking_lease::{
    AcquireLease, AdmitMessage, LeaseAcquire, LeaseDecision, LeaseRelease, PreemptLease,
    ReleaseLease, SpeakingLeaseRepoPort,
};
use bcs_service_api::{ServiceError, ServiceResult};

#[derive(Clone)]
pub struct SqlSpeakingLeaseRepo {
    db: Arc<dyn DbPlugin>,
    env: String,
    flavor: DbSqlFlavor,
}

fn require_positive(now_ms: i64, idle_timeout_ms: i64) -> Result<(), ServiceError> {
    if idle_timeout_ms <= 0 {
        return Err(ServiceError::InvalidOperation {
            message: "speaking lease idle timeout must be positive".to_string(),
            request_id: None,
        });
    }
    if now_ms <= 0 {
        return Err(ServiceError::InvalidOperation {
            message: "speaking lease requires a positive server clock".to_string(),
            request_id: None,
        });
    }
    Ok(())
}

fn column_i64(row: &DbRow, name: &str) -> i64 {
    row.get(name)
        .and_then(|value| value.as_i64())
        .unwrap_or(0)
}

fn column_str(row: &DbRow, name: &str) -> String {
    row.get(name)
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_string()
}

/// Live row → lease; released/lapsed rows are NOT current leases.
fn row_is_live(row: &DbRow, now_ms: i64) -> bool {
    column_i64(row, "released") == 0 && now_ms < column_i64(row, "expires_at_ms")
}

fn row_to_lease(row: &DbRow) -> Option<SessionSpeakingLease> {
    let lease_id = column_str(row, "lease_id");
    if lease_id.is_empty() {
        return None;
    }
    Some(SessionSpeakingLease {
        session_id: column_str(row, "session_id"),
        holder_actor_id: column_str(row, "holder_actor_id"),
        lease_id,
        epoch: column_i64(row, "epoch").max(0) as u64,
        expires_at_ms: column_i64(row, "expires_at_ms"),
    })
}

impl SqlSpeakingLeaseRepo {
    pub fn new(db: Arc<dyn DbPlugin>, env: impl Into<String>, flavor: DbSqlFlavor) -> Self {
        Self { db, env: env.into(), flavor }
    }

    async fn db_execute(&self, sql: &str, params: Vec<DbValue>) -> ServiceResult<u64> {
        self.db
            .execute(DbStatement::with_params(sql, params))
            .await
            .map(|result| result.affected_rows)
            .map_err(|error| ServiceError::InternalError(error.to_string()))
    }

    async fn query_row(&self, session_id: &str) -> ServiceResult<Option<DbRow>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT session_id, holder_actor_id, lease_id, epoch, expires_at_ms, released \
                 FROM bcs_session_speaking_leases WHERE env = ? AND session_id = ?",
                vec![DbValue::from(self.env.clone()), DbValue::from(session_id.to_string())],
            ))
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        Ok(rows.into_iter().next())
    }

    async fn insert_first_grant(
        &self,
        session_id: &str,
        holder: &str,
        lease_id: &str,
        expires_at_ms: i64,
    ) -> ServiceResult<u64> {
        self.db_execute(
            &format!(
                "{} INTO bcs_session_speaking_leases \
                 (env, session_id, holder_actor_id, lease_id, epoch, expires_at_ms, released) \
                 VALUES (?, ?, ?, ?, 1, ?, 0)",
                self.flavor.insert_or_ignore()
            ),
            vec![
                DbValue::from(self.env.clone()),
                DbValue::from(session_id.to_string()),
                DbValue::from(holder.to_string()),
                DbValue::from(lease_id.to_string()),
                DbValue::from(expires_at_ms),
            ],
        )
        .await
    }

    /// Shared submission classification (check semantics): live → lease
    /// id → holder → epoch, mirroring the memory store exactly.
    async fn classify(
        &self,
        command: &AdmitMessage<'_>,
    ) -> ServiceResult<LeaseDecision> {
        let Some(row) = self.query_row(command.session_id).await? else {
            return Ok(LeaseDecision::Lost);
        };
        if !row_is_live(&row, command.now_ms) {
            return Ok(LeaseDecision::Lost);
        }
        if column_str(&row, "lease_id") != command.expected_lease_id {
            return Ok(LeaseDecision::Superseded {
                current_epoch: column_i64(&row, "epoch").max(0) as u64,
            });
        }
        if column_str(&row, "holder_actor_id") != command.expected_holder {
            return Ok(LeaseDecision::WrongHolder {
                holder_actor_id: column_str(&row, "holder_actor_id"),
            });
        }
        let epoch = column_i64(&row, "epoch").max(0) as u64;
        if epoch > command.expected_epoch {
            return Ok(LeaseDecision::Superseded { current_epoch: epoch });
        }
        if epoch < command.expected_epoch {
            return Ok(LeaseDecision::Lost);
        }
        row_to_lease(&row)
            .map(LeaseDecision::Admitted)
            .ok_or_else(|| ServiceError::InternalError("speaking lease row is inconsistent".into()))
    }
}

#[async_trait]
impl SpeakingLeaseRepoPort for SqlSpeakingLeaseRepo {
    async fn acquire(&self, command: AcquireLease<'_>) -> ServiceResult<LeaseAcquire> {
        require_positive(command.now_ms, command.idle_timeout_ms)?;
        let new_lease_id = uuid::Uuid::new_v4().to_string();
        let expires = command.now_ms + command.idle_timeout_ms;
        // Atomic grant over an absent/lapsed/released row: bumps the
        // persisted epoch watermark.
        let granted = self
            .db_execute(
                "UPDATE bcs_session_speaking_leases \
                 SET holder_actor_id = ?, lease_id = ?, epoch = epoch + 1, expires_at_ms = ?, \
                     released = 0, gmt_modified = CURRENT_TIMESTAMP \
                 WHERE env = ? AND session_id = ? AND (released = 1 OR expires_at_ms <= ?)",
                vec![
                    DbValue::from(command.requester_actor_id.to_string()),
                    DbValue::from(new_lease_id.clone()),
                    DbValue::from(expires),
                    DbValue::from(self.env.clone()),
                    DbValue::from(command.session_id.to_string()),
                    DbValue::from(command.now_ms),
                ],
            )
            .await?;
        if granted > 0 {
            if let Some(lease) = self.query_row(command.session_id).await?.and_then(|row| row_to_lease(&row)) {
                return Ok(LeaseAcquire::Granted(lease));
            }
            return Err(ServiceError::InternalError("speaking lease grant lost its row".into()));
        }
        // First grant: INSERT OR IGNORE — the race loser classifies below.
        let inserted = self
            .insert_first_grant(command.session_id, command.requester_actor_id, &new_lease_id, expires)
            .await?;
        if inserted > 0 {
            if let Some(lease) = self.query_row(command.session_id).await?.and_then(|row| row_to_lease(&row)) {
                return Ok(LeaseAcquire::Granted(lease));
            }
            return Err(ServiceError::InternalError("speaking lease grant lost its row".into()));
        }
        match self.query_row(command.session_id).await? {
            Some(row) if row_is_live(&row, command.now_ms) => {
                let lease = row_to_lease(&row).ok_or_else(|| {
                    ServiceError::InternalError("speaking lease row is inconsistent".into())
                })?;
                if lease.holder_actor_id == command.requester_actor_id {
                    Ok(LeaseAcquire::SelfHeld(lease))
                } else {
                    Ok(LeaseAcquire::Occupied {
                        holder_actor_id: lease.holder_actor_id.clone(),
                        epoch: lease.epoch,
                        expires_at_ms: lease.expires_at_ms,
                    })
                }
            }
            // Neither branch wrote and no live row: a concurrent writer
            // lapsed the row between our statements — surface a retryable
            // conflict instead of a silent skip.
            _ => Ok(LeaseAcquire::Occupied {
                holder_actor_id: String::new(),
                epoch: 0,
                expires_at_ms: 0,
            }),
        }
    }

    async fn preempt(&self, command: PreemptLease<'_>) -> ServiceResult<SessionSpeakingLease> {
        require_positive(command.now_ms, command.idle_timeout_ms)?;
        let lease_id = uuid::Uuid::new_v4().to_string();
        let expires = command.now_ms + command.idle_timeout_ms;
        // Update-first over any row (bumps the epoch); INSERT for a fresh
        // session. Both statements are individually atomic; the read-back
        // verifies the winner is us, else a retryable conflict.
        let updated = self
            .db_execute(
                "UPDATE bcs_session_speaking_leases \
                 SET holder_actor_id = ?, lease_id = ?, epoch = epoch + 1, expires_at_ms = ?, \
                     released = 0, gmt_modified = CURRENT_TIMESTAMP \
                 WHERE env = ? AND session_id = ?",
                vec![
                    DbValue::from(command.requester_actor_id.to_string()),
                    DbValue::from(lease_id.clone()),
                    DbValue::from(expires),
                    DbValue::from(self.env.clone()),
                    DbValue::from(command.session_id.to_string()),
                ],
            )
            .await?;
        if updated == 0 {
            self.insert_first_grant(command.session_id, command.requester_actor_id, &lease_id, expires)
                .await?;
        }
        let row = self
            .query_row(command.session_id)
            .await?
            .ok_or_else(|| ServiceError::Conflict("speaking lease preemption race; retry".into()))?;
        let lease = row_to_lease(&row)
            .ok_or_else(|| ServiceError::InternalError("speaking lease row is inconsistent".into()))?;
        if lease.lease_id != lease_id {
            // Another preemptor won the window between statement and read.
            return Err(ServiceError::Conflict("speaking lease preemption race; retry".into()));
        }
        Ok(lease)
    }

    async fn release(&self, command: ReleaseLease<'_>) -> ServiceResult<LeaseRelease> {
        if command.now_ms <= 0 {
            return Err(ServiceError::InvalidOperation {
                message: "speaking lease requires a positive server clock".to_string(),
                request_id: None,
            });
        }
        let released = self
            .db_execute(
                "UPDATE bcs_session_speaking_leases \
                 SET released = 1, holder_actor_id = '', lease_id = '', expires_at_ms = 0, \
                     gmt_modified = CURRENT_TIMESTAMP \
                 WHERE env = ? AND session_id = ? AND released = 0 AND holder_actor_id = ? \
                     AND lease_id = ? AND epoch = ?",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(command.session_id.to_string()),
                    DbValue::from(command.holder_actor_id.to_string()),
                    DbValue::from(command.lease_id.to_string()),
                    DbValue::from(command.epoch as i64),
                ],
            )
            .await?;
        Ok(if released > 0 { LeaseRelease::Released } else { LeaseRelease::NotHeld })
    }

    async fn admit_message(&self, command: AdmitMessage<'_>) -> ServiceResult<LeaseDecision> {
        require_positive(command.now_ms, command.idle_timeout_ms)?;
        // One conditional UPDATE is the atomic admission+renewal: the same
        // lease id, holder and epoch under a live expiry.
        let renewed = self
            .db_execute(
                "UPDATE bcs_session_speaking_leases \
                 SET expires_at_ms = ?, gmt_modified = CURRENT_TIMESTAMP \
                 WHERE env = ? AND session_id = ? AND released = 0 AND expires_at_ms > ? \
                     AND lease_id = ? AND holder_actor_id = ? AND epoch = ?",
                vec![
                    DbValue::from(command.now_ms + command.idle_timeout_ms),
                    DbValue::from(self.env.clone()),
                    DbValue::from(command.session_id.to_string()),
                    DbValue::from(command.now_ms),
                    DbValue::from(command.expected_lease_id.to_string()),
                    DbValue::from(command.expected_holder.to_string()),
                    DbValue::from(command.expected_epoch as i64),
                ],
            )
            .await?;
        if renewed > 0 {
            let row = self
                .query_row(command.session_id)
                .await?
                .ok_or_else(|| ServiceError::InternalError("speaking lease update lost its row".into()))?;
            let lease = row_to_lease(&row)
                .ok_or_else(|| ServiceError::InternalError("speaking lease row is inconsistent".into()))?;
            return Ok(LeaseDecision::Admitted(lease));
        }
        // No row matched: classify why (the same decision ordering).
        self.classify(&command).await
    }

    async fn check_submission(&self, command: AdmitMessage<'_>) -> ServiceResult<LeaseDecision> {
        require_positive(command.now_ms, command.idle_timeout_ms)?;
        self.classify(&command).await
    }

    async fn current(&self, session_id: &str) -> ServiceResult<Option<SessionSpeakingLease>> {
        match self.query_row(session_id).await? {
            Some(row) if row_is_live(&row, i64::MAX / 2) => Ok(row_to_lease(&row)),
            // A lapsed/released row still reports its shape for management
            // queries — matching the memory store (which keeps rows alive
            // only until reacquire): release blanks the holder, so surface
            // nothing once released.
            Some(row) if column_i64(&row, "released") == 0 => Ok(row_to_lease(&row)),
            _ => Ok(None),
        }
    }

    async fn healthy(&self) -> ServiceResult<()> {
        self.db
            .health_check()
            .await
            .map(|_| ())
            .map_err(|error| ServiceError::InternalError(error.to_string()))
    }
}