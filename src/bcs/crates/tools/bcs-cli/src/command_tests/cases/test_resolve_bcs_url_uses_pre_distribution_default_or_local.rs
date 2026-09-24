//! cases implementation.
use super::*;


    #[test]
    #[serial]
    pub(super) fn test_resolve_bcs_url_uses_pre_distribution_default_or_local() {
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_url = std::env::var("MOLTIS_BCS_URL").ok();
        let original_base_url = std::env::var("BCS_API_BASE_URL").ok();
        let original_agentclaw_env = std::env::var("AGENTCLAW_ENV").ok();

        safe_remove_var("BOT_DATA_DIR");
        safe_remove_var("MOLTIS_BCS_URL");
        safe_remove_var("BCS_API_BASE_URL");
        safe_set_var("AGENTCLAW_ENV", "pre");

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



    #[test]
    pub(super) fn test_classify_auth_error_message_timeout() {
        assert_eq!(
            classify_auth_error_message("OAuth2 authorization timed out locally after 2 minutes"),
            "auth_timeout"
        );
    }



    #[test]
    pub(super) fn test_classify_auth_error_message_expired() {
        assert_eq!(
            classify_auth_error_message(
                "OAuth2 authorization expired on server. Please rerun the command."
            ),
            "auth_expired"
        );
    }



    #[test]
    pub(super) fn test_classify_auth_error_message_request_failed() {
        assert_eq!(
            classify_auth_error_message("Invalid bots list response"),
            "request_failed"
        );
    }



    #[tokio::test]
    pub(super) async fn test_detect_network_env_localhost() {
        let env = detect_network_env("http://localhost:21000", false).await;
        assert_eq!(env, NetworkEnv::Prod);
    }



    #[tokio::test]
    pub(super) async fn test_detect_network_env_127() {
        let env = detect_network_env("http://127.0.0.1:21000", false).await;
        assert_eq!(env, NetworkEnv::Prod);
    }



    #[test]
    pub(super) fn test_is_localhost_url() {
        assert!(is_localhost_url("http://localhost:21000"));
        assert!(is_localhost_url("https://127.0.0.1:8080"));
        assert!(is_localhost_url("http://127.0.0.1"));
        assert!(!is_localhost_url("https://bcs.example.com"));
        assert!(!is_localhost_url("http://10.0.0.1:21000"));
    }



    #[test]
    pub(super) fn test_leave_command_is_not_available() {
        let err = match Cli::try_parse_from(["bcs-cli", "leave"]) {
            Ok(_) => panic!("expected leave command to be unavailable"),
            Err(err) => err,
        };

        assert_eq!(err.kind(), ErrorKind::InvalidSubcommand);
    }



    #[test]
    pub(super) fn test_channel_bind_command_parses_defaults() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "channel",
            "bind",
            "--account",
            "robot_1",
            "--target-id",
            "group_1",
            "--robot-code",
            "robot_1",
            "--client-id",
            "client_id",
            "--client-secret",
            "secret",
        ])
        .unwrap();

        match cli.command {
            Commands::Channel {
                command:
                    ChannelCommands::Bind {
                        account,
                        target_kind,
                        target_id,
                        group_chat_scope,
                        visibility,
                        env,
                        send_mode,
                        message_type,
                        ..
                    },
                ..
            } => {
                assert_eq!(account, "robot_1");
                assert_eq!(target_kind, "group");
                assert_eq!(target_id, "group_1");
                assert_eq!(group_chat_scope, None);
                assert_eq!(visibility, "lead_only");
                assert_eq!(env, "dev");
                assert_eq!(send_mode, "normal");
                assert_eq!(message_type, "markdown");
            }
            _ => panic!("expected channel bind command"),
        }
    }



    #[test]
    pub(super) fn test_channel_bind_payload_builds_group_dingtalk_normal() {
        let command = ChannelCommands::Bind {
            account: "robot_1".to_string(),
            target_kind: "group".to_string(),
            target_id: "group_1".to_string(),
            group_chat_scope: Some("conversation_shared".to_string()),
            visibility: "full_transcript".to_string(),
            env: "pre".to_string(),
            robot_code: "robot_1".to_string(),
            client_id: "client_id".to_string(),
            client_secret: "secret".to_string(),
            send_mode: "normal".to_string(),
            card_template_id: None,
            message_type: "text".to_string(),
        };

        let payload = build_channel_bind_payload(&command).unwrap();

        assert_eq!(
            payload,
            json!({
                "channel_type": "ding_talk",
                "account_ref": "robot_1",
                "target": { "group": { "group_id": "group_1" } },
                "group_chat_scope": "conversation_shared",
                "outbound_visibility": "full_transcript",
                "env": "pre",
                "config": {
                    "channel_type": "ding_talk",
                    "robot_code": "robot_1",
                    "client_id": "client_id",
                    "client_secret": "secret",
                    "send_mode": {
                        "mode": "normal",
                        "message_type": "text"
                    }
                }
            })
        );
    }



    #[test]
    pub(super) fn test_channel_bind_payload_builds_bot_streaming_card() {
        let command = ChannelCommands::Bind {
            account: "robot_1".to_string(),
            target_kind: "bot".to_string(),
            target_id: "bot_1".to_string(),
            group_chat_scope: Some("per_sender".to_string()),
            visibility: "lead_only".to_string(),
            env: "dev".to_string(),
            robot_code: "robot_1".to_string(),
            client_id: "client_id".to_string(),
            client_secret: "secret".to_string(),
            send_mode: "streaming_card".to_string(),
            card_template_id: Some("card_tpl".to_string()),
            message_type: "markdown".to_string(),
        };

        let payload = build_channel_bind_payload(&command).unwrap();

        assert_eq!(payload["target"], json!({ "bot": { "bot_id": "bot_1" } }));
        assert_eq!(payload["group_chat_scope"], "per_sender");
        assert_eq!(
            payload["config"]["send_mode"],
            json!({
                "mode": "streaming_card",
                "card_template_id": "card_tpl",
                "fallback_message_type": "markdown"
            })
        );
    }



    #[test]
    pub(super) fn test_channel_bind_payload_requires_card_template_for_streaming_card() {
        let command = ChannelCommands::Bind {
            account: "robot_1".to_string(),
            target_kind: "group".to_string(),
            target_id: "group_1".to_string(),
            group_chat_scope: None,
            visibility: "lead_only".to_string(),
            env: "dev".to_string(),
            robot_code: "robot_1".to_string(),
            client_id: "client_id".to_string(),
            client_secret: "secret".to_string(),
            send_mode: "streaming_card".to_string(),
            card_template_id: None,
            message_type: "markdown".to_string(),
        };

        let err = build_channel_bind_payload(&command).unwrap_err();
        assert!(err.to_string().contains("card-template-id"));
    }



    #[test]
    pub(super) fn test_channel_bind_debug_payload_redacts_client_secret() {
        let command = ChannelCommands::Bind {
            account: "robot_1".to_string(),
            target_kind: "group".to_string(),
            target_id: "group_1".to_string(),
            group_chat_scope: None,
            visibility: "lead_only".to_string(),
            env: "dev".to_string(),
            robot_code: "robot_1".to_string(),
            client_id: "client_id".to_string(),
            client_secret: "secret".to_string(),
            send_mode: "normal".to_string(),
            card_template_id: None,
            message_type: "markdown".to_string(),
        };
        let payload = build_channel_bind_payload(&command).unwrap();

        let redacted = redact_channel_bind_debug_payload(&payload);

        assert_eq!(payload["config"]["client_secret"], "secret");
        assert_eq!(redacted["config"]["client_secret"], "<redacted>");
    }



    #[test]
    pub(super) fn test_channel_list_command_parses() {
        let cli = Cli::try_parse_from(["bcs-cli", "channel", "list"]).unwrap();

        match cli.command {
            Commands::Channel {
                command: ChannelCommands::List,
                ..
            } => {}
            _ => panic!("expected channel list command"),
        }
    }



    #[test]
    pub(super) fn test_channel_conversation_id_command_parses_session() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "channel",
            "conversation-id",
            "--session",
            "group_1:session_1",
        ])
        .unwrap();

        match cli.command {
            Commands::Channel {
                command: ChannelCommands::ConversationId { session },
                ..
            } => {
                assert_eq!(session, "group_1:session_1");
            }
            _ => panic!("expected channel conversation-id command"),
        }
    }



    #[test]
    pub(super) fn test_channel_unbind_command_parses_id() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "channel",
            "unbind",
            "--id",
            "binding_1",
        ])
        .unwrap();

        match cli.command {
            Commands::Channel {
                command: ChannelCommands::Unbind { id },
                ..
            } => {
                assert_eq!(id, "binding_1");
            }
            _ => panic!("expected channel unbind command"),
        }
    }



    #[test]
    pub(super) fn test_chat_command_timeout_ms_unset_by_default() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat {
                timeout_ms, detach, ..
            } => {
                assert_eq!(timeout_ms, None, "timeout_ms should be unset by default");
                assert!(!detach, "detach should default to false");
            }
            _ => panic!("expected chat command"),
        }
    }



    #[test]
    pub(super) fn test_chat_command_defaults_to_after_last_tool_call_response_mode() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { response_mode, .. } => {
                assert_eq!(response_mode.as_deref(), Some("after-last-tool-call"));
            }
            _ => panic!("expected chat command"),
        }
    }



    #[test]
    pub(super) fn test_discover_command_accepts_organization_scope() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "discover",
            "--organization-code",
            "promo-2026",
            "--role",
            "traffic_analyst",
        ])
        .unwrap();

        match cli.command {
            Commands::Discover {
                organization_code,
                role,
                ..
            } => {
                assert_eq!(organization_code.as_deref(), Some("promo-2026"));
                assert_eq!(role.as_deref(), Some("traffic_analyst"));
            }
            _ => panic!("expected discover command"),
        }
    }



    #[test]
    pub(super) fn test_discover_command_accepts_repeated_skill_filters() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "discover",
            "-q",
            "deployment",
            "--skill",
            "code_review",
            "--skill",
            "sql",
        ])
        .unwrap();

        match cli.command {
            Commands::Discover { query, skills, .. } => {
                assert_eq!(query.as_deref(), Some("deployment"));
                assert_eq!(
                    skills,
                    vec!["code_review".to_string(), "sql".to_string()]
                );
            }
            _ => panic!("expected discover command"),
        }
    }



    #[test]
    pub(super) fn test_discover_command_rejects_legacy_plural_skills_filter() {
        let error = Cli::try_parse_from(["bcs-cli", "discover", "--skills", "sql"])
            .err()
            .expect("legacy --skills must be rejected");

        assert_eq!(error.kind(), ErrorKind::UnknownArgument);
    }



    #[test]
    pub(super) fn test_chat_command_accepts_organization_code() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-b",
            "--message",
            "hello",
            "--organization-code",
            "promo-2026",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { organization_code, .. } => {
                assert_eq!(organization_code.as_deref(), Some("promo-2026"));
            }
            _ => panic!("expected chat command"),
        }
    }



    #[test]
    pub(super) fn test_chat_command_accepts_detach_flag() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--detach",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { detach, .. } => assert!(detach),
            _ => panic!("expected chat command"),
        }
    }



    #[test]
    pub(super) fn test_chat_command_accepts_explicit_timeout_ms() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--timeout-ms",
            "1200",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { timeout_ms, .. } => assert_eq!(timeout_ms, Some(1_200)),
            _ => panic!("expected chat command"),
        }
    }



    #[test]
    pub(super) fn test_chat_command_accepts_repeated_tags() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--tag",
            "tag1",
            "--tag",
            "tag2",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { tags, .. } => assert_eq!(tags, vec!["tag1", "tag2"]),
            _ => panic!("expected chat command"),
        }
    }



    #[test]
    pub(super) fn test_chat_command_accepts_after_last_tool_call_response_mode() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--response-mode",
            "after-last-tool-call",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { response_mode, .. } => {
                assert_eq!(response_mode.as_deref(), Some("after-last-tool-call"));
            }
            _ => panic!("expected chat command"),
        }
    }



    #[test]
    pub(super) fn test_chat_command_rejects_zero_timeout_ms() {
        let err = match Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--timeout-ms",
            "0",
        ]) {
            Ok(_) => panic!("expected parse failure"),
            Err(err) => err,
        };

        assert_eq!(err.kind(), ErrorKind::ValueValidation);
        let rendered = err.to_string();
        assert!(rendered.contains("--timeout-ms"));
        assert!(rendered.contains("86400000"));
    }



    #[test]
    pub(super) fn test_chat_command_rejects_timeout_ms_over_limit() {
        let err = match Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--timeout-ms",
            "86400001",
        ]) {
            Ok(_) => panic!("expected parse failure"),
            Err(err) => err,
        };

        assert_eq!(err.kind(), ErrorKind::ValueValidation);
        let rendered = err.to_string();
        assert!(rendered.contains("--timeout-ms"));
        assert!(rendered.contains("86400000"));
    }



    // ------------------------------------------------------------------
    // session subcommand parse tests
    // ------------------------------------------------------------------

    #[test]
    pub(super) fn test_session_create_parses_minimal_args() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "create",
            "--group",
            "g-1",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::Create {
                        group,
                        title,
                        kind,
                        input,
                        meta,
                        group_context_delivery,
                    },
                ..
            } => {
                assert_eq!(group, "g-1");
                assert!(title.is_none());
                assert!(kind.is_none());
                assert!(input.is_none());
                assert!(meta.is_none());
                assert!(group_context_delivery.is_none());
            }
            _ => panic!("expected session create command"),
        }
    }



    #[test]
    pub(super) fn test_session_create_accepts_group_context_delivery() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "create",
            "--group",
            "g-1",
            "--group-context-delivery",
            "inject",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::Create {
                        group_context_delivery,
                        ..
                    },
                ..
            } => {
                assert_eq!(group_context_delivery.as_deref(), Some("inject"));
            }
            _ => panic!("expected session create command"),
        }
    }



    #[test]
    pub(super) fn test_session_create_rejects_invalid_group_context_delivery() {
        let err = match Cli::try_parse_from([
            "bcs-cli",
            "session",
            "create",
            "--group",
            "g-1",
            "--group-context-delivery",
            "silent",
        ]) {
            Ok(_) => panic!("expected invalid delivery mode to be rejected"),
            Err(err) => err,
        };

        assert_eq!(err.kind(), ErrorKind::InvalidValue);
        assert!(err.to_string().contains("send"));
        assert!(err.to_string().contains("inject"));
    }



    #[test]
    pub(super) fn test_session_list_accepts_filters() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "list",
            "--group",
            "g-1",
            "--status",
            "running",
            "-q",
            "tag",
            "--participant",
            "bot-a",
            "--offset",
            "10",
            "--limit",
            "50",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::List {
                        group,
                        status,
                        q,
                        participant,
                        offset,
                        limit,
                    },
                ..
            } => {
                assert_eq!(group, "g-1");
                assert_eq!(status.as_deref(), Some("running"));
                assert_eq!(q.as_deref(), Some("tag"));
                assert_eq!(participant.as_deref(), Some("bot-a"));
                assert_eq!(offset, Some(10));
                assert_eq!(limit, Some(50));
            }
            _ => panic!("expected session list command"),
        }
    }



    #[test]
    pub(super) fn test_session_get_requires_sid() {
        // Missing positional sid should fail.
        let err = match Cli::try_parse_from(["bcs-cli", "session", "get"]) {
            Ok(_) => panic!("expected parse failure"),
            Err(err) => err,
        };
        assert_eq!(err.kind(), ErrorKind::MissingRequiredArgument);

        // Provided sid round-trips.
        let cli =
            Cli::try_parse_from(["bcs-cli", "session", "get", "g-1:abcdef01"]).unwrap();
        match cli.command {
            Commands::Session {
                command: SessionCommands::Get { session },
                ..
            } => assert_eq!(session, "g-1:abcdef01"),
            _ => panic!("expected session get command"),
        }
    }



    #[test]
    pub(super) fn test_session_chat_round_trips() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "chat",
            "--session",
            "g-1:00000001",
            "--message",
            "hello",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command: SessionCommands::Chat { session, message },
                ..
            } => {
                assert_eq!(session, "g-1:00000001");
                assert_eq!(message, "hello");
            }
            _ => panic!("expected session chat command"),
        }
    }



    #[test]
    pub(super) fn test_session_messages_accepts_view_bot_and_limit() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "messages",
            "g-1:abcdef01",
            "--view-bot",
            "bot-a",
            "--limit",
            "100",
            "--before",
            "1700000000000",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::Messages {
                        session,
                        view_bot,
                        limit,
                        before,
                    },
                ..
            } => {
                assert_eq!(session, "g-1:abcdef01");
                assert_eq!(view_bot.as_deref(), Some("bot-a"));
                assert_eq!(limit, Some(100));
                assert_eq!(before, Some(1_700_000_000_000));
            }
            _ => panic!("expected session messages command"),
        }
    }
