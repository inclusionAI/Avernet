use super::{Cli, Commands, ErrorKind, Parser, apply_group_participant_tags};

#[test]
fn test_create_group_accepts_manager_instead_of_driver() {
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
fn test_create_group_rejects_driver_and_manager_together() {
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
fn test_create_group_requires_driver_or_manager() {
    let error =
        match Cli::try_parse_from(["bcs-cli", "create-group", "--participants", "worker-1"]) {
            Ok(_) => panic!("expected create-group to require a lead bot"),
            Err(error) => error,
        };

    assert_eq!(error.kind(), ErrorKind::MissingRequiredArgument);
}

#[test]
fn test_create_group_rejects_empty_participant_tag_components() {
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
fn test_add_member_accepts_only_group_and_bot_uuid() {
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
fn test_add_member_rejects_role_argument() {
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
fn test_leave_command_is_not_available() {
    let err = match Cli::try_parse_from(["bcs-cli", "leave"]) {
        Ok(_) => panic!("expected leave command to be unavailable"),
        Err(err) => err,
    };

    assert_eq!(err.kind(), ErrorKind::InvalidSubcommand);
}

#[test]
fn test_chat_command_timeout_ms_unset_by_default() {
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
fn test_chat_command_defaults_to_after_last_tool_call_response_mode() {
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
fn test_discover_command_accepts_organization_scope() {
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
fn test_discover_command_accepts_repeated_skill_filters() {
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
            assert_eq!(skills, vec!["code_review".to_string(), "sql".to_string()]);
        }
        _ => panic!("expected discover command"),
    }
}

#[test]
fn test_discover_command_rejects_legacy_plural_skills_filter() {
    let error = Cli::try_parse_from(["bcs-cli", "discover", "--skills", "sql"])
        .err()
        .expect("legacy --skills must be rejected");

    assert_eq!(error.kind(), ErrorKind::UnknownArgument);
}

#[test]
fn test_chat_command_accepts_organization_code() {
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
        Commands::Chat {
            organization_code, ..
        } => {
            assert_eq!(organization_code.as_deref(), Some("promo-2026"));
        }
        _ => panic!("expected chat command"),
    }
}

#[test]
fn test_chat_command_accepts_detach_flag() {
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
fn test_chat_command_accepts_explicit_timeout_ms() {
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
fn test_chat_command_accepts_repeated_tags() {
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
fn test_chat_command_accepts_after_last_tool_call_response_mode() {
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
fn test_chat_command_rejects_zero_timeout_ms() {
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
fn test_chat_command_rejects_timeout_ms_over_limit() {
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
