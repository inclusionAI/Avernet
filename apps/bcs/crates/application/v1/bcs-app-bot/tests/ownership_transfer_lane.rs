//! Task 14 behavioral lane suite: the REAL `OwnershipTransferServiceImpl`
//! over the REAL authority Core and the REAL memory-twin transfer store —
//! the RED list that must observe PERSISTED facts, not scripted branches:
//! the typed conflict mapping, the 不落单 stale-version proof, the
//! committed `invalidated(owner_changed)` accept with its OT12 same-code
//! retry, the read-only expiry projection, the historical accepted-receipt
//! invariance, and the concealment 404 (spec §10/§11, plan Task 14).

use std::sync::Arc;

use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, BotAuthorityHook,
    BotOwnership, BotOwnershipTransferPage, CreateBotOwnershipTransfer, DecideBotOwnershipTransfer,
    GetBotOwnership, GetBotOwnershipTransfer, ListBotOwnershipTransfers, OwnershipTransferService,
    TransferListDirection,
};
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::types::TransferStatus;

use bcs_app_bot::{BotAuthorityHookImpl, OwnershipTransferServiceImpl};
use bcs_edge_permission::authority::BotAuthorityCoreServiceImpl;

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

fn create_command(caller: &str, bot: &str, to: &str, version: u64) -> CreateBotOwnershipTransfer {
    CreateBotOwnershipTransfer {
        caller: human_caller(caller),
        bot_id: bot.to_string(),
        to_user_id: to.to_string(),
        expected_owner_version: version,
        client_request_id: uuid::Uuid::new_v4().to_string(),
    }
}

fn decide_command(caller: &str, transfer_id: &str) -> DecideBotOwnershipTransfer {
    DecideBotOwnershipTransfer {
        caller: human_caller(caller),
        transfer_id: transfer_id.to_string(),
    }
}

struct Lane {
    service: OwnershipTransferServiceImpl,
    repo: Arc<MemoryBotRepo>,
    _dir: tempfile::TempDir,
}

impl Lane {
    fn new() -> Self {
        let dir = tempfile::tempdir().expect("temp dir");
        let repo = Arc::new(MemoryBotRepo::with_base_dir(dir.path().to_path_buf()));
        let core = Arc::new(BotAuthorityCoreServiceImpl::new(repo.clone()));
        let hook: Arc<dyn BotAuthorityHook> = Arc::new(BotAuthorityHookImpl::new(core.clone()));
        let service = OwnershipTransferServiceImpl::new(core, hook);
        Self {
            service,
            repo,
            _dir: dir,
        }
    }

    async fn seed_owned(&self, bot: &str, owner: &str) {
        self.repo
            .seed_authority_owned(bot, owner)
            .await
            .expect("seed owner edge");
    }

    async fn seed_human(&self, user: &str) {
        self.repo
            .ensure_human_actor(user, user)
            .await
            .expect("seed human actor");
    }

    async fn seed_manager(&self, bot: &str, user: &str) {
        self.repo
            .seed_authority_manager_source(bot, user, "direct", "manual")
            .await
            .expect("seed manager source");
    }

    async fn stored_transfers(&self, bot: &str) -> Vec<(String, String, Option<String>)> {
        self.repo
            .authority_transfer_statuses(bot)
            .await
            .expect("stored transfer rows")
    }

    async fn created_transfer_id(&self, command: CreateBotOwnershipTransfer) -> String {
        let creation = self
            .service
            .create_transfer(command)
            .await
            .expect("create succeeds");
        assert!(creation.created);
        creation.receipt.transfer_id
    }
}

fn error_code(error: &ApplicationError) -> String {
    error.code().to_string()
}

/// A future-deadline text in the store's DB-time shape (memory twin).
const FUTURE_DEADLINE: &str = "2099-01-01 00:00:00";
/// A clearly lapsed deadline.
const LAPSED_DEADLINE: &str = "2000-01-01 00:00:00";

