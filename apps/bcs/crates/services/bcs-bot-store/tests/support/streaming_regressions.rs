use super::*;

use bcs_test_support::capture_request_logs;

#[tokio::test]
async fn coverage_legacy_owner_listing_filters_namespaces_environment_and_sensitive_credentials() {
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), bcs_db_api::DbSqlFlavor::Sqlite);
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::new("ALTER TABLE bcs_bots ADD COLUMN gmt_create TEXT")).await.unwrap();
    for (id, owner, status, actor, deleted, row_env) in [
        ("owned", Some("staff"), "hidden", "bot", 0, env.as_str()),
        ("default:staff", None, "online", "bot", 0, env.as_str()),
        ("20261113_abcdefgh:staff", None, "online", "bot", 0, env.as_str()),
        ("untrusted:staff", None, "online", "bot", 0, env.as_str()),
        ("other-owner", Some("other"), "online", "bot", 0, env.as_str()),
        ("deleted", Some("staff"), "online", "bot", 1, env.as_str()),
        ("human_staff", Some("staff"), "online", "human", 0, env.as_str()),
        ("other-env", Some("staff"), "online", "bot", 0, "different-env"),
    ] {
        let bot_info = serde_json::json!({
            "summary": "summary",
            "agent_code": uuid::Uuid::new_v4().to_string(),
            "agent_token": uuid::Uuid::new_v4().to_string(),
        }).to_string();
        db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, created_by, status, actor_kind, is_deleted, env, bot_info, session_token, name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            vec![id.into(), owner.map(Value::from).unwrap_or(Value::Null), status.into(), actor.into(), (deleted as i64).into(), row_env.into(),
                bot_info.into(), uuid::Uuid::new_v4().to_string().into(), "Display name".into()])).await.unwrap();
    }
    let bots = repo.list_legacy_bots_for_owner("staff", &env).await.unwrap();
    let mut ids = bots.iter().map(|b| b.bot_uuid.as_str()).collect::<Vec<_>>(); ids.sort_unstable();
    assert_eq!(ids, ["20261113_abcdefgh:staff", "default:staff", "owned"]);
    for bot in &bots {
        assert_eq!(bot.actor_kind, ActorKind::Bot);
        assert_eq!(bot.capabilities.name.as_deref(), Some("Display name"));
        assert_eq!(bot.capabilities.summary.as_deref(), Some("summary"));
        assert!(bot.capabilities.agent_code.is_none()); assert!(bot.capabilities.agent_token.is_none());
        assert_eq!(bot.env.as_deref(), Some(env.as_str()));
    }
    assert_eq!(bots.iter().find(|b| b.bot_uuid == "owned").unwrap().status, ActorStatus::Hidden);
    assert_eq!(bots.iter().find(|b| b.bot_uuid == "owned").unwrap().capabilities.visibility, "private");
    assert!(repo.list_legacy_bots_for_owner("absent", &env).await.unwrap().is_empty());
    db.execute(DbStatement::new("DROP TABLE bcs_bots")).await.unwrap();
    assert!(matches!(repo.list_legacy_bots_for_owner("staff", &env).await, Err(ServiceError::InternalError(_))));
}

#[tokio::test]
async fn coverage_memory_streaming_requires_the_current_token_and_preserves_promoted_identity() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    let token = repo.connect_or_promote_streaming("fresh".into()).await.unwrap();
    assert!(repo.is_connected("fresh").await);
    assert!(matches!(repo.connect_or_promote_streaming("fresh".into()).await, Err(ConnectStreamError::AlreadyConnected(_))));
    assert!(repo.register_streaming_connection("fresh".into()).await.is_err());
    repo.set_protocol_version("fresh", 3).await;
    assert_eq!(repo.get_protocol_version("fresh").await, 3);
    assert_eq!(repo.get_protocol_version("missing").await, 1);
    repo.disconnect_streaming("fresh").await;
    assert!(matches!(repo.connect_or_promote_streaming("fresh".into()).await, Err(ConnectStreamError::AlreadyRegistered(_))));
    assert_eq!(repo.reconnect_streaming(token.clone()).await.unwrap(), ("fresh".into(), token.clone()));
    assert!(repo.reconnect_streaming(token).await.is_err());
    assert!(repo.reconnect_streaming("unknown".into()).await.is_err());
    assert!(repo.send_request("fresh", "chat.send", serde_json::json!({}), 1).await.unwrap_err().contains("not connected"));
    repo.resolve_pending_request("missing", serde_json::Value::Null).await;
    repo.disconnect_streaming("fresh").await;
    repo.disconnect_streaming("missing").await;
    assert!(repo.list_connected().await.is_empty());
    assert_eq!(repo.register_http_connection("http".into(), "http-token".into()).await, "http-token");
    assert_eq!(repo.find_bot_by_token("http-token").await.as_deref(), Some("http"));
    assert!(repo.register_streaming_connection("http".into()).await.is_ok());
    assert!(repo.register_streaming_connection("new-stream".into()).await.is_ok());
    repo.register_with_owner_and_token("mock".into(), BotCapabilities { name: Some("Kept name".into()), ..Default::default() }, "owner", &mock_token()).await.unwrap();
    let promoted = repo.connect_or_promote_streaming("mock".into()).await.unwrap();
    assert!(!bcs_service_api::is_mock_token(&promoted));
    assert_eq!(repo.load_token("mock").await.as_deref(), Some(promoted.as_str()));
    assert_eq!(repo.get("mock").await.unwrap().capabilities.name.as_deref(), Some("Kept name"));
    repo.disconnect_streaming("mock").await;
    assert!(repo.reconnect_streaming(promoted).await.is_ok());
}

