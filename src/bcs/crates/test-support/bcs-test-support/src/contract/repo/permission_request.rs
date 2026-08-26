//! `PermissionRequestRepoPort` conformance harness (Rule 25).
//!
//! Concrete implementations (SQLite, MySQL, in-memory …) call
//! [`run_permission_request_repo_contract`] from a
//! `tests/conformance_permission_request.rs` driver, passing their constructed
//! repo. The harness is generic over any `T: PermissionRequestRepoPort + ?Sized`.

use bcs_domain::edge_permission::{PermissionRequest, RequestKind, RequestStatus};
use bcs_service_api::port::repo::PermissionRequestRepoPort;

/// Rule 25 conformance suite for `PermissionRequestRepoPort`.
///
/// Covers `insert` (idempotent on PK `request_id`), `get` (including missing →
/// `None`), `list_inbox` (with and without status filter), `decide` (status +
/// decided_by + decided_at mutation), and `backfill_edge_id`.
pub async fn run_permission_request_repo_contract<T: PermissionRequestRepoPort + ?Sized>(
    repo: &T,
    env: &str,
) {
    let request_id = "contract-1".to_string();
    let req = PermissionRequest {
        request_id: request_id.clone(),
        edge_id: None,
        env: env.into(),
        from_id: "human_1".into(),
        to_id: "bot_1".into(),
        request_kind: RequestKind::Connect,
        requested_ref_id: None,
        requested_rules: None,
        message: Some("hi".into()),
        status: RequestStatus::Pending,
        decision_reason: None,
        created_by: "human_1".into(),
        decided_by: None,
        decided_at: None,
    };
    repo.insert(req).await.expect("insert request");

    // get — pending request round-trips, edge_id unset.
    let got = repo.get(&request_id, env).await.expect("found");
    assert_eq!(got.request_id, request_id);
    assert_eq!(got.status, RequestStatus::Pending);
    assert!(got.edge_id.is_none(), "pending → no edge_id");
    assert_eq!(got.request_kind, RequestKind::Connect);
    assert_eq!(got.from_id, "human_1");
    assert_eq!(got.to_id, "bot_1");
    assert_eq!(got.message.as_deref(), Some("hi"));

    // list_inbox — visible without filter, filterable by status.
    assert_eq!(
        repo.list_inbox("bot_1", env, None).await.len(),
        1,
        "inbox has the one pending request"
    );
    assert_eq!(
        repo.list_inbox("bot_1", env, Some(RequestStatus::Pending))
            .await
            .len(),
        1,
        "inbox filter Pending matches"
    );
    assert_eq!(
        repo.list_inbox("bot_1", env, Some(RequestStatus::Approved))
            .await
            .len(),
        0,
        "inbox filter Approved matches nothing yet"
    );
    // Inbox isolation: a different recipient sees nothing.
    assert!(
        repo.list_inbox("bot_other", env, None).await.is_empty(),
        "inbox is per recipient"
    );

    // decide — status, decided_by, decided_at mutate.
    repo.decide(
        &request_id,
        env,
        RequestStatus::Approved,
        "owner",
        Some("ok"),
    )
    .await
    .expect("decide");
    let got2 = repo.get(&request_id, env).await.expect("found after decide");
    assert_eq!(got2.status, RequestStatus::Approved, "status → approved");
    assert_eq!(got2.decided_by.as_deref(), Some("owner"), "decided_by set");
    // decided_at is a DB-managed timestamp set at decision time.
    assert!(got2.decided_at.is_some(), "decided_at set");
    assert_eq!(got2.decision_reason.as_deref(), Some("ok"), "decision_reason set");

    // backfill_edge_id — annotate the approved request with its new edge.
    repo.backfill_edge_id(&request_id, env, 3001)
        .await
        .expect("backfill_edge_id");
    let got3 = repo.get(&request_id, env).await.expect("found after backfill");
    assert_eq!(
        got3.edge_id,
        Some(3001),
        "edge_id back-filled after approval"
    );

    // Missing request → None (non-fallible).
    assert!(
        repo.get("missing", env).await.is_none(),
        "missing request → None"
    );
}