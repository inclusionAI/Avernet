//! Durable managed-delivery transactions shared with canonical message storage.
//! Session locks serialize managed writes in this process; SQL CAS still
//! guards against concurrent legacy sequence allocation and stale callbacks.
use super::mysql::MySqlMessageStore;
use async_trait::async_trait;
use bcs_db_api::{DbError, DbRow, DbStatement, DbTransactionStep, DbValue, db_get_column};
use bcs_domain::message_delivery::{
    MessageDeliveryState, MessageDeliveryStatus as Status, PersistedMessageDelivery,
};
use bcs_domain::{DeliveryType, PersistedMessage, PersistedMessageStatus};
use bcs_service_api::port::repo::MessageRepoPort;
use bcs_service_api::port::repo::message_delivery::*;
use std::collections::BTreeSet;
use tracing::Instrument;

/// Capacity keys sort before session keys. All keys are deduplicated and acquired
/// in one order, including multi-session transitions and multi-target replies.
#[derive(Default)]
pub(crate) struct DeliveryWriterLocks {
    directory: tokio::sync::Mutex<(std::collections::BTreeMap<(u8, String), std::sync::Weak<tokio::sync::Mutex<()>>>, usize)>,
}

impl DeliveryWriterLocks {
    async fn acquire(&self, keys: BTreeSet<(u8, String)>) -> Vec<tokio::sync::OwnedMutexGuard<()>> {
        let locks = {
            let mut directory = self.directory.lock().await;
            directory.1 += 1;
            if directory.1 >= 128 {
                directory.0.retain(|_, lock| lock.strong_count() > 0);
                directory.1 = 0;
            }
            keys.into_iter().map(|key| {
                let entry = directory.0.entry(key).or_default();
                if let Some(lock) = entry.upgrade() { lock } else {
                    let lock = std::sync::Arc::new(tokio::sync::Mutex::new(()));
                    *entry = std::sync::Arc::downgrade(&lock);
                    lock
                }
            }).collect::<Vec<_>>()
        };
        let mut guards = Vec::with_capacity(locks.len());
        for lock in locks { guards.push(lock.lock_owned().await); }
        guards
    }
}

#[cfg(test)]
mod writer_lock_tests {
    use super::*;

    #[tokio::test]
    async fn sessions_are_independent_and_capacity_keys_remain_shared() {
        let locks = DeliveryWriterLocks::default();
        let a = locks.acquire(BTreeSet::from([(1, "a".into())])).await;
        let b = locks.acquire(BTreeSet::from([(1, "b".into())]));
        let _b = tokio::time::timeout(std::time::Duration::from_secs(1), b).await.unwrap();
        let mut same = Box::pin(locks.acquire(BTreeSet::from([(1, "a".into())])));
        assert!(tokio::time::timeout(std::time::Duration::from_millis(10), &mut same).await.is_err());
        drop(a);
        drop(same.await);
        let first = locks.acquire(BTreeSet::from([(0, "bot".into()), (1, "c".into())])).await;
        let mut other_session = Box::pin(locks.acquire(BTreeSet::from([(0, "bot".into()), (1, "d".into())])));
        assert!(tokio::time::timeout(std::time::Duration::from_millis(10), &mut other_session).await.is_err());
        drop(first);
        drop(other_session.await);
    }

    #[tokio::test]
    async fn cancelling_multi_key_wait_releases_acquired_locks() {
        let locks = DeliveryWriterLocks::default();
        let held = locks.acquire(BTreeSet::from([(1, "b".into())])).await;
        let mut pending = Box::pin(locks.acquire(BTreeSet::from([(1, "a".into()), (1, "b".into())])));
        assert!(tokio::time::timeout(std::time::Duration::from_millis(10), &mut pending).await.is_err());
        drop(pending);
        drop(locks.acquire(BTreeSet::from([(1, "a".into())])).await);
        drop(held);
        for i in 0..512 { drop(locks.acquire(BTreeSet::from([(1, i.to_string())])).await); }
        assert!(locks.directory.lock().await.0.len() <= 128);
    }
}

