//! The shared driver-neutral case suites of the `ownership_transfer` test
//! binary (plan Task 8): the brief's RED snapshot and the OT01-OT18/OT20-21
//! repo subcases, written driver-neutrally so the SQLite and Memory
//! conformance runs stay byte-identical assertions. The drivers live in
//! `mod.rs`; the SQLite-only SQL-level proofs (drift guards, statement
//! budgets, raw facts, the DB-clock boundary) live in the test entry.

use bcs_domain::{BotAccessRelation, TransferAction, TransferStatus};
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, ListOwnershipTransfers, OwnershipTransfer,
    TransferListDirection,
};
use bcs_service_api::types::AuditActor;
use bcs_service_api::ServiceError;

use super::{create_with_key, Harness, FUTURE_DEADLINE, LAPSED_DEADLINE};

pub(crate) fn fresh_key() -> String {
    uuid::Uuid::new_v4().to_string()
}

/// The receipt inside a committed outcome, or panic with the outcome.
pub(crate) fn receipt_of(outcome: &CommittedTransferOutcome) -> &OwnershipTransfer {
    match outcome {
        CommittedTransferOutcome::Receipt(receipt) => receipt,
        other => panic!("expected a committed receipt, got {:?}", other),
    }
}

/// Assert the outcome is a receipt with the given status and return it.
pub(crate) fn assert_receipt(
    outcome: &CommittedTransferOutcome,
    want: TransferStatus,
) -> &OwnershipTransfer {
    let receipt = receipt_of(outcome);
    assert_eq!(
        receipt.status, want,
        "committed receipt status must be {want:?}, got {:?}",
        receipt.status
    );
    receipt
}

pub(crate) fn expect_conflict<T>(outcome: Result<T, ServiceError>, what: &str) {
    match outcome {
        Err(ServiceError::Authority(AuthorityError::Conflict(_))) => {}
        other => panic!("{what} must be a Conflict, got {:?}", other.map(|_| "()")),
    }
}

pub(crate) fn expect_forbidden<T>(outcome: Result<T, ServiceError>, what: &str) {
    match outcome {
        Err(ServiceError::Authority(AuthorityError::Forbidden(_))) => {}
        other => panic!("{what} must be Forbidden, got {:?}", other.map(|_| "()")),
    }
}

pub(crate) fn expect_invalid_subject<T>(outcome: Result<T, ServiceError>, what: &str) {
    match outcome {
        Err(ServiceError::Authority(AuthorityError::InvalidSubject(_))) => {}
        other => panic!("{what} must be InvalidSubject, got {:?}", other.map(|_| "()")),
    }
}

pub(crate) fn expect_concealed<T>(outcome: Result<T, ServiceError>, what: &str) {
    match outcome {
        Err(ServiceError::Authority(AuthorityError::OwnershipTransferNotFound { .. })) => {}
        other => panic!(
            "{what} must be the 404 concealment branch, got {:?}",
            other.map(|_| "()")
        ),
    }
}

// ---------------------------------------------------------------------------
// Case 1 — the brief's RED snapshot, verbatim semantics
// ---------------------------------------------------------------------------

