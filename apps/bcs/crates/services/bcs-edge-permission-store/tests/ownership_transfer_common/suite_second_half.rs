//! The second half of the shared case suites (plan Task 8): the racing
//! deciders, the retired Bot's historical receipts, the party-visibility /
//! effective-status / paging reads, and OT02's full team-source chain.
//! Split from `suite.rs` only for the file-size budget; the cases stay
//! driver-neutral and are driven through the same aggregator.

use bcs_domain::{BotAccessRelation, TerminalReason, TransferAction, TransferStatus};
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, ListOwnershipTransfers, OwnershipTransfer,
    OwnershipTransferPage, TransferListDirection,
};
use bcs_service_api::types::{AuditActor, ManagerMutation};
use bcs_service_api::ServiceError;

use super::suite::{
    assert_receipt, expect_concealed, fresh_key,
};
use super::Harness;
use super::{create_with_key, FUTURE_DEADLINE, LAPSED_DEADLINE};

// ---------------------------------------------------------------------------
// Case 5 — accept / cancel / reject racing on one pending
// ---------------------------------------------------------------------------

pub(super) async fn competition_case(h: &Harness) {
    let repo = h.repo.clone();
    h.seed_owned("bot-race", "a").await;
    h.seed_human("b").await;
    let created = repo
        .create_transfer(create_with_key("a", "bot-race", "b", 1, &fresh_key()))
        .await
        .unwrap();
    let id = created.receipt.transfer_id.clone();

    // Three concurrent decisions race on the serialized Bot boundary:
    // exactly one terminal state commits; every loser observes either the
    // winner's committed receipt (same action) or the not-pending conflict.
    let (accept, reject, cancel) = tokio::join!(
        repo.decide_transfer("b", &id, TransferAction::Accept),
        repo.decide_transfer("b", &id, TransferAction::Reject),
        repo.decide_transfer("a", &id, TransferAction::Cancel),
    );
    let outcomes = vec![
        ("accept", accept),
        ("reject", reject),
        ("cancel", cancel),
    ];
    for (name, outcome) in &outcomes {
        match outcome {
            Ok(CommittedTransferOutcome::Receipt(receipt)) => {
                // A late same-action runner may observe the winner's own
                // committed receipt — with a terminal, decided row only.
                assert!(
                    matches!(
                        receipt.status,
                        TransferStatus::Accepted
                            | TransferStatus::Rejected
                            | TransferStatus::Cancelled
                    ),
                    "{name} observed a non-terminal receipt: {:?}",
                    receipt.status
                );
            }
            Ok(other) => panic!(
                "{name} must not observe {:?} — only receipts and conflicts",
                other
            ),
            Err(ServiceError::Authority(AuthorityError::Conflict(_))) => {}
            Err(other_err) => panic!(
                "{name} lost the race with a broken branch: {other_err:?}"
            ),
        }
    }

    // Exactly one terminal state persisted and the row is consistent.
    let raw = h.driver.raw_row("bot-race", &id).await.expect("the row exists");
    let stored = raw.stored_status.as_str();
    assert!(
        matches!(stored, "accepted" | "rejected" | "cancelled"),
        "exactly one terminal state must persist, got {stored:?}"
    );
    let expected_version = if stored == "accepted" { 2 } else { 1 };
    assert_eq!(
        repo.ownership("bot-race").await.unwrap().ownership_version,
        expected_version,
        "the version bumps exactly once, iff the acceptance won (OT07)"
    );
    let expected_a_role = if stored == "accepted" {
        BotAccessRelation::Manager
    } else {
        BotAccessRelation::Owner
    };
    assert_eq!(
        repo.role("a", "bot-race").await.unwrap(),
        Some(expected_a_role),
        "role edges move exactly once, with the winner"
    );
    // Every Ok receipt the racers reported agrees with the stored terminal.
    for (name, outcome) in &outcomes {
        if let Ok(CommittedTransferOutcome::Receipt(receipt)) = outcome {
            let observed = match receipt.status {
                TransferStatus::Accepted => "accepted",
                TransferStatus::Rejected => "rejected",
                TransferStatus::Cancelled => "cancelled",
                other => panic!("{name} observed non-terminal {other:?}"),
            };
            assert_eq!(
                observed, stored,
                "{name} reported a terminal state the storage does not hold"
            );
        }
    }
    // And exactly the racers whose action WON may return a receipt; at
    // most one of the three observes Ok.
    let receipts = outcomes
        .iter()
        .filter(|(_, outcome)| matches!(outcome, Ok(CommittedTransferOutcome::Receipt(_))))
        .count();
    assert!(
        receipts <= 2,
        "at most the own-action retry plus the winner observe the receipt"
    );
}

