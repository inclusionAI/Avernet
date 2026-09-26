//! Bounded operational scans for the local in-memory delivery repository.

use crate::MemoryMessageRepo;
use bcs_domain::message_delivery::{MessageDeliveryStatus as Status, PersistedMessageDelivery};
use bcs_service_api::port::repo::message_delivery::{
    DeliveryWorkBatch, MessageDeliveryRepoError, MessageDeliveryRepoPort,
};

pub(super) async fn work_batch(
    repo: &MemoryMessageRepo,
    kind: DeliveryWorkBatch,
    now_ms: i64,
    after: &str,
    limit: usize,
) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError> {
    let all = repo.list_deliveries(None).await?;
    let limit = limit.min(200);
    if matches!(kind, DeliveryWorkBatch::Control) {
        return Ok(control_batch(&all, now_ms, limit));
    }
    if matches!(kind, DeliveryWorkBatch::Expired) {
        return Ok(expired_batch(&all, now_ms, limit));
    }
    let mut rows: Vec<_> = all.into_iter().filter(|row| {
        row.delivery_id.as_str() > after
            && matches!(row.state.status, Status::Dispatching | Status::Running | Status::Cancelling)
    }).collect();
    rows.sort_by(|a, b| a.delivery_id.cmp(&b.delivery_id));
    rows.truncate(limit);
    Ok(rows)
}

fn control_batch(all: &[PersistedMessageDelivery], now_ms: i64, limit: usize) -> Vec<PersistedMessageDelivery> {
    let mut result = Vec::new();
    let mut pages = Vec::new();
    for category in 0..4 {
        let mut rows: Vec<_> = all.iter().filter(|row| match category {
            0 => row.state.status == Status::Dispatching && row.run_deadline_at_ms.is_some_and(|t| t <= now_ms),
            1 => row.state.status == Status::Running && row.run_deadline_at_ms.is_some_and(|t| t <= now_ms),
            2 => row.state.status == Status::Cancelling && row.abort_request_id.is_none(),
            _ => row.state.status == Status::Cancelling && row.abort_request_id.is_some()
                && row.cancel_deadline_at_ms.is_some_and(|t| t <= now_ms),
        }).cloned().collect();
        rows.sort_by(|a, b| {
            let deadline = |row: &PersistedMessageDelivery| match category {
                0 | 1 => row.run_deadline_at_ms,
                2 => None,
                _ => row.cancel_deadline_at_ms,
            };
            (deadline(a), &a.delivery_id).cmp(&(deadline(b), &b.delivery_id))
        });
        let quota = (limit / 4 + usize::from(category < limit % 4))
            .max(1).min(limit - result.len()).min(rows.len());
        result.extend(rows.drain(..quota));
        pages.push(rows);
    }
    for rows in pages { result.extend(rows.into_iter().take(limit - result.len())); }
    result
}

fn expired_batch(all: &[PersistedMessageDelivery], now_ms: i64, limit: usize) -> Vec<PersistedMessageDelivery> {
    let mut result = Vec::new();
    let statuses = [Status::Queued, Status::PendingContext, Status::Unknown, Status::CancelUnknown];
    for (index, status) in statuses.into_iter().enumerate() {
        let remaining_statuses = statuses.len() - index;
        let size = (limit - result.len()).div_ceil(remaining_statuses);
        let mut rows: Vec<_> = all.iter().filter(|row| {
            row.state.status == status && row.expire_at_ms.is_some_and(|t| t <= now_ms)
        }).cloned().collect();
        rows.sort_by(|a, b| (a.expire_at_ms, &a.delivery_id).cmp(&(b.expire_at_ms, &b.delivery_id)));
        rows.truncate(size);
        result.extend(rows);
    }
    result
}