async fn red_snapshot_case(h: &Harness) {
    let repo = h.repo.clone();
    h.seed_owned("bot-red", "a").await;
    h.seed_human("b").await;
    let key = fresh_key();

    let created = repo
        .create_transfer(create_with_key("a", "bot-red", "b", 1, &key))
        .await
        .unwrap();
    assert!(created.created, "the first committed create reports itself");
    let request = created.receipt.clone();
    assert_eq!(request.status, TransferStatus::Pending);
    assert_eq!(request.from_user_id, "a");
    assert_eq!(request.to_user_id, "b");
    assert_eq!(request.expected_owner_version, 1);
    assert_eq!(request.bot_id, "bot-red");
    assert!(!request.transfer_id.is_empty());
    assert!(request.expires_at > request.gmt_create, "deadline = create + 7d");

    // The pending replay BEFORE anything is decided: same key, same
    // payload — the ORIGINAL receipt returns with created=false.
    let replay = repo
        .create_transfer(create_with_key("a", "bot-red", "b", 1, &key))
        .await
        .unwrap();
    assert!(!replay.created, "the durable idempotency key replays");
    assert_eq!(replay.receipt, request);
    assert_eq!(
        h.driver.row_count("bot-red").await,
        1,
        "the replay must not create a second row or pending"
    );

    // The brief's RED snippet decides the acceptance and re-decides it:
    // the retry observes the SAME committed result.
    let accepted = repo
        .decide_transfer("b", &request.transfer_id, TransferAction::Accept)
        .await
        .unwrap();
    let accepted_receipt = assert_receipt(&accepted, TransferStatus::Accepted);
    assert_eq!(accepted_receipt.result_owner_version, Some(2));
    assert_eq!(
        accepted_receipt.decision_actor,
        Some(AuditActor::Human {
            user_id: "b".to_string()
        })
    );

    assert_eq!(
        repo.ownership("bot-red").await.unwrap().owner_user_id,
        "b",
        "ownership switched in the same commit"
    );
    assert_eq!(
        repo.ownership("bot-red").await.unwrap().ownership_version,
        2
    );
    assert_eq!(
        repo.role("a", "bot-red").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "the previous owner keeps manager access (OT14)"
    );
    assert_eq!(
        repo.role("b", "bot-red").await.unwrap(),
        Some(BotAccessRelation::Owner)
    );

    assert_eq!(
        repo.decide_transfer("b", &request.transfer_id, TransferAction::Accept)
            .await
            .unwrap(),
        accepted,
        "the accept retry must return the identical committed outcome"
    );

    // Same-key replay AFTER the acceptance: the durable receipt no longer
    // plays today, the row's CURRENT (terminal) state — created=false, and
    // the receipt reports the committed acceptance (the initiator is no
    // longer the owner; §10.1 step 1 never re-executes).
    let replay_after = repo
        .create_transfer(create_with_key("a", "bot-red", "b", 1, &key))
        .await
        .unwrap();
    assert!(!replay_after.created);
    assert_eq!(replay_after.receipt, receipt_of(&accepted).clone());

    // Incompatible actions on the decided row are conflicts.
    expect_conflict(
        repo.decide_transfer("b", &request.transfer_id, TransferAction::Reject)
            .await,
        "reject on accepted",
    );
    expect_conflict(
        repo.decide_transfer("a", &request.transfer_id, TransferAction::Cancel)
            .await,
        "cancel on accepted",
    );

    // The previous owner (now manager) cannot initiate another transfer;
    // the new owner can (OT14 — 原来的 owner 仅是 manager).
    expect_forbidden(
        repo.create_transfer(create_with_key("a", "bot-red", "b", 2, &fresh_key()))
            .await,
        "manager (previous owner) initiating",
    );
    h.seed_human("c").await;
    let next = repo
        .create_transfer(create_with_key("b", "bot-red", "c", 2, &fresh_key()))
        .await
        .unwrap();
    assert!(next.created, "the new owner initiates the next round");
}

// ---------------------------------------------------------------------------
// Case 2 — create validation, the pending slot and the idempotency key
// ---------------------------------------------------------------------------

