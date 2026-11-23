//! In-memory Event retention.

use super::*;
use super::delivery::delivery_unblocks_lane;

impl MemoryEventStore {
    pub(super) async fn purge_expired(
        &self,
        command: EventRetentionRequest,
    ) -> Result<EventRetentionResult, EventRepoError> {
        if command.event_limit == 0 || command.audit_limit == 0 || command.env.is_empty() {
            return Err(EventRepoError::InvalidInput(
                "retention limits and env must be non-empty".into(),
            ));
        }
        let _retention_guard = self.lock_named(format!("retention:{}", command.env)).await;
        let mut state = self.state.write().await;
        let mut event_ids = state
            .events
            .values()
            .filter(|event| event.env == command.env && event.retention_until_ms <= command.now_ms)
            .filter(|event| event_can_be_purged(&state, &event.envelope.event_id))
            .map(|event| event.envelope.event_id.clone())
            .collect::<Vec<_>>();
        event_ids.sort();
        event_ids.truncate(command.event_limit as usize);

        let mut result = EventRetentionResult::default();
        for event_id in event_ids {
            let target_ids = state
                .targets
                .values()
                .filter(|target| target.env == command.env && target.event_id == event_id)
                .map(|target| target.target_id.clone())
                .collect::<Vec<_>>();
            for target_id in target_ids {
                if let Some(delivery_id) = state.delivery_by_target.remove(&target_id) {
                    if state.deliveries.remove(&delivery_id).is_some() {
                        result.deliveries_deleted += 1;
                    }
                    result.attempts_deleted += state
                        .attempts
                        .remove(&delivery_id)
                        .map_or(0, |attempts| attempts.len() as u64);
                }
                state.targets.remove(&target_id);
            }
            if let Some(event) = state.events.remove(&event_id) {
                state.producers.remove(&(
                    event.env,
                    event.producer,
                    event.producer_key,
                    event.envelope.event_type,
                ));
                result.events_deleted += 1;
            }
        }
        Ok(result)
    }
}

pub(super) fn event_can_be_purged(state: &MemoryState, event_id: &str) -> bool {
    let targets = state
        .targets
        .values()
        .filter(|target| target.event_id == event_id)
        .collect::<Vec<_>>();
    if targets.is_empty() {
        return true;
    }
    if targets.iter().any(|target| {
        target.status == EventFanoutTargetStatus::Pending
            || state.targets.values().any(|dependent| {
                dependent.depends_on_target_id.as_deref() == Some(target.target_id.as_str())
            })
    }) {
        return false;
    }
    targets.iter().all(|target| {
        state
            .delivery_by_target
            .get(&target.target_id)
            .and_then(|delivery_id| state.deliveries.get(delivery_id))
            .is_none_or(delivery_unblocks_lane)
    })
}
