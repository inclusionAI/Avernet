//! Free-standing classification/projection helpers of the connect lane
//! (plan Task 12 fix round, behavior-preserving move out of `lib.rs`).

use std::collections::HashSet;

use bcs_domain::actor::ActorKind;
use bcs_service_api::RequestAuthHeaders;
use bcs_service_api::ServiceError;
use bcs_user_directory_api::{UserDirectoryLookupContext, UserDirectoryPlugin};

pub(crate) fn actor_kind_of(id: &str) -> ActorKind {
    if id.starts_with("human_") {
        ActorKind::Human
    } else {
        ActorKind::Bot
    }
}

pub(crate) fn is_lock_wait_timeout(err: &ServiceError) -> bool {
    let message = err.to_string();
    message.contains("1205") || message.to_ascii_lowercase().contains("lock wait timeout")
}

pub(crate) fn normalize_policy_value(value: &str) -> String {
    value.trim().to_ascii_lowercase()
}

pub(crate) fn department_matches_allowlist_entry(actual: &str, allowed: &str) -> bool {
    actual == allowed || actual.starts_with(&format!("{allowed}-"))
}

pub(crate) fn is_private_visibility(value: &str) -> bool {
    matches!(normalize_policy_value(value).as_str(), "private")
}

pub(crate) fn bot_friend_ext_scope_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> HashSet<String> {
    friend_ext
        .get(key)
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str())
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(ToOwned::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

pub(crate) fn bot_friend_ext_no_check_scope_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
) -> HashSet<String> {
    bot_friend_ext_scope_friend_deps(friend_ext, "no_check_scope_friend_deps")
}

pub(crate) fn bot_friend_ext_view_scope_user_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
) -> HashSet<String> {
    bot_friend_ext_scope_friend_deps(friend_ext, "view_scope_user_friend_deps")
}

pub(crate) fn bot_friend_ext_view_scope_agent_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
) -> HashSet<String> {
    bot_friend_ext_scope_friend_deps(friend_ext, "view_scope_agent_friend_deps")
}

pub(crate) fn user_directory_lookup_context(request_auth: Option<&RequestAuthHeaders>) -> UserDirectoryLookupContext {
    let Some(request_auth) = request_auth else {
        return UserDirectoryLookupContext::default();
    };
    if !request_auth.forwarded_headers.is_empty() {
        return UserDirectoryLookupContext {
            forwarded_headers: request_auth.forwarded_headers.clone(),
        };
    }
    let mut forwarded_headers = Vec::new();
    if let Some(value) = &request_auth.authorization {
        forwarded_headers.push(("authorization".to_string(), value.clone()));
    }
    if let Some(value) = &request_auth.cookie {
        forwarded_headers.push(("cookie".to_string(), value.clone()));
    }
    UserDirectoryLookupContext { forwarded_headers }
}