async fn create_validation_and_slot_case(h: &Harness) {
    let repo = h.repo.clone();
    h.seed_owned("bot-cv", "a").await;
    h.seed_human("b").await;
    h.seed_human("c").await;
    h.seed_human("m").await;
    h.seed_manager_source("bot-cv", "m", "direct", "manual").await;

    // One stored pending per Bot: a different key's second create is the
    // ownership_transfer_pending conflict (OT04's single-writer property).
    let first = repo
        .create_transfer(create_with_key("a", "bot-cv", "b", 1, &fresh_key()))
        .await
        .unwrap();
    assert!(first.created);
    expect_conflict(
        repo.create_transfer(create_with_key("a", "bot-cv", "c", 1, &fresh_key()))
            .await,
        "second pending on the same bot",
    );

    // Same key, same payload: idempotent replay (created=false).
    let replay = repo
        .create_transfer(create_with_key(
            "a",
            "bot-cv",
            "b",
            1,
            &first.receipt.client_request_id,
        ))
        .await
        .unwrap();
    assert!(!replay.created);
    assert_eq!(replay.receipt, first.receipt);

    // Same key, different payload: idempotency conflict (OT05).
    expect_conflict(
        repo.create_transfer(create_with_key(
            "a",
            "bot-cv",
            "c",
            1,
            &first.receipt.client_request_id,
        ))
        .await,
        "same key different recipient",
    );
    expect_conflict(
        repo.create_transfer(create_with_key(
            "a",
            "bot-cv",
            "b",
            2,
            &first.receipt.client_request_id,
        ))
        .await,
        "same key different version",
    );

    // Non-owner initiators fail closed: a manager (Gate 0 does NOT include
    // transfer initiation) and a stranger (OT10).
    expect_forbidden(
        repo.create_transfer(create_with_key("m", "bot-cv", "b", 1, &fresh_key()))
            .await,
        "manager initiating a transfer",
    );
    expect_forbidden(
        repo.create_transfer(create_with_key("b", "bot-cv", "c", 1, &fresh_key()))
            .await,
        "stranger initiating a transfer",
    );

    // Self-transfer and unknown recipient are subject errors (OT11).
    expect_invalid_subject(
        repo.create_transfer(create_with_key("a", "bot-cv", "a", 1, &fresh_key()))
            .await,
        "self-transfer",
    );
    // Blank identities never reach the matching layer (structural
    // fail-closed of the store).
    expect_invalid_subject(
        repo.create_transfer(create_with_key("a", "bot-cv", " ", 1, &fresh_key()))
            .await,
        "blank recipient",
    );
    expect_invalid_subject(
        repo.create_transfer(create_with_key(" ", "bot-cv", "b", 1, &fresh_key()))
            .await,
        "blank actor",
    );
    expect_invalid_subject(
        repo.create_transfer(create_with_key("a", "bot-cv", "ghost", 1, &fresh_key()))
            .await,
        "unknown recipient",
    );

    // Unknown / uninitialized / corrupted liveness branches.
    assert!(
        matches!(
            repo.create_transfer(create_with_key("a", "ghost-bot", "b", 1, &fresh_key()))
                .await,
            Err(ServiceError::BotNotFound(_))
        ),
        "unknown bot must be BotNotFound"
    );
    h.driver.seed_uninitialized_bot("bot-zero").await;
    assert!(
        matches!(
            repo.create_transfer(create_with_key("a", "bot-zero", "b", 1, &fresh_key()))
                .await,
            Err(ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized { .. }
            ))
        ),
        "uninitialized bot must be OwnershipNotInitialized"
    );

    // The stale-version create is rejected WITHOUT persisting any row and
    // WITHOUT consuming the cleanup lane (§10.1 step 3; 不落单).
    let rows_before = h.driver.row_count("bot-cv").await;
    expect_conflict(
        repo.create_transfer(create_with_key("a", "bot-cv", "b", 99, &fresh_key()))
            .await,
        "stale expected_owner_version",
    );
    assert_eq!(
        h.driver.row_count("bot-cv").await,
        rows_before,
        "the stale-version create must not persist any row"
    );
    // The valid pending survives untouched.
    assert_eq!(
        h.driver.raw_row("bot-cv", &first.receipt.transfer_id).await,
        Some(super::RawTransferRow {
            stored_status: "pending".to_string(),
            terminal_reason: None,
        }),
        "the still-valid pending must survive the stale create attempt"
    );
}

// ---------------------------------------------------------------------------
// Case 3 — A→B→A round trip; the stale-pending owner_changed branch
// ---------------------------------------------------------------------------