#[tokio::test]
async fn create_and_key_replay_keep_the_historical_receipt() {
    let lane = Lane::new();
    lane.seed_owned("bot-a", "user-a").await;
    lane.seed_human("user-a").await;
    lane.seed_human("user-b").await;

    let key = uuid::Uuid::new_v4().to_string();
    let mut command = create_command("user-a", "bot-a", "user-b", 1);
    command.client_request_id = key.clone();
    let first = lane
        .service
        .create_transfer(command)
        .await
        .expect("the owner creates");
    assert!(first.created);
    assert_eq!(first.receipt.status, TransferStatus::Pending);
    assert_eq!(first.receipt.from_user_id, "user-a");
    assert_eq!(first.receipt.to_user_id, "user-b");
    assert_eq!(first.receipt.bot_name_snapshot, "bot-a");

    // Same key, same payload: the ORIGINAL receipt, no second pending.
    let mut replay_command = create_command("user-a", "bot-a", "user-b", 1);
    replay_command.client_request_id = key;
    let replay = lane
        .service
        .create_transfer(replay_command)
        .await
        .expect("the same-key replay returns the receipt");
    assert!(!replay.created);
    assert_eq!(replay.receipt.transfer_id, first.receipt.transfer_id);
    assert_eq!(replay.receipt, first.receipt);

    // The stored row shape: exactly one pending.
    assert_eq!(lane.stored_transfers("bot-a").await.len(), 1);
    let (_, status, reason) = lane.stored_transfers("bot-a").await[0].clone();
    assert_eq!(status, "pending");
    assert_eq!(reason, None);
}

#[tokio::test]
async fn replay_after_ownership_moved_on_does_not_require_current_ownership() {
    // spec §10.1: idempotent replay of a committed receipt works for the
    // ORIGINAL initiator without re-requiring the owner role.
    let lane = Lane::new();
    lane.seed_owned("bot-a", "user-a").await;
    lane.seed_human("user-b").await;
    let key = uuid::Uuid::new_v4().to_string();
    let mut command = create_command("user-a", "bot-a", "user-b", 1);
    command.client_request_id = key;
    let first = lane
        .service
        .create_transfer(command.clone())
        .await
        .expect("create");
    assert!(first.created);

    lane.service
        .accept_transfer(decide_command("user-b", &first.receipt.transfer_id))
        .await
        .expect("b accepts — owner is now b");

    // The FORMER owner replays the same key: the historical accepted
    // receipt, created=false.
    let replay = lane
        .service
        .create_transfer(command)
        .await
        .expect("historical replay never re-requires ownership");
    assert!(!replay.created);
    assert_eq!(replay.receipt.status, TransferStatus::Accepted);
    assert_eq!(replay.receipt.result_owner_version, Some(2));
}

#[tokio::test]
async fn manager_cannot_initiate_and_the_store_in_lock_denies() {
    let lane = Lane::new();
    lane.seed_owned("bot-a", "user-a").await;
    lane.seed_human("user-b").await;
    lane.seed_human("user-m").await;
    lane.seed_manager("bot-a", "user-m").await;

    let err = lane
        .service
        .create_transfer(create_command("user-m", "bot-a", "user-b", 1))
        .await
        .expect_err("the manager never initiates");
    assert_eq!(error_code(&err), "forbidden");
    assert!(lane.stored_transfers("bot-a").await.is_empty());
}

#[tokio::test]
async fn stale_version_conflicts_without_persisting_any_row() {
    let lane = Lane::new();
    lane.seed_owned("bot-a", "user-a").await;
    lane.seed_human("user-b").await;

    let err = lane
        .service
        .create_transfer(create_command("user-a", "bot-a", "user-b", 99))
        .await
        .expect_err("the stale snapshot is a conflict");
    assert_eq!(error_code(&err), "ownership_changed");
    assert!(matches!(err, ApplicationError::Conflict { .. }));
    // 查库断言: NOTHING was persisted — no row, no cleanup, no slot.
    assert!(lane.stored_transfers("bot-a").await.is_empty());
}

