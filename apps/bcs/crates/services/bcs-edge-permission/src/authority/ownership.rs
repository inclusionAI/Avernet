//! `BotAuthorityCoreService` ownership-transfer surface (plan Task 8, spec
//! §10/§11).
//!
//! The whole transfer contract is owned by the REPO (the one-Bot-transaction
//! create/decide lanes and the party-visible reads): a validation outside
//! those transactions adds no authority, exactly like the manager/team-sync
//! mutation lanes. The Core therefore does NOT pre-read anything and
//! forwards every command verbatim; committed domain results
//! (`OwnerChanged` / `Expired` / `Invalidated`) are results, never errors —
//! the Core must not wrap them into failures or trigger retries of its own.
//! The forwarding bodies live in [`super::reads`] (trait impls cannot be
//! split across blocks); this module pins that verbatim delegation with
//! recording tests, including the no-extra-reads rule for the read lanes.

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::sync::Mutex;

    use async_trait::async_trait;
    use bcs_domain::{BotAccessRelation, OwnershipState, TransferAction, TransferStatus};
    use bcs_service_api::core::BotAuthorityCoreService;
    use bcs_service_api::port::repo::BotAuthorityRepoPort;
    use bcs_service_api::types::error::AuthorityError;
    use bcs_service_api::types::ownership_transfer::{
        CommittedTransferOutcome, CreateOwnershipTransfer, CreateTransferResult,
        ListOwnershipTransfers, OwnershipTransfer, OwnershipTransferPage, TransferListDirection,
    };
    use bcs_service_api::types::{
        AuditActor, BotManagerList, ManagerMutation, ManagerMutationResult,
    };
    use bcs_service_api::{ServiceError, ServiceResult};

    use crate::authority::BotAuthorityCoreServiceImpl;

    /// Recording double: proves the Core forwards the whole transfer
    /// surface to the repo port VERBATIM (actor/bot/key/payload/action
    /// fields and the viewer/direction paging arguments) and surfaces both
    /// committed results and errors unchanged — no shortcut, no pre-read
    /// that could diverge from the repo-owned in-transaction validation,
    /// and no mutation of `CommittedTransferOutcome` values.
    #[derive(Default)]
    struct RecordingRepo {
        create_calls: Mutex<Vec<CreateOwnershipTransfer>>,
        decide_calls: Mutex<Vec<(String, String, TransferAction)>>,
        get_calls: Mutex<Vec<(String, String)>>,
        list_calls: Mutex<Vec<ListOwnershipTransfers>>,
        verdict: Mutex<Verdict>,
    }

    /// Pluggable reply of the double, so each test pins its own branch.
    enum Verdict {
        Create(Result<CreateTransferResult, ServiceError>),
        Decide(Result<CommittedTransferOutcome, ServiceError>),
        Get(Result<OwnershipTransfer, ServiceError>),
        List(Result<OwnershipTransferPage, ServiceError>),
        Unused,
    }

    impl Default for Verdict {
        fn default() -> Self {
            Verdict::Unused
        }
    }

    fn receipt(status: TransferStatus) -> OwnershipTransfer {
        OwnershipTransfer {
            transfer_id: "transfer-1".to_string(),
            env: "local".to_string(),
            bot_id: "bot-a".to_string(),
            from_user_id: "a".to_string(),
            to_user_id: "b".to_string(),
            expected_owner_version: 1,
            client_request_id: "cr-1".to_string(),
            status,
            expires_at: 4_102_444_800_000,
            decision_actor: None,
            decided_at: None,
            result_owner_version: None,
            terminal_reason: None,
            bot_name_snapshot: "bot-a".to_string(),
            gmt_create: 0,
            gmt_modified: 0,
        }
    }

    #[async_trait]
    impl BotAuthorityRepoPort for RecordingRepo {
        async fn ownership(&self, _bot_id: &str) -> ServiceResult<OwnershipState> {
            unimplemented!("the transfer lanes never answer through ownership itself")
        }

        async fn role(
            &self,
            _user_id: &str,
            _bot_id: &str,
        ) -> ServiceResult<Option<BotAccessRelation>> {
            unimplemented!("the transfer lanes never answer through role itself")
        }

        async fn roles_for(
            &self,
            _pairs: &[(String, String)],
        ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
            unimplemented!("the transfer lanes never answer through roles_for itself")
        }

        async fn mutate_manager(
            &self,
            _actor: AuditActor,
            _bot_id: &str,
            _mutation: ManagerMutation,
        ) -> ServiceResult<ManagerMutationResult> {
            unimplemented!("the transfer lanes never mutate managers")
        }

        async fn list_managers(
            &self,
            _bot_id: &str,
            _offset: u64,
            _limit: u64,
        ) -> ServiceResult<BotManagerList> {
            unimplemented!("the transfer lanes never list managers")
        }

        async fn sync_team(
            &self,
            _command: bcs_service_api::types::team_manager_sync::TeamManagerSync,
        ) -> ServiceResult<bcs_service_api::types::team_manager_sync::TeamSyncReceipt> {
            unimplemented!("the transfer lanes never sync teams")
        }

        async fn create_transfer(
            &self,
            command: CreateOwnershipTransfer,
        ) -> ServiceResult<CreateTransferResult> {
            self.create_calls.lock().unwrap().push(command.clone());
            let mut slot = self.verdict.lock().unwrap();
            match std::mem::replace(&mut *slot, Verdict::Unused) {
                Verdict::Create(reply) => reply,
                other => {
                    *slot = other;
                    Err(ServiceError::InternalError(
                        "create_transfer double mismatch".to_string(),
                    ))
                }
            }
        }

        async fn decide_transfer(
            &self,
            actor_user_id: &str,
            transfer_id: &str,
            action: TransferAction,
        ) -> ServiceResult<CommittedTransferOutcome> {
            self.decide_calls.lock().unwrap().push((
                actor_user_id.to_string(),
                transfer_id.to_string(),
                action,
            ));
            let mut slot = self.verdict.lock().unwrap();
            match std::mem::replace(&mut *slot, Verdict::Unused) {
                Verdict::Decide(reply) => reply,
                other => {
                    *slot = other;
                    Err(ServiceError::InternalError(
                        "decide_transfer double mismatch".to_string(),
                    ))
                }
            }
        }

        async fn get_transfer(
            &self,
            viewer_user_id: &str,
            transfer_id: &str,
        ) -> ServiceResult<OwnershipTransfer> {
            self.get_calls
                .lock()
                .unwrap()
                .push((viewer_user_id.to_string(), transfer_id.to_string()));
            let mut slot = self.verdict.lock().unwrap();
            match std::mem::replace(&mut *slot, Verdict::Unused) {
                Verdict::Get(reply) => reply,
                other => {
                    *slot = other;
                    Err(ServiceError::InternalError(
                        "get_transfer double mismatch".to_string(),
                    ))
                }
            }
        }

        async fn list_transfers(
            &self,
            query: ListOwnershipTransfers,
        ) -> ServiceResult<OwnershipTransferPage> {
            self.list_calls.lock().unwrap().push(query);
            let mut slot = self.verdict.lock().unwrap();
            match std::mem::replace(&mut *slot, Verdict::Unused) {
                Verdict::List(reply) => reply,
                other => {
                    *slot = other;
                    Err(ServiceError::InternalError(
                        "list_transfers double mismatch".to_string(),
                    ))
                }
            }
        }
    }

    /// Assemble the production Core over the recording double. Each test
    /// configures exactly one verdict and calls exactly one Core method, so
    /// the one-shot verdict swap keeps the assertion surface exact.
    fn core_with(verdict: Verdict) -> (BotAuthorityCoreServiceImpl, Arc<RecordingRepo>) {
        let repo = Arc::new(RecordingRepo {
            create_calls: Mutex::new(Vec::new()),
            decide_calls: Mutex::new(Vec::new()),
            get_calls: Mutex::new(Vec::new()),
            list_calls: Mutex::new(Vec::new()),
            verdict: Mutex::new(verdict),
        });
        let repo_clone = repo.clone();
        (BotAuthorityCoreServiceImpl::new(repo), repo_clone)
    }

    fn create_command() -> CreateOwnershipTransfer {
        CreateOwnershipTransfer {
            actor_user_id: "a".to_string(),
            bot_id: "bot-a".to_string(),
            to_user_id: "b".to_string(),
            expected_owner_version: 3,
            client_request_id: "cr-1".to_string(),
        }
    }

    /// The create command reaches the repo VERBATIM and the result comes
    /// back unchanged (both the created receipt and the `created` flag).
    #[tokio::test]
    async fn core_forwards_create_transfer_verbatim() {
        let result = CreateTransferResult {
            receipt: receipt(TransferStatus::Pending),
            created: true,
        };
        let (core, repo) = core_with(Verdict::Create(Ok(result.clone())));
        let outcome = core.create_transfer(create_command()).await.unwrap();
        assert_eq!(outcome, result);
        assert_eq!(repo.create_calls.lock().unwrap().as_slice(), &[create_command()]);
    }

    /// The decide action reaches the repo VERBATIM; committed domain results
    /// and errors surface unchanged — never wrapped by the Core.
    #[tokio::test]
    async fn core_forwards_decide_transfer_verbatim() {
        let (core, repo) = core_with(Verdict::Decide(Ok(CommittedTransferOutcome::OwnerChanged)));
        let outcome = core
            .decide_transfer("b", "transfer-1", TransferAction::Accept)
            .await
            .unwrap();
        assert_eq!(outcome, CommittedTransferOutcome::OwnerChanged);
        assert_eq!(
            repo.decide_calls.lock().unwrap().as_slice(),
            &[(
                "b".to_string(),
                "transfer-1".to_string(),
                TransferAction::Accept,
            )]
        );

        // The error branch (e.g. an incompatible terminal action) passes
        // through the same verbatim path.
        let (core, repo) = core_with(Verdict::Decide(Err(ServiceError::Authority(
            AuthorityError::Conflict("ownership_transfer_not_pending".to_string()),
        ))));
        let outcome = core
            .decide_transfer("a", "transfer-1", TransferAction::Cancel)
            .await;
        assert!(
            matches!(
                &outcome,
                Err(ServiceError::Authority(AuthorityError::Conflict(_)))
            ),
            "the Core surfaces the repo branch unchanged, got {outcome:?}"
        );
        assert_eq!(
            repo.decide_calls.lock().unwrap().as_slice(),
            &[(
                "a".to_string(),
                "transfer-1".to_string(),
                TransferAction::Cancel,
            )]
        );
    }

    /// The read lanes forward the viewer verbatim (no identity substitution,
    /// no hidden re-read) and surface both the receipt and the concealment
    /// branch unchanged.
    #[tokio::test]
    async fn core_forwards_transfer_reads_verbatim() {
        let expected = receipt(TransferStatus::Accepted);
        let (core, repo) = core_with(Verdict::Get(Ok(expected.clone())));
        let outcome = core.get_transfer("b", "transfer-1").await.unwrap();
        assert_eq!(outcome, expected);
        assert_eq!(
            repo.get_calls.lock().unwrap().as_slice(),
            &[("b".to_string(), "transfer-1".to_string())]
        );

        let (core, repo) = core_with(Verdict::Get(Err(ServiceError::Authority(
            AuthorityError::OwnershipTransferNotFound {
                transfer_id: "transfer-x".to_string(),
            },
        ))));
        assert!(
            matches!(
                core.get_transfer("stranger", "transfer-x").await,
                Err(ServiceError::Authority(
                    AuthorityError::OwnershipTransferNotFound { .. }
                ))
            ),
            "the concealment branch passes through the Core unchanged"
        );
        assert_eq!(
            repo.get_calls.lock().unwrap().as_slice(),
            &[("stranger".to_string(), "transfer-x".to_string())]
        );
    }

    /// The listing forwards every filter/paging field verbatim and the
    /// page (items + total) comes back untouched.
    #[tokio::test]
    async fn core_forwards_list_transfers_verbatim() {
        let query = ListOwnershipTransfers {
            viewer_user_id: "b".to_string(),
            direction: TransferListDirection::Received,
            status: Some(TransferStatus::Pending),
            offset: 20,
            limit: 100,
        };
        let page = OwnershipTransferPage {
            items: vec![receipt(TransferStatus::Pending)],
            total: 1,
        };
        let (core, repo) = core_with(Verdict::List(Ok(page.clone())));
        let outcome = core.list_transfers(query.clone()).await.unwrap();
        assert_eq!(outcome, page);
        assert_eq!(repo.list_calls.lock().unwrap().as_slice(), &[query]);
    }
}