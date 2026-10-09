//! Task 14 facade unit suite: `OwnershipTransferServiceImpl` over a
//! scripted recording Core — the HumanOnly gating, the
//! shape-validation precedence, the typed-conflict fixed-code mapping,
//! and the committed-outcome mapping (the Step-3 table, including the
//! persisted-`terminal_reason` re-check of the Invalidated branch and
//! the OT12 same-code promise). The persisted-facts RED list
//! (invalidate-then-409, 不落单, concealment, history invariance, the
//! read-only expiry projection) runs against the REAL store in
//! `ownership_transfer_lane.rs`.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_domain::{BotAccessRelation, TerminalReason, TransferStatus};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, BotAuthorityHook,
    BotOwnership, BotOwnershipTransferReceipt, CreateBotOwnershipTransfer,
    DecideBotOwnershipTransfer, GetBotOwnership, GetBotOwnershipTransfer,
    ListBotOwnershipTransfers, OwnershipTransferService, TransferListDirection,
};
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::types::error::{AuthorityError, ServiceError, TransferConflict};
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, CreateTransferResult, OwnershipTransfer, OwnershipTransferPage,
};
use bcs_service_api::types::{
    AuditActor, BotManagerList, CreateOwnershipTransfer, ListOwnershipTransfers, ManagerMutation,
    ManagerMutationResult, OwnershipState, TeamManagerSync, TeamSyncReceipt, TransferAction,
};
use bcs_service_api::ServiceResult;

use bcs_app_bot::OwnershipTransferServiceImpl;

fn ownership_record(id: u8) -> OwnershipTransfer {
    OwnershipTransfer {
        transfer_id: format!("transfer-{id}"),
        env: "local".to_string(),
        bot_id: "bot-a".to_string(),
        from_user_id: "user-a".to_string(),
        to_user_id: "user-b".to_string(),
        expected_owner_version: 1,
        client_request_id: format!("key-{id}"),
        status: TransferStatus::Pending,
        expires_at: 1_800_000_000_000,
        decision_actor: None,
        decided_at: None,
        result_owner_version: None,
        terminal_reason: None,
        bot_name_snapshot: "Bot A".to_string(),
        gmt_create: 1,
        gmt_modified: 1,
    }
}

/// Create-result box the Core answers `create_transfer` with.
type CreateAnswer = Box<dyn FnMut() -> ServiceResult<CreateTransferResult> + Send>;
/// Decide-answer box the Core answers `decide_transfer` with.
type DecideAnswer = Box<dyn FnMut() -> ServiceResult<CommittedTransferOutcome> + Send>;
/// Get-answer box the Core answers `get_transfer` with.
type GetAnswer = Box<dyn FnMut() -> ServiceResult<OwnershipTransfer> + Send>;

/// Recording Core: captures every transfer call and answers from a
/// swappable script; every unrelated Core question is a hard failure
/// (this facade never calls it).
#[derive(Default)]
struct RecordingCore {
    creates: Mutex<Vec<CreateOwnershipTransfer>>,
    decides: Mutex<Vec<(String, String, TransferAction)>>,
    gets: Mutex<Vec<(String, String)>>,
    ownerships: Mutex<Vec<String>>,
    lists: Mutex<Vec<ListOwnershipTransfers>>,
    ownership_error: Mutex<Option<ServiceError>>,
    create_answer: Mutex<Option<CreateAnswer>>,
    decide_answer: Mutex<Option<DecideAnswer>>,
    get_answer: Mutex<Option<GetAnswer>>,
}

impl RecordingCore {
    fn set_create_answer(&self, answer: CreateAnswer) {
        *self.create_answer.lock().expect("create answer lock") = Some(answer);
    }

    fn set_decide_answer(&self, answer: DecideAnswer) {
        *self.decide_answer.lock().expect("decide answer lock") = Some(answer);
    }

    fn set_get_answer(&self, answer: GetAnswer) {
        *self.get_answer.lock().expect("get answer lock") = Some(answer);
    }

    fn set_owner_error(&self, error: ServiceError) {
        *self.ownership_error.lock().expect("ownership error lock") = Some(error);
    }
}

#[async_trait]
impl BotAuthorityCoreService for RecordingCore {
    async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState> {
        self.ownerships
            .lock()
            .expect("ownerships lock")
            .push(bot_id.to_string());
        if let Some(error) = self
            .ownership_error
            .lock()
            .expect("ownership error lock")
            .take()
        {
            return Err(error);
        }
        Ok(OwnershipState {
            owner_user_id: "user-a".to_string(),
            ownership_version: 3,
        })
    }