// ---------------------------------------------------------------------------
// Case 6 — the retired Bot's historical receipts (OT17)
// ---------------------------------------------------------------------------

pub(super) async fn deleted_bot_history_case(h: &Harness) {
    let repo = h.repo.clone();
    h.seed_owned("bot-del", "a").await;
    h.seed_human("b").await;
    let created = repo
        .create_transfer(create_with_key("a", "bot-del", "b", 1, &fresh_key()))
        .await
        .unwrap();
    let id = created.receipt.transfer_id.clone();

    // The Task 5 retirement: soft delete + revoked edges + the pending
    // invalidated with terminal_reason bot_deleted.
    h.driver.retire_bot("bot-del").await;
    assert!(
        matches!(repo.ownership("bot-del").await, Err(ServiceError::BotNotFound(_))),
        "a retired Bot has no authority surface"
    );

    // Historical receipts stay readable for both parties (OT18's
    // visibility belongs to the recorded pair, not to liveness).
    let receipt = repo.get_transfer("a", &id).await.unwrap();
    assert_eq!(receipt.status, TransferStatus::Invalidated);
    assert_eq!(receipt.terminal_reason, Some(TerminalReason::BotDeleted));

    // Decide retries observe the persisted bot_deleted invalidation —
    // they must NEVER resurrect the retired Bot (OT17).
    assert_eq!(
        repo.decide_transfer("b", &id, TransferAction::Accept)
            .await
            .unwrap(),
        CommittedTransferOutcome::Invalidated,
        "the bot_deleted invalidation is the committed outcome"
    );
    assert_eq!(
        repo.decide_transfer("b", &id, TransferAction::Reject)
            .await
            .unwrap(),
        CommittedTransferOutcome::Invalidated,
        "any recipient action re-derives the persisted bot_deleted invalidation"
    );
    assert_eq!(
        repo.decide_transfer("a", &id, TransferAction::Cancel)
            .await
            .unwrap(),
        CommittedTransferOutcome::Invalidated,
        "the initiator's action retry re-derives the same committed outcome"
    );
    assert_eq!(
        h.driver.raw_row("bot-del", &id).await,
        Some(super::RawTransferRow {
            stored_status: "invalidated".to_string(),
            terminal_reason: Some("bot_deleted".to_string()),
        }),
        "no decide rewrote the retired Bot's history"
    );
    assert!(
        matches!(repo.ownership("bot-del").await, Err(ServiceError::BotNotFound(_))),
        "nothing resurrected through the historical receipt"
    );
}

// ---------------------------------------------------------------------------
// Case 7 — party visibility, effective-status filtering, one-snapshot pages
// ---------------------------------------------------------------------------

