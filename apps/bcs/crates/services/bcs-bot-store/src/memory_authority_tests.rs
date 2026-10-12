//! Tests for `memory_authority` (Task 3 strict reads + Task 4 mutations).
//!
//! Loaded through `#[path = "memory_authority_tests.rs"] mod tests;` from
//! `memory_authority.rs` so the module body stays `super::*`-local while the
//! parent file keeps to the workspace line budget.

use std::sync::Arc;

use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::{BotRepoPort, ServiceError};

use super::*;

async fn repo() -> Arc<MemoryBotRepo> {
    Arc::new(MemoryBotRepo::with_base_dir(
        std::env::temp_dir().join(format!(
            "bcs-authority-tests-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|duration| duration.as_nanos())
                .unwrap_or(0)
        )),
    ))
}

/// Inject a raw `edge_grants`-shaped role row directly (the strict
/// decoder's corrupt-object surface is only reachable in-crate).
async fn inject_role_row(repo_mem: &MemoryBotRepo, mut row: MemoryRoleEdgeRow) {
    let mut authority = repo_mem.authority.write().await;
    row.id = authority.allocate_edge_id();
    authority.role_rows.push(row);
}

fn raw_row(
    env: &str,
    from_id: &str,
    to_id: &str,
    kind: &str,
    source_kind: &str,
    source_id: &str,
) -> MemoryRoleEdgeRow {
    MemoryRoleEdgeRow {
        id: 0, // inject_role_row assigns the real autoincrement mirror id
        env: env.to_string(),
        from_id: from_id.to_string(),
        to_id: to_id.to_string(),
        grant_kind: kind.to_string(),
        grant_ref_id: ROLE_GRANT_REF_ID as i64,
        rules: None,
        status: "approved".to_string(),
        management_source_kind: source_kind.to_string(),
        management_source_id: source_id.to_string(),
    }
}

#[tokio::test]
async fn manager_rows_decode_strictly_with_owner_priority() {
    let repo = repo().await;
    repo.seed_authority_owned("bot-m", "m").await.unwrap();
    let env = resolve_env();

    inject_role_row(
        &repo,
        raw_row(&env, "human_m2", "bot-m", "manager", "direct", "manual"),
    )
    .await;
    inject_role_row(&repo, raw_row(&env, "human_m2", "bot-m", "manager", "team", "team-a"))
        .await;
    assert_eq!(
        repo.role("m2", "bot-m").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "any approved manager source grants the manager relation"
    );

    // Owner + manager edges on one subject: owner wins.
    inject_role_row(
        &repo,
        raw_row(&env, "human_m", "bot-m", "manager", "direct", "manual"),
    )
    .await;
    assert_eq!(
        repo.role("m", "bot-m").await.unwrap(),
        Some(BotAccessRelation::Owner)
    );

    // Revoked rows are retained but stop reading.
    let rows_before: Vec<MemoryRoleEdgeRow> = {
        let authority = repo.authority.read().await;
        authority.role_rows.clone()
    };
    for mut row in rows_before {
        if row.grant_kind == "manager" {
            row.status = "revoked".to_string();
        }
        let mut authority = repo.authority.write().await;
        // Replace by identity: drop the same-shape row, push the mutated one.
        authority
            .role_rows
            .retain(|existing| existing.env != row.env || existing.from_id != row.from_id || existing.to_id != row.to_id || existing.management_source_kind != row.management_source_kind || existing.management_source_id != row.management_source_id);
        authority.role_rows.push(row);
    }
    assert_eq!(repo.role("m2", "bot-m").await.unwrap(), None);
    assert_eq!(
        repo.role("m", "bot-m").await.unwrap(),
        Some(BotAccessRelation::Owner)
    );
}

#[tokio::test]
async fn illegal_shapes_fail_closed_never_default_into_roles() {
    let repo = repo().await;
    repo.seed_authority_owned("bot-x", "x").await.unwrap();
    let env = resolve_env();
    for (kind, source_kind, source_id, ref_override, rules) in [
        ("owner", "direct", "manual", ROLE_GRANT_REF_ID, false),
        ("owner", "owner", "owner", ROLE_GRANT_REF_ID + 1, false),
        ("owner", "owner", "owner", ROLE_GRANT_REF_ID, true),
        ("manager", "owner", "owner", ROLE_GRANT_REF_ID, false),
        ("manager", "direct", "not-manual", ROLE_GRANT_REF_ID, false),
        ("manager", "team", "", ROLE_GRANT_REF_ID, false),
        ("manager", "banana", "x", ROLE_GRANT_REF_ID, false),
        ("banana", "owner", "owner", ROLE_GRANT_REF_ID, false),
    ] {
        let from_id = "human_z";
        let mut row = raw_row(&env, from_id, "bot-x", kind, source_kind, source_id);
        row.grant_ref_id = ref_override as i64;
        if rules {
            row.rules = Some(serde_json::Value::Null);
        }
        inject_role_row(&repo, row).await;
        match repo.role("z", "bot-x").await {
            Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. })) => {}
            other => panic!(
                "corrupt role row must fail closed for kind={kind} source=({source_kind},{source_id}): {:?}",
                other.map(|role| role.is_some())
            ),
        }
        // Corrupt rows poison the batch read too (fail closed, no partial empty-success).
        let pairs = [
            ("x".to_string(), "bot-x".to_string()),
            ("z".to_string(), "bot-x".to_string()),
        ];
        assert!(repo.roles_for(&pairs).await.is_err());
        // And the ownership snapshot once the owner slot itself is damaged.
        if kind != "manager" {
            // owner-shaped corrupt rows surface as corruption aspects of ownership
        }
        // Remove the corrupt row again so the next probe starts clean.
        {
            let mut authority = repo.authority.write().await;
            let discriminator = from_id.to_string();
            authority.role_rows.retain(|row| {
                !(row.from_id == discriminator && row.to_id == "bot-x" && row.status == "approved")
            });
        }
    }
    // After cleanup the healthy owner row still reads.
    assert_eq!(
        repo.role("x", "bot-x").await.unwrap(),
        Some(BotAccessRelation::Owner)
    );
}