    async fn role(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        unreachable!("role questions go through the hook")
    }

    async fn roles_for(
        &self,
        _pairs: &[(String, String)],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        unreachable!("the ownership lane never batch-reads roles")
    }

    async fn mutate_manager(
        &self,
        _actor: AuditActor,
        _bot_id: &str,
        _mutation: ManagerMutation,
    ) -> ServiceResult<ManagerMutationResult> {
        unreachable!("the ownership lane never mutates managers")
    }

    async fn list_managers(
        &self,
        _bot_id: &str,
        _offset: u64,
        _limit: u64,
    ) -> ServiceResult<BotManagerList> {
        unreachable!("the ownership lane never lists managers")
    }

    async fn sync_team(&self, _command: TeamManagerSync) -> ServiceResult<TeamSyncReceipt> {
        unreachable!("the ownership lane never runs team syncs")
    }

    async fn create_transfer(
        &self,
        command: CreateOwnershipTransfer,
    ) -> ServiceResult<CreateTransferResult> {
        self.creates.lock().expect("creates lock").push(command);
        match self.create_answer.lock().expect("create answer lock").as_mut() {
            Some(answer) => answer(),
            None => Ok(CreateTransferResult {
                receipt: ownership_record(1),
                created: true,
            }),
        }
    }

    async fn decide_transfer(
        &self,
        actor_user_id: &str,
        transfer_id: &str,
        action: TransferAction,
    ) -> ServiceResult<CommittedTransferOutcome> {
        self.decides
            .lock()
            .expect("decides lock")
            .push((
                actor_user_id.to_string(),
                transfer_id.to_string(),
                action,
            ));
        match self.decide_answer.lock().expect("decide answer lock").as_mut() {
            Some(answer) => answer(),
            None => unreachable!("decide tests always script the committed outcome"),
        }
    }

    async fn get_transfer(
        &self,
        viewer_user_id: &str,
        transfer_id: &str,
    ) -> ServiceResult<OwnershipTransfer> {
        self.gets.lock().expect("gets lock").push((
            viewer_user_id.to_string(),
            transfer_id.to_string(),
        ));
        match self.get_answer.lock().expect("get answer lock").as_mut() {
            Some(answer) => answer(),
            None => Ok(ownership_record(1)),
        }
    }

    async fn list_transfers(
        &self,
        query: ListOwnershipTransfers,
    ) -> ServiceResult<OwnershipTransferPage> {
        self.lists.lock().expect("lists lock").push(query);
        Ok(OwnershipTransferPage {
            items: vec![ownership_record(1)],
            total: 1,
        })
    }
}

/// Answer the hook's questions from a fixed map: `true` owners pass,
/// anyone else gets the typed Forbidden branch (the real hook's
/// `require_owner` semantics, Task 3).
struct FixedHook {
    manage: BTreeMap<String, bool>,
    owner: BTreeMap<String, bool>,
}

#[async_trait]
impl BotAuthorityHook for FixedHook {
    async fn can_manage(&self, user_id: &str, _bot_id: &str) -> ServiceResult<bool> {
        Ok(self.manage.get(user_id).copied().unwrap_or(false))
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        if self.owner.get(user_id).copied().unwrap_or(false) {
            Ok(())
        } else {
            Err(ServiceError::Authority(AuthorityError::Forbidden(
                format!("user '{user_id}' is not the owner of bot '{bot_id}'"),
            )))
        }
    }
}

fn human_caller(id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: None,
        user: Some(AuthenticatedUserIdentity {
            id: id.to_string(),
            username: id.to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn bot_only_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".to_string()),
        user: None,
        bot: Some(bcs_service_api::application::v1::AuthenticatedBotIdentity {
            bot_uuid: "bot-self".to_string(),
            owner_id: "user-a".to_string(),
            app_id: 1,
            agent_code: String::new(),
        }),
        app: None,
        access_key: None,
    }
}

fn access_key_only_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: None,
        user: None,
        bot: None,
        app: None,
        access_key: Some(bcs_service_api::application::v1::AuthenticatedAccessKeyIdentity {
            access_key: "ak-1".to_string(),
            expire_at: time::OffsetDateTime::from_unix_timestamp(1_900_000_000)
                .expect("future timestamp"),
        }),
    }
}

