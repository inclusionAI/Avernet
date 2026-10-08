//! `BotAuthorityCoreService` mutation/list surface (plan Task 4, spec
//! §5.4/§6).
//!
//! The mutation contract is owned by the REPO: validation, edge writes and
//! the `bot_manager_changes` audit are ONE transaction there, and a
//! validation read outside that transaction adds no authority. The Core
//! therefore does NOT pre-read the Bot before forwarding (that would be a
//! racy duplicate of the in-transaction checks, unlike the read-side
//! validation-before-answer composition in [`super::reads`]) — it delegates
//! verbatim and composes nothing on top, keeping exactly one validation
//! site per branch. The application layer (Human manager API) reaches
//! these methods through [`crate::authority`] and never the repo port.
//!
//! The delegation bodies live in [`super::reads`] (trait impls cannot be
//! split across blocks); this module carries the recording tests that pin
//! the verbatim delegation and its no-extra-reads rule.

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::sync::Mutex;

    use async_trait::async_trait;
    use bcs_domain::{BotAccessRelation, ManagementSource, OwnershipState};
    use bcs_service_api::core::BotAuthorityCoreService;
    use bcs_service_api::port::repo::BotAuthorityRepoPort;
    use bcs_service_api::types::{
        AuditActor, BotManagerList, BotManagerSummary, ManagerMutation, ManagerMutationResult,
    };
    use bcs_service_api::{ServiceError, ServiceResult};


    /// Recording double: proves the Core forwards mutations and listings to
    /// the repo port verbatim (exact actor/bot/mutation/offset/limit) and
    /// surfaces both results and errors unchanged — no shortcut path, no
    /// pre-read that could diverge from the in-transaction validation.
    struct RecordingRepo {
        mutate_calls: Mutex<Vec<(AuditActor, String, ManagerMutation)>>,
        list_calls: Mutex<Vec<(String, u64, u64)>>,
        mutate_result: Mutex<Result<ManagerMutationResult, ServiceError>>,
        list_result: Mutex<Result<BotManagerList, ServiceError>>,
    }

    impl Default for RecordingRepo {
        fn default() -> Self {
            Self {
                mutate_calls: Mutex::new(Vec::new()),
                list_calls: Mutex::new(Vec::new()),
                mutate_result: Mutex::new(Ok(ManagerMutationResult::default())),
                list_result: Mutex::new(Ok(BotManagerList {
                    owner_user_id: String::new(),
                    managers: Vec::new(),
                })),
            }
        }
    }

    #[async_trait]
    impl BotAuthorityRepoPort for RecordingRepo {
        async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState> {
            let _ = bot_id;
            unimplemented!("mutate/list flows never answer through ownership reads")
        }

        async fn role(
            &self,
            _user_id: &str,
            _bot_id: &str,
        ) -> ServiceResult<Option<BotAccessRelation>> {
            unimplemented!("mutate/list flows never answer through role reads")
        }

        async fn roles_for(
            &self,
            _pairs: &[(String, String)],
        ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
            unimplemented!("mutate/list flows never answer through roles_for")
        }

        async fn mutate_manager(
            &self,
            actor: AuditActor,
            bot_id: &str,
            mutation: ManagerMutation,
        ) -> ServiceResult<ManagerMutationResult> {
            self.mutate_calls
                .lock()
                .unwrap()
                .push((actor, bot_id.to_string(), mutation));
            let result = self.mutate_result.lock().unwrap();
            match result.as_ref() {
                Ok(value) => Ok(value.clone()),
                Err(err) => Err(clone_service_error(err)),
            }
        }

        async fn list_managers(
            &self,
            bot_id: &str,
            offset: u64,
            limit: u64,
        ) -> ServiceResult<BotManagerList> {
            self.list_calls
                .lock()
                .unwrap()
                .push((bot_id.to_string(), offset, limit));
            let result = self.list_result.lock().unwrap();
            match result.as_ref() {
                Ok(value) => Ok(value.clone()),
                Err(err) => Err(clone_service_error(err)),
            }
        }

        async fn sync_team(
            &self,
            _command: bcs_service_api::types::team_manager_sync::TeamManagerSync,
        ) -> ServiceResult<bcs_service_api::types::team_manager_sync::TeamSyncReceipt> {
            unreachable!("mutate/list recording tests never synchronize teams")
        }
    }

    fn clone_service_error(err: &ServiceError) -> ServiceError {
        match err {
            ServiceError::Authority(
                bcs_service_api::types::error::AuthorityError::Forbidden(reason),
            ) => ServiceError::Authority(
                bcs_service_api::types::error::AuthorityError::Forbidden(reason.clone()),
            ),
            other => panic!("unsupported test error shape: {:?}", other),
        }
    }

    fn owned_state(owner: &str) -> OwnershipState {
        OwnershipState {
            owner_user_id: owner.to_string(),
            ownership_version: 1,
        }
    }

    #[tokio::test]
    async fn core_forwards_mutations_verbatim_to_the_repo() {
        let repo = Arc::new(RecordingRepo::default());
        *repo.mutate_result.lock().unwrap() = Ok(ManagerMutationResult {
            changed: true,
            remaining_team_sources: vec!["team-a".to_string()],
        });
        let core =
            super::super::reads::BotAuthorityCoreServiceImpl::new(repo.clone());
        let actor = AuditActor::Human {
            user_id: "a".to_string(),
        };
        let mutation = ManagerMutation::GrantDirect {
            user_id: "b".to_string(),
        };
        let result = core
            .mutate_manager(actor.clone(), "bot-a", mutation.clone())
            .await
            .unwrap();
        assert!(result.changed);
        assert_eq!(result.remaining_team_sources, vec!["team-a"]);
        assert_eq!(
            repo.mutate_calls.lock().unwrap().len(),
            1,
            "exactly one delegated call — the Core adds no extra validation reads"
        );
        assert_eq!(
            repo.mutate_calls.lock().unwrap()[0],
            (actor, "bot-a".to_string(), mutation),
            "actor, bot and mutation must arrive at the repo verbatim"
        );

        // Errors surface unchanged (never flattened into denies).
        *repo.mutate_result.lock().unwrap() = Err(ServiceError::Authority(
            bcs_service_api::types::error::AuthorityError::Forbidden("actor lost the role".into()),
        ));
        assert!(core
            .mutate_manager(
                AuditActor::Human {
                    user_id: "a".to_string()
                },
                "bot-a",
                ManagerMutation::RevokeNonTeam {
                    user_id: "b".to_string()
                },
            )
            .await
            .is_err());
    }

    #[tokio::test]
    async fn core_forwards_listing_verbatim_to_the_repo() {
        let repo = Arc::new(RecordingRepo::default());
        *repo.list_result.lock().unwrap() = Ok(BotManagerList {
            owner_user_id: owned_state("a").owner_user_id,
            managers: vec![BotManagerSummary {
                user_id: "m".to_string(),
                sources: vec![ManagementSource::Direct],
            }],
        });
        let core =
            super::super::reads::BotAuthorityCoreServiceImpl::new(repo.clone());
        let list = core.list_managers("bot-a", 3, 7).await.unwrap();
        assert_eq!(list.owner_user_id, "a");
        assert_eq!(list.managers.len(), 1);
        assert_eq!(list.managers[0].user_id, "m");
        assert_eq!(
            repo.list_calls.lock().unwrap()[0],
            ("bot-a".to_string(), 3, 7),
            "offset/limit arrive at the repo verbatim"
        );
    }
}