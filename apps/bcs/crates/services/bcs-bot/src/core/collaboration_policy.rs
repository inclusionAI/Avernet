//! Provider scope is an upper boundary: friendship and visibility cannot widen it.
use bcs_service_api::{ProviderCollaborationScope, ServiceError, ServiceResult};
use bcs_service_api::port::repo::collaboration_actor::CollaborationActor;

pub(super) fn enforce_owner_scope(actors: &[CollaborationActor], ids: &[String]) -> ServiceResult<Option<String>> {
    let mut owner = None;
    for actor in actors {
        if actor.provider_id.is_none() { continue; }
        let config = actor.provider_config.as_deref()
            .ok_or_else(|| ServiceError::InternalError("affiliated Provider is missing".into()))?;
        let scope = ProviderCollaborationScope::from_config(config)
            .map_err(|_| ServiceError::InternalError("invalid Provider collaboration policy".into()))?;
        if scope == ProviderCollaborationScope::SameOwner {
            if actor.provider_disabled { return Err(ServiceError::Forbidden("Provider is disabled".into())); }
            let value = actor.owner.as_deref().filter(|value| !value.trim().is_empty())
                .ok_or_else(|| ServiceError::Forbidden("same_owner requires a known Bot owner".into()))?;
            if owner.is_some_and(|owner| owner != value) {
                return Err(ServiceError::Forbidden("Provider collaboration requires the same owner".into()));
            }
            owner = Some(value);
        }
    }
    let Some(owner) = owner else { return Ok(None); };
    for id in ids {
        let actor_owner = if let Some(staff) = id.strip_prefix("human_") {
            Some(staff)
        } else {
            actors.iter().find(|actor| actor.actor_id == *id).and_then(|actor| actor.owner.as_deref())
        };
        if actor_owner != Some(owner) {
            return Err(ServiceError::Forbidden("Provider collaboration requires the same owner".into()));
        }
    }
    Ok(Some(owner.to_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn actor(id: &str, owner: Option<&str>, config: Option<&str>) -> CollaborationActor {
        CollaborationActor { actor_id: id.into(), owner: owner.map(str::to_owned),
            provider_id: config.map(|_| "provider".into()), provider_config: config.map(str::to_owned), provider_disabled: false }
    }
    #[test]
    fn scope_is_symmetric_and_checks_every_human_and_bot() {
        let restricted = actor("local", Some("alice"), Some(r#"{"collaboration_scope":"same_owner"}"#));
        let own = actor("cloud", Some("alice"), None);
        let other = actor("other", Some("bob"), None);
        let actors = vec![restricted, own, other];
        assert_eq!(enforce_owner_scope(&actors, &["local".into(), "cloud".into(), "human_alice".into()]).unwrap(), Some("alice".into()));
        for ids in [["other", "local"], ["local", "other"], ["human_bob", "local"], ["local", "missing"]] {
            assert!(enforce_owner_scope(&actors, &ids.map(str::to_owned)).is_err());
        }
    }
    #[test]
    fn missing_bound_provider_and_disabled_provider_do_not_become_standard() {
        let mut missing = actor("local", Some("alice"), None);
        missing.provider_id = Some("absent".into());
        assert!(matches!(enforce_owner_scope(&[missing], &["local".into()]), Err(ServiceError::InternalError(_))));
        let mut disabled = actor("local", Some("alice"), Some(r#"{"collaboration_scope":"same_owner"}"#));
        disabled.provider_disabled = true;
        assert!(matches!(enforce_owner_scope(&[disabled], &["local".into()]), Err(ServiceError::Forbidden(_))));
    }
    #[test]
    fn a_shared_restricted_provider_does_not_make_different_owners_equal() {
        let config = Some(r#"{"collaboration_scope":"same_owner"}"#);
        let actors = [actor("local-alice", Some("alice"), config), actor("local-bob", Some("bob"), config)];
        assert!(matches!(enforce_owner_scope(&actors, &["local-alice".into(), "local-bob".into()]), Err(ServiceError::Forbidden(_))));
    }
    #[test]
    fn legacy_policy_preserves_behavior_but_unknown_owner_and_bad_policy_fail_closed() {
        assert_eq!(enforce_owner_scope(&[actor("legacy", None, Some("{}"))], &["legacy".into()]).unwrap(), None);
        for config in [r#"{"collaboration_scope":"same_owner"}"#, r#"{"collaboration_scope":"unknown"}"#, "[]"] {
            assert!(enforce_owner_scope(&[actor("local", None, Some(config))], &["local".into()]).is_err());
        }
    }
}