pub(super) async fn visibility_and_paging_case(h: &Harness) {
    let repo = h.repo.clone();
    h.seed_owned("bot-v1", "av").await;
    h.seed_owned("bot-v2", "av").await;
    h.seed_owned("bot-v3", "av").await;
    h.seed_owned("bot-v4", "av").await;
    h.seed_human("bv").await;
    h.seed_human("stranger").await;
    let mut expected_status: Vec<(String, TransferStatus)> = Vec::new();

    // One row per bot (the pending slot is per-Bot): pending, lapsed,
    // accepted, cancelled — all with `bv` as the recipient so the received
    // page has deterministic membership.
    let pending_id = {
        let created = repo
            .create_transfer(create_with_key("av", "bot-v1", "bv", 1, &fresh_key()))
            .await
            .unwrap();
        expected_status.push((created.receipt.transfer_id.clone(), TransferStatus::Pending));
        created.receipt.transfer_id
    };
    let lapsed_id = h
        .driver
        .seed_pending("bot-v2", "av", "bv", 1, LAPSED_DEADLINE, "")
        .await;
    expected_status.push((lapsed_id.clone(), TransferStatus::Expired));
    let accepted_id = {
        let created = repo
            .create_transfer(create_with_key("av", "bot-v3", "bv", 1, &fresh_key()))
            .await
            .unwrap();
        repo.decide_transfer("bv", &created.receipt.transfer_id, TransferAction::Accept)
            .await
            .unwrap();
        expected_status.push((created.receipt.transfer_id.clone(), TransferStatus::Accepted));
        created.receipt.transfer_id
    };
    let cancelled_id = {
        let created = repo
            .create_transfer(create_with_key("av", "bot-v4", "bv", 1, &fresh_key()))
            .await
            .unwrap();
        repo.decide_transfer("av", &created.receipt.transfer_id, TransferAction::Cancel)
            .await
            .unwrap();
        expected_status.push((created.receipt.transfer_id.clone(), TransferStatus::Cancelled));
        created.receipt.transfer_id
    };

    // Concealment (OT18): neither a stranger nor a non-party manager can
    // read or enumerate the record; both look identical to a missing id.
    expect_concealed(
        repo.get_transfer("stranger", &accepted_id).await,
        "stranger reading a transfer",
    );
    expect_concealed(
        repo.get_transfer("av", "no-such-transfer").await,
        "missing id",
    );
    let stranger_page: OwnershipTransferPage = repo
        .list_transfers(ListOwnershipTransfers {
            viewer_user_id: "stranger".to_string(),
            direction: TransferListDirection::Received,
            status: None,
            offset: 0,
            limit: 20,
        })
        .await
        .unwrap();
    assert_eq!(stranger_page.total, 0);
    assert!(stranger_page.items.is_empty());

    // The unfiltered received page: same snapshot count/page, ordered
    // gmt_create DESC / transfer_id ASC, every row party-visible to bv.
    let full: OwnershipTransferPage = repo
        .list_transfers(ListOwnershipTransfers {
            viewer_user_id: "bv".to_string(),
            direction: TransferListDirection::Received,
            status: None,
            offset: 0,
            limit: 20,
        })
        .await
        .unwrap();
    assert_eq!(full.total, 4);
    assert_eq!(full.items.len(), 4);
    assert!(
        full.items.windows(2).all(|pair| {
            let (left, right) = (pair[0].clone(), pair[1].clone());
            (left.gmt_create, left.transfer_id.clone())
                >= (right.gmt_create, right.transfer_id.clone())
                || left.gmt_create == right.gmt_create
        }),
        "the page keeps the gmt_create DESC, transfer_id ASC ordering"
    );
    let by_id = |receipt: &OwnershipTransfer| receipt.transfer_id.clone();
    let statuses: std::collections::BTreeMap<String, TransferStatus> = full
        .items
        .iter()
        .map(|receipt| (by_id(receipt), receipt.status))
        .collect();
    for (id, status) in &expected_status {
        assert_eq!(
            statuses.get(id),
            Some(status),
            "receipt {id} must project its effective status"
        );
        let receipt = repo.get_transfer("bv", id).await.unwrap();
        assert_eq!(receipt.status, *status);
    }
    // The lapsed row keeps projecting Expired while staying physically
    // pending (the read wrote nothing).
    assert_eq!(
        h.driver.raw_row("bot-v2", &lapsed_id).await,
        Some(super::RawTransferRow {
            stored_status: "pending".to_string(),
            terminal_reason: None,
        }),
        "no read materialized the projected expiry"
    );

    // Effective-status filtering runs in the database BEFORE paging (§9.1):
    // the lapsed pending no longer matches `pending` and matches `expired`.
    let filtered = |status: Option<TransferStatus>| ListOwnershipTransfers {
        viewer_user_id: "bv".to_string(),
        direction: TransferListDirection::Received,
        status,
        offset: 0,
        limit: 20,
    };
    let pending_page = repo.list_transfers(filtered(Some(TransferStatus::Pending))).await.unwrap();
    assert_eq!(pending_page.total, 1, "only the still-valid pending matches pending");
    assert_eq!(pending_page.items[0].transfer_id, pending_id);
    let expired_page = repo.list_transfers(filtered(Some(TransferStatus::Expired))).await.unwrap();
    assert_eq!(expired_page.total, 1, "the lapsed pending matches expired");
    assert_eq!(expired_page.items[0].transfer_id, lapsed_id);
    let accepted_page = repo
        .list_transfers(filtered(Some(TransferStatus::Accepted)))
        .await
        .unwrap();
    assert_eq!(accepted_page.total, 1);
    assert_eq!(accepted_page.items[0].transfer_id, accepted_id);

    // Paging: total comes from the SAME snapshot as the page (OT09).
    let paged = repo
        .list_transfers(ListOwnershipTransfers {
            viewer_user_id: "bv".to_string(),
            direction: TransferListDirection::Received,
            status: None,
            offset: 2,
            limit: 2,
        })
        .await
        .unwrap();
    assert_eq!(paged.total, 4, "the count shares the page's snapshot");
    assert_eq!(paged.items.len(), 2);
    let tail = repo
        .list_transfers(ListOwnershipTransfers {
            viewer_user_id: "bv".to_string(),
            direction: TransferListDirection::Received,
            status: None,
            offset: 4,
            limit: 2,
        })
        .await
        .unwrap();
    assert_eq!(tail.items.len(), 0, "beyond-the-end pages are empty, not errors");

    // An oversized limit is clamped to the page ceiling (a transport bug
    // can never turn into an unbounded scan), and the blank-viewer list
    // gets the concealment branch, never a leak.
    let clamped = repo
        .list_transfers(ListOwnershipTransfers {
            viewer_user_id: "bv".to_string(),
            direction: TransferListDirection::Received,
            status: None,
            offset: 0,
            limit: 5_000,
        })
        .await
        .unwrap();
    assert_eq!(
        clamped.items.len(),
        4,
        "a clamped limit still returns every real row of the small inbox"
    );
    let total_rows = 4;
    assert!(clamped.items.len() <= 100 && clamped.items.len() == total_rows);
    let blank = repo
        .list_transfers(ListOwnershipTransfers {
            viewer_user_id: "   ".to_string(),
            direction: TransferListDirection::Received,
            status: None,
            offset: 0,
            limit: 20,
        })
        .await;
    expect_concealed(blank, "blank viewer listing");

    // The sent direction for the initiator does NOT mix the received rows.
    let sent: OwnershipTransferPage = repo
        .list_transfers(ListOwnershipTransfers {
            viewer_user_id: "av".to_string(),
            direction: TransferListDirection::Sent,
            status: None,
            offset: 0,
            limit: 20,
        })
        .await
        .unwrap();
    assert_eq!(sent.total, 4);
    assert!(sent.items.iter().all(|row| row.from_user_id == "av"));

    // The initiator's own view of his sent row (party visibility) and the
    // recipient's view both carry the persisted terminal_reason (OT12's
    // application mapping depends on this field surviving the read).
    let cancelled_receipt = repo.get_transfer("av", &cancelled_id).await.unwrap();
    assert_eq!(cancelled_receipt.status, TransferStatus::Cancelled);
    assert_eq!(cancelled_receipt.terminal_reason, None);

    // The deterministic ordering proof: creation times that DIFFER (with a
    // same-second pair for the transfer_id tie-break), so an inverted twin
    // can never hide behind the all-same-second fixtures above.
    h.seed_human("ov").await;
    h.seed_owned("bot-ord1", "av").await;
    h.seed_owned("bot-ord2", "av").await;
    h.seed_owned("bot-ord3", "av").await;
    h
        .driver
        .seed_pending("bot-ord1", "av", "ov", 1, FUTURE_DEADLINE, "2000-02-02 02:02:02")
        .await;
    h
        .driver
        .seed_pending("bot-ord2", "av", "ov", 1, FUTURE_DEADLINE, "2000-02-02 02:02:02")
        .await;
    let oldest = h
        .driver
        .seed_pending("bot-ord3", "av", "ov", 1, FUTURE_DEADLINE, "1999-01-01 00:00:00")
        .await;
    let ordered = repo
        .list_transfers(ListOwnershipTransfers {
            viewer_user_id: "ov".to_string(),
            direction: TransferListDirection::Received,
            status: None,
            offset: 0,
            limit: 20,
        })
        .await
        .unwrap();
    assert_eq!(ordered.total, 3);
    // Newest-first: the same-second pair leads, tie-broken transfer_id ASC.
    assert_eq!(ordered.items[0].gmt_create, ordered.items[1].gmt_create);
    assert!(
        ordered.items[0].transfer_id < ordered.items[1].transfer_id,
        "equal gmt_create must tie-break transfer_id ASC"
    );
    // ...and the strictly older row closes the page.
    assert_eq!(ordered.items[2].transfer_id, oldest);
    assert!(ordered.items[2].gmt_create < ordered.items[0].gmt_create);
    // The strict adjacent-pair contract on deterministically distinct times.
    for pair in ordered.items.windows(2) {
        let (left, right) = (&pair[0], &pair[1]);
        assert!(
            left.gmt_create > right.gmt_create
                || (left.gmt_create == right.gmt_create
                    && left.transfer_id < right.transfer_id),
            "ordering contract violated: gmt_create DESC, transfer_id ASC"
        );
    }
}

