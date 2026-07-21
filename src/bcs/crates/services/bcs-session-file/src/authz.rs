//! Pure authz helpers — no IO, no async. Kept in a dedicated module so the
//! service implementation can unit-test the predicate in isolation, and so the
//! service crate has no `group_repo` dependency (mutate-authz inputs are fed
//! from the HTTP layer).

use bcs_domain::ActorRef;

/// Object-key derivation: `session-files/{env}/{session_id}/{file_id}/{file_name}`.
///
/// `env` is taken from [`crate::SessionFileServiceConfig`] `env` field (set by
/// bootstrap to match the repo's `env` column — see `MySqlSessionFileStore`).
/// Keeping the env segment in the key mirrors the per-row DB `env` column so
/// prod/gray/pre/dev objects remain isolated in the storage backend.
pub fn derive_key(env: &str, session_id: &str, file_id: &str, file_name: &str) -> String {
    format!("session-files/{env}/{session_id}/{file_id}/{file_name}")
}

/// Test whether `caller` may mutate (delete / share) a file owned by `owner`.
///
/// Mutate-authz lives entirely in the service layer; the inputs the predicate
/// needs (`caller_identities`, `session_creator`, `driver_bot`) are *resolved*
/// by the HTTP layer and passed via [`bcs_service_api::application::session_files::DeleteFileCommand`]
/// / [`bcs_service_api::application::session_files::ShareMintCommand`].
///
/// - `caller_identities = [caller.actor_id] + owned bot UUIDs` (HTTP `caller_identities()`).
/// - `session_creator` = `session.created_by` (resolved by HTTP via `SessionRepoPort`).
/// - `driver_bot` = `group.driver_bot` (resolved by HTTP via `GroupRepoPort`).
///
/// Returns `true` if any of the caller's identities matches `owner.actor_id`,
/// `session_creator`, or `driver_bot`. Pure synchronous function — no registry
/// lookups — so the service crate avoids the `group_repo` dependency entirely.
pub fn can_mutate(
    caller_identities: &[String],
    owner: &ActorRef,
    session_creator: Option<&str>,
    driver_bot: Option<&str>,
) -> bool {
    if caller_identities.iter().any(|id| id == &owner.actor_id) {
        return true;
    }
    if let Some(creator) = session_creator {
        if caller_identities.iter().any(|id| id == creator) {
            return true;
        }
    }
    if let Some(driver) = driver_bot {
        if caller_identities.iter().any(|id| id == driver) {
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{ActorKind, ActorRef};

    fn actor(id: &str) -> ActorRef {
        ActorRef { actor_kind: ActorKind::Human, actor_id: id.into() }
    }

    #[test]
    fn owner_match_allows_mutate() {
        assert!(can_mutate(&["u1".into()], &actor("u1"), None, None));
    }

    #[test]
    fn creator_match_allows_mutate() {
        assert!(can_mutate(&["u1".into()], &actor("u2"), Some("u1"), None));
    }

    #[test]
    fn driver_bot_match_allows_mutate() {
        assert!(can_mutate(&["bot-x".into()], &actor("u2"), None, Some("bot-x")));
    }

    #[test]
    fn no_match_denies() {
        assert!(!can_mutate(&["u9".into()], &actor("u1"), Some("u2"), Some("bot-y")));
    }

    #[test]
    fn empty_identities_denies() {
        assert!(!can_mutate(&[], &actor("u1"), Some("u2"), Some("bot-x")));
    }

    #[test]
    fn multiple_identities_one_match_allows() {
        let ids = vec!["u1".to_string(), "bot-a".into(), "u3".into()];
        assert!(can_mutate(&ids, &actor("u3"), None, None));
    }
}