struct WriterTimer(&'static str, Option<std::time::Instant>);
impl WriterTimer {
    fn new(stage: &'static str) -> Self {
        Self(stage, tracing::enabled!(target: "bcs_reply_profile", tracing::Level::DEBUG).then(std::time::Instant::now))
    }
}
impl Drop for WriterTimer {
    fn drop(&mut self) {
        if let Some(start) = self.1 {
            tracing::debug!(target: "bcs_reply_profile", stage = self.0, elapsed_us = start.elapsed().as_micros() as u64, "repository writer elapsed");
        }
    }
}

async fn control_page(db: &dyn bcs_db_api::DbPlugin, env: &str, category: usize, now: i64, limit: usize) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError> {
    let (predicate, order) = match category {
        0 => ("status = 'dispatching' AND run_deadline_at_ms <= ?", "run_deadline_at_ms, delivery_id"),
        1 => ("status = 'running' AND run_deadline_at_ms <= ?", "run_deadline_at_ms, delivery_id"),
        2 => ("status = 'cancelling' AND abort_request_id IS NULL", "delivery_id"),
        _ => ("status = 'cancelling' AND cancel_deadline_at_ms <= ? AND abort_request_id IS NOT NULL", "cancel_deadline_at_ms, delivery_id"),
    };
    let mut params = vec![env.into()];
    if category != 2 { params.push(now.into()); }
    params.push((limit as i64).into());
    db.query(DbStatement::with_params(format!("SELECT * FROM bcs_message_deliveries WHERE env = ? AND {predicate} ORDER BY {order} LIMIT ?"), params)).await.map_err(storage)?.into_iter().map(row_to_delivery).collect()
}

const COLS: &[&str] = &[
    "delivery_id",
    "env",
    "source_message_id",
    "target_bot_id",
    "session_id",
    "group_id",
    "source_session_seq",
    "flow_kind",
    "kind",
    "status",
    "state_version",
    "may_have_been_sent",
    "wait_reason",
    "available_at_ms",
    "expire_at_ms",
    "created_at_ms",
    "updated_at_ms",
    "run_id",
    "idempotency_key",
    "attempt_no",
    "request_id",
    "send_started_at_ms",
    "submitted_at_ms",
    "accepted_at_ms",
    "run_deadline_at_ms",
    "terminal_at_ms",
    "bound_to_delivery_id",
    "cancel_requested_at_ms",
    "cancel_requested_by",
    "cancel_reason",
    "abort_request_id",
    "abort_started_at_ms",
    "cancel_deadline_at_ms",
    "last_error_code",
    "semantic_projection_json",
    "transport_context_json",
    "downstream_run_id",
    "context_selection_json",
];

fn storage(error: impl std::fmt::Display) -> MessageDeliveryRepoError {
    MessageDeliveryRepoError::Storage(error.to_string())
}

pub(crate) fn active(d: &PersistedMessageDelivery) -> bool {
    d.state.kind == DeliveryType::Send && matches!(d.state.status, Status::Dispatching | Status::Running | Status::Unknown | Status::Cancelling | Status::CancelUnknown)
}
pub(crate) fn unfinished(d: &PersistedMessageDelivery) -> bool {
    matches!(d.state.status, Status::Queued | Status::Dispatching | Status::Running | Status::Unknown | Status::Cancelling | Status::CancelUnknown | Status::PendingContext | Status::Bound)
}

pub(crate) fn validate_admission(
    command: &AdmitMessageDeliveries,
) -> Result<(), MessageDeliveryRepoError> {
    crate::mysql::serialize_visibility(&command.message).map_err(|e| MessageDeliveryRepoError::Invalid(e.to_string()))?;
    if let Some(display) = &command.display_message {
        let m = &display.message;
        crate::mysql::serialize_visibility(m).map_err(|e| MessageDeliveryRepoError::Invalid(e.to_string()))?;
        if command.message.message_type != "run_reply" || command.event.is_some()
            || m.message_type != "chat" || m.session_id != command.message.session_id
            || m.group_id != command.message.group_id || m.sender_id != command.message.sender_id
            || m.sender_type != command.message.sender_type || m.owner_bot_id != command.message.owner_bot_id
            || m.run_id != command.message.run_id || display.message_id == command.message_id
            || m.visibility_domain != command.message.visibility_domain || m.audience != command.message.audience
            || display.message_id.is_empty()
        {
            return Err(MessageDeliveryRepoError::Invalid("invalid run reply display companion".into()));
        }
        if let Some(event) = &display.event {
            if event.event.subject.id != display.message_id
                || event.event.scope.session_id.as_deref() != Some(m.session_id.as_str())
                || event.event.scope.group_id.as_deref() != Some(m.group_id.as_str())
            { return Err(MessageDeliveryRepoError::Invalid("display event scope mismatch".into())); }
        }
    }
    if command.message.message_type == "run_reply" && command.event.is_some() {
        return Err(MessageDeliveryRepoError::Invalid("run reply must not publish a message event".into()));
    }
    if command.message.message_type == "run_reply" && command.message.run_id.is_empty() {
        return Err(MessageDeliveryRepoError::Invalid("run reply requires a run identity".into()));
    }
    if let Some(event) = &command.event {
        if event.event.subject.id != command.message_id
            || event.event.scope.session_id.as_deref() != Some(command.message.session_id.as_str())
            || event.event.scope.group_id.as_deref() != Some(command.message.group_id.as_str())
        {
            return Err(MessageDeliveryRepoError::Invalid(
                "message event scope mismatch".into(),
            ));
        }
    }
    if command.message_id.is_empty()
        || command.message.session_id.is_empty()
        || command.message.group_id.is_empty()
        || command.now_ms < 0
        || command
            .message
            .content
            .get("text")
            .and_then(serde_json::Value::as_str)
            .is_none()
    {
        return Err(MessageDeliveryRepoError::Invalid(
            "canonical message identity/text is required".into(),
        ));
    }
    if let Some(attachments) = command.message.content.get("attachments") {
        serde_json::from_value::<Vec<bcs_domain::Attachment>>(attachments.clone()).map_err(
            |_| MessageDeliveryRepoError::Invalid("invalid canonical attachments".into()),
        )?;
    }
    let mut bots = BTreeSet::new();
    for target in &command.targets {
        if target.target_bot_id.is_empty()
            || !bots.insert(&target.target_bot_id)
            || !target.semantic_projection_json.is_object()
        {
            return Err(MessageDeliveryRepoError::Invalid(
                "invalid or duplicate target".into(),
            ));
        }
    }
    Ok(())
}

pub(crate) fn canonical(command: &AdmitMessageDeliveries, seq: i64) -> PersistedMessage {
    let m = &command.message;
    PersistedMessage {
        message_id: command.message_id.clone(),
        group_id: m.group_id.clone(),
        session_id: m.session_id.clone(),
        session_seq: seq,
        sender_id: m.sender_id.clone(),
        sender_type: m.sender_type,
        message_type: m.message_type.clone(),
        content: m.content.clone(),
        client_msg_id: m.client_msg_id.clone(),
        owner_bot_id: m.owner_bot_id.clone(),
        status: PersistedMessageStatus::Normal,
        created_at: m.created_at,
        run_id: m.run_id.clone(),
        visibility_domain: Some(m.visibility_domain),
        audience: m.audience.clone(),
    }
}

pub(crate) fn canonical_display(command: &AdmitMessageDeliveries, seq: i64) -> Option<PersistedMessage> {
    command.display_message.as_ref().map(|display| {
        let mut cloned = command.clone();
        cloned.message_id = display.message_id.clone();
        cloned.message = display.message.clone();
        canonical(&cloned, seq)
    })
}

/// Plan per-target admission and causal context binding under the writer lock.
/// The returned changed context rows must commit with the new rows and message.
pub(crate) fn plan_admission(
    env: &str,
    command: &AdmitMessageDeliveries,
    seq: i64,
    existing: &[PersistedMessageDelivery],
    queued_counts: Option<&std::collections::BTreeMap<String, usize>>,
) -> Result<(Vec<PersistedMessageDelivery>, Vec<DeliveryCompareAndSet>), MessageDeliveryRepoError> {
    validate_admission(command)?;
    let mut admitted = Vec::new();
    let mut changes = Vec::new();
    for target in &command.targets {
        let queued = queued_counts.and_then(|counts| counts.get(&target.target_bot_id).copied()).unwrap_or_else(|| existing
            .iter()
            .filter(|d| {
                d.env == env
                    && d.target_bot_id == target.target_bot_id
                    && d.state.kind == DeliveryType::Send
                    && d.state.status == Status::Queued
            })
            .count());
        let status = if target.kind == DeliveryType::Inject {
            Status::PendingContext
        } else if queued >= target.max_queued as usize {
            Status::RejectedCapacity
        } else {
            Status::Queued
        };
        let run_id = (target.kind == DeliveryType::Send).then(|| uuid::Uuid::new_v4().to_string());
        let row = PersistedMessageDelivery {
            delivery_id: uuid::Uuid::new_v4().to_string(),
            env: env.into(),
            source_message_id: command.message_id.clone(),
            target_bot_id: target.target_bot_id.clone(),
            session_id: command.message.session_id.clone(),
            group_id: command.message.group_id.clone(),
            source_session_seq: seq,
            flow_kind: command.flow_kind,
            state: MessageDeliveryState {
                kind: target.kind,
                status,
                state_version: 1,
                may_have_been_sent: false,
            },
            wait_reason: None,
            available_at_ms: command.now_ms,
            expire_at_ms: command.expire_at_ms,
            created_at_ms: command.now_ms,
            updated_at_ms: command.now_ms,
            idempotency_key: run_id.clone(),
            run_id,
            attempt_no: 0,
            request_id: None,
            send_started_at_ms: None,
            submitted_at_ms: None,
            accepted_at_ms: None,
            run_deadline_at_ms: None,
            terminal_at_ms: (status == Status::RejectedCapacity).then_some(command.now_ms),
            bound_to_delivery_id: None,
            cancel_requested_at_ms: None,
            cancel_requested_by: None,
            cancel_reason: None,
            abort_request_id: None,
            abort_started_at_ms: None,
            cancel_deadline_at_ms: None,
            last_error_code: None,
            semantic_projection_json: target.semantic_projection_json.clone(),
            transport_context_json: None,
            context_selection_json: None,
        };
        if status == Status::Queued {
            for context in existing.iter().filter(|d| {
                d.env == env
                    && d.target_bot_id == target.target_bot_id
                    && d.session_id == row.session_id
                    && d.state.kind == DeliveryType::Inject
                    && d.state.status == Status::PendingContext
                    && d.source_session_seq < seq
                    && d.expire_at_ms.is_none_or(|t| t > command.now_ms)
            }) {
                let mut bound = context.clone();
                bound.state.status = Status::Bound;
                bound.state.state_version =
                    context.state.state_version.checked_add(1).ok_or_else(|| {
                        MessageDeliveryRepoError::Invalid("state version exhausted".into())
                    })?;
                bound.bound_to_delivery_id = Some(row.delivery_id.clone());
                bound.updated_at_ms = command.now_ms;
                changes.push(DeliveryCompareAndSet {
                    expected_state_version: context.state.state_version,
                    delivery: bound,
                });
            }
        }
        admitted.push(row);
    }
    Ok((admitted, changes))
}

pub(crate) fn apply_changes(
    rows: &mut [PersistedMessageDelivery],
    changes: &[DeliveryCompareAndSet],
) -> Result<(), MessageDeliveryRepoError> {
    let mut seen = BTreeSet::new();
    for change in changes {
        let next = &change.delivery;
        let old = rows
            .iter_mut()
            .find(|r| r.delivery_id == next.delivery_id && r.env == next.env)
            .ok_or(MessageDeliveryRepoError::Conflict)?;
        if !seen.insert(&next.delivery_id)
            || old.state.state_version != change.expected_state_version
        {
            return Err(MessageDeliveryRepoError::Conflict);
        }
        if change.expected_state_version.checked_add(1) != Some(next.state.state_version)
            || old.source_message_id != next.source_message_id
            || old.target_bot_id != next.target_bot_id
            || old.session_id != next.session_id
            || old.group_id != next.group_id
            || old.source_session_seq != next.source_session_seq
            || old.flow_kind != next.flow_kind
            || old.state.kind != next.state.kind
            || old.run_id != next.run_id
            || old.idempotency_key != next.idempotency_key
        {
            return Err(MessageDeliveryRepoError::Invalid(
                "immutable identity or version changed".into(),
            ));
        }
        *old = next.clone();
    }
    Ok(())
}

fn row_to_delivery(row: DbRow) -> Result<PersistedMessageDelivery, MessageDeliveryRepoError> {
    let mut map = serde_json::Map::new();
    for name in COLS {
        let value = row
            .get(name)
            .ok_or_else(|| storage("missing delivery column"))?;
        let json = if *name == "may_have_been_sent" {
            serde_json::Value::Bool(
                value
                    .as_bool()
                    .ok_or_else(|| storage("invalid send marker"))?,
            )
        } else if name.ends_with("_json") {
            match value {
                DbValue::Null => serde_json::Value::Null,
                _ => serde_json::from_str(
                    value
                        .as_str()
                        .ok_or_else(|| storage("invalid projection"))?,
                )
                .map_err(storage)?,
            }
        } else {
            match value {
                DbValue::Null => serde_json::Value::Null,
                DbValue::String(s) => s.clone().into(),
                DbValue::I64(n) => (*n).into(),
                DbValue::U64(n) => (*n).into(),
                DbValue::Bool(b) => (*b).into(),
                _ => return Err(storage("invalid delivery column type")),
            }
        };
        map.insert((*name).into(), json);
    }
    serde_json::from_value(map.into()).map_err(storage)
}

fn values(row: &PersistedMessageDelivery) -> Result<Vec<DbValue>, MessageDeliveryRepoError> {
    let json = serde_json::to_value(row).map_err(storage)?;
    COLS.iter()
        .map(|name| {
            if *name == "downstream_run_id" {
                return Ok(row.transport_context_json.as_ref().and_then(|v| v.get("downstream_run_id")).and_then(|v| v.as_str()).into());
            }
            let value = &json[*name];
            if name.ends_with("_json") && !value.is_null() {
                return Ok(DbValue::from(value.to_string()));
            }
            match value {
                serde_json::Value::Null => Ok(DbValue::Null),
                serde_json::Value::String(s) => Ok(DbValue::from(s.clone())),
                serde_json::Value::Bool(b) => Ok(DbValue::from(*b)),
                serde_json::Value::Number(n) => n
                    .as_i64()
                    .map(DbValue::from)
                    .ok_or_else(|| storage("delivery integer exceeds database range")),
                _ => Err(storage("unsupported delivery field")),
            }
        })
        .collect()
}

fn insert_delivery(
    row: &PersistedMessageDelivery,
) -> Result<DbTransactionStep, MessageDeliveryRepoError> {
    Ok(DbTransactionStep::Execute(DbStatement::with_params(
        format!(
            "INSERT INTO bcs_message_deliveries ({}) VALUES ({})",
            COLS.join(", "),
            vec!["?"; COLS.len()].join(", ")
        ),
        values(row)?,
    )))
}

fn update_delivery(
    change: &DeliveryCompareAndSet,
) -> Result<DbTransactionStep, MessageDeliveryRepoError> {
    let mut params = values(&change.delivery)?;
    params.extend([
        DbValue::from(change.delivery.env.as_str()),
        DbValue::from(change.delivery.delivery_id.as_str()),
        DbValue::from(change.expected_state_version),
    ]);
    Ok(DbTransactionStep::ExecuteChecked {
        statement: DbStatement::with_params(
            format!(
                "UPDATE bcs_message_deliveries SET {} WHERE env = ? AND delivery_id = ? AND state_version = ?",
                COLS.iter()
                    .map(|c| format!("{c} = ?"))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            params,
        ),
        expected_affected_rows: 1,
    })
}

fn insert_message(
    message: &PersistedMessage,
    env: &str,
    source: &bcs_domain::NewMessage,
) -> Result<DbTransactionStep, MessageDeliveryRepoError> {
    let sender_type = serde_json::to_value(message.sender_type).map_err(storage)?;
    let (domain, audience_kind, audience_ids) = crate::mysql::serialize_visibility(source).map_err(storage)?;
    Ok(DbTransactionStep::Execute(DbStatement::with_params(
        "INSERT INTO bcs_messages (message_id, group_id, session_id, session_seq, env, sender_id, sender_type, message_type, content, client_msg_id, owner_bot_id, status, created_at, run_id, visibility_domain, audience_kind, audience_actor_ids_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'normal', ?, ?, ?, ?, ?)",
        vec![
            message.message_id.as_str().into(),
            message.group_id.as_str().into(),
            message.session_id.as_str().into(),
            message.session_seq.into(),
            env.into(),
            message.sender_id.as_str().into(),
            sender_type
                .as_str()
                .ok_or_else(|| storage("invalid sender type"))?
                .into(),
            message.message_type.as_str().into(),
            message.content.to_string().into(),
            message.client_msg_id.as_deref().into(),
            message.owner_bot_id.as_deref().into(),
            message.created_at.into(),
            message.run_id.as_str().into(),
            domain.into(),
            audience_kind.into(),
            audience_ids.as_deref().into(),
        ],
    )))
}

impl MySqlMessageStore {
    async fn delivery_transaction(
        &self,
        mut changes: Vec<DeliveryCompareAndSet>,
        admission: Option<AdmitMessageDeliveries>,
    ) -> Result<Option<DeliveryAdmissionResult>, MessageDeliveryRepoError> {
        let waiting = WriterTimer::new("repository.writer_wait");
        let mut keys = BTreeSet::new();
        for change in &changes {
            keys.insert((1, change.delivery.session_id.clone()));
            // Direct repo callers must retain the same capacity guarantee as
            // callers going through the application's Bot mutation locks.
            if change.delivery.state.kind == DeliveryType::Send {
                keys.insert((0, change.delivery.target_bot_id.clone()));
            }
        }
        if let Some(command) = &admission {
            keys.insert((1, command.message.session_id.clone()));
            for target in &command.targets {
                if target.kind == DeliveryType::Send {
                    keys.insert((0, target.target_bot_id.clone()));
                }
            }
        }
        let _guards = self.delivery_writer.acquire(keys).await;
        drop(waiting);
        let _held = WriterTimer::new("repository.writer_held");
        async {
        if let Some(ref command) = admission {
            validate_admission(command)?;
        }
        let mut rows: Vec<PersistedMessageDelivery> = Vec::new();
        for change in &changes {
            if !rows
                .iter()
                .any(|row| row.delivery_id == change.delivery.delivery_id)
            {
                if let Some(row) = self.get_delivery(&change.delivery.delivery_id).await? {
                    rows.push(row);
                }
            }
        }
        if changes.iter().any(|c| c.delivery.env != self.env) {
            return Err(MessageDeliveryRepoError::Invalid(
                "environment mismatch".into(),
            ));
        }
        apply_changes(&mut rows, &changes)?;
        let mut queued_counts = std::collections::BTreeMap::new();
        if let Some(command) = &admission {
            for target in &command.targets {
                if target.kind != DeliveryType::Send { continue; }
                let counts = self.db.query(DbStatement::with_params("SELECT COUNT(*) AS n FROM bcs_message_deliveries WHERE env = ? AND target_bot_id = ? AND kind = 'send' AND status = 'queued'", vec![self.env.as_str().into(), target.target_bot_id.as_str().into()])).await.map_err(storage)?;
                let mut count: i64 = db_get_column(&counts[0], "n").map_err(storage)?;
                // Reflect primary/context CAS changes that have not committed yet.
                for change in changes.iter().filter(|c| c.delivery.target_bot_id == target.target_bot_id && c.delivery.state.kind == DeliveryType::Send) {
                    if let Some(old) = self.get_delivery(&change.delivery.delivery_id).await? {
                        count += i64::from(change.delivery.state.status == Status::Queued) - i64::from(old.state.status == Status::Queued);
                    }
                }
                queued_counts.insert(target.target_bot_id.clone(), count.max(0) as usize);
                let context = self.db.query(DbStatement::with_params("SELECT * FROM bcs_message_deliveries WHERE env = ? AND target_bot_id = ? AND session_id = ? AND kind = 'inject' AND status = 'pending_context' AND (expire_at_ms IS NULL OR expire_at_ms > ?)", vec![self.env.as_str().into(), target.target_bot_id.as_str().into(), command.message.session_id.as_str().into(), command.now_ms.into()])).await.map_err(storage)?;
                for row in context { let row = row_to_delivery(row)?; if !rows.iter().any(|d| d.delivery_id == row.delivery_id) { rows.push(row); } }
            }
        }
        let mut steps = Vec::new();
        let mut admitted = None;
        let event = admission.as_ref().and_then(|c| c.event.clone().or_else(|| c.display_message.as_ref().and_then(|d| d.event.clone())));
        if let Some(command) = admission {
            // Sender-scoped idempotency lookup, including env. Re-entry returns
            // the old target outcomes, never inserts a fresh set of deliveries.
            if let Some(client_id) = command.message.client_msg_id.as_deref() {
                let duplicates = self.db.query(DbStatement::with_params(
                    "SELECT message_id FROM bcs_messages WHERE env = ? AND session_id = ? AND sender_id = ? AND client_msg_id = ?",
                    vec![self.env.as_str().into(), command.message.session_id.as_str().into(),
                        command.message.sender_id.as_str().into(), client_id.into()],
                )).await.map_err(storage)?;
                if let Some(duplicate) = duplicates.first() {
                    if !changes.is_empty() {
                        return Err(MessageDeliveryRepoError::Conflict);
                    }
                    let id: String = db_get_column(duplicate, "message_id").map_err(storage)?;
                    let message = self
                        .get_message_by_id(&command.message.session_id, &id)
                        .await
                        .map_err(storage)?
                        .ok_or_else(|| storage("idempotent message is missing"))?;
                    return Ok(Some(DeliveryAdmissionResult {
                        deliveries: self.lookup(DeliveryLookup::Message(id)).await?,
                        message,
                        duplicate: true,
                    }));
                }
            }
            let sequence = self.db.query(DbStatement::with_params(
                "SELECT current_msg_seq FROM bcs_group_sessions WHERE env = ? AND session_id = ?",
                vec![self.env.as_str().into(), command.message.session_id.as_str().into()],
            )).await.map_err(storage)?;
            let old_seq: i64 = db_get_column(
                sequence
                    .first()
                    .ok_or_else(|| storage("canonical session is missing"))?,
                "current_msg_seq",
            )
            .map_err(storage)?;
            let seq = old_seq
                .checked_add(1 + i64::from(command.display_message.is_some()))
                .ok_or_else(|| storage("session sequence exhausted"))?;
            steps.push(DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_params("UPDATE bcs_group_sessions SET current_msg_seq = ? WHERE env = ? AND session_id = ? AND current_msg_seq = ?",
                    vec![seq.into(), self.env.as_str().into(), command.message.session_id.as_str().into(), old_seq.into()]),
                expected_affected_rows: 1,
            });
            let message = canonical(&command, seq);
            let (deliveries, bound) = plan_admission(&self.env, &command, seq, &rows, Some(&queued_counts))?;
            if let Some(display) = canonical_display(&command, seq - 1) {
                steps.push(insert_message(&display, &self.env, &command.message)?);
            }
            steps.push(insert_message(&message, &self.env, &command.message)?);
            // Changes may release context that this reply rebinds. Preserve
            // ordered CAS steps rather than collapsing versions.
            changes.extend(bound);
            for delivery in &deliveries {
                steps.push(insert_delivery(delivery)?);
            }
            admitted = Some(DeliveryAdmissionResult {
                message,
                deliveries,
                duplicate: false,
            });
        }
        for change in &changes {
            steps.push(update_delivery(change)?);
        }
        if let Some(event) = event {
            let plan = bcs_event_store::EventAppendTransactionPlan::build(
                &event,
                self.flavor,
                steps.len(),
            )
            .map_err(storage)?;
            steps.extend(plan.steps);
        }
        // Driver work may outlive an aborted caller. Keep the capacity/session
        // guards until the transaction actually returns, not just until await
        // is dropped; otherwise a new admission could read stale capacity.
        let db = self.db.clone();
        let (transaction, _guards) = tokio::spawn(async move {
            let result = db.transaction(steps).await;
            (result, _guards)
        }.in_current_span()).await.map_err(storage)?;
        transaction
            .map_err(|error| match error {
                DbError::ConditionFailed { .. } => MessageDeliveryRepoError::Conflict,
                other => storage(other),
            })?;
        Ok(admitted)
        }.instrument(tracing::debug_span!(target: "bcs_reply_profile", "repo_writer_held")).await
    }
}

#[async_trait]
impl MessageDeliveryRepoPort for MySqlMessageStore {
    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<BoundDeliveryContexts, MessageDeliveryRepoError> {
        let count = self.db.query(DbStatement::with_params("SELECT COUNT(*) AS total FROM bcs_message_deliveries WHERE env = ? AND bound_to_delivery_id = ? AND status = 'bound'", vec![self.env.as_str().into(), carrier.into()])).await.map_err(storage)?;
        let total: i64 = db_get_column(count.first().ok_or_else(|| storage("missing bound count"))?, "total").map_err(storage)?;
        let rows = self.db.query(DbStatement::with_params("SELECT * FROM bcs_message_deliveries WHERE env = ? AND bound_to_delivery_id = ? AND status = 'bound' ORDER BY source_session_seq DESC, delivery_id DESC LIMIT ?", vec![self.env.as_str().into(), carrier.into(), (limit.min(1025) as i64).into()])).await.map_err(storage)?.into_iter().map(row_to_delivery).collect::<Result<_, _>>()?;
        Ok(BoundDeliveryContexts { rows, total: total as u64 })
    }
    async fn lookup(&self, scope: DeliveryLookup) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError> {
        let mut params = vec![self.env.as_str().into()];
        let predicate = match scope {
            DeliveryLookup::Id(id) => { params.push(id.into()); "delivery_id = ?" }
            DeliveryLookup::Request(id) => { params.push(id.into()); "request_id = ?" }
            DeliveryLookup::Run { bot, alias } => {
                let mut params = Vec::new();
                for _ in 0..3 { params.extend([DbValue::from(self.env.as_str()), DbValue::from(bot.as_str()), DbValue::from(alias.as_str())]); }
                return self.db.query(DbStatement::with_params("SELECT * FROM bcs_message_deliveries WHERE env = ? AND target_bot_id = ? AND run_id = ? UNION SELECT * FROM bcs_message_deliveries WHERE env = ? AND target_bot_id = ? AND request_id = ? UNION SELECT * FROM bcs_message_deliveries WHERE env = ? AND target_bot_id = ? AND downstream_run_id = ?", params)).await.map_err(storage)?.into_iter().map(row_to_delivery).collect();
            }
            DeliveryLookup::Bound(id) => { params.push(id.into()); "bound_to_delivery_id = ? AND status = 'bound'" }
            DeliveryLookup::Lane { bot, session } => { params.extend([bot.into(), session.into()]); "target_bot_id = ? AND session_id = ? AND kind = 'send' AND status IN ('queued','dispatching','running','unknown','cancelling','cancel_unknown')" }
            DeliveryLookup::BotPending(bot) => { params.push(bot.into()); "target_bot_id = ? AND status IN ('queued','dispatching','running','unknown','cancelling','cancel_unknown','pending_context','bound') LIMIT 1" }
            DeliveryLookup::Message(id) => { params.push(id.into()); "source_message_id = ?" }
            DeliveryLookup::Successor { bot, session, after_seq, exclude, now_ms } => {
                params.extend([bot.into(), session.into(), after_seq.into(), exclude.into(), now_ms.into()]);
                "target_bot_id = ? AND session_id = ? AND source_session_seq > ? AND delivery_id <> ? AND kind = 'send' AND status = 'queued' AND may_have_been_sent = 0 AND (expire_at_ms IS NULL OR expire_at_ms > ?) ORDER BY source_session_seq LIMIT 1"
            }
        };
        self.db.query(DbStatement::with_params(format!("SELECT * FROM bcs_message_deliveries WHERE env = ? AND {predicate}"), params)).await.map_err(storage)?.into_iter().map(row_to_delivery).collect()
    }
    async fn queued_bots(&self, after: &str, limit: usize) -> Result<Vec<String>, MessageDeliveryRepoError> {
        self.db.query(DbStatement::with_params("SELECT DISTINCT target_bot_id FROM bcs_message_deliveries WHERE env = ? AND kind = 'send' AND status = 'queued' AND target_bot_id > ? ORDER BY target_bot_id LIMIT ?", vec![self.env.as_str().into(), after.into(), (limit.min(256) as i64).into()])).await.map_err(storage)?.iter().map(|r| db_get_column(r, "target_bot_id").map_err(storage)).collect()
    }
    async fn active_count(&self, bot: &str) -> Result<u64, MessageDeliveryRepoError> {
        let rows = self.db.query(DbStatement::with_params("SELECT COUNT(*) AS n FROM bcs_message_deliveries WHERE env = ? AND target_bot_id = ? AND kind = 'send' AND status IN ('dispatching','running','unknown','cancelling','cancel_unknown')", vec![self.env.as_str().into(), bot.into()])).await.map_err(storage)?;
        let n: i64 = db_get_column(&rows[0], "n").map_err(storage)?;
        Ok(n as u64)
    }
    async fn lane_blocked(&self, row: &PersistedMessageDelivery) -> Result<bool, MessageDeliveryRepoError> {
        Ok(!self.db.query(DbStatement::with_params("SELECT delivery_id FROM bcs_message_deliveries WHERE env = ? AND target_bot_id = ? AND session_id = ? AND kind = 'send' AND delivery_id <> ? AND ((status = 'queued' AND source_session_seq < ?) OR status IN ('dispatching','running','unknown','cancelling','cancel_unknown')) LIMIT 1", vec![self.env.as_str().into(), row.target_bot_id.as_str().into(), row.session_id.as_str().into(), row.delivery_id.as_str().into(), row.source_session_seq.into()])).await.map_err(storage)?.is_empty())
    }
    async fn queued_heads(&self, bot: &str, after_session: &str, limit: usize) -> Result<Vec<bcs_service_api::core::message_delivery::DeliveryScheduleEntry>, MessageDeliveryRepoError> {
        self.db.query(DbStatement::with_params("SELECT q.delivery_id, q.session_id, q.source_session_seq, q.state_version, q.may_have_been_sent, q.available_at_ms, q.expire_at_ms FROM bcs_message_deliveries q WHERE q.env = ? AND q.target_bot_id = ? AND q.kind = 'send' AND q.status = 'queued' AND q.session_id > ? AND NOT EXISTS (SELECT 1 FROM bcs_message_deliveries p WHERE p.env = q.env AND p.target_bot_id = q.target_bot_id AND p.session_id = q.session_id AND p.kind = 'send' AND p.status IN ('queued','dispatching','running','unknown','cancelling','cancel_unknown') AND p.source_session_seq < q.source_session_seq) ORDER BY q.session_id LIMIT ?", vec![self.env.as_str().into(), bot.into(), after_session.into(), (limit.min(64) as i64).into()])).await.map_err(storage)?.iter().map(|r| Ok(bcs_service_api::core::message_delivery::DeliveryScheduleEntry {
            delivery_id: db_get_column(r, "delivery_id").map_err(storage)?, session_id: db_get_column(r, "session_id").map_err(storage)?, source_session_seq: db_get_column::<i64>(r, "source_session_seq").map_err(storage)? as u64,
            state: MessageDeliveryState { kind: DeliveryType::Send, status: Status::Queued, state_version: db_get_column::<i64>(r, "state_version").map_err(storage)? as u64, may_have_been_sent: db_get_column::<i64>(r, "may_have_been_sent").map_err(storage)? != 0 },
            available_at_ms: db_get_column(r, "available_at_ms").map_err(storage)?, expire_at_ms: r.get("expire_at_ms").and_then(DbValue::as_i64),
        })).collect()
    }
    async fn work_batch(&self, kind: DeliveryWorkBatch, now_ms: i64, after: &str, limit: usize) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError> {
        if matches!(kind, DeliveryWorkBatch::Control) {
            let limit = limit.min(200);
            if limit == 0 { return Ok(Vec::new()); }
            let mut result = Vec::new();
            let mut seen = BTreeSet::new();
            let mut full = Vec::new();
            // Reserve a share for each disjoint action before lending spare capacity.
            for category in 0..4 {
                let quota = (limit / 4 + usize::from(category < limit % 4)).max(1).min(limit - result.len());
                if quota == 0 { continue; }
                let rows = control_page(self.db.as_ref(), &self.env, category, now_ms, quota).await?;
                if rows.len() == quota { full.push((category, quota)); }
                for row in rows { if seen.insert(row.delivery_id.clone()) { result.push(row); } }
            }
            for (category, quota) in full {
                if result.len() == limit { break; }
                // Re-read only a bounded prefix, not an unbounded OFFSET cursor.
                let rows = control_page(self.db.as_ref(), &self.env, category, now_ms, quota + limit - result.len()).await?;
                for row in rows {
                    if seen.insert(row.delivery_id.clone()) { result.push(row); }
                    if result.len() == limit { break; }
                }
            }
            return Ok(result);
        }
        if matches!(kind, DeliveryWorkBatch::Expired) {
            let columns = COLS.iter().map(|c| match *c { "semantic_projection_json" => "'{}' AS semantic_projection_json", "transport_context_json" => "NULL AS transport_context_json", c => c }).collect::<Vec<_>>().join(",");
            // Independent status ranges preserve the expiry index ordering.
            // Successful transitions remove rows; no OFFSET or full due-set sort.
            let mut rows = Vec::new();
            let limit = limit.min(200);
            for (status, size) in [("queued", limit.div_ceil(2)), ("pending_context", limit / 2)] {
                let size = if status == "pending_context" && rows.is_empty() { limit } else { size };
                if size == 0 { continue; }
                rows.extend(self.db.query(DbStatement::with_params(format!("SELECT {columns} FROM bcs_message_deliveries WHERE env = ? AND status = ? AND expire_at_ms <= ? ORDER BY expire_at_ms, delivery_id LIMIT ?"), vec![self.env.as_str().into(), status.into(), now_ms.into(), (size as i64).into()])).await.map_err(storage)?.into_iter().map(row_to_delivery).collect::<Result<Vec<_>,_>>()?);
            }
            return Ok(rows);
        }
        let mut params = vec![self.env.as_str().into(), after.into()];
        let predicate = match kind {
            DeliveryWorkBatch::Expired => { params.push(now_ms.into()); "status IN ('queued','pending_context') AND expire_at_ms <= ?" }
            DeliveryWorkBatch::Control => unreachable!("control classes handled above"),
            DeliveryWorkBatch::Recovery => "status IN ('dispatching','running','cancelling')",
        };
        params.push((limit.min(200) as i64).into());
        let columns = COLS.iter().map(|c| match *c { "semantic_projection_json" => "'{}' AS semantic_projection_json", "transport_context_json" => "NULL AS transport_context_json", column => column }).collect::<Vec<_>>().join(",");
        self.db.query(DbStatement::with_params(format!("SELECT {columns} FROM bcs_message_deliveries WHERE env = ? AND delivery_id > ? AND {predicate} ORDER BY delivery_id LIMIT ?"), params)).await.map_err(storage)?.into_iter().map(row_to_delivery).collect()
    }
    async fn queue_statistics(&self) -> Result<Vec<DeliveryQueueStatistic>, MessageDeliveryRepoError> {
        self.db.query(DbStatement::with_params("SELECT flow_kind, kind, status, COALESCE(wait_reason, 'none') AS wait_reason, COUNT(*) AS n, MIN(created_at_ms) AS oldest FROM bcs_message_deliveries WHERE env = ? AND status IN ('queued','dispatching','running','unknown','cancelling','cancel_unknown','pending_context','bound') GROUP BY flow_kind, kind, status, wait_reason", vec![self.env.as_str().into()])).await.map_err(storage)?.iter().map(|r| Ok(DeliveryQueueStatistic {
            flow_kind: db_get_column(r, "flow_kind").map_err(storage)?, kind: db_get_column(r, "kind").map_err(storage)?, status: db_get_column(r, "status").map_err(storage)?, wait_reason: db_get_column(r, "wait_reason").map_err(storage)?, count: db_get_column::<i64>(r, "n").map_err(storage)? as u64, oldest_created_at_ms: r.get("oldest").and_then(DbValue::as_i64),
        })).collect()
    }
    async fn load_policy(&self) -> Result<bcs_config_api::message_delivery::DeliveryPolicyRecord, MessageDeliveryRepoError> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT version, policy_json, updated_by, updated_at_ms FROM bcs_message_delivery_policy WHERE env = ?", vec![self.env.as_str().into()],
        )).await.map_err(storage)?;
        let Some(row) = rows.first() else { return Ok(Default::default()); };
        let policy_json: String = db_get_column(row, "policy_json").map_err(storage)?;
        let version: i64 = db_get_column(row, "version").map_err(storage)?;
        if version <= 0 { return Err(storage("invalid delivery policy version")); }
        let record = bcs_config_api::message_delivery::DeliveryPolicyRecord {
            version: version as u64,
            policy: serde_json::from_str(&policy_json).map_err(storage)?,
            updated_by: db_get_column(row, "updated_by").map_err(storage)?,
            updated_at_ms: db_get_column(row, "updated_at_ms").map_err(storage)?,
        };
        record.policy.validate().map_err(storage)?;
        Ok(record)
    }

    async fn replace_policy(&self, expected_version: u64, record: bcs_config_api::message_delivery::DeliveryPolicyRecord) -> Result<(), MessageDeliveryRepoError> {
        if record.version != expected_version.saturating_add(1) || record.version > i64::MAX as u64 {
            return Err(MessageDeliveryRepoError::Conflict);
        }
        record.policy.validate().map_err(|e| MessageDeliveryRepoError::Invalid(e.to_string()))?;
        let policy = serde_json::to_string(&record.policy).map_err(storage)?;
        let statement = if expected_version == 0 {
            DbStatement::with_params("INSERT INTO bcs_message_delivery_policy (env, version, policy_json, updated_by, updated_at_ms) VALUES (?, ?, ?, ?, ?)",
                vec![self.env.as_str().into(), (record.version as i64).into(), policy.into(), record.updated_by.into(), record.updated_at_ms.into()])
        } else {
            DbStatement::with_params("UPDATE bcs_message_delivery_policy SET version = ?, policy_json = ?, updated_by = ?, updated_at_ms = ? WHERE env = ? AND version = ?",
                vec![(record.version as i64).into(), policy.into(), record.updated_by.into(), record.updated_at_ms.into(), self.env.as_str().into(), (expected_version as i64).into()])
        };
        self.db.transaction(vec![DbTransactionStep::ExecuteChecked { statement, expected_affected_rows: 1 }]).await
            .map_err(|e| match e { DbError::ConditionFailed { .. } => MessageDeliveryRepoError::Conflict, other if other.is_duplicate_key() => MessageDeliveryRepoError::Conflict, other => storage(other) })?;
        Ok(())
    }
    fn is_durable(&self) -> bool {
        true
    }
    async fn list_unfinished_deliveries(
        &self,
    ) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError> {
        self.db.query(DbStatement::with_params(
            "SELECT * FROM bcs_message_deliveries WHERE env = ? AND status IN ('queued','dispatching','running','unknown','cancelling','cancel_unknown','pending_context','bound') ORDER BY source_session_seq, delivery_id",
            vec![self.env.as_str().into()],
        )).await.map_err(storage)?.into_iter().map(row_to_delivery).collect()
    }
    async fn get_delivery(
        &self,
        id: &str,
    ) -> Result<Option<PersistedMessageDelivery>, MessageDeliveryRepoError> {
        self.db
            .query(DbStatement::with_params(
                "SELECT * FROM bcs_message_deliveries WHERE env = ? AND delivery_id = ?",
                vec![self.env.as_str().into(), id.into()],
            ))
            .await
            .map_err(storage)?
            .into_iter()
            .next()
            .map(row_to_delivery)
            .transpose()
    }
    async fn admit(
        &self,
        command: AdmitMessageDeliveries,
    ) -> Result<DeliveryAdmissionResult, MessageDeliveryRepoError> {
        self.delivery_transaction(Vec::new(), Some(command))
            .await?
            .ok_or_else(|| storage("missing admission result"))
    }
    async fn list_deliveries(
        &self,
        session_id: Option<&str>,
    ) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError> {
        let (sql, params) = match session_id {
            Some(session) => (
                "SELECT * FROM bcs_message_deliveries WHERE env = ? AND session_id = ? ORDER BY source_session_seq, delivery_id",
                vec![self.env.as_str().into(), session.into()],
            ),
            None => (
                "SELECT * FROM bcs_message_deliveries WHERE env = ? ORDER BY source_session_seq, delivery_id",
                vec![self.env.as_str().into()],
            ),
        };
        self.db
            .query(DbStatement::with_params(sql, params))
            .await
            .map_err(storage)?
            .into_iter()
            .map(row_to_delivery)
            .collect()
    }
    async fn commit_transition(
        &self,
        changes: Vec<DeliveryCompareAndSet>,
        reply: Option<AdmitMessageDeliveries>,
    ) -> Result<Option<DeliveryAdmissionResult>, MessageDeliveryRepoError> {
        self.delivery_transaction(changes, reply).await
    }
}