#[tokio::test]
async fn coverage_memory_promotion_disk_failure_keeps_mock_credentials_and_disconnected_state() {
    let dir = tempfile::tempdir().unwrap();
    let base = dir.path().join("bots");
    let repo = MemoryBotRepo::with_base_dir(base.clone());
    let original = mock_token();
    repo.register_with_owner_and_token("blocked".into(), BotCapabilities::default(), "owner", &original).await.unwrap();
    // Replacing the test-owned directory with a file fails reliably even when
    // permission-based fault injection would succeed under a privileged user.
    std::fs::remove_dir_all(&base).unwrap();
    std::fs::write(&base, "blocked directory").unwrap();
    assert!(matches!(repo.connect_or_promote_streaming("blocked".into()).await, Err(ConnectStreamError::InternalError(_))));
    assert_eq!(repo.load_token("blocked").await.as_deref(), Some(original.as_str()));
    assert!(!repo.is_connected("blocked").await);
}

#[tokio::test]
async fn coverage_persistent_owner_registration_rotates_tokens_without_reassigning_ownership() {
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::new(db.clone());
    repo.register_with_owner_and_token("owned".into(), BotCapabilities::default(), "original-owner", "old-token").await.unwrap();
    let capabilities = BotCapabilities { name: Some("Updated".into()), summary: Some("Description".into()),
        domains: vec!["domain".into()], scopes: vec!["scope".into()], visibility: "public".into(),
        agent_code: Some("agent-code".into()), agent_token: Some(uuid::Uuid::new_v4().to_string()), ..Default::default() };
    repo.register_with_owner_and_token("owned".into(), capabilities, "different-owner", "new-token").await.unwrap();
    assert!(repo.find_bot_by_token("old-token").await.is_none());
    assert_eq!(repo.find_bot_by_token("new-token").await.as_deref(), Some("owned"));
    let bot = repo.get("owned").await.unwrap();
    assert_eq!(bot.created_by.as_deref(), Some("original-owner"));
    assert_eq!(bot.capabilities.name.as_deref(), Some("Updated"));
    assert_eq!(bot.capabilities.domains, ["domain"]);
    assert_eq!(bot.capabilities.scopes, ["scope"]);
    assert_eq!(repo.find_bot_by_agent_code("agent-code").await.as_deref(), Some("owned"));
    assert!(repo.find_bot_by_agent_code("").await.is_none());
    let reloaded = PersistentBotRepo::new(db);
    assert_eq!(reloaded.load_token("owned").await.as_deref(), Some("new-token"));
    assert_eq!(reloaded.get("owned").await.unwrap().created_by.as_deref(), Some("original-owner"));
}

#[tokio::test]
async fn coverage_persistent_human_backfill_is_idempotent_and_name_repair_updates_the_cache() {
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), bcs_db_api::DbSqlFlavor::Sqlite);
    assert!(repo.ensure_human_actor("staff", "Original name").await.unwrap().created);
    assert!(repo.get("human_staff").await.is_some());
    db.execute(DbStatement::new("UPDATE bcs_bots SET bot_info = '{}' WHERE bot_uuid = 'human_staff'")).await.unwrap();
    assert!(!repo.ensure_human_actor("staff", "Must not replace name").await.unwrap().created);
    assert_eq!(repo.get("human_staff").await.unwrap().capabilities.summary.as_deref(), Some("写点什么介绍自己"));
    repo.update_human_name("staff", "Repaired name").await.unwrap();
    assert_eq!(repo.get("human_staff").await.unwrap().capabilities.name.as_deref(), Some("Repaired name"));
    assert!(!repo.ensure_human_actor("staff", "Ignored").await.unwrap().created);
    let reloaded = PersistentBotRepo::new(db.clone());
    assert_eq!(reloaded.get("human_staff").await.unwrap().capabilities.name.as_deref(), Some("Repaired name"));
    db.execute(DbStatement::new("CREATE TRIGGER reject_name BEFORE UPDATE OF name ON bcs_bots BEGIN SELECT RAISE(ABORT, 'injected name failure'); END")).await.unwrap();
    assert!(repo.update_human_name("staff", "Rejected name").await.is_err());
    assert_eq!(repo.get("human_staff").await.unwrap().capabilities.name.as_deref(), Some("Repaired name"));
}