#[tokio::test]
async fn mismatch_accept_commits_invalidated_owner_changed_with_the_same_code() {
    let lane = Lane::new();
    lane.seed_owned("bot-a", "user-a").await;
    lane.seed_human("user-b").await;
    // A pending whose version snapshot no longer matches the stored
    // authority (v1): the accept must commit invalidated(owner_changed)
    // FIRST and then map 409/ownership_changed — never a rollback.
    let transfer_id = lane
        .repo
        .seed_authority_pending_transfer_custom(
            "bot-a",
            "user-a",
            "user-b",
            9,
            FUTURE_DEADLINE,
            "",
        )
        .await
        .expect("seed the mismatched pending");

    let err = lane
        .service
        .accept_transfer(decide_command("user-b", &transfer_id))
        .await
        .expect_err("mismatch accept maps the committed invalidation");
    assert_eq!(error_code(&err), "ownership_changed");
    assert!(matches!(err, ApplicationError::Conflict { .. }));
    // 查库断言: the persisted transfer row — invalidated with the
    // owner_changed terminal reason (OT12's re-read source).
    let rows = lane.stored_transfers("bot-a").await;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].1, "invalidated");
    assert_eq!(rows[0].2.as_deref(), Some("owner_changed"));

    // The same receiver's response-loss RETRY answers with the SAME
    // code (OT12: never degraded to not_pending). Cancel belongs to the
    // initiator (spec §9), so its retry uses the recorded from-party.
    for (action, caller) in [("accept", "user-b"), ("reject", "user-b"), ("cancel", "user-a")] {
        let err = match action {
            "accept" => {
                lane.service
                    .accept_transfer(decide_command(caller, &transfer_id))
                    .await
            }
            "reject" => {
                lane.service
                    .reject_transfer(decide_command(caller, &transfer_id))
                    .await
            }
            _ => {
                lane.service
                    .cancel_transfer(decide_command(caller, &transfer_id))
                    .await
            }
        }
        .expect_err("the invalidated retry keeps its committed outcome");
        assert_eq!(
            error_code(&err),
            "ownership_changed",
            "{action} retry must keep the OT12 code"
        );
    }
    // The stored row never changed (committed, then immutable).
    let rows = lane.stored_transfers("bot-a").await;
    assert_eq!(rows[0].1, "invalidated");
    assert_eq!(rows[0].2.as_deref(), Some("owner_changed"));
    // Ownership NEVER moved: still the original owner, version 1.
    let ownership = lane
        .service
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
            ownership_version: 1,
        }
    );
}

#[tokio::test]
async fn expired_pending_projects_lapsed_and_recent_ansers_the_conflict() {
    let lane = Lane::new();
    lane.seed_owned("bot-a", "user-a").await;
    lane.seed_human("user-b").await;
    let transfer_id = lane
        .repo
        .seed_authority_pending_transfer_custom(
            "bot-a",
            "user-a",
            "user-b",
            1,
            LAPSED_DEADLINE,
            "",
        )
        .await
        .expect("seed the lapsed pending");

    // The read projects `expired` WITHOUT materializing anything.
    let receipt = lane
        .service
        .get_transfer(GetBotOwnershipTransfer {
            caller: human_caller("user-b"),
            transfer_id: transfer_id.clone(),
        })
        .await
        .expect("party read");
    assert_eq!(receipt.status, TransferStatus::Expired);
    assert_eq!(receipt.decided_by.as_deref(), Some("ownership-deadline"));
    assert_eq!(receipt.terminal_reason, None);
    // 查库断言: the physical row is STILL `pending` — reads never write.
    let rows = lane.stored_transfers("bot-a").await;
    assert_eq!(rows[0].2, None);
    assert_eq!(rows[0].1, "pending");

    // The accept observes the committed `expired` outcome: the fixed
    // ownership_transfer_expired conflict code.
    let err = lane
        .service
        .accept_transfer(decide_command("user-b", &transfer_id))
        .await
        .expect_err("lapsed accept maps 409/ownership_transfer_expired");
    assert_eq!(error_code(&err), "ownership_transfer_expired");
}

#[tokio::test]
async fn accepted_history_replay_never_rewrites_the_original_receipt() {
    let lane = Lane::new();
    lane.seed_owned("bot-a", "user-a").await;
    lane.seed_human("user-a").await;
    lane.seed_human("user-b").await;

    // a → b accepted (v2, owner b).
    let t1 = lane.created_transfer_id(create_command("user-a", "bot-a", "user-b", 1)).await;
    let accepted = lane
        .service
        .accept_transfer(decide_command("user-b", &t1))
        .await
        .expect("b accepts");
    assert_eq!(accepted.status, TransferStatus::Accepted);
    assert_eq!(accepted.result_owner_version, Some(2));

    // Ownership moves on: b → a accepted (v3, owner a).
    let t2 = lane.created_transfer_id(create_command("user-b", "bot-a", "user-a", 2)).await;
    let moved_on = lane
        .service
        .accept_transfer(decide_command("user-a", &t2))
        .await
        .expect("a accepts the return transfer");
    assert_eq!(moved_on.result_owner_version, Some(3));

    // The historical accepted retry by b answers with the ORIGINAL
    // receipt — the live owner never changes history.
    let retry = lane
        .service
        .accept_transfer(decide_command("user-b", &t1))
        .await
        .expect("the committed retry observes the winner");
    assert_eq!(retry.status, TransferStatus::Accepted);
    assert_eq!(retry.result_owner_version, Some(2));
    assert_eq!(retry.transfer_id, t1);

    // Incompatible actions on the decided row: the typed
    // ownership_transfer_not_pending conflict.
    let err = lane
        .service
        .reject_transfer(decide_command("user-b", &t1))
        .await
        .expect_err("reject on accepted");
    assert_eq!(error_code(&err), "ownership_transfer_not_pending");
}

