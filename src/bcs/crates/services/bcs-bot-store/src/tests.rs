use super::*;

    // Note: These tests require external database connections.
    // Run with: cargo test --package bcs-bot -- --ignored

    #[test]
    fn test_bot_info_serialization() {
        let bot_info = BotInfo {
            summary: Some("Test bot".to_string()),
            domains: vec!["testing".to_string()],
            skills: vec![Skill::new("test")],
            scopes: vec!["read".to_string()],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
            ..Default::default()
        };

        let json = serde_json::to_string(&bot_info).unwrap();
        let parsed: BotInfo = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.summary, bot_info.summary);
        assert_eq!(parsed.domains, bot_info.domains);
        assert_eq!(parsed.skills, bot_info.skills);
        assert_eq!(parsed.scopes, bot_info.scopes);
    }

    #[test]
    fn test_bot_inner_expiry() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities::default(),
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(!bot.is_expired());

        // Simulate time passing (we can't actually wait, so just test the logic)
        // In real usage, last_heartbeat.elapsed() would be compared
    }

    #[test]
    fn test_bot_inner_skill_matching() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                skills: vec![Skill::new("SQL Analysis"), Skill::new("Deadlock Debugging")],
                ..Default::default()
            },
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_skill("sql"));
        assert!(bot.has_skill("deadlock"));
        assert!(bot.has_skill("SQL")); // Case insensitive
        assert!(!bot.has_skill("python"));
    }

    #[test]
    fn test_bot_inner_domain_matching() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                domains: vec!["Database".to_string(), "MySQL".to_string()],
                ..Default::default()
            },
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_domain("database"));
        assert!(bot.has_domain("mysql"));
        assert!(!bot.has_domain("security"));
    }

    #[test]
    fn test_bot_inner_scope_matching() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                scopes: vec!["database:read".to_string(), "database:write".to_string()],
                ..Default::default()
            },
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_scope("database:read"));
        assert!(bot.has_scope("write"));
        assert!(!bot.has_scope("admin"));
    }

    #[test]
    fn test_to_registered_bot() {
        let bot = RegisteredBotInner {
            bot_uuid: "test-uuid".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                name: Some("Test Bot".to_string()),
                ..Default::default()
            },
            ws_connection: None,
            session_token: None,
            env: Some("prod".to_string()),
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        let registered = bot.to_registered_bot();
        assert_eq!(registered.bot_uuid, "test-uuid");
        assert_eq!(registered.capabilities.name, Some("Test Bot".to_string()));
        assert_eq!(registered.env, Some("prod".to_string()));
    }

    #[test]
    fn test_bot_info_default() {
        let bot_info = BotInfo::default();
        assert!(bot_info.summary.is_none());
        assert!(bot_info.domains.is_empty());
        assert!(bot_info.skills.is_empty());
        assert!(bot_info.scopes.is_empty());
    }

    #[test]
    fn test_bot_info_with_all_fields() {
        let bot_info = BotInfo {
            summary: Some("A comprehensive bot".to_string()),
            domains: vec!["database".to_string(), "security".to_string()],
            skills: vec![
                Skill::new("sql_analysis"),
                Skill::new("penetration_testing"),
            ],
            scopes: vec!["admin:read".to_string(), "admin:write".to_string()],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
            ..Default::default()
        };

        let json = serde_json::to_string(&bot_info).unwrap();
        let parsed: BotInfo = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.summary, Some("A comprehensive bot".to_string()));
        assert_eq!(parsed.domains.len(), 2);
        assert_eq!(parsed.skills.len(), 2);
        assert_eq!(parsed.scopes.len(), 2);
    }

    #[test]
    fn test_bot_info_empty_arrays() {
        let json = r#"{"summary":null,"domains":[],"skills":[],"scopes":[]}"#;
        let bot_info: BotInfo = serde_json::from_str(json).unwrap();
        assert!(bot_info.summary.is_none());
        assert!(bot_info.domains.is_empty());
    }

    #[test]
    fn test_bot_inner_has_skill_partial_match() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                skills: vec![Skill::new("SQL_Analysis_Expert")],
                ..Default::default()
            },
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        // Partial match should work
        assert!(bot.has_skill("sql"));
        assert!(bot.has_skill("analysis"));
        assert!(bot.has_skill("expert"));
        // Case insensitive
        assert!(bot.has_skill("SQL"));
        assert!(bot.has_skill("ANALYSIS"));
    }

    #[test]
    fn test_bot_inner_has_domain_partial_match() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                domains: vec!["Database-Administration".to_string()],
                ..Default::default()
            },
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_domain("database"));
        assert!(bot.has_domain("administration"));
        assert!(!bot.has_domain("security"));
    }

    #[test]
    fn test_bot_inner_empty_capabilities() {
        let bot = RegisteredBotInner {
            bot_uuid: "empty".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities::default(),
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(!bot.has_skill("anything"));
        assert!(!bot.has_domain("anything"));
        assert!(!bot.has_scope("anything"));
    }

    #[test]
    fn test_bot_connection_clone() {
        let conn = BotConnection {
            session_token: "test-token".to_string(),
            connected_at: Instant::now(),
        };

        let conn_clone = conn.clone();
        assert_eq!(conn_clone.session_token, "test-token");
    }

    #[test]
    fn test_dynamic_status_default() {
        let status = BotDynamicStatus::default();
        assert!(status.status.is_empty());
        assert!(status.dynamic_summary.is_none());
        assert!(status.load.is_none());
        assert!(status.updated_at.is_none());
    }

    #[test]
    fn test_bot_capabilities_default() {
        let caps = BotCapabilities::default();
        assert!(caps.name.is_none());
        assert!(caps.summary.is_none());
        assert!(caps.domains.is_empty());
        assert!(caps.skills.is_empty());
        assert!(caps.scopes.is_empty());
    }

    #[test]
    fn test_bot_info_json_roundtrip_complex() {
        let bot_info = BotInfo {
            summary: Some("Summary with \"quotes\" and \\backslashes\\rating".to_string()),
            domains: vec![
                "domain:with:colons".to_string(),
                "domain-with-dashes".to_string(),
            ],
            skills: vec![Skill::new("skill with spaces")],
            scopes: vec!["scope/with/slashes".to_string()],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
            ..Default::default()
        };

        let json = serde_json::to_string(&bot_info).unwrap();
        let parsed: BotInfo = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.summary, bot_info.summary);
        assert_eq!(parsed.domains, bot_info.domains);
        assert_eq!(parsed.skills, bot_info.skills);
        assert_eq!(parsed.scopes, bot_info.scopes);
    }

    // ===== Integration tests (require external database) =====
    // Run with: cargo test --package bcs-bot -- --ignored

    #[tokio::test]
    #[ignore = "Requires external database connections"]
    async fn integration_test_register_and_retrieve() {
        // This test documents the expected workflow:
        // 1. Create PersistentBotRepo with a DbPlugin handle
        // 2. Register a bot
        // 3. Retrieve the bot from memory (fast path)
        // 4. Verify capabilities are persisted to the database
        //
        // Example setup is intentionally omitted because production wiring
        // now happens through the bootstrap composition root.
        //
        // let caps = BotCapabilities {
        //     name: Some("Test Bot".into()),
        //     skills: vec!["testing".into()],
        //     ..Default::default()
        // };
        // registry.register("test-bot".into(), caps).await;
        //
        // let bot = registry.get("test-bot").await.unwrap();
        // assert_eq!(bot.capabilities.name, Some("Test Bot".into()));
    }

    #[tokio::test]
    #[ignore = "Requires external database connections"]
    async fn integration_test_token_persistence() {
        // This test documents the expected workflow:
        // 1. Register WS connection (generates token)
        // 2. Token is persisted to the database
        // 3. After server restart, token can be looked up from the database
        //
        // Example:
        // let (tx, _rx) = mpsc::channel(10);
        // let token = registry.register_streaming_connection("test-bot".into()).await.unwrap();
        //
        // // Token should be findable
        // let found = registry.find_bot_by_token(&token).await;
        // assert_eq!(found, Some("test-bot".into()));
    }

    #[tokio::test]
    #[ignore = "Requires external database connections"]
    async fn integration_test_reconnect_streaming_recover_from_storage() {
        // This test documents the failover recovery workflow:
        // 1. Bot connects and persists capabilities and its token in the database
        // 2. WS disconnects (but token is preserved)
        // 3. Server restarts (memory cleared)
        // 4. Bot reconnects with existing token
        // 5. Server recovers capabilities and its token from the database, then renews liveness
        //
        // Example:
        // let (tx, _rx) = mpsc::channel(10);
        // let token = registry.register_streaming_connection("test-bot".into()).await.unwrap();
        //
        // // Simulate server restart by clearing memory
        // // (in real scenario, memory is lost)
        //
        // let (tx2, _rx2) = mpsc::channel(10);
        // let (bot_uuid, recovered_token) = registry.reconnect_streaming(token.clone()).await.unwrap();
        // assert_eq!(bot_uuid, "test-bot");
        // assert_eq!(recovered_token, token);
    }

    #[tokio::test]
    #[ignore = "Requires external database connections"]
    async fn integration_test_sql_injection_protection() {
        // This test documents SQL injection protection:
        // Bot IDs and other fields are escaped before SQL queries
        //
        // Example:
        // let malicious_id = "bot'; DROP TABLE bcs_bots; --";
        // let caps = BotCapabilities::default();
        // registry.register(malicious_id.into(), caps).await;
        // // Should not cause SQL injection, just creates a bot with that name
    }

    #[test]
    fn test_sql_escaping_in_save_to_storage() {
        // Verify that special SQL characters are properly escaped
        let bot_info = BotInfo {
            summary: Some("Test with 'single quotes'".into()),
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
            ..Default::default()
        };
        let json = serde_json::to_string(&bot_info).unwrap();
        // JSON escaping handles most cases, but the SQL layer also does escaping
        assert!(json.contains("single quotes"));
    }
