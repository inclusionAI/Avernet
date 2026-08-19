//! `PermissionProfileRepoPort` conformance harness (Rule 25).
//!
//! Concrete implementations (SQLite, MySQL, in-memory …) call
//! [`run_permission_profile_repo_contract`] from a
//! `tests/conformance_permission_profile.rs` driver, passing their constructed
//! repo. The harness is generic over any `T: PermissionProfileRepoPort + ?Sized`.

use bcs_domain::edge_permission::ProfileStatus;
use bcs_service_api::port::repo::PermissionProfileRepoPort;

/// Rule 25 conformance suite for `PermissionProfileRepoPort`.
///
/// Covers `ensure_default_profile` idempotency (D12 rule 2: never overwrite or
/// bump an existing default), the wildcard-allow seed, and `upsert_revision`
/// (profile_id unchanged, `revision` / `rules_template` / `digest` bumped).
pub async fn run_permission_profile_repo_contract<T: PermissionProfileRepoPort + ?Sized>(
    repo: &T,
    env: &str,
) {
    let bot = "contract_bot:001";

    // ensure_default_profile is idempotent (D12 rule 2: no overwrite, no
    // revision bump). Calling twice must yield the same default row.
    repo.ensure_default_profile(bot, env)
        .await
        .expect("ensure_default_profile (1)");
    repo.ensure_default_profile(bot, env)
        .await
        .expect("ensure_default_profile (2) idempotent");

    let p = repo
        .get_active_default(bot, env)
        .await
        .expect("default exists after ensure");
    assert!(p.is_default, "seeded profile is_default");
    assert_eq!(p.name, "default", "seeded profile name");
    assert_eq!(p.revision, 1, "revision stays 1 (idempotent)");
    assert_eq!(p.status, ProfileStatus::Active);
    assert_eq!(p.created_by, "system", "default profile created_by = system");

    // rules_template is the wildcard-allow seed (spec §5.1.1). Stored as JSON;
    // compare against the parsed form so impls may round-trip either as text
    // or object.
    let rules_str = p.rules_template.to_string();
    assert!(
        rules_str.contains(r#""tool":"*""#),
        "wildcard-allow seed rules_template (got {rules_str})"
    );
    assert!(
        rules_str.contains(r#""effect":"allow""#),
        "wildcard-allow effect (got {rules_str})"
    );

    // upsert_revision: profile_id unchanged, revision bumped (D12 rule 2).
    // Only rules_template / revision / digest / updated_by / updated_at move;
    // bot_id/env/name/is_default/status/created_by/created_at are left as-is.
    let mut p2 = p.clone();
    p2.revision = 2;
    p2.rules_template = serde_json::json!([
        {"tool": "read", "specifier": "*", "effect": "allow"}
    ]);
    p2.digest = "new_digest".to_string();
    p2.updated_by = Some("admin".to_string());
    p2.updated_at = p.updated_at.saturating_add(1);
    repo.upsert_revision(p2).await.expect("upsert_revision");

    let p3 = repo
        .get_active_default(bot, env)
        .await
        .expect("default after upsert");
    assert_eq!(
        p3.permission_profile_id, p.permission_profile_id,
        "profile_id unchanged (D12 rule 2)"
    );
    assert_eq!(p3.revision, 2, "revision bumped to 2");
    assert_eq!(p3.digest, "new_digest", "digest bumped");
    assert_eq!(
        p3.updated_by.as_deref(),
        Some("admin"),
        "updated_by bump visible"
    );
    // Untouched columns.
    assert!(p3.is_default, "is_default untouched");
    assert_eq!(p3.status, ProfileStatus::Active, "status untouched");
    assert_eq!(p3.bot_id, bot, "bot_id untouched");
    assert_eq!(p3.env, env, "env untouched");
    assert_eq!(p3.name, "default", "name untouched");
    assert_eq!(p3.created_by, "system", "created_by untouched");
    assert_eq!(p3.created_at, p.created_at, "created_at untouched");

    // Missing default for an unknown bot → None.
    assert!(
        repo.get_active_default("contract_bot:unknown", env).await.is_none(),
        "unknown bot has no default"
    );
}