#[tokio::test]
async fn coverage_persistent_streaming_reconnects_without_allowing_tokenless_takeover() {
    let (_, rows) = capture_request_logs("streaming-regression", async {
            let db = sqlite_db().await;
            let repo = PersistentBotRepo::new(db.clone());
            let token = repo.connect_or_promote_streaming("fresh".into()).await.unwrap();
            assert!(repo.is_connected("fresh").await);
            assert!(matches!(repo.connect_or_promote_streaming("fresh".into()).await, Err(ConnectStreamError::AlreadyConnected(_))));
            assert!(repo.register_streaming_connection("fresh".into()).await.is_err());
            repo.set_protocol_version("fresh", 3).await;
            assert_eq!(repo.get_protocol_version("fresh").await, 3);
            assert_eq!(repo.get_protocol_version("unknown").await, 1);
            assert_eq!(repo.list_connected().await, ["fresh"]);
            repo.disconnect_streaming("fresh").await;
            assert!(matches!(repo.connect_or_promote_streaming("fresh".into()).await, Err(ConnectStreamError::AlreadyRegistered(_))));
            assert_eq!(repo.reconnect_streaming(token.clone()).await.unwrap(), ("fresh".into(), token.clone()));
            assert!(repo.reconnect_streaming(token).await.is_err());
            assert!(repo.reconnect_streaming("unknown-token".into()).await.is_err());
            assert!(repo.send_request("fresh", "chat.send", serde_json::json!({}), 10).await.unwrap_err().contains("not connected"), "the repository cannot fabricate WebSocket delivery");
            repo.resolve_pending_request("missing", serde_json::Value::Null).await;
            repo.disconnect_streaming("fresh").await;
            repo.disconnect_streaming("unknown").await;
            assert!(!repo.is_connected("unknown").await);
            assert!(repo.list_connected().await.is_empty());
            assert_eq!(repo.register_http_connection("http".into(), "http-fixture-token".into()).await, "http-fixture-token");
            assert_eq!(repo.find_bot_by_token("http-fixture-token").await.as_deref(), Some("http"));
            assert!(repo.register_streaming_connection("http".into()).await.is_ok());

            let capabilities = BotCapabilities { name: Some("Persisted name".into()), visibility: "protected".into(), ..Default::default() };
            repo.register_with_owner_and_token("persisted".into(), capabilities, "owner", &mock_token()).await.unwrap();
            // A new repository has no runtime cache: promotion must hydrate
            // from durable identity and persist the replacement before publish.
            let restarted = PersistentBotRepo::new(db.clone());
            let promoted = restarted.connect_or_promote_streaming("persisted".into()).await.unwrap();
            assert!(!bcs_service_api::is_mock_token(&promoted));
            let restarted_again = PersistentBotRepo::new(db);
            assert!(matches!(restarted_again.connect_or_promote_streaming("persisted".into()).await, Err(ConnectStreamError::AlreadyRegistered(_))));
            assert_eq!(restarted_again.reconnect_streaming(promoted.clone()).await.unwrap().1, promoted);
            assert_eq!(restarted_again.get("persisted").await.unwrap().capabilities.name.as_deref(), Some("Persisted name"));
    }).await;
    for branch in ["promote_mock", "already_registered", "already_connected"] {
        assert!(rows.iter().any(|row| row["fields"]["branch"] == branch), "missing {branch}");
    }
}

#[tokio::test]
async fn coverage_persistent_mock_promotion_write_failure_keeps_the_original_identity() {
    let (_, rows) = capture_request_logs("failed-streaming-regression", async {
            let db = sqlite_db().await;
            let repo = PersistentBotRepo::new(db.clone());
            let original = mock_token();
            repo.register_with_owner_and_token("blocked".into(), BotCapabilities::default(), "owner", &original).await.unwrap();
            db.execute(DbStatement::new("CREATE TRIGGER reject_token_promotion BEFORE UPDATE OF session_token ON bcs_bots BEGIN SELECT RAISE(ABORT, 'injected token write failure'); END")).await.unwrap();
            assert!(matches!(repo.connect_or_promote_streaming("blocked".into()).await, Err(ConnectStreamError::InternalError(_))));
            assert_eq!(repo.load_token("blocked").await.as_deref(), Some(original.as_str()));
            assert!(!repo.is_connected("blocked").await);
            let restarted = PersistentBotRepo::new(db);
            assert_eq!(restarted.load_token("blocked").await.as_deref(), Some(original.as_str()));
    }).await;
    assert!(rows.iter().any(|row| row["fields"]["message"].as_str().is_some_and(|message| message.contains("promote_mock DB persist failed"))));
}
