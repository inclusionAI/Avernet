//! cases implementation.
use super::*;


    // Helper to safely set env var
    #[allow(unsafe_code)]
    pub(super) fn safe_set_var(key: &str, value: impl AsRef<std::ffi::OsStr>) {
        unsafe {
            std::env::set_var(key, value);
        }
    }



    // Helper to safely remove env var
    #[allow(unsafe_code)]
    pub(super) fn safe_remove_var(key: &str) {
        unsafe {
            std::env::remove_var(key);
        }
    }



    pub(super) fn write_session_file(temp_dir: &TempDir, value: serde_json::Value) {
        let session_dir = temp_dir.path().join(".bcs");
        std::fs::create_dir_all(&session_dir).unwrap();
        let session_file = session_dir.join("session.json");
        let mut file = std::fs::File::create(session_file).unwrap();
        file.write_all(serde_json::to_string_pretty(&value).unwrap().as_bytes())
            .unwrap();
    }



    #[test]
    pub(super) fn test_create_group_accepts_manager_instead_of_driver() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "create-group",
            "--manager",
            "manager-bot",
            "--participants",
            "worker-1,worker-2",
        ])
        .unwrap();

        match cli.command {
            Commands::CreateGroup {
                driver,
                manager,
                participants,
                ..
            } => {
                assert!(driver.is_none());
                assert_eq!(manager.as_deref(), Some("manager-bot"));
                assert_eq!(participants, "worker-1,worker-2");
            }
            _ => panic!("expected create-group command"),
        }
    }



    #[test]
    pub(super) fn test_create_group_rejects_driver_and_manager_together() {
        let error = match Cli::try_parse_from([
            "bcs-cli",
            "create-group",
            "--driver",
            "driver-bot",
            "--manager",
            "manager-bot",
            "--participants",
            "worker-1",
        ]) {
            Ok(_) => panic!("expected --driver and --manager to conflict"),
            Err(error) => error,
        };

        assert_eq!(error.kind(), ErrorKind::ArgumentConflict);
    }



    #[test]
    pub(super) fn test_create_group_requires_driver_or_manager() {
        let error = match Cli::try_parse_from([
            "bcs-cli",
            "create-group",
            "--participants",
            "worker-1",
        ]) {
            Ok(_) => panic!("expected create-group to require a lead bot"),
            Err(error) => error,
        };

        assert_eq!(error.kind(), ErrorKind::MissingRequiredArgument);
    }



    #[test]
    pub(super) fn test_create_group_rejects_empty_participant_tag_components() {
        let participant = || bcs_protocol::ParticipantInfo {
            bot_uuid: "driver-bot".to_string(),
            role: None,
            tags: Vec::new(),
            message_view_scope: None,
        };

        for value in ["=tenant-a", "driver-bot="] {
            let mut participants = vec![participant()];
            let error = apply_group_participant_tags(
                &mut participants,
                "driver-bot",
                None,
                &[value.to_string()],
            )
            .expect_err("empty participant tag components must be rejected");

            assert!(error.to_string().contains("must not be empty"));
        }
    }



    #[test]
    pub(super) fn test_add_member_accepts_only_group_and_bot_uuid() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "add-member",
            "--group",
            "group-1",
            "--bot-uuid",
            "worker-1",
        ])
        .unwrap();

        match cli.command {
            Commands::AddMember {
                group, bot_uuid, ..
            } => {
                assert_eq!(group, "group-1");
                assert_eq!(bot_uuid, "worker-1");
            }
            _ => panic!("expected add-member command"),
        }
    }



    #[test]
    pub(super) fn test_add_member_rejects_role_argument() {
        let result = Cli::try_parse_from([
            "bcs-cli",
            "add-member",
            "--group",
            "group-1",
            "--bot-uuid",
            "worker-1",
            "--role",
            "worker",
        ]);

        let error = match result {
            Ok(_) => panic!("group add-member must not expose role selection"),
            Err(error) => error,
        };

        assert_eq!(error.kind(), ErrorKind::UnknownArgument);
    }



    #[test]
    pub(super) fn collaboration_validate_command_parses_yaml_path() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "collaboration",
            "validate",
            "/tmp/workflow.yaml",
        ])
        .unwrap();

        match cli.command {
            Commands::Collaboration {
                token: None,
                command: CollaborationCommands::Validate { file },
            } => assert_eq!(file, PathBuf::from("/tmp/workflow.yaml")),
            _ => panic!("expected collaboration validate command"),
        }
    }



    #[test]
    pub(super) fn collaborate_permission_alias_parses_current_session() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "collaborate",
            "permission",
            "--session",
            "group-1:abc12345",
        ])
        .unwrap();

        match cli.command {
            Commands::Collaboration {
                token: None,
                command: CollaborationCommands::Permission { session },
            } => assert_eq!(session, "group-1:abc12345"),
            _ => panic!("expected collaborate permission command"),
        }
    }



    #[test]
    pub(super) fn collaborate_run_alias_parses_yaml_session_bindings_and_input() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "collaborate",
            "--token",
            "test-token",
            "run",
            "/tmp/workflow.yaml",
            "--session",
            "group-1:abc12345",
            "--binding",
            "planner=bot-driver",
            "--binding",
            "writer=20260412_abc:100005",
            "--input",
            r#"{"question":"resolve it"}"#,
        ])
        .unwrap();

        match cli.command {
            Commands::Collaboration {
                token: Some(token),
                command:
                    CollaborationCommands::Run {
                        file,
                        session,
                        bindings,
                        input,
                        panel_component: None,
                        panel_params: None,
                        panel_tab_id: None,
                        panel_tab_title: None,
                        panel_tab_closable: None,
                    },
            } => {
                assert_eq!(token, "test-token");
                assert_eq!(file, PathBuf::from("/tmp/workflow.yaml"));
                assert_eq!(session, "group-1:abc12345");
                assert_eq!(
                    bindings,
                    vec!["planner=bot-driver", "writer=20260412_abc:100005"]
                );
                assert_eq!(input, r#"{"question":"resolve it"}"#);
            }
            _ => panic!("expected collaborate run command"),
        }
    }



    #[test]
    pub(super) fn collaborate_run_parses_panel_arguments() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "collaborate",
            "run",
            "/tmp/workflow.yaml",
            "--session",
            "group-1:abc12345",
            "--binding",
            "writer=bot-writer",
            "--panel-component",
            "partnerPanel.OneShotRunView",
            "--panel-params",
            r#"{"scene":"release","runId":"{{bcs.run_id}}"}"#,
            "--panel-tab-id",
            "one-shot-{{bcs.run_id}}",
            "--panel-tab-title",
            "一次性协作",
            "--panel-tab-closable",
            "true",
        ])
        .unwrap();

        match cli.command {
            Commands::Collaboration {
                command:
                    CollaborationCommands::Run {
                        panel_component,
                        panel_params,
                        panel_tab_id,
                        panel_tab_title,
                        panel_tab_closable,
                        ..
                    },
                ..
            } => {
                assert_eq!(
                    panel_component.as_deref(),
                    Some("partnerPanel.OneShotRunView")
                );
                assert_eq!(
                    panel_params.as_deref(),
                    Some(r#"{"scene":"release","runId":"{{bcs.run_id}}"}"#)
                );
                assert_eq!(panel_tab_id.as_deref(), Some("one-shot-{{bcs.run_id}}"));
                assert_eq!(panel_tab_title.as_deref(), Some("一次性协作"));
                assert_eq!(panel_tab_closable, Some(true));
            }
            _ => panic!("expected collaborate run command"),
        }
    }



    #[test]
    pub(super) fn collaborate_run_requires_panel_component_for_dependent_arguments() {
        let error = Cli::try_parse_from([
            "bcs-cli",
            "collaborate",
            "run",
            "/tmp/workflow.yaml",
            "--session",
            "group-1:abc12345",
            "--binding",
            "writer=bot-writer",
            "--panel-params",
            "{}",
        ])
        .err()
        .expect("panel params without a component must be rejected");
        assert!(error.to_string().contains("--panel-component"));
    }



    #[test]
    pub(super) fn panel_opening_message_requires_object_params() {
        let error = build_panel_opening_message(
            Some("partnerPanel.OneShotRunView".to_string()),
            Some("[]".to_string()),
            None,
            None,
            None,
        )
        .expect_err("array params must be rejected");
        assert!(error.to_string().contains("must be a JSON object"));
    }



    #[test]
    pub(super) fn collaboration_create_command_parses_repeated_bindings() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "collaboration",
            "--token",
            "test-token",
            "create",
            "/tmp/workflow.yaml",
            "--driver",
            "bot-driver",
            "--binding",
            "planner=bot-driver",
            "--binding",
            "writer=20260412_abc:100005",
        ])
        .unwrap();

        match cli.command {
            Commands::Collaboration {
                token: Some(token),
                command:
                    CollaborationCommands::Create {
                        file,
                        driver,
                        bindings,
                        ..
                    },
            } => {
                assert_eq!(token, "test-token");
                assert_eq!(file, PathBuf::from("/tmp/workflow.yaml"));
                assert_eq!(driver, "bot-driver");
                assert_eq!(
                    bindings,
                    vec!["planner=bot-driver", "writer=20260412_abc:100005"]
                );
            }
            _ => panic!("expected collaboration create command"),
        }
    }



    #[test]
    pub(super) fn custom_group_binding_parser_preserves_bot_uuid_colons() {
        let bindings = parse_custom_group_bindings(&[
            "planner=bot-driver".to_string(),
            "writer=20260412_abc:100005".to_string(),
        ])
        .unwrap();

        assert_eq!(bindings["planner"].bot_ids, vec!["bot-driver"]);
        assert_eq!(bindings["writer"].bot_ids, vec!["20260412_abc:100005"]);
    }



    #[test]
    pub(super) fn custom_group_binding_validation_requires_assigned_slots_and_driver() {
        let validation = serde_json::json!({
            "participants": [
                {"binding": "planner", "required": true, "assigned": true},
                {"binding": "writer", "required": false, "assigned": true}
            ]
        });
        let bindings = parse_custom_group_bindings(&[
            "planner=bot-driver".to_string(),
            "writer=bot-writer".to_string(),
        ])
        .unwrap();
        validate_custom_group_bindings(&bindings, &validation, "bot-driver").unwrap();

        let missing = parse_custom_group_bindings(&["planner=bot-driver".to_string()]).unwrap();
        let error = validate_custom_group_bindings(&missing, &validation, "bot-driver")
            .unwrap_err();
        assert!(error.to_string().contains("writer"));
    }



    #[test]
    pub(super) fn test_structured_result_ok_serialization() {
        let result = StructuredResult {
            status: "ok".to_string(),
            message: None,
            network_env: None,
            auth_url: None,
            timeout_secs: None,
            log_file: Some("/tmp/bcs.log".to_string()),
        };
        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains("\"status\":\"ok\""));
        assert!(json.contains("\"log_file\":"));
    }



    #[test]
    pub(super) fn test_structured_result_serialization() {
        let result = StructuredResult {
            status: "auth_timeout".to_string(),
            message: Some("timeout".to_string()),
            network_env: Some("office".to_string()),
            auth_url: None,
            timeout_secs: Some(120),
            log_file: Some("/tmp/bcs.log".to_string()),
        };
        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains("\"status\":\"auth_timeout\""));
        assert!(json.contains("\"network_env\":\"office\""));
        assert!(json.contains("\"timeout_secs\":120"));
    }



    #[test]
    pub(super) fn test_normalize_bcs_api_url_from_ws_endpoint() {
        assert_eq!(
            normalize_bcs_api_url("ws://localhost:21000/ws/bot").as_deref(),
            Some("http://localhost:21000")
        );
        assert_eq!(
            normalize_bcs_api_url("wss://bcs-pre.example.com/ws/bot").as_deref(),
            Some("https://bcs-pre.example.com")
        );
    }



    #[test]
    pub(super) fn test_normalize_bcs_api_url_keeps_http_base() {
        assert_eq!(
            normalize_bcs_api_url("https://bcs.example.com/").as_deref(),
            Some("https://bcs.example.com")
        );
    }



    #[test]
    pub(super) fn test_normalize_bcs_ws_url_from_http_base() {
        assert_eq!(
            normalize_bcs_ws_url("http://localhost:21000").as_deref(),
            Some("ws://localhost:21000/ws/bot")
        );
    }



    #[test]
    pub(super) fn test_normalize_bcs_ws_url_from_https_base() {
        assert_eq!(
            normalize_bcs_ws_url("https://bcs.example.com").as_deref(),
            Some("wss://bcs.example.com/ws/bot")
        );
    }



    #[test]
    pub(super) fn test_normalize_bcs_ws_url_keeps_existing_ws_endpoints() {
        assert_eq!(
            normalize_bcs_ws_url("ws://localhost:21000/ws/bot").as_deref(),
            Some("ws://localhost:21000/ws/bot")
        );
        assert_eq!(
            normalize_bcs_ws_url("wss://bcs.example.com/ws/bot").as_deref(),
            Some("wss://bcs.example.com/ws/bot")
        );
    }



    /// Test token discovery priority: CLI arg > env var > session file
    #[test]
    pub(super) fn test_token_discovery_priority_cli_first() {
        // CLI arg should take highest priority
        let explicit_token = "explicit-token-123";
        let result = discover_token(Some(explicit_token));
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), explicit_token);
    }



    /// Test token discovery from BCN_BOT_TOKEN environment variable
    #[test]
    #[serial]
    pub(super) fn test_token_discovery_from_env_var() {
        let temp_dir = TempDir::new().unwrap();
        let data_dir = temp_dir.path().to_path_buf();

        // Save original env
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_token = std::env::var("BCN_BOT_TOKEN").ok();

        // Set test env vars
        safe_set_var("BOT_DATA_DIR", &data_dir);
        safe_set_var("BCN_BOT_TOKEN", "env-token-456");

        // No explicit token, should use env var
        let result = discover_token(None);

        // Restore env
        if let Some(orig) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", orig);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(orig) = original_token {
            safe_set_var("BCN_BOT_TOKEN", orig);
        } else {
            safe_remove_var("BCN_BOT_TOKEN");
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "env-token-456");
    }



    /// Test token discovery from session file
    #[test]
    #[serial]
    pub(super) fn test_token_discovery_from_session_file() {
        let temp_dir = TempDir::new().unwrap();
        let data_dir = temp_dir.path().to_path_buf();
        let bcs_dir = data_dir.join(".bcs");
        std::fs::create_dir_all(&bcs_dir).unwrap();

        // Write session file
        let session_file = bcs_dir.join("session.json");
        let session_content = json!({
            "bot_uuid": "bot-test-789",
            "token": "file-token-789",
            "bcs_url": "ws://localhost:21000/ws/bot"
        });
        let mut file = std::fs::File::create(&session_file).unwrap();
        file.write_all(
            serde_json::to_string_pretty(&session_content)
                .unwrap()
                .as_bytes(),
        )
        .unwrap();

        // Save original env
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_token = std::env::var("BCN_BOT_TOKEN").ok();

        // Set test data dir, clear BCN_BOT_TOKEN
        safe_set_var("BOT_DATA_DIR", &data_dir);
        safe_remove_var("BCN_BOT_TOKEN");

        // No explicit token, no env var, should use session file
        let result = discover_token(None);

        // Restore env
        if let Some(orig) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", orig);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(orig) = original_token {
            safe_set_var("BCN_BOT_TOKEN", orig);
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "file-token-789");
    }



    /// Test token discovery priority: CLI arg overrides env var
    #[test]
    #[serial]
    pub(super) fn test_token_cli_overrides_env() {
        let temp_dir = TempDir::new().unwrap();
        let data_dir = temp_dir.path().to_path_buf();

        // Save original env
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_token = std::env::var("BCN_BOT_TOKEN").ok();

        safe_set_var("BOT_DATA_DIR", &data_dir);
        safe_set_var("BCN_BOT_TOKEN", "env-token");

        // Explicit token should override env var
        let result = discover_token(Some("cli-token"));

        // Restore env
        if let Some(orig) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", orig);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(orig) = original_token {
            safe_set_var("BCN_BOT_TOKEN", orig);
        } else {
            safe_remove_var("BCN_BOT_TOKEN");
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "cli-token");
    }



    /// Test token discovery priority: env var overrides session file
    #[test]
    #[serial]
    pub(super) fn test_token_env_overrides_file() {
        let temp_dir = TempDir::new().unwrap();
        let data_dir = temp_dir.path().to_path_buf();
        let bcs_dir = data_dir.join(".bcs");
        std::fs::create_dir_all(&bcs_dir).unwrap();

        // Write session file
        let session_file = bcs_dir.join("session.json");
        let session_content = json!({
            "bot_id": "bot-test",
            "token": "file-token",
            "bcs_url": "ws://localhost:21000/ws/bot"
        });
        let mut file = std::fs::File::create(&session_file).unwrap();
        file.write_all(
            serde_json::to_string_pretty(&session_content)
                .unwrap()
                .as_bytes(),
        )
        .unwrap();

        // Save original env
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_token = std::env::var("BCN_BOT_TOKEN").ok();

        safe_set_var("BOT_DATA_DIR", &data_dir);
        safe_set_var("BCN_BOT_TOKEN", "env-token");

        // Env var should override session file
        let result = discover_token(None);

        // Restore env
        if let Some(orig) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", orig);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(orig) = original_token {
            safe_set_var("BCN_BOT_TOKEN", orig);
        } else {
            safe_remove_var("BCN_BOT_TOKEN");
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "env-token");
    }



    /// Test token discovery returns an empty token when no source is available,
    /// allowing the CLI to proceed without authentication.
    #[test]
    #[serial]
    pub(super) fn test_token_not_found_returns_empty() {
        let temp_dir = TempDir::new().unwrap();
        let data_dir = temp_dir.path().to_path_buf();

        // Save original env
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_token = std::env::var("BCN_BOT_TOKEN").ok();

        // Set empty environment (no token anywhere)
        safe_set_var("BOT_DATA_DIR", &data_dir);
        safe_remove_var("BCN_BOT_TOKEN");

        // Should succeed with an empty token (no auth)
        let result = discover_token(None);

        // Restore env
        if let Some(orig) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", orig);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(orig) = original_token {
            safe_set_var("BCN_BOT_TOKEN", orig);
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "");
    }



    #[test]
    #[serial]
    pub(super) fn test_resolve_bcs_url_from_session_file() {
        let temp_dir = TempDir::new().unwrap();
        write_session_file(
            &temp_dir,
            json!({
                "bot_uuid": null,
                "token": "session-token",
                "bcs_url": "ws://localhost:21000/ws/bot"
            }),
        );

        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_url = std::env::var("MOLTIS_BCS_URL").ok();
        let original_base_url = std::env::var("BCS_API_BASE_URL").ok();

        safe_set_var("BOT_DATA_DIR", temp_dir.path());
        safe_remove_var("MOLTIS_BCS_URL");
        safe_remove_var("BCS_API_BASE_URL");

        let cli = Cli {
            url: None,
            cookie: None,
            log_level: "info".to_string(),
            json: false,
            no_json: true,
            #[cfg(debug_assertions)]
            debug: false,
            command: Commands::Health,
        };
        let resolved = resolve_bcs_url(&cli).unwrap();

        if let Some(value) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", value);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(value) = original_url {
            safe_set_var("MOLTIS_BCS_URL", value);
        } else {
            safe_remove_var("MOLTIS_BCS_URL");
        }
        if let Some(value) = original_base_url {
            safe_set_var("BCS_API_BASE_URL", value);
        } else {
            safe_remove_var("BCS_API_BASE_URL");
        }

        assert_eq!(resolved, "http://localhost:21000");
    }



    #[test]
    #[serial]
    pub(super) fn test_resolve_bcs_url_uses_distribution_default_or_local() {
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_url = std::env::var("MOLTIS_BCS_URL").ok();
        let original_base_url = std::env::var("BCS_API_BASE_URL").ok();
        let original_agentclaw_env = std::env::var("AGENTCLAW_ENV").ok();

        safe_remove_var("BOT_DATA_DIR");
        safe_remove_var("MOLTIS_BCS_URL");
        safe_remove_var("BCS_API_BASE_URL");
        safe_remove_var("AGENTCLAW_ENV");

        let cli = Cli {
            url: None,
            cookie: None,
            log_level: "info".to_string(),
            json: false,
            no_json: true,
            #[cfg(debug_assertions)]
            debug: false,
            command: Commands::Health,
        };
        let result = resolve_bcs_url(&cli);
        let expected = bcs_cli::resolve_compiled_distribution_default_url()
            .unwrap()
            .unwrap_or_else(|| "http://127.0.0.1:21000".to_string());

        if let Some(value) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", value);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(value) = original_url {
            safe_set_var("MOLTIS_BCS_URL", value);
        } else {
            safe_remove_var("MOLTIS_BCS_URL");
        }
        if let Some(value) = original_base_url {
            safe_set_var("BCS_API_BASE_URL", value);
        } else {
            safe_remove_var("BCS_API_BASE_URL");
        }
        if let Some(value) = original_agentclaw_env {
            safe_set_var("AGENTCLAW_ENV", value);
        } else {
            safe_remove_var("AGENTCLAW_ENV");
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), expected);
    }
