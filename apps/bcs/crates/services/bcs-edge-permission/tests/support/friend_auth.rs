//! SQLite fixture for the friend-auth trigger integration tests.
use std::sync::Arc;
use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::{
    DbBotActorConfigStore, DbEdgeGrantStore, DbPermissionProfileStore, DbPermissionRequestStore,
};
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort, PermissionRequestRepoPort,
};

pub async fn assemble() -> (
    Arc<dyn EdgeGrantRepoPort>,
    Arc<dyn PermissionProfileRepoPort>,
    Arc<dyn PermissionRequestRepoPort>,
    Arc<dyn BotActorConfigRepoPort>,
    Arc<LocalSqliteDbPlugin>,
) {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));

    db.execute(DbStatement::new(
        "CREATE TABLE edge_grants (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            env VARCHAR(32) NOT NULL, \
            from_id VARCHAR(128) NOT NULL, \
            to_id VARCHAR(128) NOT NULL, \
            grant_kind VARCHAR(32) NOT NULL, \
            grant_ref_id BIGINT NOT NULL, \
            rules TEXT, \
            status VARCHAR(16) NOT NULL DEFAULT 'approved', \
            originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any', \
            originator_policy_data TEXT, \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            UNIQUE (from_id, to_id, env, grant_ref_id))",
    ))
    .await
    .expect("create edge_grants");

    db.execute(DbStatement::new(
        "CREATE TABLE permission_profiles (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            bot_id VARCHAR(128) NOT NULL, \
            env VARCHAR(32) NOT NULL, \
            name VARCHAR(128) NOT NULL DEFAULT 'default', \
            description VARCHAR(512), \
            rules_template TEXT NOT NULL, \
            revision INTEGER NOT NULL DEFAULT 1, \
            digest VARCHAR(128) NOT NULL, \
            is_default INTEGER NOT NULL DEFAULT 0, \
            status VARCHAR(16) NOT NULL DEFAULT 'active', \
            created_by VARCHAR(128) NOT NULL, \
            updated_by VARCHAR(128), \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .expect("create permission_profiles");

    db.execute(DbStatement::new(
        "CREATE TABLE permission_requests (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            request_id VARCHAR(64) NOT NULL, \
            edge_id BIGINT, \
            env VARCHAR(32) NOT NULL, \
            from_id VARCHAR(128) NOT NULL, \
            to_id VARCHAR(128) NOT NULL, \
            request_kind VARCHAR(32) NOT NULL, \
            requested_ref_id BIGINT, \
            requested_rules TEXT, \
            message TEXT, \
            status VARCHAR(16) NOT NULL DEFAULT 'pending', \
            decision_reason TEXT, \
            created_by VARCHAR(128) NOT NULL, \
            decided_by VARCHAR(128), \
            decided_at TEXT, \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .expect("create permission_requests");

    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (\
            bot_uuid TEXT NOT NULL, \
            env TEXT NOT NULL, \
            name TEXT NOT NULL DEFAULT '', \
            visibility TEXT NOT NULL DEFAULT 'public', \
            user_visibility TEXT NOT NULL DEFAULT 'protected', \
            friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL', \
            bot_info TEXT DEFAULT NULL, \
            friend_ext TEXT DEFAULT NULL, \
            status TEXT NOT NULL DEFAULT 'online', \
            created_by TEXT, \
            is_deleted INTEGER NOT NULL DEFAULT 0, \
            PRIMARY KEY (bot_uuid, env))",
    ))
    .await
    .expect("create bcs_bots");

    let db_ref = db.clone();
    let edge_grants: Arc<dyn EdgeGrantRepoPort> = Arc::new(DbEdgeGrantStore::sqlite(db.clone()));
    let profiles: Arc<dyn PermissionProfileRepoPort> =
        Arc::new(DbPermissionProfileStore::sqlite(db.clone()));
    let requests: Arc<dyn PermissionRequestRepoPort> =
        Arc::new(DbPermissionRequestStore::sqlite(db.clone()));
    let bot_config: Arc<dyn BotActorConfigRepoPort> =
        Arc::new(DbBotActorConfigStore::sqlite(db.clone()));

    (edge_grants, profiles, requests, bot_config, db_ref)
}

pub async fn seed_bot(db: &LocalSqliteDbPlugin, bot_id: &str, strategy: &str) {
    let bot_info = serde_json::json!({"friend_check_in_strategy": strategy, "friend_ext": {}});
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, env, visibility, user_visibility, friend_check_in_strategy, bot_info, created_by) VALUES (?, 'dev', 'protected', 'protected', ?, ?, '85020')",
        vec![DbValue::from(bot_id), DbValue::from(strategy), DbValue::from(bot_info.to_string())],
    )).await.expect("seed bot");
}
