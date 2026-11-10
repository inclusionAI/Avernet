//! Bounded Provider registration identity snapshot; independent of transport/storage.
use super::{AuthorityActor, OwnershipInitialization};
use crate::{ServiceError, ServiceResult};

const MAX_INITIAL_MANAGERS: usize = 100;
fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation { message: message.into(), request_id: None }
}
fn valid_user_id(id: &str) -> bool {
    !id.is_empty() && id == id.trim() && !id.starts_with("human_")
        && id.len() <= 128 && !id.chars().any(char::is_control)
}

/// First entry is the owner, remaining entries form a deduplicated initial set.
/// Check every entry before deduplication; malformed identities are never skipped.
/// At most 101 input entries bound transaction work even for duplicate-heavy input.
pub fn normalize_provider_owners(owners: &[String]) -> ServiceResult<(String, Vec<String>)> {
    if owners.is_empty() || owners.len() > MAX_INITIAL_MANAGERS + 1 {
        return Err(invalid("owners must contain between 1 and 101 user ids"));
    }
    let owner = owners[0].trim().to_string();
    let mut managers = Vec::new();
    for input in owners {
        let user = input.trim();
        if !valid_user_id(user) {
            return Err(invalid("owners must contain valid raw user ids"));
        }
        if user != owner && !managers.iter().any(|existing| existing == user) {
            managers.push(user.to_string());
        }
    }
    Ok((owner, managers))
}

/// Store-boundary validation: only Provider initialization can carry managers.
/// The normalized snapshot is byte-exact, distinct and excludes the owner.
pub fn validate_initial_managers(initialization: &OwnershipInitialization) -> ServiceResult<()> {
    let managers = &initialization.initial_manager_user_ids;
    if managers.is_empty() { return Ok(()); }
    if !matches!(&initialization.mutation.actor, AuthorityActor::Provider { provider_id } if !provider_id.trim().is_empty()) {
        return Err(invalid("initial managers require a Provider actor"));
    }
    if managers.len() > MAX_INITIAL_MANAGERS || !valid_user_id(&initialization.owner_user_id) {
        return Err(invalid("invalid Provider authority initialization"));
    }
    let mut seen = std::collections::HashSet::new();
    for user in managers {
        if !valid_user_id(user) || user == &initialization.owner_user_id || !seen.insert(user) {
            return Err(invalid("initial managers must be distinct valid user ids excluding the owner"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bounds_raw_input_and_preserves_order_after_normalization() {
        let ids = [" alice ", " bob ", "alice", "bob", "Carol", "carol"].map(str::to_string);
        assert_eq!(normalize_provider_owners(&ids).ok(),
            Some(("alice".into(), vec!["bob".into(), "Carol".into(), "carol".into()])));
        assert!(normalize_provider_owners(&vec!["alice".into(); 101]).is_ok());
        assert!(normalize_provider_owners(&vec!["alice".into(); 102]).is_err());
        for id in ["", " ", "human_alice", "al\nice"] {
            assert!(normalize_provider_owners(&["owner".into(), id.into()]).is_err());
        }
    }

    #[test]
    fn only_provider_snapshots_accept_distinct_non_owner_managers() {
        use super::super::AuthorityMutation;
        let mut value = OwnershipInitialization { owner_user_id: "alice".into(),
            initial_manager_user_ids: vec!["bob".into()], mutation: AuthorityMutation {
                mutation_id: "init".into(), actor: AuthorityActor::Human {user_id: "alice".into()},
            } };
        assert!(validate_initial_managers(&value).is_err());
        value.mutation.actor = AuthorityActor::Provider {provider_id: "provider".into()};
        assert!(validate_initial_managers(&value).is_ok());
        for managers in [vec!["alice".into()], vec!["bob".into(), "bob".into()], vec![" bob ".into()],
            vec!["human_bob".into()], vec!["bob".into(); 101]] {
            value.initial_manager_user_ids = managers;
            assert!(validate_initial_managers(&value).is_err());
        }
    }
}
