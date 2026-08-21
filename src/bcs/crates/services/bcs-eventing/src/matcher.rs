//! Event filter and scope matching policy.

use bcs_service_api::types::{EventScope, EventSubscriptionScope, EventSubscriptionScopeType};

use crate::EventCatalog;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum EventMatcherError {
    #[error("group scope must include a non-empty id")]
    ResourceScopeMissingId,
    #[error("group scope id exceeds 256 bytes")]
    ResourceScopeIdTooLong,
    #[error("event filter is empty or exceeds 256 bytes")]
    InvalidFilterLength,
    #[error("event filter contains invalid syntax")]
    InvalidFilterSyntax,
    #[error("event filter is not registered in the public Event Catalog")]
    UnknownFilter,
}

pub fn validate_subscription_scope(
    scope: &EventSubscriptionScope,
) -> Result<(), EventMatcherError> {
    match scope.scope_type {
        EventSubscriptionScopeType::Group if scope.id.is_empty() => {
            return Err(EventMatcherError::ResourceScopeMissingId);
        }
        EventSubscriptionScopeType::Group if scope.id.len() > 256 => {
            return Err(EventMatcherError::ResourceScopeIdTooLong);
        }
        EventSubscriptionScopeType::Group => {}
    }
    Ok(())
}

pub fn validate_event_filter(
    catalog: &EventCatalog,
    filter: &str,
) -> Result<(), EventMatcherError> {
    if filter.is_empty() || filter.len() > 256 {
        return Err(EventMatcherError::InvalidFilterLength);
    }
    let (namespace, wildcard) = filter
        .strip_suffix(".*")
        .map_or((filter, false), |namespace| (namespace, true));
    if namespace.split('.').count() < 2 && !wildcard || !valid_namespace(namespace) {
        return Err(EventMatcherError::InvalidFilterSyntax);
    }
    if filter.contains('*') && !wildcard {
        return Err(EventMatcherError::InvalidFilterSyntax);
    }
    if !catalog.is_registered_filter(filter) {
        return Err(EventMatcherError::UnknownFilter);
    }
    Ok(())
}

pub fn event_filter_matches(filter: &str, event_type: &str) -> bool {
    filter == event_type
        || filter.strip_suffix(".*").is_some_and(|namespace| {
            event_type
                .strip_prefix(namespace)
                .is_some_and(|suffix| suffix.starts_with('.'))
        })
}

pub fn subscription_scope_matches(
    subscription: &EventSubscriptionScope,
    event: &EventScope,
) -> bool {
    matches!(subscription.scope_type, EventSubscriptionScopeType::Group)
        && event.group_id.as_deref() == Some(subscription.id.as_str())
}

fn valid_namespace(value: &str) -> bool {
    !value.is_empty() && value.split('.').all(valid_segment)
}

fn valid_segment(segment: &str) -> bool {
    let mut characters = segment.chars();
    let Some(first) = characters.next() else {
        return false;
    };
    first.is_ascii_lowercase()
        && characters.all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '_'
        })
}