fn create_command(caller: &str) -> CreateBotOwnershipTransfer {
    CreateBotOwnershipTransfer {
        caller: human_caller(caller),
        bot_id: "bot-a".to_string(),
        to_user_id: "user-b".to_string(),
        expected_owner_version: 1,
        client_request_id: "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1".to_string(),
    }
}

fn decide_command(caller: &str) -> DecideBotOwnershipTransfer {
    DecideBotOwnershipTransfer {
        caller: human_caller(caller),
        transfer_id: "transfer-1".to_string(),
    }
}

fn facade(
    manage: &[(&str, bool)],
    owner: &[(&str, bool)],
) -> (Arc<RecordingCore>, OwnershipTransferServiceImpl) {
    let core = Arc::new(RecordingCore::default());
    let hook: Arc<dyn BotAuthorityHook> = Arc::new(FixedHook {
        manage: manage
            .iter()
            .map(|(user, allowed)| ((*user).to_string(), *allowed))
            .collect(),
        owner: owner
            .iter()
            .map(|(user, allowed)| ((*user).to_string(), *allowed))
            .collect(),
    });
    let impl_ = OwnershipTransferServiceImpl::new(core.clone(), hook);
    (core, impl_)
}

fn owner_facade() -> (Arc<RecordingCore>, OwnershipTransferServiceImpl) {
    facade(&[("user-a", true)], &[("user-a", true)])
}

#[tokio::test]
async fn non_human_principals_are_rejected_on_every_use_case() {
    let (_, service) = owner_facade();
    let errors = [
        service
            .get_ownership(GetBotOwnership {
                caller: bot_only_caller(),
                bot_id: "bot-a".to_string(),
            })
            .await
            .expect_err("Bot-only caller rejected"),
        service
            .create_transfer(CreateBotOwnershipTransfer {
                caller: bot_only_caller(),
                bot_id: "bot-a".to_string(),
                to_user_id: "user-b".to_string(),
                expected_owner_version: 1,
                client_request_id: "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1".to_string(),
            })
            .await
            .expect_err("Bot-only caller rejected"),
        service
            .list_transfers(ListBotOwnershipTransfers {
                caller: bot_only_caller(),
                direction: TransferListDirection::Received,
                status: None,
                offset: 0,
                limit: 20,
            })
            .await
            .expect_err("Bot-only caller rejected"),
        service
            .get_transfer(GetBotOwnershipTransfer {
                caller: bot_only_caller(),
                transfer_id: "transfer-1".to_string(),
            })
            .await
            .expect_err("Bot-only caller rejected"),
        service
            .accept_transfer(DecideBotOwnershipTransfer {
                caller: bot_only_caller(),
                transfer_id: "transfer-1".to_string(),
            })
            .await
            .expect_err("Bot-only caller rejected"),
        service
            .reject_transfer(DecideBotOwnershipTransfer {
                caller: access_key_only_caller(),
                transfer_id: "transfer-1".to_string(),
            })
            .await
            .expect_err("AccessKey-only caller rejected"),
    ];
    for error in errors {
        assert!(matches!(error, ApplicationError::Forbidden(_)));
        assert_eq!(error.code(), "forbidden");
    }
}

#[tokio::test]
async fn create_gates_shape_before_ownership_and_the_store() {
    let (core, service) = owner_facade();
    // 转交给自己、零版本和空白身份是纯 400，永不进 store。
    let mut command = create_command("user-a");
    command.to_user_id = "user-a".to_string();
    let err = service
        .create_transfer(command)
        .await
        .expect_err("self transfer");
    assert_eq!(err.code(), "invalid_request");
    let mut command = create_command("user-a");
    command.expected_owner_version = 0;
    let err = service
        .create_transfer(command)
        .await
        .expect_err("zero version");
    assert_eq!(err.code(), "invalid_request");
    let mut command = create_command("user-a");
    command.bot_id = " ".to_string();
    let err = service
        .create_transfer(command)
        .await
        .expect_err("blank bot id");
    assert_eq!(err.code(), "invalid_request");
    let mut command = create_command("user-a");
    command.client_request_id = " ".to_string();
    let err = service
        .create_transfer(command)
        .await
        .expect_err("blank idempotency key");
    assert_eq!(err.code(), "invalid_request");
    assert!(core.creates.lock().expect("creates lock").is_empty());

    // The VALIDATED command forwards to the Core with the VERIFIED user.
    let creation = service
        .create_transfer(create_command("user-a"))
        .await
        .expect("create succeeds");
    assert!(creation.created);
    assert_eq!(creation.receipt.transfer_id, "transfer-1");
    let creates = core.creates.lock().expect("creates lock");
    assert_eq!(creates.len(), 1);
    assert_eq!(creates[0].actor_user_id, "user-a");
    assert_eq!(creates[0].to_user_id, "user-b");
    assert_eq!(creates[0].expected_owner_version, 1);
}