async fn round_trip_and_stale_pending_case(h: &Harness) {
    let repo = h.repo.clone();
    h.seed_owned("bot-rt", "a").await;
    // `a` becomes the B→A round-trip RECIPIENT: the recipient must be a
    // live same-env Human at create time, exactly like any other recipient.
    h.seed_human("a").await;
    h.seed_human("b").await;
    h.seed_human("c").await;

    // A→B: version 2, B owner, A manager.
    let p1 = repo
        .create_transfer(create_with_key("a", "bot-rt", "b", 1, &fresh_key()))
        .await
        .unwrap();
    assert!(p1.created);
    let r1 = repo
        .decide_transfer("b", &p1.receipt.transfer_id, TransferAction::Accept)
        .await
        .unwrap();
    assert_receipt(&r1, TransferStatus::Accepted);

    // B→A: the owner changes hands back (version 3, A owner again). The
    // A→B→A circle completes without touching the earlier receipts.
    let p2 = repo
        .create_transfer(create_with_key("b", "bot-rt", "a", 2, &fresh_key()))
        .await
        .unwrap();
    let r2 = repo
        .decide_transfer("a", &p2.receipt.transfer_id, TransferAction::Accept)
        .await
        .unwrap();
    assert_receipt(&r2, TransferStatus::Accepted);
    assert_eq!(
        repo.ownership("bot-rt").await.unwrap(),
        bcs_service_api::types::OwnershipState {
            owner_user_id: "a".to_string(),
            ownership_version: 3,
        }
    );

    // OT06: replaying the OLD a→b acceptance after ownership has moved on
    // returns the historical receipt and never moves the owner back to B.
    let replayed = repo
        .decide_transfer("b", &p1.receipt.transfer_id, TransferAction::Accept)
        .await
        .unwrap();
    assert_eq!(replayed, r1, "the old acceptance replays its own receipt");
    assert_eq!(
        repo.ownership("bot-rt").await.unwrap().owner_user_id,
        "a",
        "the replay must not resurrect the old ownership"
    );
    assert_eq!(
        repo.role("b", "bot-rt").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "B stays a manager after the round trip (OT14)"
    );

    // A stale expected-version pending (still future-dated) against the
    // CURRENT authority commits as invalidated(owner_changed) on accept —
    // OT12: a NORMAL domain result (OwnerChanged), no edges changed, no
    // version bump, slot released, and the SAME result on retry.
    let stale_version = h
        .driver
        .seed_pending("bot-rt", "a", "b", 99, FUTURE_DEADLINE, "")
        .await;
    let before = repo.ownership("bot-rt").await.unwrap();
    let changed = repo
        .decide_transfer("b", &stale_version, TransferAction::Accept)
        .await
        .unwrap();
    assert_eq!(changed, CommittedTransferOutcome::OwnerChanged);
    assert_eq!(
        repo.ownership("bot-rt").await.unwrap(),
        before,
        "the invalidation must not touch roles or the version"
    );
    assert_eq!(
        h.driver.raw_row("bot-rt", &stale_version).await,
        Some(super::RawTransferRow {
            stored_status: "invalidated".to_string(),
            terminal_reason: Some("owner_changed".to_string()),
        }),
        "the owner_changed invalidation is persisted (OT12)"
    );
    // Non-degraded retry: the response-loss retry re-derives OwnerChanged
    // from the persisted terminal_reason, never a generic Invalidated.
    assert_eq!(
        repo.decide_transfer("b", &stale_version, TransferAction::Accept)
            .await
            .unwrap(),
        CommittedTransferOutcome::OwnerChanged,
        "invalidated(owner_changed) retries must stay OwnerChanged (OT12)"
    );
    // The committed invalidation released the pending slot: a fresh
    // legitimate create goes through.
    let fresh = repo
        .create_transfer(create_with_key("a", "bot-rt", "c", 3, &fresh_key()))
        .await
        .unwrap();
    assert!(fresh.created, "the committed invalidation frees the slot");

    // A wrong-from pending (the initiator is no longer the current owner)
    // takes the same owner_changed branch.
    h.seed_owned("bot-wf", "a").await;
    h.seed_human("d").await;
    let wrong_from = h
        .driver
        .seed_pending("bot-wf", "former-owner", "d", 1, FUTURE_DEADLINE, "")
        .await;
    let changed = repo
        .decide_transfer("d", &wrong_from, TransferAction::Accept)
        .await
        .unwrap();
    assert_eq!(
        changed,
        CommittedTransferOutcome::OwnerChanged,
        "a pending whose from is no longer the owner invalidates owner_changed"
    );
    assert_eq!(
        repo.ownership("bot-wf").await.unwrap().owner_user_id,
        "a",
        "no ownership changed through the wrong-from invalidation"
    );

    // The create-cleanup path: the current owner's fresh create
    // materializes the mismatched pending for this Bot as
    // invalidated(owner_changed), then creates its own new pending.
    let cleaned = repo
        .create_transfer(create_with_key("a", "bot-wf", "d", 1, &fresh_key()))
        .await
        .unwrap();
    assert!(cleaned.created, "the fresh create succeeds after the cleanup");
    assert_eq!(
        h.driver.raw_row("bot-wf", &wrong_from).await,
        Some(super::RawTransferRow {
            stored_status: "invalidated".to_string(),
            terminal_reason: Some("owner_changed".to_string()),
        }),
        "the mismatched pending was materialized by the create cleanup (§10.1 step 4)"
    );
    // And the new owner-passed-bot keeps its own pending intact.
    assert_eq!(
        h.driver.raw_row("bot-wf", &cleaned.receipt.transfer_id).await,
        Some(super::RawTransferRow {
            stored_status: "pending".to_string(),
            terminal_reason: None,
        })
    );
}

