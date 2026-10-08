//! `sync_team` Core surface (plan Task 7, spec §5.4/§6).
//!
//! The repo owns the ONE-TRANSACTION team-sync contract (credential-scope
//! re-validation, durable idempotency, aggregate reconcile, service-actor
//! audit, binding and receipt). The Core therefore does NOT pre-read the
//! Bot here — exactly like the `mutate_manager` delegation, a validation
//! outside the mutation transaction adds no authority, and composing one
//! in would be a racy duplicate of the in-transaction guards. This module
//! carries the recording tests that pin the verbatim delegation: the
//! command (including the verified credential) reaches the repo exactly
//! once, byte-for-byte, and results/errors surface unchanged.

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use async_trait::async_trait;
    use bcs_domain::OwnershipState;
    use bcs_service_api::core::BotAuthorityCoreService;
    use bcs_service_api::port::repo::BotAuthorityRepoPort;
    use bcs_service_api::types::team_manager_sync::{
        TeamManagerOperation, TeamManagerSync, TeamSyncReceipt, VerifiedTeamManagerService,
    };
    use bcs_service_api::types::{AuditActor, BotAccessRelation, BotManagerList, ManagerMutation};
    use bcs_service_api::{ServiceError, ServiceResult};

    /// Recording double: proves the Core forwards one sync command to the
    /// repo port verbatim (the full `TeamManagerSync` — credential, teams,
    /// members, key — nothing dropped, no local shortcut, no extra
    /// validation reads first) and surfaces results/errors unchanged.
    struct RecordingRepo {
        sync_calls: std::sync::Mutex<Vec<TeamManagerSync>>,
        extra_calls: std::sync::Mutex<usize>,
        sync_result: std::sync::Mutex<Result<TeamSyncReceipt, ServiceError>>,
    }

    impl Default for RecordingRepo {
        fn default() -> Self {
            Self {
                sync_calls: std::sync::Mutex::new(Vec::new()),
                extra_calls: std::sync::Mutex::new(0),
                sync_result: std::sync::Mutex::new(Ok(sample_receipt())),
            }
        }
    }

    fn verified_service() -> VerifiedTeamManagerService {
        VerifiedTeamManagerService {
            service_id: "team-sync-svc".to_string(),
            env: "local".to_string(),
            allowed_bots: None,
            allowed_teams: None,
            allowed_operations: None,
        }
    }

    fn sample_command() -> TeamManagerSync {
        TeamManagerSync {
            service: verified_service(),
            bot_id: "bot-a".to_string(),
            team_id: "team-old".to_string(),
            operation: TeamManagerOperation::Sync,
            manager_user_ids: vec!["m1".to_string(), "m2".to_string()],
            idempotency_key: "key-1".to_string(),
        }
    }

    fn sample_receipt() -> TeamSyncReceipt {
        TeamSyncReceipt {
            operation_id: "op-1".to_string(),
            bot_id: "bot-a".to_string(),
            team_id: "team-old".to_string(),
            operation: TeamManagerOperation::Sync,
            granted_count: 2,
            revoked_count: 0,
        }
    }

    fn clone_authority_error(err: &ServiceError) -> ServiceError {
        match err {
            ServiceError::Authority(
                bcs_service_api::types::error::AuthorityError::Conflict(reason),
            ) => ServiceError::Authority(
                bcs_service_api::types::error::AuthorityError::Conflict(reason.clone()),
            ),
            other => panic!("unsupported test error shape: {:?}", other),
        }
    }

    #[async_trait]
    impl BotAuthorityRepoPort for RecordingRepo {
        async fn ownership(&self, _bot_id: &str) -> ServiceResult<OwnershipState> {
            // A pre-validation read would land here FIRST and betray the
            // Core composing a racy duplicate of the in-transaction guards.
            *self.extra_calls.lock().unwrap() += 1;
            unreachable!("the sync delegation composes no ownership pre-read")
        }

        async fn role(
            &self,
            _user_id: &str,
            _bot_id: &str,
        ) -> ServiceResult<Option<BotAccessRelation>> {
            *self.extra_calls.lock().unwrap() += 1;
            unreachable!("the sync delegation composes no role pre-read")
        }

        async fn roles_for(
            &self,
            _pairs: &[(String, String)],
        ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
            *self.extra_calls.lock().unwrap() += 1;
            unreachable!("the sync delegation composes no roles_for pre-read")
        }

        async fn mutate_manager(
            &self,
            _actor: AuditActor,
            _bot_id: &str,
            _mutation: ManagerMutation,
        ) -> ServiceResult<bcs_service_api::types::ManagerMutationResult> {
            *self.extra_calls.lock().unwrap() += 1;
            unreachable!("the sync delegation never performs direct mutations")
        }

        async fn list_managers(
            &self,
            _bot_id: &str,
            _offset: u64,
            _limit: u64,
        ) -> ServiceResult<BotManagerList> {
            *self.extra_calls.lock().unwrap() += 1;
            unreachable!("the sync delegation never lists managers")
        }

        async fn sync_team(
            &self,
            command: TeamManagerSync,
        ) -> ServiceResult<TeamSyncReceipt> {
            self.sync_calls.lock().unwrap().push(command);
            let result = self.sync_result.lock().unwrap();
            match result.as_ref() {
                Ok(value) => Ok(value.clone()),
                Err(err) => Err(clone_authority_error(err)),
            }
        }

        async fn create_transfer(
            &self,
            _command: bcs_service_api::types::ownership_transfer::CreateOwnershipTransfer,
        ) -> ServiceResult<bcs_service_api::types::ownership_transfer::CreateTransferResult> {
            *self.extra_calls.lock().unwrap() += 1;
            unreachable!("the sync delegation never creates transfers")
        }

        async fn decide_transfer(
            &self,
            _actor_user_id: &str,
            _transfer_id: &str,
            _action: bcs_domain::TransferAction,
        ) -> ServiceResult<bcs_service_api::types::ownership_transfer::CommittedTransferOutcome> {
            *self.extra_calls.lock().unwrap() += 1;
            unreachable!("the sync delegation never decides transfers")
        }

        async fn get_transfer(
            &self,
            _viewer_user_id: &str,
            _transfer_id: &str,
        ) -> ServiceResult<bcs_service_api::types::ownership_transfer::OwnershipTransfer> {
            *self.extra_calls.lock().unwrap() += 1;
            unreachable!("the sync delegation never reads transfers")
        }

        async fn list_transfers(
            &self,
            _query: bcs_service_api::types::ownership_transfer::ListOwnershipTransfers,
        ) -> ServiceResult<bcs_service_api::types::ownership_transfer::OwnershipTransferPage> {
            *self.extra_calls.lock().unwrap() += 1;
            unreachable!("the sync delegation never lists transfers")
        }
    }

    #[tokio::test]
    async fn core_forwards_team_sync_verbatim_to_the_repo() {
        let repo = Arc::new(RecordingRepo::default());
        *repo.sync_result.lock().unwrap() = Ok(sample_receipt());
        let core =
            super::super::reads::BotAuthorityCoreServiceImpl::new(repo.clone());

        let command = sample_command();
        let receipt = core.sync_team(command.clone()).await.unwrap();
        assert_eq!(receipt, sample_receipt());
        assert_eq!(
            repo.sync_calls.lock().unwrap().len(),
            1,
            "exactly one delegated call — the Core adds no extra validation reads"
        );
        assert_eq!(repo.sync_calls.lock().unwrap()[0], command);
        assert_eq!(*repo.extra_calls.lock().unwrap(), 0);

        // Errors surface unchanged (never flattened into denies).
        *repo.sync_result.lock().unwrap() = Err(ServiceError::Authority(
            bcs_service_api::types::error::AuthorityError::Conflict(
                "idempotency key reused with a different payload".into(),
            ),
        ));
        assert!(core.sync_team(sample_command()).await.is_err());

        // A move command forwards with its move shape intact.
        *repo.sync_result.lock().unwrap() = Ok(TeamSyncReceipt {
            operation_id: "op-2".to_string(),
            bot_id: "bot-a".to_string(),
            team_id: "team-old".to_string(),
            operation: TeamManagerOperation::Move {
                new_team_id: "team-new".to_string(),
            },
            granted_count: 1,
            revoked_count: 1,
        });
        let mut move_command = sample_command();
        move_command.idempotency_key = "key-move".to_string();
        move_command.operation = TeamManagerOperation::Move {
            new_team_id: "team-new".to_string(),
        };
        let moved = core.sync_team(move_command.clone()).await.unwrap();
        assert_eq!(
            moved.operation,
            TeamManagerOperation::Move {
                new_team_id: "team-new".to_string()
            }
        );
        assert_eq!(repo.sync_calls.lock().unwrap().last(), Some(&move_command));
    }
}