#[tokio::test]
async fn manager_cannot_initiate_a_transfer() {
    // No application-side owner gate exists (spec §10.1: the
    // idempotency branch runs first and never re-requires
    // ownership) — a FRESH create by a non-owner gets the STORE's
    // typed `Forbidden` and this facade maps it to the fixed 403
    // `forbidden` code.
    let (core, service) = owner_facade();
    core.set_create_answer(Box::new(|| {
        Err(ServiceError::Authority(AuthorityError::Forbidden(
            "user 'user-m' is not the owner of bot 'bot-a'".to_string(),
        )))
    }));
    let err = service
        .create_transfer(create_command("user-m"))
        .await
        .expect_err("manager initiation is forbidden");
    // The store's typed Forbidden branch maps through the shared
    // authority mapping (`ForbiddenCode` with the fixed `forbidden`
    // code).
    assert!(matches!(
        err,
        ApplicationError::Forbidden(_) | ApplicationError::ForbiddenCode { .. }
    ));
    assert_eq!(err.code(), "forbidden");
    // The verified command DID reach the Core — the denial is the
    // store's in-transaction predicate, never a facade shortcut.
    assert_eq!(core.creates.lock().expect("creates lock").len(), 1);
}

#[tokio::test]
async fn typed_create_conflicts_map_to_their_fixed_codes() {
    let (core, service) = owner_facade();
    type ErrorCase = Box<dyn Fn() -> ServiceError + Send>;
    let cases: Vec<(ErrorCase, &str)> = vec![
        (
            Box::new(|| {
                ServiceError::Authority(AuthorityError::TransferConflict(
                    TransferConflict::PendingSlot {
                        bot_id: "bot-a".to_string(),
                    },
                ))
            }),
            "ownership_transfer_pending",
        ),
        (
            Box::new(|| {
                ServiceError::Authority(AuthorityError::TransferConflict(
                    TransferConflict::VersionSnapshotStale {
                        bot_id: "bot-a".to_string(),
                        expected_owner_version: 9,
                        current_owner_version: 1,
                    },
                ))
            }),
            "ownership_changed",
        ),
        (
            Box::new(|| {
                ServiceError::Authority(AuthorityError::TransferConflict(
                    TransferConflict::IdempotencyBody {
                        bot_id: "bot-a".to_string(),
                        client_request_id: "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1".to_string(),
                    },
                ))
            }),
            "idempotency_conflict",
        ),
        // The create lane's recipient branch: 400
        // invalid_transfer_recipient (the caller already passed the
        // owner gate).
        (
            Box::new(|| {
                ServiceError::Authority(AuthorityError::InvalidSubject(
                    "recipient not a live human".to_string(),
                ))
            }),
            "invalid_transfer_recipient",
        ),
        (
            Box::new(|| {
                ServiceError::Authority(AuthorityError::OwnershipNotInitialized {
                    bot_id: "bot-a".to_string(),
                    env: "local".to_string(),
                })
            }),
            "ownership_not_initialized",
        ),
        (
            Box::new(|| {
                ServiceError::Authority(AuthorityError::Forbidden(
                    "someone else owns it now".to_string(),
                ))
            }),
            "forbidden",
        ),
        (
            Box::new(|| {
                ServiceError::Authority(AuthorityError::CorruptAuthority {
                    bot_id: "bot-a".to_string(),
                    env: "local".to_string(),
                    detail: "SELECT leaked from bcs_bots".to_string(),
                })
            }),
            "internal_error",
        ),
        (
            Box::new(|| {
                ServiceError::InternalError("SQL fragmentation in the message".to_string())
            }),
            "internal_error",
        ),
    ];
    for (case, want_code) in cases {
        core.set_create_answer(Box::new(move || Err(case())));
        let err = service
            .create_transfer(create_command("user-a"))
            .await
            .expect_err("the scripted branch surfaces");
        assert_eq!(err.code(), want_code);
        if want_code == "internal_error" {
            // 500 branch: the transport contract sanitizes the
            // message; the application keeps it off error codes.
            assert!(matches!(err, ApplicationError::Internal(_)));
        }
    }
}

