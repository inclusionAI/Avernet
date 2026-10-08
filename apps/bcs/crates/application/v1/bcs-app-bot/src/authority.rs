//! `BotAuthorityHook` implementation: the centralized manage-permission
//! gate for the bot application facade (plan Task 3, spec §6/§13.5).
//!
//! Every bot-management use case (status changes, manager mutations,
//! ownership transfer, delivery batches …) asks THIS hook and nothing else.
//! The hook resolves exclusively through [`BotAuthorityCoreService`] — the
//! application layer depends on Core only, NEVER the authority repo port —
//! so strictness improvements (validation order, corrupt-authority
//! handling) land in one place for every caller.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::BotAuthorityHook;
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::types::BotAccessRelation;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::{ServiceError, ServiceResult};

/// Centralized authority hook over the injected strict authority core.
pub struct BotAuthorityHookImpl {
    core: Arc<dyn BotAuthorityCoreService>,
}

impl BotAuthorityHookImpl {
    pub fn new(core: Arc<dyn BotAuthorityCoreService>) -> Self {
        Self { core }
    }
}

#[async_trait]
impl BotAuthorityHook for BotAuthorityHookImpl {
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
        Ok(self.core.role(user_id, bot_id).await?.is_some())
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        match self.core.role(user_id, bot_id).await? {
            Some(BotAccessRelation::Owner) => Ok(()),
            // Owner-only surfaces deny every non-owner with the fixed
            // Forbidden branch (subject-shape and validation errors keep
            // their own branches through Core).
            _ => Err(ServiceError::Authority(AuthorityError::Forbidden(format!(
                "user '{user_id}' is not the owner of bot '{bot_id}'"
            )))),
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use async_trait::async_trait;
    use bcs_service_api::types::{BotAccessRelation, OwnershipState};
    use bcs_service_api::core::BotAuthorityCoreService;
    use bcs_service_api::types::error::AuthorityError;
    use bcs_service_api::{ServiceError, ServiceResult};

    use super::*;

    /// Recording double of the Core: proves the Hook actually RESOLVES the
    /// questions through the Core (not through any repo or local shortcut).
    #[derive(Default)]
    struct RecordingCore {
        role_calls: std::sync::Mutex<Vec<(String, String)>>,
        roles: std::collections::BTreeMap<(String, String), Result<Option<BotAccessRelation>, ServiceError>>,
    }

    #[async_trait]
    impl BotAuthorityCoreService for RecordingCore {
        async fn ownership(&self, _bot_id: &str) -> ServiceResult<OwnershipState> {
            unimplemented!("the hook never answers through ownership itself")
        }

        async fn role(
            &self,
            user_id: &str,
            bot_id: &str,
        ) -> ServiceResult<Option<BotAccessRelation>> {
            self.role_calls
                .lock()
                .unwrap()
                .push((user_id.to_string(), bot_id.to_string()));
            match self.roles.get(&(user_id.to_string(), bot_id.to_string())) {
                Some(Ok(role)) => Ok(*role),
                Some(Err(err)) => Err(clone_error(err)),
                None => Ok(None),
            }
        }

        async fn roles_for(
            &self,
            _pairs: &[(String, String)],
        ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
            unimplemented!("the hook never answers through roles_for itself")
        }
    }

    fn clone_error(err: &ServiceError) -> ServiceError {
        match err {
            ServiceError::Authority(AuthorityError::OwnershipNotInitialized { bot_id, env }) => {
                ServiceError::Authority(AuthorityError::OwnershipNotInitialized {
                    bot_id: bot_id.clone(),
                    env: env.clone(),
                })
            }
            ServiceError::Authority(AuthorityError::CorruptAuthority { bot_id, env, detail }) => {
                ServiceError::Authority(AuthorityError::CorruptAuthority {
                    bot_id: bot_id.clone(),
                    env: env.clone(),
                    detail: detail.clone(),
                })
            }
            other => panic!("unsupported test error shape: {:?}", other),
        }
    }

    #[tokio::test]
    async fn can_manage_resolves_through_the_core() {
        let mut core = RecordingCore::default();
        core.roles.insert(
            ("user-a".into(), "bot-a".into()),
            Ok(Some(BotAccessRelation::Owner)),
        );
        let core = Arc::new(core);
        let hook = BotAuthorityHookImpl::new(core.clone());

        assert!(hook.can_manage("user-a", "bot-a").await.unwrap());
        assert!(!hook.can_manage("user-b", "bot-a").await.unwrap());

        let calls = core.role_calls.lock().unwrap().clone();
        assert_eq!(
            calls,
            vec![
                ("user-a".to_string(), "bot-a".to_string()),
                ("user-b".to_string(), "bot-a".to_string()),
            ],
            "the hook must actually call Core::role with the exact arguments"
        );
    }

    #[tokio::test]
    async fn can_manage_projects_manager_roles_to_true() {
        let mut core = RecordingCore::default();
        core.roles.insert(
            ("user-m".into(), "bot-a".into()),
            Ok(Some(BotAccessRelation::Manager)),
        );
        let hook = BotAuthorityHookImpl::new(Arc::new(core));
        assert!(hook.can_manage("user-m", "bot-a").await.unwrap());
    }

    #[tokio::test]
    async fn require_owner_gates_on_the_owner_relation() {
        let mut core = RecordingCore::default();
        core.roles.insert(
            ("user-a".into(), "bot-a".into()),
            Ok(Some(BotAccessRelation::Owner)),
        );
        core.roles.insert(
            ("user-m".into(), "bot-a".into()),
            Ok(Some(BotAccessRelation::Manager)),
        );
        let hook = BotAuthorityHookImpl::new(Arc::new(core));

        hook.require_owner("user-a", "bot-a").await.unwrap();
        assert!(matches!(
            hook.require_owner("user-m", "bot-a").await,
            Err(ServiceError::Authority(AuthorityError::Forbidden(_)))
        ));
        assert!(matches!(
            hook.require_owner("user-nobody", "bot-a").await,
            Err(ServiceError::Authority(AuthorityError::Forbidden(_)))
        ));
    }

    #[tokio::test]
    async fn core_validation_errors_propagate_never_flatten_to_denies() {
        let mut core = RecordingCore::default();
        core.roles.insert(
            ("user-a".into(), "bot-zero".into()),
            Err(ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized {
                    bot_id: "bot-zero".into(),
                    env: "local".into(),
                },
            )),
        );
        core.roles.insert(
            ("user-a".into(), "bot-broken".into()),
            Err(ServiceError::Authority(
                AuthorityError::CorruptAuthority {
                    bot_id: "bot-broken".into(),
                    env: "local".into(),
                    detail: "owner slot damaged".into(),
                },
            )),
        );
        let hook = BotAuthorityHookImpl::new(Arc::new(core));

        assert!(matches!(
            hook.can_manage("user-a", "bot-zero").await,
            Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized { .. }))
        ));
        assert!(matches!(
            hook.require_owner("user-a", "bot-broken").await,
            Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
        ));
    }
}