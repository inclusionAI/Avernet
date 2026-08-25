//! `EdgeGrantRepoPort` conformance harness (Rule 25).
//!
//! Concrete implementations (SQLite, MySQL, in-memory …) call
//! [`run_edge_grant_repo_contract`] from a `tests/conformance_edge_grant.rs`
//! driver, passing their constructed repo. The harness is generic over any
//! `T: EdgeGrantRepoPort + ?Sized`, so the same suite covers every impl.
//!
//! Driver responsibility: before invoking the harness, the driver MUST seed
//! `target_bot`'s default profile (via `PermissionProfileRepoPort`) so the
//! contract's `get_default_profile_id(target_bot, env)` call resolves. The
//! harness itself only depends on the `EdgeGrantRepoPort` trait.

use bcs_domain::edge_permission::{
    EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType,
};
use bcs_service_api::port::repo::EdgeGrantRepoPort;

/// Rule 25 conformance suite for `EdgeGrantRepoPort`.
///
/// Covers the friend-edge lifecycle (D12 symmetric + wrong-ref discrimination),
/// idempotent insert on the `(from, to, env, grant_ref_id)` unique key,
/// `list_active_grants`, `list_friends`, `revoke_grant`, and the default
/// profile id read. `env` / `target_bot` / `human` are driver-supplied so the
/// same suite can target either seeded or fresh state.
pub async fn run_edge_grant_repo_contract<T: EdgeGrantRepoPort + ?Sized>(
    repo: &T,
    env: &str,
    target_bot: &str,
    human: &str,
) {
    // 1. No friend edge initially (no grants seeded for this human→target pair).
    assert!(
        !repo.has_friend_edge(human, target_bot, env).await,
        "no friend edge before any grant"
    );

    // 2. Driver MUST have seeded a default profile for target_bot (via
    //    PermissionProfileRepoPort). The EdgeGrantRepoPort can read it back.
    let default_id = repo
        .get_default_profile_id(target_bot, env)
        .await
        .expect("driver must seed target's default profile before running contract");

    // 3. Insert a friend edge (human → target, grant_ref = target's default).
    let edge = EdgeGrant {
        edge_id: 0,
        env: env.into(),
        from_id: human.into(),
        to_id: target_bot.into(),
        grant_kind: GrantKind::PermissionProfile,
        grant_ref_id: default_id,
        rules: None,
        status: EdgeStatus::Approved,
        originator_policy_type: OriginatorPolicyType::Any,
        originator_policy_data: None,
    };
    let edge_id = repo.insert_grant(edge.clone()).await.unwrap();

    // 4. has_friend_edge both directions (D12 symmetric).
    assert!(
        repo.has_friend_edge(human, target_bot, env).await,
        "friend edge after insert (forward)"
    );
    assert!(
        repo.has_friend_edge(target_bot, human, env).await,
        "friend edge after insert (reverse)"
    );

    // 5. list_active_grants has exactly the friend edge.
    let grants = repo.list_active_grants(human, target_bot, env).await;
    assert_eq!(grants.len(), 1, "one active grant after friend insert");
    assert_eq!(grants[0].edge_id, edge_id);

    // 6. list_friends(human) contains target.
    assert!(
        repo.list_friends(human, env)
            .await
            .iter()
            .any(|f| f == target_bot),
        "target appears in human's friend list"
    );

    // 7. Insert a WRONG-ref edge (same pair, wrong grant_ref_id). It is active
    //    but is NOT a friend edge (D12 discrimination: only the default-ref
    //    edge counts as a friend).
    let wrong = EdgeGrant {
        edge_id: 0,
        grant_ref_id: default_id + 1,
        ..edge.clone()
    };
    repo.insert_grant(wrong).await.unwrap();
    let grants2 = repo.list_active_grants(human, target_bot, env).await;
    assert_eq!(grants2.len(), 2, "both edges active (friend + wrong-ref)");

    //    friend list still contains target exactly once (wrong-ref excluded by D12).
    let friends2: Vec<_> = repo
        .list_friends(human, env)
        .await
        .into_iter()
        .filter(|f| f == target_bot)
        .collect();
    assert_eq!(
        friends2.len(),
        1,
        "target appears once in friend list (D12 wrong-ref excluded)"
    );

    // 8. Idempotent insert: duplicate (from,to,env,grant_ref) is a no-op
    //    (SQLite ON CONFLICT DO NOTHING / MySQL INSERT IGNORE). A second insert
    //    with the same key — even under a different `edge_id` — does NOT add a
    //    new active grant.
    let dup = EdgeGrant {
        edge_id: 1234,
        ..edge.clone()
    };
    let dup_id = repo.insert_grant(dup)
        .await
        .expect("dup insert must not error (ON CONFLICT)");
    assert_eq!(dup_id, edge_id, "duplicate insert returns existing id");
    assert_eq!(
        repo.list_active_grants(human, target_bot, env).await.len(),
        2,
        "dup blocked by unique key — still two active grants"
    );

    // 9. Revoke the friend edge → has_friend_edge false (default revoked, the
    //    wrong-ref edge cannot substitute for a friend edge).
    repo.revoke_grant(edge_id, env).await.unwrap();
    assert!(
        !repo.has_friend_edge(human, target_bot, env).await,
        "revoked friend edge → not friends (wrong-ref does not count)"
    );
    assert_eq!(
        repo.list_active_grants(human, target_bot, env).await.len(),
        1,
        "wrong-ref still active after friend revoke"
    );
}