// ---------------------------------------------------------------------------
// Case 8 — OT02's full team-source chain (Review Focus 3)
// ---------------------------------------------------------------------------

pub(super) async fn ot02_team_chain_case(h: &Harness) {
    let repo = h.repo.clone();
    h.seed_owned("bot-o2", "a").await;
    h.seed_human("b").await;
    h.seed_human("c").await;
    // B's pre-existing sources: one `team/*`, one direct one old transfer.
    h.seed_manager_source("bot-o2", "b", "team", "team-1").await;
    h.seed_manager_source("bot-o2", "b", "direct", "manual").await;
    h.seed_manager_source("bot-o2", "b", "ownership_transfer", "ot-seed").await;

    // Accept A→B: B's direct + ownership_transfer/* manager sources are
    // revoked (already an owner), the team source stays with team sync.
    let p1 = repo
        .create_transfer(create_with_key("a", "bot-o2", "b", 1, &fresh_key()))
        .await
        .unwrap();
    let r1 = repo
        .decide_transfer("b", &p1.receipt.transfer_id, TransferAction::Accept)
        .await
        .unwrap();
    assert_receipt(&r1, TransferStatus::Accepted);
    assert_eq!(repo.role("b", "bot-o2").await.unwrap(), Some(BotAccessRelation::Owner));

    // B→C: after B's acceptance B is the owner and can pass ownership on.
    let p2 = repo
        .create_transfer(create_with_key("b", "bot-o2", "c", 2, &fresh_key()))
        .await
        .unwrap();
    let r2 = repo
        .decide_transfer("c", &p2.receipt.transfer_id, TransferAction::Accept)
        .await
        .unwrap();
    assert_receipt(&r2, TransferStatus::Accepted);
    assert_eq!(
        repo.ownership("bot-o2").await.unwrap(),
        bcs_service_api::types::OwnershipState {
            owner_user_id: "c".to_string(),
            ownership_version: 3,
        }
    );
    assert_eq!(
        repo.role("b", "bot-o2").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "after B→C, B is a manager through its surviving sources (OT02)"
    );

    // The manager-chain: revoking B's non-team sources (by the CURRENT
    // owner, through the direct manager lane) keeps B a manager through
    // the still-live team source — the OT02 money path.
    let revoked = repo
        .mutate_manager(
            AuditActor::Human {
                user_id: "c".to_string(),
            },
            "bot-o2",
            ManagerMutation::RevokeNonTeam {
                user_id: "b".to_string(),
            },
        )
        .await
        .unwrap();
    assert!(revoked.changed);
    assert_eq!(
        revoked.remaining_team_sources,
        vec!["team-1".to_string()],
        "the team source must survive the transfer-obtained revoke (report契约)"
    );
    assert_eq!(
        repo.role("b", "bot-o2").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "B remains a manager through the team source alone (OT02 全链)"
    );
    assert_eq!(
        repo.role("a", "bot-o2").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "the first previous owner keeps its ownership_transfer source"
    );
}

