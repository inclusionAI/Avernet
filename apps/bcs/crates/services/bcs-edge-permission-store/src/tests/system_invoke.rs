use super::*;

fn system_grant(from: &str, to: &str) -> EdgeGrant {
    EdgeGrant {
        grant_kind: GrantKind::SystemInvoke,
        originator_policy_type: OriginatorPolicyType::SameAsFrom,
        ..default_grant(from, to, "dev", 0)
    }
}

#[tokio::test]
async fn checked_read_errors_for_query_failure_not_empty_success() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    let store = DbEdgeGrantStore::sqlite(db);
    assert!(store.list_runtime_grants_checked("a", "b", "dev").await.is_err());
    // The old infallible compatibility contract is deliberately unchanged.
    assert!(store.list_active_grants("a", "b", "dev").await.is_empty());
}

#[tokio::test]
async fn checked_read_never_returns_partial_success_for_damaged_runtime_rows() {
    for update in [
        "grant_kind = 'unrecognized'", "grant_ref_id = 9", "rules = ''",
        "rules = 'null'", "rules = 'not-json'", "originator_policy_type = 'any'",
        "originator_policy_data = ''", "originator_policy_data = 'null'",
    ] {
        let store = sqlite_store().await;
        store.insert_grant(default_grant("a", "b", "dev", 99)).await.unwrap();
        let id = store.insert_grant(system_grant("a", "b")).await.unwrap();
        store.db.execute(DbStatement::with_params(
            format!("UPDATE edge_grants SET {update} WHERE id = ?"), vec![id.into()],
        )).await.unwrap();
        assert!(store.list_runtime_grants_checked("a", "b", "dev").await.is_err(), "{update}");
        // Damage to another pair must not cause errors or partial access here.
        assert!(store.list_runtime_grants_checked("c", "b", "dev").await.unwrap().is_empty());
    }
}

#[tokio::test]
async fn shape_error_does_not_write_and_checked_read_excludes_revoked_rows() {
    let store = sqlite_store().await;
    let mut invalid = system_grant("a", "b");
    invalid.from_id = "human_1".into();
    assert!(store.insert_grant(invalid).await.is_err());
    assert!(store.list_runtime_grants_checked("human_1", "b", "dev").await.unwrap().is_empty());
    let id = store.insert_grant(system_grant("a", "b")).await.unwrap();
    store.revoke_grant(id, "dev").await.unwrap();
    // Even a damaged historical row cannot authorize or break current reads.
    store.db.execute(DbStatement::with_params(
        "UPDATE edge_grants SET grant_kind = 'old-unknown' WHERE id = ?", vec![id.into()],
    )).await.unwrap();
    assert!(store.list_runtime_grants_checked("a", "b", "dev").await.unwrap().is_empty());
}

#[test]
fn new_and_legacy_db_strings_are_exact_and_unknowns_fail() {
    for (kind, text) in [
        (GrantKind::PermissionProfile, "permission_profile"), (GrantKind::Rules, "rules"),
        (GrantKind::Owner, "owner"), (GrantKind::Manager, "manager"),
        (GrantKind::SystemInvoke, "system_invoke"),
    ] {
        assert_eq!(grant_kind_str(kind), text);
        assert_eq!(parse_grant_kind(text).unwrap(), kind);
    }
    for (kind, text) in [
        (RequestKind::Connect, "connect"), (RequestKind::PermissionProfile, "permission_profile"),
        (RequestKind::Rules, "rules"), (RequestKind::Revoke, "revoke"),
        (RequestKind::SystemInvoke, "system_invoke"),
    ] {
        assert_eq!(request_kind_str(kind), text);
        assert_eq!(parse_request_kind(text).unwrap(), kind);
    }
    assert!(parse_grant_kind("SystemInvoke").is_err());
    assert!(parse_request_kind("system").is_err());
}

#[tokio::test]
async fn checked_identity_comparison_stays_exact_on_case_insensitive_columns() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new(
        "CREATE TABLE edge_grants (id INTEGER PRIMARY KEY, \
         env TEXT COLLATE NOCASE NOT NULL, from_id TEXT COLLATE NOCASE NOT NULL, \
         to_id TEXT COLLATE NOCASE NOT NULL, grant_kind TEXT COLLATE NOCASE NOT NULL, \
         grant_ref_id INTEGER NOT NULL, rules TEXT, status TEXT NOT NULL, \
         originator_policy_type TEXT NOT NULL, originator_policy_data TEXT)"
    )).await.unwrap();
    db.execute(DbStatement::new(
        "INSERT INTO edge_grants VALUES (1, 'dev', 'bot-a', 'bot-b', \
         'system_invoke', 0, NULL, 'approved', 'same_as_from', NULL)"
    )).await.unwrap();
    let store = DbEdgeGrantStore::sqlite(db.clone());
    assert_eq!(store.list_runtime_grants_checked("bot-a", "bot-b", "dev").await.unwrap().len(), 1);
    for (from, to, env) in [("BOT-A", "bot-b", "dev"), ("bot-a", "BOT-B", "dev"),
        ("bot-a", "bot-b", "DEV"), ("bot-a ", "bot-b", "dev")]
    {
        assert!(store.list_runtime_grants_checked(from, to, env).await.unwrap().is_empty());
    }
    db.execute(DbStatement::new("UPDATE edge_grants SET grant_kind = 'Owner'")).await.unwrap();
    assert!(store.list_runtime_grants_checked("bot-a", "bot-b", "dev").await.is_err(),
        "case-insensitive SQL must not hide an unknown kind as authority metadata");
}
