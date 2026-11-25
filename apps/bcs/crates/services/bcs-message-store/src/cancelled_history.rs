//! Cancelled queue messages remain durable, but never reappear in display history.
use std::collections::{HashMap, HashSet};
use bcs_domain::message_delivery::{MessageDeliveryStatus, PersistedMessageDelivery};

/// One pass over this Session's in-memory deliveries, not one scan per message.
pub(super) fn hidden_ids(deliveries: &[PersistedMessageDelivery]) -> HashSet<&str> {
    let mut states = HashMap::new();
    for delivery in deliveries {
        let entry = states.entry(delivery.source_message_id.as_str()).or_insert((false, true));
        entry.0 |= delivery.state.status == MessageDeliveryStatus::Cancelled;
        entry.1 &= delivery.state.is_unsent_terminal();
    }
    states.into_iter().filter_map(|(id, (cancelled, unsent))| (cancelled && unsent).then_some(id)).collect()
}

// One source-indexed aggregate; a status WHERE clause could select a global
// status index instead and scan unrelated cancelled messages. Filter before LIMIT.
pub(super) const VISIBLE_SQL: &str = "NOT EXISTS (
    SELECT 1 FROM bcs_message_deliveries history_delivery
    WHERE history_delivery.env = bcs_messages.env
      AND history_delivery.session_id = bcs_messages.session_id
      AND history_delivery.source_message_id = bcs_messages.message_id
    GROUP BY history_delivery.source_message_id
    HAVING MAX(CASE WHEN history_delivery.status = 'cancelled' THEN 1 ELSE 0 END) = 1
      AND MAX(CASE WHEN history_delivery.may_have_been_sent <> 0
        OR history_delivery.status NOT IN ('cancelled', 'expired', 'rejected_capacity', 'discarded_context')
        THEN 1 ELSE 0 END) = 0
)";