#[tokio::test]
async fn decide_maps_committed_outcomes_by_the_step3_table() {
    let (core, service) = owner_facade();

    // Receipt → the historical receipt (200 family).
    core.set_decide_answer(Box::new(|| {
        Ok(CommittedTransferOutcome::Receipt(ownership_record(1)))
    }));
    let receipt = service
        .accept_transfer(decide_command("user-b"))
        .await
        .expect("receipt");
    assert_eq!(receipt.transfer_id, "transfer-1");

    // OwnerChanged → 409 ownership_changed (a committed domain
    // result: never an exception and never rolled back here).
    core.set_decide_answer(Box::new(|| Ok(CommittedTransferOutcome::OwnerChanged)));
    let err = service
        .accept_transfer(decide_command("user-b"))
        .await
        .expect_err("owner changed");
    assert!(matches!(err, ApplicationError::Conflict { .. }));
    assert_eq!(err.code(), "ownership_changed");

    // Expired → 409 ownership_transfer_expired.
    core.set_decide_answer(Box::new(|| Ok(CommittedTransferOutcome::Expired)));
    let err = service
        .accept_transfer(decide_command("user-b"))
        .await
        .expect_err("expired");
    assert_eq!(err.code(), "ownership_transfer_expired");

    // Concealment keeps its fixed 404 code.
    core.set_decide_answer(Box::new(|| {
        Err(ServiceError::Authority(
            AuthorityError::OwnershipTransferNotFound {
                transfer_id: "transfer-1".to_string(),
            },
        ))
    }));
    let err = service
        .accept_transfer(decide_command("user-c"))
        .await
        .expect_err("concealed");
    assert!(matches!(err, ApplicationError::NotFound { .. }));
    assert_eq!(err.code(), "ownership_transfer_not_found");

    // A wrong-party-role caller keeps the 403 branch.
    core.set_decide_answer(Box::new(|| {
        Err(ServiceError::Authority(AuthorityError::Forbidden(
            "the initiator cannot accept".to_string(),
        )))
    }));
    let err = service
        .accept_transfer(decide_command("user-a"))
        .await
        .expect_err("wrong party role");
    assert_eq!(err.code(), "forbidden");

    // The typed incompatible-terminal conflict → 409
    // ownership_transfer_not_pending.
    core.set_decide_answer(Box::new(|| {
        Err(ServiceError::Authority(AuthorityError::TransferConflict(
            TransferConflict::NotPending {
                transfer_id: "transfer-1".to_string(),
                status: "accepted".to_string(),
            },
        )))
    }));
    let err = service
        .reject_transfer(decide_command("user-b"))
        .await
        .expect_err("incompatible terminal action");
    assert!(matches!(err, ApplicationError::Conflict { .. }));
    assert_eq!(err.code(), "ownership_transfer_not_pending");
}

#[tokio::test]
async fn invalidated_recheck_uses_the_persisted_terminal_reason() {
    // Ok(Invalidated) + persisted terminal_reason owner_changed →
    // the SAME code as the invalidation itself (OT12: response-loss
    // retries never degrade to not_pending).
    let (core, service) = owner_facade();
    core.set_decide_answer(Box::new(|| Ok(CommittedTransferOutcome::Invalidated)));
    let mut invalidated = ownership_record(2);
    invalidated.status = TransferStatus::Invalidated;
    invalidated.terminal_reason = Some(TerminalReason::OwnerChanged);
    let row = invalidated.clone();
    core.set_get_answer(Box::new(move || Ok(row.clone())));
    let err = service
        .accept_transfer(decide_command("user-b"))
        .await
        .expect_err("owner_changed retry keeps its code");
    assert_eq!(err.code(), "ownership_changed");
    // The re-check read AS the verified party (never a body value).
    let gets = core.gets.lock().expect("gets lock");
    assert_eq!(
        gets.last().cloned().unwrap(),
        ("user-b".into(), "transfer-1".into())
    );
    drop(gets);

    // Every other invalidation reason → ownership_transfer_not_pending.
    let (core, service) = owner_facade();
    core.set_decide_answer(Box::new(|| Ok(CommittedTransferOutcome::Invalidated)));
    let mut deleted = ownership_record(3);
    deleted.status = TransferStatus::Invalidated;
    deleted.terminal_reason = Some(TerminalReason::BotDeleted);
    let row = deleted.clone();
    core.set_get_answer(Box::new(move || Ok(row.clone())));
    let err = service
        .accept_transfer(decide_command("user-b"))
        .await
        .expect_err("bot-deleted invalidation");
    assert_eq!(err.code(), "ownership_transfer_not_pending");

    // A terminal row without a recorded reason is the same
    // not-pending family — never upgraded to ownership_changed.
    core.set_decide_answer(Box::new(|| Ok(CommittedTransferOutcome::Invalidated)));
    let mut plain = ownership_record(4);
    plain.status = TransferStatus::Invalidated;
    plain.terminal_reason = None;
    core.set_get_answer(Box::new(move || Ok(plain.clone())));
    let err = service
        .reject_transfer(decide_command("user-b"))
        .await
        .expect_err("reasonless invalidation");
    assert_eq!(err.code(), "ownership_transfer_not_pending");
}

