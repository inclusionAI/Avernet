//! Durable cutover: legacy data never gates startup or becomes a request/list fallback.
mod helpers;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use serde_json::json;

// Public resolver entry name, not a credential.
const TEST_SIGNING_KEY_NAME: &str = "friend-cutover-test";

#[tokio::test]
async fn durable_cutover_ignores_old_requests_and_uses_edge_friendships_after_restart() {
    // This test binary has one case; install its test-only secret before startup.
    unsafe {
        std::env::set_var("BCS_SECRET_FRIEND_CUTOVER_TEST", "test-only-friend-cutover-signing-key");
    }
    let dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&dir.path().into());
    let db_path = dir.path().join("friend-cutover.sqlite");
    config.database.sqlite.path = db_path.to_str().unwrap().into();
    config.auth.allow_mock_headers = true;
    config.secret.provider = "env".into();
    config.gateway_principal.signing_key_secret = Some(TEST_SIGNING_KEY_NAME.into());
    config.group_session_ws.signing_key_secret = TEST_SIGNING_KEY_NAME.into();
    config.session_files.backend.insert(
        "data_dir".into(), toml::Value::String(dir.path().join("files").to_str().unwrap().into()),
    );
    let db = LocalSqliteDbPlugin::new_file(db_path.to_str().unwrap()).unwrap();
    bcs::migrations::run_sqlite_migrations(&db).await.unwrap();
    db.execute(DbStatement::new(
        "INSERT INTO bcs_friend_requests (request_id, from_bot, to_bot, status, env) \
         VALUES ('legacy-pending', 'legacyA', 'legacyB', 'pending', 'dev')"
    )).await.unwrap();
    let server = bcs::BcsServer::new_with_storage(config.clone()).await
        .expect("unconverted legacy requests must not prevent startup");
    let (addr, task, state) = server.run_on_random_port_with_state().await.unwrap();
    struct Stop(tokio::task::AbortHandle);
    impl Drop for Stop {
        fn drop(&mut self) { self.0.abort(); }
    }
    let stop = Stop(task.abort_handle());
    let http = reqwest::Client::builder().no_proxy()
        .timeout(std::time::Duration::from_secs(10)).build().unwrap();
    let mut caller = helpers::MockBot::connect(addr).await;
    caller.register("cutover-caller", &[], addr).await;
    let mut target = helpers::MockBot::connect(addr).await;
    target.register("cutover-target", &[], addr).await;
    target.http_client(addr).set_visibility(&target.bot_id, "protected").await.unwrap();
    db.execute(DbStatement::with_params(
        "UPDATE bcs_friend_requests SET from_bot = ?, to_bot = ? WHERE request_id = 'legacy-pending'",
        vec![DbValue::from(caller.bot_id.clone()), DbValue::from(target.bot_id.clone())],
    )).await.unwrap();
    let history = db.query(DbStatement::new(
        "SELECT version, checksum FROM bcs_schema_migrations ORDER BY version"
    )).await.unwrap();
    assert_eq!(history.last().unwrap().get_i64("version").unwrap(), Some(44));
    assert!(state.connect_service.get_request("legacy-pending").await.is_err());
    for action in ["accept", "reject"] {
        let response = http.post(format!("http://{addr}/friends/requests/legacy-pending/{action}"))
            .bearer_auth(&target.token).send().await.unwrap();
        assert_eq!(response.status(), reqwest::StatusCode::NOT_FOUND);
    }
    let rows = db.query(DbStatement::new("SELECT request_id FROM permission_requests")).await.unwrap();
    assert!(rows.is_empty(), "startup must not import legacy requests");

    // A legacy pending request between this pair cannot block a new request.
    let response = http.post(format!("http://{addr}/friends/request"))
        .bearer_auth(&caller.token).json(&json!({ "to_bot": target.bot_id }))
        .send().await.unwrap();
    assert_eq!(response.status(), reqwest::StatusCode::CREATED);
    let body: serde_json::Value = response.json().await.unwrap();
    let request_id = body["data"]["id"].as_str().unwrap();
    assert_ne!(request_id, "legacy-pending");
    let response = http.post(format!("http://{addr}/friends/requests/{request_id}/accept"))
        .bearer_auth(&target.token).send().await.unwrap();
    assert_eq!(response.status(), reqwest::StatusCode::OK);
    // Remove compatibility mirrors. The list must still report the active edge.
    db.execute(DbStatement::new("DELETE FROM bcs_friendships")).await.unwrap();
    db.execute(DbStatement::new("DELETE FROM bcs_actor_relations WHERE is_creator = 0")).await.unwrap();
    let list_url = format!("http://{addr}/bots/{}/friends", caller.bot_id);
    let response = http.get(&list_url).bearer_auth(&caller.token).send().await.unwrap();
    assert_eq!(response.status(), reqwest::StatusCode::OK);
    let entries: serde_json::Value = response.json().await.unwrap();
    assert_eq!(entries["data"].as_array().unwrap().len(), 1);
    assert_eq!(entries["data"][0]["bot_uuid"], target.bot_id);
    state.connect_service.revoke_friend(&caller.bot_id, &target.bot_id, None).await.unwrap();
    // A stale legacy mirror must not resurrect an edge-revoked friendship.
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_friendships (left_bot, right_bot, env) VALUES (?, ?, 'dev')",
        vec![DbValue::from(caller.bot_id.clone()), DbValue::from(target.bot_id.clone())],
    )).await.unwrap();
    let response = http.get(&list_url).bearer_auth(&caller.token).send().await.unwrap();
    assert_eq!(response.status(), reqwest::StatusCode::OK);
    let entries: serde_json::Value = response.json().await.unwrap();
    assert!(entries["data"].as_array().unwrap().is_empty());
    drop(caller);
    drop(target);
    drop(stop);
    let _ = task.await;
    drop(state);

    // Restart neither imports the old request nor resets the new decision.
    let server = bcs::BcsServer::new_with_storage(config).await.unwrap();
    let (_addr, task, state) = server.run_on_random_port_with_state().await.unwrap();
    let _stop = Stop(task.abort_handle());
    assert!(state.connect_service.get_request("legacy-pending").await.is_err());
    assert_eq!(state.connect_service.get_request(request_id).await.unwrap().status,
        bcs_domain::edge_permission::RequestStatus::Approved);
    let after = db.query(DbStatement::new(
        "SELECT version, checksum FROM bcs_schema_migrations ORDER BY version"
    )).await.unwrap();
    assert_eq!(after, history);
    let old = db.query(DbStatement::new("SELECT status FROM bcs_friend_requests WHERE request_id = 'legacy-pending'"))
        .await.unwrap();
    assert_eq!(old[0].get_string("status").unwrap().as_deref(), Some("pending"));
}