// ---------------------------------------------------------------------------
// Case 4 — the DB-clock expiry boundary
// ---------------------------------------------------------------------------

async fn expiration_boundary_case(h: &Harness) {
    let repo = h.repo.clone();
    h.seed_owned("bot-ex", "a").await;
    h.seed_human("b").await;
    h.seed_human("c").await;

    // A lapsed pending on its own bot: accepting materializes `expired`
    // with the system decider and the logical decision time expires_at.
    let lapsed = h
        .driver
        .seed_pending("bot-ex", "a", "b", 1, LAPSED_DEADLINE, "")
        .await;
    let owner_before = repo.ownership("bot-ex").await.unwrap();
    let outcome = repo
        .decide_transfer("b", &lapsed, TransferAction::Accept)
        .await
        .unwrap();
    assert_eq!(
        outcome,
        CommittedTransferOutcome::Expired,
        "the lapsed pending materializes expired as a committed domain result"
    );
    assert_eq!(repo.ownership("bot-ex").await.unwrap(), owner_before);
    assert_eq!(
        h.driver.raw_row("bot-ex", &lapsed).await,
        Some(super::RawTransferRow {
            stored_status: "expired".to_string(),
            terminal_reason: None,
        }),
        "the expiry materialization persisted without a terminal reason"
    );
    // The stored decision keeps §9.1's logical time: decided_at = expires_at
    // and the decided marker is the fixed system identifier — visible on
    // the receipt WITHOUT having materialized the decision columns first.
    let receipt = repo
        .get_transfer("b", &lapsed)
        .await
        .unwrap();
    assert_eq!(receipt.status, TransferStatus::Expired);
    assert_eq!(
        receipt.decision_actor,
        Some(AuditActor::System {
            name: "ownership-deadline".to_string()
        })
    );
    assert_eq!(receipt.decided_at, Some(receipt.expires_at));

    // Retries after the committed expiry observe Expired for any action.
    assert_eq!(
        repo.decide_transfer("b", &lapsed, TransferAction::Reject)
            .await
            .unwrap(),
        CommittedTransferOutcome::Expired
    );
    assert_eq!(
        repo.decide_transfer("a", &lapsed, TransferAction::Cancel)
            .await
            .unwrap(),
        CommittedTransferOutcome::Expired
    );
    // The expired slot is free for a fresh create (pending does not block).
    let fresh = repo
        .create_transfer(create_with_key("a", "bot-ex", "c", 1, &fresh_key()))
        .await
        .unwrap();
    assert!(fresh.created);

    // The READ side never materializes: a physically pending row whose
    // deadline lapsed PROJECTS as expired while the raw storage keeps
    // `pending` (OT09: expires_at 投影时计算, GET 不物化).
    h.seed_owned("bot-proj", "a").await;
    let projected = h
        .driver
        .seed_pending("bot-proj", "a", "b", 1, LAPSED_DEADLINE, "")
        .await;
    let receipt = repo.get_transfer("b", &projected).await.unwrap();
    assert_eq!(receipt.status, TransferStatus::Expired);
    assert_eq!(receipt.decided_at, Some(receipt.expires_at));
    assert_eq!(
        receipt.decision_actor,
        Some(AuditActor::System {
            name: "ownership-deadline".to_string()
        })
    );
    assert_eq!(
        h.driver.raw_row("bot-proj", &projected).await,
        Some(super::RawTransferRow {
            stored_status: "pending".to_string(),
            terminal_reason: None,
        }),
        "the read must not materialize the expired projection (no write side effects)"
    );

    // The create-cleanup path materializes the lapsed pending of ITS bot
    // and proceeds (§10.1 step 4: 过期不阻塞合法新建, OT08).
    let cleaned = repo
        .create_transfer(create_with_key("a", "bot-proj", "c", 1, &fresh_key()))
        .await
        .unwrap();
    assert!(cleaned.created);
    assert_eq!(
        h.driver.raw_row("bot-proj", &projected).await,
        Some(super::RawTransferRow {
            stored_status: "expired".to_string(),
            terminal_reason: None,
        }),
        "the create cleanup materialized the lapsed pending"
    );
}