#[tokio::test]
async fn decided_actions_forward_the_verified_user_and_action() {
    let (core, service) = owner_facade();
    core.set_decide_answer(Box::new(|| {
        Ok(CommittedTransferOutcome::Receipt(ownership_record(1)))
    }));
    service
        .reject_transfer(decide_command("user-b"))
        .await
        .expect("reject");
    service
        .cancel_transfer(decide_command("user-a"))
        .await
        .expect("cancel");
    assert_eq!(
        *core.decides.lock().expect("decides lock"),
        vec![
            ("user-b".to_string(), "transfer-1".to_string(), TransferAction::Reject),
            ("user-a".to_string(), "transfer-1".to_string(), TransferAction::Cancel),
        ]
    );
}

#[tokio::test]
async fn get_ownership_reads_through_the_hook_and_core() {
    let (core, service) = owner_facade();
    let ownership = service
        .get_ownership(GetBotOwnership {
            caller: human_caller("user-a"),
            bot_id: "bot-a".to_string(),
        })
        .await
        .expect("ownership read");
    assert_eq!(
        ownership,
        BotOwnership {
            bot_id: "bot-a".to_string(),
            owner_user_id: "user-a".to_string(),
            ownership_version: 3,
        }
    );
    assert_eq!(
        *core.ownerships.lock().expect("ownerships lock"),
        vec!["bot-a".to_string()]
    );

    // A caller with no current role is forbidden before any read.
    let (core, service) = facade(&[], &[]);
    let err = service
        .get_ownership(GetBotOwnership {
            caller: human_caller("user-stranger"),
            bot_id: "bot-a".to_string(),
        })
        .await
        .expect_err("no current role");
    assert_eq!(err.code(), "forbidden");
    assert!(core.ownerships.lock().expect("ownerships lock").is_empty());

    // Corrupt authority fails closed as 500 (never a plain deny and
    // never a leaked detail code other than internal_error).
    let (core, service) = owner_facade();
    core.set_owner_error(ServiceError::Authority(AuthorityError::CorruptAuthority {
        bot_id: "bot-a".to_string(),
        env: "local".to_string(),
        detail: "damaged owner slot".to_string(),
    }));
    let err = service
        .get_ownership(GetBotOwnership {
            caller: human_caller("user-a"),
            bot_id: "bot-a".to_string(),
        })
        .await
        .expect_err("corrupt fails closed");
    assert_eq!(err.code(), "internal_error");
}

#[tokio::test]
async fn list_rejects_out_of_range_windows_and_forwards_the_viewer() {
    let (core, service) = owner_facade();
    for limit in [0u64, 101] {
        let err = service
            .list_transfers(ListBotOwnershipTransfers {
                caller: human_caller("user-a"),
                direction: TransferListDirection::Received,
                status: None,
                offset: 0,
                limit,
            })
            .await
            .expect_err("limit bounded");
        assert_eq!(err.code(), "invalid_request");
    }
    let page = service
        .list_transfers(ListBotOwnershipTransfers {
            caller: human_caller("user-a"),
            direction: TransferListDirection::Sent,
            status: Some(TransferStatus::Pending),
            offset: 4,
            limit: 20,
        })
        .await
        .expect("list forwards");
    assert_eq!(page.total, 1);
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.offset, 4);
    assert_eq!(page.limit, 20);
    let lists = core.lists.lock().expect("lists lock");
    assert_eq!(lists.len(), 1);
    assert_eq!(lists[0].viewer_user_id, "user-a");
    assert_eq!(lists[0].direction, TransferListDirection::Sent);
    assert_eq!(lists[0].status, Some(TransferStatus::Pending));
    assert_eq!(lists[0].offset, 4);
    assert_eq!(lists[0].limit, 20);
}