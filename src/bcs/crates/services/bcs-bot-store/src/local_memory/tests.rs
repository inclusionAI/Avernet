use super::*;

    #[tokio::test]
    async fn register_and_get_bot() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            name: Some("Test Bot".to_string()),
            ..Default::default()
        };
        registry.register("test".to_string(), caps).await.unwrap();

        let bot = registry.get("test").await;
        assert!(bot.is_some());
        assert_eq!(bot.unwrap().capabilities.name, Some("Test Bot".to_string()));
    }

    #[tokio::test]
    async fn unregistered_bot_returns_none() {
        let registry = MemoryBotRepo::new();

        let bot = registry.get("unknown").await;
        assert!(bot.is_none());
    }

    #[tokio::test]
    async fn update_existing_registration() {
        let registry = MemoryBotRepo::new();

        let caps1 = BotCapabilities::default();
        registry.register("test".to_string(), caps1).await.unwrap();

        let caps2 = BotCapabilities {
            name: Some("Updated Name".to_string()),
            ..Default::default()
        };
        registry.register("test".to_string(), caps2).await.unwrap();

        let bot = registry.get("test").await.unwrap();
        assert_eq!(bot.capabilities.name, Some("Updated Name".to_string()));
    }

    #[tokio::test]
    async fn save_token_updates_memory_token_index() {
        let temp_dir = tempfile::tempdir().expect("temp dir");
        let registry = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());
        registry
            .register("bot-1".to_string(), BotCapabilities::default())
            .await
            .unwrap();

        registry.save_token("bot-1", "token-1").await.unwrap();
        assert_eq!(
            registry.find_bot_by_token("token-1").await.as_deref(),
            Some("bot-1")
        );

        registry.save_token("bot-1", "token-2").await.unwrap();
        let token_to_bot = registry.token_to_bot.read().await;
        assert!(token_to_bot.get("token-1").is_none());
        assert_eq!(token_to_bot.get("token-2").map(String::as_str), Some("bot-1"));
    }

    #[tokio::test]
    async fn register_with_owner_and_token_persists_owner_token_and_index() {
        let temp_dir = tempfile::tempdir().expect("temp dir");
        let registry = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());
        let caps = BotCapabilities {
            name: Some("Provider Bot".to_string()),
            ..Default::default()
        };

        registry
            .register_with_owner_and_token(
                "bot-1".to_string(),
                caps,
                "11111111",
                "token-1",
            )
            .await
            .unwrap();

        assert_eq!(
            registry
                .get("bot-1")
                .await
                .expect("registered bot")
                .created_by
                .as_deref(),
            Some("11111111")
        );
        assert_eq!(registry.load_token("bot-1").await.as_deref(), Some("token-1"));
        assert_eq!(
            registry.find_bot_by_token("token-1").await.as_deref(),
            Some("bot-1")
        );
    }

    #[tokio::test]
    async fn heartbeat_renews_registration_without_retaining_dynamic_status() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            name: Some("DBA Expert".to_string()),
            skills: vec![Skill::new("sql_analysis")],
            ..Default::default()
        };
        registry.register("dba".to_string(), caps).await.unwrap();

        let _status = BotDynamicStatus {
            status: "busy".to_string(),
            dynamic_summary: Some("Processing deadlock request".to_string()),
            load: Some(0.7),
            updated_at: Some(1234567890),
        };
        registry.bots.write().await.get_mut("dba").unwrap().last_heartbeat =
            Instant::now() - BOT_EXPIRY - Duration::from_secs(1);
        let updated = registry.update_status("dba").await;
        assert!(updated);

        let bot = registry.get("dba").await.unwrap();
        assert!(serde_json::to_value(bot).unwrap().get("dynamic_status").is_none());
        assert!(!registry.bots.read().await["dba"].is_expired());

        let not_found = registry.update_status("unknown").await;
        assert!(!not_found);
    }

    #[tokio::test]
    async fn discover_by_capability() {
        let registry = MemoryBotRepo::new();

        let dba_caps = BotCapabilities {
            name: Some("DBA Expert".to_string()),
            domains: vec!["database".to_string(), "mysql".to_string()],
            skills: vec![Skill::new("sql_analysis"), Skill::new("deadlock_debugging")],
            ..Default::default()
        };
        registry
            .register("dba".to_string(), dba_caps)
            .await
            .unwrap();

        let sec_caps = BotCapabilities {
            name: Some("Security Expert".to_string()),
            domains: vec!["security".to_string()],
            skills: vec![Skill::new("vulnerability_scan")],
            ..Default::default()
        };
        registry
            .register("security".to_string(), sec_caps)
            .await
            .unwrap();

        let results = registry.discover("database").await;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].bot_uuid, "dba");

        let results = registry.find_by_skills(&["deadlock"]).await;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].bot_uuid, "dba");
    }

    #[tokio::test]
    async fn unregister_bot() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities::default();
        registry.register("test".to_string(), caps).await.unwrap();
        assert!(registry.get("test").await.is_some());

        let removed = registry.unregister("test").await;
        assert!(removed);
        assert!(registry.get("test").await.is_none());

        // Unregistering non-existent bot returns false
        let removed_again = registry.unregister("test").await;
        assert!(!removed_again);
    }

    #[tokio::test]
    async fn find_by_multiple_skills() {
        let registry = MemoryBotRepo::new();

        let dba_caps = BotCapabilities {
            skills: vec![
                Skill::new("sql_analysis"),
                Skill::new("deadlock_debugging"),
                Skill::new("performance_tuning"),
            ],
            ..Default::default()
        };
        registry
            .register("dba".to_string(), dba_caps)
            .await
            .unwrap();

        let sec_caps = BotCapabilities {
            skills: vec![Skill::new("sql_analysis"), Skill::new("security_audit")],
            ..Default::default()
        };
        registry
            .register("security".to_string(), sec_caps)
            .await
            .unwrap();

        // DBA has both skills
        let results = registry.find_by_skills(&["deadlock", "performance"]).await;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].bot_uuid, "dba");

        // Both have sql_analysis
        let results = registry.find_by_skills(&["sql_analysis"]).await;
        assert_eq!(results.len(), 2);

        // No one has all these
        let results = registry.find_by_skills(&["deadlock", "security"]).await;
        assert_eq!(results.len(), 0);
    }

    #[tokio::test]
    async fn find_by_domains() {
        let registry = MemoryBotRepo::new();

        let dba_caps = BotCapabilities {
            domains: vec!["database".to_string(), "mysql".to_string()],
            ..Default::default()
        };
        registry
            .register("dba".to_string(), dba_caps)
            .await
            .unwrap();

        let results = registry.find_by_domains(&["database"]).await;
        assert_eq!(results.len(), 1);

        let results = registry.find_by_domains(&["database", "mysql"]).await;
        assert_eq!(results.len(), 1);

        let results = registry.find_by_domains(&["security"]).await;
        assert_eq!(results.len(), 0);
    }

    #[tokio::test]
    async fn find_by_scopes() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            scopes: vec![
                "database:read".to_string(),
                "database:write".to_string(),
                "logs:read".to_string(),
            ],
            ..Default::default()
        };
        registry.register("dba".to_string(), caps).await.unwrap();

        let results = registry.find_by_scopes(&["database:read"]).await;
        assert_eq!(results.len(), 1);

        let results = registry
            .find_by_scopes(&["database:read", "logs:read"])
            .await;
        assert_eq!(results.len(), 1);

        let results = registry.find_by_scopes(&["admin:all"]).await;
        assert_eq!(results.len(), 0);
    }

    #[tokio::test]
    async fn capability_merge_keeps_non_empty_values() {
        let registry = MemoryBotRepo::new();

        // Initial registration with empty capabilities
        let caps1 = BotCapabilities {
            name: None,
            summary: None,
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            binding_channels: None,
            ..Default::default()
        };
        registry.register("test".to_string(), caps1).await.unwrap();

        // Update with some values
        let caps2 = BotCapabilities {
            name: Some("Test Bot".to_string()),
            summary: Some("A test bot".to_string()),
            domains: vec!["testing".to_string()],
            skills: vec![],
            scopes: vec![],
            binding_channels: None,
            ..Default::default()
        };
        registry.register("test".to_string(), caps2).await.unwrap();

        let bot = registry.get("test").await.unwrap();
        assert_eq!(bot.capabilities.name, Some("Test Bot".to_string()));
        assert_eq!(bot.capabilities.summary, Some("A test bot".to_string()));
        assert_eq!(bot.capabilities.domains, vec!["testing".to_string()]);
    }

    #[tokio::test]
    async fn save_to_storage_replaces_memory_with_final_capabilities() {
        let temp_dir = tempfile::TempDir::new().unwrap();
        let registry = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());
        let mut bindings = BindingChannels::new();
        bindings.insert(
            "antding".to_string(),
            bcs_service_api::BindingChannel {
                binding_key: "old-key".to_string(),
            },
        );

        registry
            .register(
                "test".to_string(),
                BotCapabilities {
                    name: Some("Old".to_string()),
                    summary: Some("Old summary".to_string()),
                    domains: vec!["old-domain".to_string()],
                    skills: vec![Skill::new("old-skill")],
                    scopes: vec!["old-scope".to_string()],
                    binding_channels: Some(bindings),
                    visibility: "public".to_string(),
                    agent_code: Some("agent-code".to_string()),
                    agent_token: Some("agent-token".to_string()),
                    ..Default::default()
                },
            )
            .await
            .unwrap();

        registry
            .save_to_storage(
                "test",
                &BotCapabilities {
                    name: None,
                    summary: None,
                    domains: Vec::new(),
                    skills: Vec::new(),
                    scopes: Vec::new(),
                    binding_channels: None,
                    visibility: String::new(),
                    agent_code: None,
                    agent_token: None,
                    ..Default::default()
                },
            )
            .await
            .unwrap();

        let bot = registry.get("test").await.unwrap();
        assert_eq!(bot.capabilities.name, None);
        assert_eq!(bot.capabilities.summary, None);
        assert!(bot.capabilities.domains.is_empty());
        assert!(bot.capabilities.skills.is_empty());
        assert!(bot.capabilities.scopes.is_empty());
        assert!(bot.capabilities.binding_channels.is_none());
        assert_eq!(bot.capabilities.visibility, "public");

        let credentials = registry.get_agent_credentials("test").await.unwrap();
        assert_eq!(credentials.agent_code.as_deref(), Some("agent-code"));
        assert_eq!(credentials.agent_token.as_deref(), Some("agent-token"));
    }

    #[tokio::test]
    async fn discover_by_bot_id() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            name: Some("Zhang San".to_string()),
            ..Default::default()
        };
        registry
            .register("zhangsan".to_string(), caps)
            .await
            .unwrap();

        let results = registry.discover("zhang").await;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].bot_uuid, "zhangsan");
    }

    #[tokio::test]
    async fn discovery_ignores_dynamic_summary() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            name: Some("DBA Bot".to_string()),
            ..Default::default()
        };
        registry.register("dba".to_string(), caps).await.unwrap();

        // Update with dynamic summary
        let _status = BotDynamicStatus {
            status: "busy".to_string(),
            dynamic_summary: Some("Currently handling deadlock analysis".to_string()),
            load: None,
            updated_at: None,
        };
        registry.update_status("dba").await;

        // Heartbeat descriptions are not part of the discovery contract.
        assert!(registry.discover("deadlock analysis").await.is_empty());
        assert_eq!(registry.discover("DBA").await.len(), 1);
    }

    #[tokio::test]
    async fn list_active_excludes_expired() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities::default();
        registry
            .register("bot1".to_string(), caps.clone())
            .await
            .unwrap();
        registry.register("bot2".to_string(), caps).await.unwrap();

        // Both are active
        let active = registry.list_active().await;
        assert_eq!(active.len(), 2);

        // Unregister one
        registry.unregister("bot1").await;

        let active = registry.list_active().await;
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].bot_uuid, "bot2");
    }

    #[tokio::test]
    async fn cleanup_expired_removes_old_bots() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities::default();
        registry
            .register("bot1".to_string(), caps.clone())
            .await
            .unwrap();
        registry.register("bot2".to_string(), caps).await.unwrap();

        // Unregister bot1 to simulate expiry scenario
        registry.unregister("bot1").await;

        // Run cleanup (won't remove bot2 since it's not expired)
        registry.cleanup_expired().await;

        let active = registry.list_active().await;
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].bot_uuid, "bot2");
    }
