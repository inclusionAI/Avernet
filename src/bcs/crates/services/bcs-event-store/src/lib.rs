//! Transactional persistence for public BCS Events and subscriptions.

pub mod db;
pub mod memory;
pub mod recorder;
pub mod transaction_plan;

mod timestamp;

pub use db::DbEventStore;
pub use memory::MemoryEventStore;
pub use recorder::EventRecorder;
pub use transaction_plan::{
    EventAppendTransactionPlan, GroupDeletionEventTransactionPlan,
    GroupProvisioningEventTransactionPlan,
};

use bcs_service_api::types::{EventScope, EventSubscriptionScope};

pub(crate) fn validate_scope(scope: &EventSubscriptionScope) -> Result<(), String> {
    if scope.id.is_empty() {
        return Err("group scope must include a non-empty id".to_string());
    }
    Ok(())
}

pub(crate) fn event_filter_matches(filter: &str, event_type: &str) -> bool {
    filter == event_type
        || filter.strip_suffix(".*").is_some_and(|family| {
            event_type.starts_with(family) && event_type[family.len()..].starts_with('.')
        })
}

pub(crate) fn subscription_scope_matches(
    subscription: &EventSubscriptionScope,
    event: &EventScope,
) -> bool {
    event.group_id.as_deref() == Some(subscription.id.as_str())
}