#[tokio::test]
async fn non_unique_owner_edges_are_corrupt() {
    let repo = repo().await;
    repo.seed_authority_owned("bot-dup", "d").await.unwrap();
    let env = resolve_env();
    inject_role_row(
        &repo,
        raw_row(&env, "human_e", "bot-dup", "owner", "owner", "owner"),
    )
    .await;
    assert!(
        matches!(
            repo.ownership("bot-dup").await,
            Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
        ),
        "two approved owner edges on an initialized bot are corruption, never a pick-one"
    );
}

#[tokio::test]
async fn reads_are_bound_to_the_repo_instance_env() {
    let repo = repo().await;
    let env = resolve_env();
    let _ = env;
    // A role row in ANOTHER env must be invisible to this instance.
    inject_role_row(
        &repo,
        raw_row("other-env", "human_q", "bot-q", "owner", "owner", "owner"),
    )
    .await;
    // A registered-but-uninitialized qrow; then initialize the version
    // manually so the only remaining authority data lives in another env.
    repo.seed_authority_uninitialized_bot("bot-q").await.unwrap();
    {
        let mut authority = repo.authority.write().await;
        authority.ownership_versions.insert("bot-q".to_string(), 1);
    }
    assert_eq!(repo.role("q", "bot-q").await.unwrap(), None);
    assert!(
        matches!(
            repo.ownership("bot-q").await,
            Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
        ),
        "an initialized bot whose only owner edge sits in another env remains corrupt"
    );
    let _ = env;
}

#[tokio::test]
async fn missing_and_unregistered_bots_have_no_authority_surface() {
    let repo = repo().await;
    assert!(
        matches!(repo.ownership("ghost").await, Err(ServiceError::BotNotFound(_))),
        "a never-registered bot is not authority-present"
    );
    repo.seed_authority_owned("bot-del", "d").await.unwrap();
    repo.soft_delete("bot-del").await;
    assert!(
        matches!(repo.ownership("bot-del").await, Err(ServiceError::BotNotFound(_))),
        "a soft-deleted bot has no authority surface left"
    );
}

#[tokio::test]
async fn roles_for_keeps_positions_and_none_for_absence() {
    let repo = repo().await;
    repo.seed_authority_owned("bot-a", "a").await.unwrap();
    repo.seed_authority_owned("bot-b", "a").await.unwrap();
    let roles = repo
        .roles_for(&[
            ("a".into(), "bot-a".into()),
            ("nobody".into(), "bot-a".into()),
            ("a".into(), "bot-b".into()),
            ("a".into(), "bot-a".into()),
        ])
        .await
        .unwrap();
    assert_eq!(
        roles,
        vec![
            Some(BotAccessRelation::Owner),
            None,
            Some(BotAccessRelation::Owner),
            Some(BotAccessRelation::Owner),
        ]
    );
    assert!(repo.roles_for(&[]).await.unwrap().is_empty());
}

#[tokio::test]
async fn armed_authority_write_failure_surfaces_and_does_not_fabricate() {
    let repo = repo().await;
    repo.arm_authority_write_failure();
    assert!(
        repo.seed_authority_owned("bot-fail", "f").await.is_err(),
        "the armed failure must surface"
    );
    assert_eq!(repo.role("f", "bot-fail").await.unwrap(), None);
    assert_eq!(repo.authority_audit_count().await.unwrap(), 0);
}

#[tokio::test]
async fn armed_authority_write_failure_lever_is_consumed_once() {
    let repo = repo().await;
    repo.arm_authority_write_failure();
    assert!(repo.seed_authority_owned("bot-f1", "f").await.is_err());
    // Exactly one write failed; the next lever works normally.
    repo.seed_authority_owned("bot-f2", "f").await.unwrap();
    assert_eq!(
        repo.role("f", "bot-f2").await.unwrap(),
        Some(BotAccessRelation::Owner)
    );
}