#[tokio::test]
async fn parties_and_only_the_parties_read_the_receipt() {
    let lane = Lane::new();
    lane.seed_owned("bot-a", "user-a").await;
    lane.seed_human("user-b").await;
    lane.seed_human("user-c").await;
    let transfer_id = lane.created_transfer_id(create_command("user-a", "bot-a", "user-b", 1)).await;

    for caller in ["user-a", "user-b"] {
        let receipt = lane
            .service
            .get_transfer(GetBotOwnershipTransfer {
                caller: human_caller(caller),
                transfer_id: transfer_id.clone(),
            })
            .await
            .unwrap_or_else(|_| panic!("{caller} is a recorded party"));
        assert_eq!(receipt.status, TransferStatus::Pending);
    }
    for caller in ["user-c", ""] {
        let err = lane
            .service
            .get_transfer(GetBotOwnershipTransfer {
                caller: human_caller(caller),
                transfer_id: transfer_id.clone(),
            })
            .await
            .expect_err("non-parties share the concealment branch");
        assert!(matches!(err, ApplicationError::NotFound { .. }));
        assert_eq!(error_code(&err), "ownership_transfer_not_found");
    }
}

#[tokio::test]
async fn inbox_and_outbox_pages_scope_to_the_verified_viewer() {
    let lane = Lane::new();
    lane.seed_owned("bot-a", "user-a").await;
    lane.seed_owned("bot-b", "user-b").await;
    lane.seed_human("user-a").await;
    lane.seed_human("user-c").await;
    lane.seed_human("user-x").await;

    let t1 = lane.created_transfer_id(create_command("user-a", "bot-a", "user-c", 1)).await;
    let t2 = lane.created_transfer_id(create_command("user-b", "bot-b", "user-a", 1)).await;

    let inbox = lane
        .service
        .list_transfers(ListBotOwnershipTransfers {
            caller: human_caller("user-a"),
            direction: TransferListDirection::Received,
            status: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("inbox");
    let BotOwnershipTransferPage { items, total, .. } = inbox;
    assert_eq!(total, 1, "only the user-b → user-a pending arrives");
    assert_eq!(items[0].transfer_id, t2);

    let outbox = lane
        .service
        .list_transfers(ListBotOwnershipTransfers {
            caller: human_caller("user-a"),
            direction: TransferListDirection::Sent,
            status: Some(TransferStatus::Pending),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("outbox");
    assert_eq!(outbox.total, 1);
    assert_eq!(outbox.items[0].transfer_id, t1);

    // An unrelated viewer sees no rows at all — the listing is
    // party-scoped, never an enumeration surface.
    let stranger = lane
        .service
        .list_transfers(ListBotOwnershipTransfers {
            caller: human_caller("user-x"),
            direction: TransferListDirection::Received,
            status: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("empty page");
    assert_eq!(stranger.total, 0);
    assert!(stranger.items.is_empty());
}

#[tokio::test]
async fn not_live_recipient_and_uninitialized_bot_keep_their_fixed_codes() {
    let lane = Lane::new();
    lane.seed_owned("bot-a", "user-a").await;

    // The recipient is not a resolvable Human: the authorize initiator
    // gets 400 invalid_transfer_recipient.
    let err = lane
        .service
        .create_transfer(create_command("user-a", "bot-a", "user-ghost", 1))
        .await
        .expect_err("ghost recipient");
    assert_eq!(error_code(&err), "invalid_transfer_recipient");
    assert!(matches!(err, ApplicationError::InvalidInput { .. }));
    assert!(lane.stored_transfers("bot-a").await.is_empty());

    // The manager/ownership shared 409 for an uninitialized bot.
    lane.repo
        .seed_authority_uninitialized_bot("bot-zero")
        .await
        .expect("uninitialized row");
    let err = lane
        .service
        .get_ownership(GetBotOwnership {
            caller: human_caller("user-a"),
            bot_id: "bot-zero".to_string(),
        })
        .await
        .expect_err("uninitialized reads the shared 409");
    assert_eq!(error_code(&err), "ownership_not_initialized");
    assert!(matches!(err, ApplicationError::Conflict { .. }));

    // A caller with no current role on a visible bot is forbidden.
    let err = lane
        .service
        .get_ownership(GetBotOwnership {
            caller: human_caller("user-stranger"),
            bot_id: "bot-a".to_string(),
        })
        .await
        .expect_err("no role");
    assert_eq!(error_code(&err), "forbidden");
}