// ---------------------------------------------------------------------------
// Case 4b — corrupted rows fail closed on read (never an implicit projection)
// ---------------------------------------------------------------------------

async fn corrupted_rows_fail_closed_case(h: &Harness) {
    let repo = h.repo.clone();
    h.seed_owned("bot-corrupt", "a").await;
    h.seed_human("b").await;
    // A pending row whose deadline text does not decode: schema CHECKs do
    // not validate timestamp TEXT, so this is the fail-closed decode path
    // the receipt reads own (未知/损坏行是数据错误, never a domain state).
    let corrupted = h
        .driver
        .seed_pending("bot-corrupt", "a", "b", 1, "not-a-timestamp", "")
        .await;
    match repo.get_transfer("b", &corrupted).await {
        Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. })) => {}
        other => panic!(
            "an undecodable expires_at must fail closed as CorruptAuthority, got {:?}",
            other.map(|_| "()")
        ),
    }
    match repo
        .list_transfers(ListOwnershipTransfers {
            viewer_user_id: "b".to_string(),
            direction: bcs_service_api::types::ownership_transfer::TransferListDirection::Received,
            status: None,
            offset: 0,
            limit: 20,
        })
        .await
    {
        Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. })) => {}
        other => panic!(
            "a corrupted row must fail the whole listing closed, got {:?}",
            other.map(|_| "()")
        ),
    }
}

// ---------------------------------------------------------------------------
// The shared suite (both drivers)
// ---------------------------------------------------------------------------

pub(crate) async fn transferred_ownership_contract_tests(h: &Harness) {
    red_snapshot_case(h).await;
    create_validation_and_slot_case(h).await;
    round_trip_and_stale_pending_case(h).await;
    expiration_boundary_case(h).await;
    corrupted_rows_fail_closed_case(h).await;
    super::suite_second_half::competition_case(h).await;
    super::suite_second_half::deleted_bot_history_case(h).await;
    super::suite_second_half::visibility_and_paging_case(h).await;
    super::suite_second_half::ot02_team_chain_case(h).await;
}

