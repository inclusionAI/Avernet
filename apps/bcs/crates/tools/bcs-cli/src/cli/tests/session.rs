use super::{Cli, Commands, ErrorKind, Parser, SessionCommands, TempDir, parse_json_arg};

// ------------------------------------------------------------------
// session subcommand parse tests
// ------------------------------------------------------------------

#[test]
fn test_session_create_parses_minimal_args() {
    let cli = Cli::try_parse_from(["bcs-cli", "session", "create", "--group", "g-1"]).unwrap();

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
fn test_session_create_accepts_group_context_delivery() {
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
fn test_session_create_rejects_invalid_group_context_delivery() {
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
fn test_session_list_accepts_filters() {
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
fn test_session_get_requires_sid() {
    // Missing positional sid should fail.
    let err = match Cli::try_parse_from(["bcs-cli", "session", "get"]) {
        Ok(_) => panic!("expected parse failure"),
        Err(err) => err,
    };
    assert_eq!(err.kind(), ErrorKind::MissingRequiredArgument);

    // Provided sid round-trips.
    let cli = Cli::try_parse_from(["bcs-cli", "session", "get", "g-1:abcdef01"]).unwrap();
    match cli.command {
        Commands::Session {
            command: SessionCommands::Get { session },
            ..
        } => assert_eq!(session, "g-1:abcdef01"),
        _ => panic!("expected session get command"),
    }
}

#[test]
fn test_session_chat_round_trips() {
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
fn test_session_messages_accepts_view_bot_and_limit() {
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

#[test]
fn test_session_patch_parses_args() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "session",
        "patch",
        "g-1:aabb0011",
        "--title",
        "new title",
    ])
    .unwrap();

    match cli.command {
        Commands::Session {
            command: SessionCommands::Patch { session, title },
            ..
        } => {
            assert_eq!(session, "g-1:aabb0011");
            assert_eq!(title, "new title");
        }
        _ => panic!("expected session patch command"),
    }
}

#[test]
fn test_session_complete_parses_args() {
    // with --output and --error
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "session",
        "complete",
        "g-1:aabb0011",
        "--output",
        r#"{"summary":"ok"}"#,
        "--error",
        "timeout",
    ])
    .unwrap();

    match cli.command {
        Commands::Session {
            command:
                SessionCommands::Complete {
                    session,
                    output,
                    error,
                },
            ..
        } => {
            assert_eq!(session, "g-1:aabb0011");
            assert_eq!(output.as_deref(), Some(r#"{"summary":"ok"}"#));
            assert_eq!(error.as_deref(), Some("timeout"));
        }
        _ => panic!("expected session complete command"),
    }

    // minimal (no optional args)
    let cli2 = Cli::try_parse_from(["bcs-cli", "session", "complete", "g-1:aabb0011"]).unwrap();

    match cli2.command {
        Commands::Session {
            command: SessionCommands::Complete { output, error, .. },
            ..
        } => {
            assert!(output.is_none());
            assert!(error.is_none());
        }
        _ => panic!("expected session complete command"),
    }
}

#[test]
fn test_session_complete_output_parse_json_arg() {
    // parse_json_arg: JSON literal
    let val = parse_json_arg(r#"{"summary":"ok"}"#).unwrap();
    assert_eq!(val["summary"], "ok");

    // parse_json_arg: @file
    let dir = TempDir::new().unwrap();
    let file_path = dir.path().join("output.json");
    std::fs::write(&file_path, r#"{"result":42}"#).unwrap();
    let at_arg = format!("@{}", file_path.display());
    let val2 = parse_json_arg(&at_arg).unwrap();
    assert_eq!(val2["result"], 42);

    // parse_json_arg: bare @ is an error
    assert!(parse_json_arg("@").is_err());
}

#[test]
fn test_session_add_member_parses_args() {
    // with --role
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "session",
        "add-member",
        "g-1:aabb0011",
        "--bot-uuid",
        "bot-dba",
        "--role",
        "consultant",
    ])
    .unwrap();

    match cli.command {
        Commands::Session {
            command:
                SessionCommands::AddMember {
                    session,
                    bot_uuid,
                    role,
                },
            ..
        } => {
            assert_eq!(session, "g-1:aabb0011");
            assert_eq!(bot_uuid, "bot-dba");
            assert_eq!(role.as_deref(), Some("consultant"));
        }
        _ => panic!("expected session add-member command"),
    }

    // without --role
    let cli2 = Cli::try_parse_from([
        "bcs-cli",
        "session",
        "add-member",
        "g-1:aabb0011",
        "--bot-uuid",
        "bot-dba",
    ])
    .unwrap();

    match cli2.command {
        Commands::Session {
            command: SessionCommands::AddMember { role, .. },
            ..
        } => {
            assert!(role.is_none());
        }
        _ => panic!("expected session add-member command"),
    }
}

#[test]
fn test_session_remove_member_parses_args() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "session",
        "remove-member",
        "g-1:aabb0011",
        "bot-dba",
    ])
    .unwrap();

    match cli.command {
        Commands::Session {
            command: SessionCommands::RemoveMember { session, bot_uuid },
            ..
        } => {
            assert_eq!(session, "g-1:aabb0011");
            assert_eq!(bot_uuid, "bot-dba");
        }
        _ => panic!("expected session remove-member command"),
    }
}

#[test]
fn test_session_set_member_mode_parses_args() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "session",
        "set-member-mode",
        "g-1:aabb0011",
        "bot-dba",
        "--mode",
        "muted",
    ])
    .unwrap();

    match cli.command {
        Commands::Session {
            command:
                SessionCommands::SetMemberMode {
                    session,
                    bot_uuid,
                    mode,
                },
            ..
        } => {
            assert_eq!(session, "g-1:aabb0011");
            assert_eq!(bot_uuid, "bot-dba");
            assert_eq!(mode, "muted");
        }
        _ => panic!("expected session set-member-mode command"),
    }
}

#[test]
fn test_session_invite_link_parses_args() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "session",
        "invite-link",
        "g-1:aabb0011",
        "--ttl-seconds",
        "3600",
    ])
    .unwrap();

    match cli.command {
        Commands::Session {
            command:
                SessionCommands::InviteLink {
                    session,
                    ttl_seconds,
                },
            ..
        } => {
            assert_eq!(session, "g-1:aabb0011");
            assert_eq!(ttl_seconds, Some(3600));
        }
        _ => panic!("expected session invite-link command"),
    }
}
