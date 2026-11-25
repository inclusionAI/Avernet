//! Reader-first SystemInvoke repo conformance. This is not a management or
//! production admission harness: raw repo writes seed trusted test fixtures.
use bcs_domain::edge_permission::{EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType};
use bcs_service_api::port::repo::{EdgeGrantRepoPort, edge_grant::FriendListQuery};

/// The same assertions run on durable and in-process implementations.
/// The fixture supplies an unused Bot pair in an isolated security namespace.
pub async fn run_system_invoke_repo_contract<T: EdgeGrantRepoPort + ?Sized>(
    repo: &T, env: &str, from: &str, to: &str,
) {
    let grant = EdgeGrant {
        edge_id: 0, env: env.into(), from_id: from.into(), to_id: to.into(),
        grant_kind: GrantKind::SystemInvoke, grant_ref_id: 0, rules: None,
        status: EdgeStatus::Approved,
        originator_policy_type: OriginatorPolicyType::SameAsFrom,
        originator_policy_data: None,
    };
    let edge_id = repo.insert_grant(grant.clone()).await.expect("seed canonical system edge");
    let listed = repo.list_runtime_grants_checked(from, to, env).await.unwrap();
    assert_eq!(listed.len(), 1);
    assert_eq!(listed[0].edge_id, edge_id);
    assert_eq!(listed[0].grant_kind, GrantKind::SystemInvoke);
    assert_eq!(repo.list_active_grants(from, to, env).await, listed);
    assert!(repo.is_authorized(from, to, env).await, "candidate existence only");
    assert!(!repo.is_authorized(to, from, env).await, "no reverse authorization");
    assert!(repo.list_runtime_grants_checked(from, to, "other-env").await.unwrap().is_empty());
    assert!(repo.list_runtime_grants_checked(&format!("{from} "), to, env).await.unwrap().is_empty());
    assert!(!repo.has_friend_edge(from, to, env).await);
    assert!(!repo.has_friend_edge(to, from, env).await);
    assert!(repo.list_friends(from, env).await.is_empty());
    assert_eq!(repo.list_friends_paginated(from, env, FriendListQuery {
        target_type: None, offset: 0, limit: 100,
    }).await.unwrap().total, 0);

    let mut bad = grant.clone();
    bad.grant_ref_id = 5;
    assert!(repo.insert_grant(bad).await.is_err());
    let mut bad = grant.clone();
    bad.rules = Some(serde_json::json!([]));
    assert!(repo.insert_grant(bad).await.is_err());
    let mut bad = grant.clone();
    bad.originator_policy_type = OriginatorPolicyType::Any;
    assert!(repo.insert_grant(bad).await.is_err());

    // The same ref=0 is namespaced by kind; revoking Rules must not touch SystemInvoke.
    let rules_id = repo.insert_grant(EdgeGrant {
        grant_kind: GrantKind::Rules, rules: Some(serde_json::json!([])), ..grant.clone()
    }).await.unwrap();
    assert_ne!(rules_id, edge_id);
    assert_eq!(repo.list_runtime_grants_checked(from, to, env).await.unwrap().len(), 2);
    repo.revoke_grant(rules_id, env).await.unwrap();
    assert_eq!(repo.list_runtime_grants_checked(from, to, env).await.unwrap().len(), 1);
    repo.revoke_grant(edge_id, env).await.unwrap();
    assert!(repo.list_runtime_grants_checked(from, to, env).await.unwrap().is_empty());
    assert!(!repo.is_authorized(from, to, env).await);

    // Human -> Bot management metadata remains excluded by both runtime reads.
    for kind in [GrantKind::Owner, GrantKind::Manager] {
        repo.insert_grant(EdgeGrant {
            from_id: "human_system_contract".into(), grant_kind: kind, ..grant.clone()
        }).await.unwrap();
    }
    assert!(repo.list_runtime_grants_checked("human_system_contract", to, env).await.unwrap().is_empty());
    assert!(!repo.is_authorized("human_system_contract", to, env).await);
}
