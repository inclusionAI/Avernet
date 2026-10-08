//! `BotAuthorityCoreService` implementation (plan Task 3, spec §5/§12.4).
//!
//! Validation-before-answer: the repo port is a strict reader whose
//! `role`/`roles_for` are pair lookups; the Core OWNS the composition that
//! makes them authorization answers (validate the Bot's authority invariant
//! first). Distinct Bots inside one batch are validated exactly once each.

use std::collections::BTreeSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{BotAccessRelation, OwnershipState};
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::error::ServiceResult;

/// Core authority resolver over the strict authority repo port.
///
/// Holds the injected repo (trait object — the composition root wires the
/// SQL or in-memory implementation; bootstrap exposes only the trait) with
/// no other state: authority queries are env-bound at the repo instance.
pub struct BotAuthorityCoreServiceImpl {
    pub(super) authority: Arc<dyn BotAuthorityRepoPort>,
}

impl BotAuthorityCoreServiceImpl {
    pub fn new(authority: Arc<dyn BotAuthorityRepoPort>) -> Self {
        Self { authority }
    }
}

#[async_trait]
impl BotAuthorityCoreService for BotAuthorityCoreServiceImpl {
    async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState> {
        // The repo read is already strict (existence/version/owner checks);
        // the Core surface is the same typed snapshot.
        self.authority.ownership(bot_id).await
    }

    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        // Validate the Bot's authority invariant FIRST (fail closed); only
        // then resolve the caller's role. `Ok(None)` stays an ordinary deny
        // — never reached for an uninitialized/corrupt/missing Bot.
        self.authority.ownership(bot_id).await?;
        self.authority.role(user_id, bot_id).await
    }

    async fn roles_for(
        &self,
        pairs: &[(String, String)],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        if pairs.is_empty() {
            return Ok(Vec::new());
        }
        // Validate each DISTINCT bot exactly once (positions can repeat a
        // bot many times; validation cost stays per-request, not per-pair),
        // then issue the single strict batch read. Any invalid Bot fails
        // the whole batch — partial roles for a batch with corrupt state
        // must never look like successful authorization data.
        let mut distinct: BTreeSet<&str> = BTreeSet::new();
        for (_user_id, bot_id) in pairs {
            distinct.insert(bot_id.as_str());
        }
        for bot_id in distinct {
            self.authority.ownership(bot_id).await?;
        }
        self.authority.roles_for(pairs).await
    }

    // Task 4's mutation/listing delegation: the REPO owns the
    // one-transaction validation/mutation/audit contract, so the Core
    // forwards verbatim — no pre-reading the Bot here, because a
    // validation outside the mutation transaction adds no authority
    // (unlike the read-side validation-before-answer composition above).
    // Contract docs: manager.rs + the trait definition; recording tests
    // live in manager.rs.
    async fn mutate_manager(
        &self,
        actor: bcs_service_api::types::AuditActor,
        bot_id: &str,
        mutation: bcs_service_api::types::ManagerMutation,
    ) -> ServiceResult<bcs_service_api::types::ManagerMutationResult> {
        self.authority.mutate_manager(actor, bot_id, mutation).await
    }

    async fn list_managers(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<bcs_service_api::types::BotManagerList> {
        self.authority.list_managers(bot_id, offset, limit).await
    }

    // Task 7's team synchronization: the REPO owns the whole
    // one-transaction contract (scope re-validation, durable
    // idempotency, chunked reconcile, service-actor audit, binding and
    // receipt), so the Core forwards the command verbatim and composes
    // nothing on top — the same no-pre-read rule as `mutate_manager`.
    // The delegation recording tests live in `team_sync.rs`.
    async fn sync_team(
        &self,
        command: bcs_service_api::types::team_manager_sync::TeamManagerSync,
    ) -> ServiceResult<bcs_service_api::types::team_manager_sync::TeamSyncReceipt> {
        self.authority.sync_team(command).await
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use async_trait::async_trait;
    use bcs_domain::{BotAccessRelation, OwnershipState};
    use bcs_service_api::port::repo::BotAuthorityRepoPort;
    use bcs_service_api::types::error::AuthorityError;
    use bcs_service_api::{ServiceError, ServiceResult};

    use super::*;

    /// Validates the Core's composition rules independently of any concrete
    /// store: the repo read order is observable so missing validation would
    /// be detectable, and the multi-owner corruption branch (structurally
    /// impossible to seed in SQL) is proven at the strict-read level by a
    /// driver-side row injection.
    #[derive(Default)]
    struct RecordingAuthorityRepo {
        ownership_calls: std::sync::Mutex<Vec<String>>,
        role: std::sync::Mutex<std::collections::HashMap<(String, String), ServiceResult<Option<BotAccessRelation>>>>,
        ownership: std::sync::Mutex<std::collections::HashMap<String, ServiceResult<OwnershipState>>>,
    }

    #[async_trait]
    impl BotAuthorityRepoPort for RecordingAuthorityRepo {
        async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState> {
            self.ownership_calls.lock().unwrap().push(bot_id.to_string());
            let map = self.ownership.lock().unwrap();
            match map.get(bot_id) {
                Some(result) => match result {
                    Ok(state) => Ok(state.clone()),
                    Err(err) => Err(clone_service_error(err)),
                },
                // Tests always configure the bots they query.
                None => unreachable!("test repo: bot not configured"),
            }
        }

        async fn role(
            &self,
            user_id: &str,
            bot_id: &str,
        ) -> ServiceResult<Option<BotAccessRelation>> {
            let map = self.role.lock().unwrap();
            match map.get(&(user_id.to_string(), bot_id.to_string())) {
                Some(result) => match result {
                    Ok(role) => Ok(*role),
                    Err(err) => Err(clone_service_error(err)),
                },
                None => Ok(None),
            }
        }

        async fn roles_for(
            &self,
            pairs: &[(String, String)],
        ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
            let mut out = Vec::with_capacity(pairs.len());
            for (user_id, bot_id) in pairs {
                out.push(self.role(user_id, bot_id).await?);
            }
            Ok(out)
        }

        async fn mutate_manager(
            &self,
            _actor: bcs_service_api::types::AuditActor,
            _bot_id: &str,
            _mutation: bcs_service_api::types::ManagerMutation,
        ) -> ServiceResult<bcs_service_api::types::ManagerMutationResult> {
            unreachable!("read-side recording tests never mutate managers")
        }

        async fn list_managers(
            &self,
            _bot_id: &str,
            _offset: u64,
            _limit: u64,
        ) -> ServiceResult<bcs_service_api::types::BotManagerList> {
            unreachable!("read-side recording tests never list managers")
        }

        async fn sync_team(
            &self,
            _command: bcs_service_api::types::team_manager_sync::TeamManagerSync,
        ) -> ServiceResult<bcs_service_api::types::team_manager_sync::TeamSyncReceipt> {
            unreachable!("read-side recording tests never synchronize teams")
        }
    }

    /// Clone the service error payload for replay; test inputs are static
    /// enough that the important branches map identity-stably.
    fn clone_service_error(err: &ServiceError) -> ServiceError {
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
            ServiceError::Authority(AuthorityError::Forbidden(reason)) => {
                ServiceError::Authority(AuthorityError::Forbidden(reason.clone()))
            }
            ServiceError::BotNotFound(bot_id) => ServiceError::BotNotFound(bot_id.clone()),
            other => panic!("unsupported test error shape: {:?}", other),
        }
    }

    fn owned_state(owner: &str, version: u64) -> ServiceResult<OwnershipState> {
        Ok(OwnershipState {
            owner_user_id: owner.to_string(),
            ownership_version: version,
        })
    }

    #[tokio::test]
    async fn core_validates_bot_before_answering_roles() {
        let repo = RecordingAuthorityRepo::default();
        repo.ownership
            .lock()
            .unwrap()
            .insert("bot-a".to_string(), owned_state("user-a", 1));
        repo.role.lock().unwrap().insert(
            ("user-a".to_string(), "bot-a".to_string()),
            Ok(Some(BotAccessRelation::Owner)),
        );
        let core = BotAuthorityCoreServiceImpl::new(Arc::new(repo));

        assert_eq!(core.ownership("bot-a").await.unwrap().owner_user_id, "user-a");
        assert_eq!(
            core.role("user-a", "bot-a").await.unwrap(),
            Some(BotAccessRelation::Owner)
        );
        // Absent role rows: an ordinary deny.
        assert_eq!(core.role("user-b", "bot-a").await.unwrap(), None);
    }

    #[tokio::test]
    async fn core_surfaces_uninitialized_and_corrupt_never_none() {
        let repo = RecordingAuthorityRepo::default();
        repo.ownership.lock().unwrap().insert(
            "bot-zero".to_string(),
            Err(ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized {
                    bot_id: "bot-zero".into(),
                    env: "local".into(),
                },
            )),
        );
        repo.ownership.lock().unwrap().insert(
            "bot-broken".to_string(),
            Err(ServiceError::Authority(
                AuthorityError::CorruptAuthority {
                    bot_id: "bot-broken".into(),
                    env: "local".into(),
                    detail: "multiple approved owner edges".into(),
                },
            )),
        );
        // An owner edge answering for an unvalidated bot would be the bug;
        // the seeded roles must never surface.
        repo.role.lock().unwrap().insert(
            ("user-a".to_string(), "bot-zero".to_string()),
            Ok(Some(BotAccessRelation::Owner)),
        );
        let core = BotAuthorityCoreServiceImpl::new(Arc::new(repo));

        assert!(matches!(
            core.role("user-a", "bot-zero").await,
            Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized { .. }))
        ));
        assert!(matches!(
            core.role("user-a", "bot-broken").await,
            Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
        ));
        // Whole-batch fail-closed when ANY involved bot is invalid. Distinct Bots
        // validate in lexical order (`bot-broken` before `bot-zero`), so the
        // surfacing branch is the pair's own; the property under test is
        // that a batch with ANY invalid bot never yields partial roles.
        match core
            .roles_for(&[
                ("user-a".into(), "bot-zero".into()),
                ("user-a".into(), "bot-broken".into()),
            ])
            .await
        {
            Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. })) => {}
            Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized { .. })) => {}
            other => panic!("batch with invalid bots must fail closed, got {:?}", other.is_ok()),
        }
    }

    #[tokio::test]
    async fn core_validates_each_distinct_bot_once_per_batch() {
        let repo = RecordingAuthorityRepo::default();
        repo.ownership.lock().unwrap().insert(
            "bot-a".to_string(),
            owned_state("user-a", 1),
        );
        repo.role.lock().unwrap().insert(
            ("user-a".to_string(), "bot-a".to_string()),
            Ok(Some(BotAccessRelation::Owner)),
        );
        let calls = Arc::new(std::sync::Mutex::new(Vec::<String>::new()));
        struct Counting {
            inner: RecordingAuthorityRepo,
            calls: Arc<std::sync::Mutex<Vec<String>>>,
        }
        #[async_trait]
        impl BotAuthorityRepoPort for Counting {
            async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState> {
                self.calls.lock().unwrap().push(bot_id.to_string());
                self.inner.ownership(bot_id).await
            }
            async fn role(
                &self,
                user_id: &str,
                bot_id: &str,
            ) -> ServiceResult<Option<BotAccessRelation>> {
                self.inner.role(user_id, bot_id).await
            }
            async fn roles_for(
                &self,
                pairs: &[(String, String)],
            ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
                self.inner.roles_for(pairs).await
            }
            async fn mutate_manager(
                &self,
                actor: bcs_service_api::types::AuditActor,
                bot_id: &str,
                mutation: bcs_service_api::types::ManagerMutation,
            ) -> ServiceResult<bcs_service_api::types::ManagerMutationResult> {
                self.inner.mutate_manager(actor, bot_id, mutation).await
            }
            async fn list_managers(
                &self,
                bot_id: &str,
                offset: u64,
                limit: u64,
            ) -> ServiceResult<bcs_service_api::types::BotManagerList> {
                self.inner.list_managers(bot_id, offset, limit).await
            }
            async fn sync_team(
                &self,
                command: bcs_service_api::types::team_manager_sync::TeamManagerSync,
            ) -> ServiceResult<bcs_service_api::types::team_manager_sync::TeamSyncReceipt> {
                self.inner.sync_team(command).await
            }
        }
        let repo = Counting {
            inner: repo,
            calls: calls.clone(),
        };
        let core = BotAuthorityCoreServiceImpl::new(Arc::new(repo));
        let pairs: Vec<(String, String)> = (0..10)
            .map(|i| (format!("user-{i}"), "bot-a".to_string()))
            .collect();
        let roles = core.roles_for(&pairs).await.unwrap();
        assert_eq!(roles.len(), 10);
        assert_eq!(roles[0], None);
        assert_eq!(
            calls.lock().unwrap().len(),
            1,
            "one distinct bot → exactly one ownership validation, never per-pair"
        );
    }
}