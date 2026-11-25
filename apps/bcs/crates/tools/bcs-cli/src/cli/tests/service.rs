use super::{
    Cli, Commands, ErrorKind, Parser, ServiceCommands, TempDir, parse_json_arg,
    service_session_summary_lines, split_service_sid,
};

// ------------------------------------------------------------------
// service subcommand parse tests
// ------------------------------------------------------------------

#[test]
fn test_service_invoke_parses_minimal_args() {
    let cli = Cli::try_parse_from(["bcs-cli", "service", "invoke", "--group", "g-1"]).unwrap();

    match cli.command {
        Commands::Service {
            command:
                ServiceCommands::Invoke {
                    group,
                    input,
                    meta,
                    session_id,

                    caller_id,
                    title,
                    detach,
                    timeout_ms,
                },
            ..
        } => {
            assert_eq!(group, "g-1");
            assert!(input.is_none());
            assert!(meta.is_none());
            assert!(session_id.is_none());
            assert!(caller_id.is_none());
            assert!(title.is_none());
            assert!(!detach);
            assert!(timeout_ms.is_none());
        }
        _ => panic!("expected service invoke command"),
    }
}

#[test]
fn test_service_command_accepts_bot_token() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "service",
        "--token",
        "bot-token",
        "invoke",
        "--group",
        "g-1",
    ])
    .unwrap();

    match cli.command {
        Commands::Service { token, .. } => {
            assert_eq!(token.as_deref(), Some("bot-token"));
        }
        _ => panic!("expected service command"),
    }
}

#[test]
fn test_service_invoke_parses_full_args() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "service",
        "invoke",
        "--group",
        "g-1",
        "--input",
        r#"{"q":"hi"}"#,
        "--meta",
        r#"{"trace":"abc"}"#,
        "--session-id",
        "g-1:00000001",

        "--caller-id",
        "client-a",
        "--title",
        "demo",
        "--detach",
        "--timeout-ms",
        "60000",
    ])
    .unwrap();

    match cli.command {
        Commands::Service {
            command:
                ServiceCommands::Invoke {
                    group,
                    input,
                    meta,
                    session_id,

                    caller_id,
                    title,
                    detach,
                    timeout_ms,
                },
            ..
        } => {
            assert_eq!(group, "g-1");
            assert_eq!(input.as_deref(), Some(r#"{"q":"hi"}"#));
            assert_eq!(meta.as_deref(), Some(r#"{"trace":"abc"}"#));
            assert_eq!(session_id.as_deref(), Some("g-1:00000001"));
            assert_eq!(caller_id.as_deref(), Some("client-a"));
            assert_eq!(title.as_deref(), Some("demo"));
            assert!(detach);
            assert_eq!(timeout_ms, Some(60_000));
        }
        _ => panic!("expected service invoke command"),
    }
}

#[test]
fn test_service_invoke_rejects_zero_timeout_ms() {
    let err = match Cli::try_parse_from([
        "bcs-cli",
        "service",
        "invoke",
        "--group",
        "g-1",
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
fn test_service_status_parses_positional_sid() {
    let cli = Cli::try_parse_from(["bcs-cli", "service", "status", "g-1:abcdef01"]).unwrap();

    match cli.command {
        Commands::Service {
            command: ServiceCommands::Status { sid, group },
            ..
        } => {
            assert_eq!(sid, "g-1:abcdef01");
            assert!(group.is_none());
        }
        _ => panic!("expected service status command"),
    }
}

#[test]
fn test_service_status_accepts_group_override() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "service",
        "status",
        "weird-sid",
        "--group",
        "g-explicit",
    ])
    .unwrap();

    match cli.command {
        Commands::Service {
            command: ServiceCommands::Status { sid, group },
            ..
        } => {
            assert_eq!(sid, "weird-sid");
            assert_eq!(group.as_deref(), Some("g-explicit"));
        }
        _ => panic!("expected service status command"),
    }
}

#[test]
fn test_service_wait_parses_positional_sid_and_timeout() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "service",
        "wait",
        "g-1:abcdef01",
        "--timeout-ms",
        "120000",
    ])
    .unwrap();

    match cli.command {
        Commands::Service {
            command:
                ServiceCommands::Wait {
                    sid,
                    group,
                    timeout_ms,
                },
            ..
        } => {
            assert_eq!(sid, "g-1:abcdef01");
            assert!(group.is_none());
            assert_eq!(timeout_ms, Some(120_000));
        }
        _ => panic!("expected service wait command"),
    }
}

// ------------------------------------------------------------------
// service helper unit tests
// ------------------------------------------------------------------

#[test]
fn test_service_session_summary_lines_include_state_machine_run() {
    let session = serde_json::json!({
        "session_id": "g-1:abcdef01",
        "group_id": "g-1",
        "status": "running",
        "state_machine_run_id": "run-1",
        "state_machine_run": {
            "run": {
                "status": "running"
            }
        }
    });

    let lines = service_session_summary_lines(&session, "Invocation submitted");
    assert!(lines.iter().any(|line| line == "  StateRun: run-1"));
    assert!(lines.iter().any(|line| line == "  RunStatus: running"));
}

#[test]
fn test_split_service_sid_recovers_group_from_colon() {
    let (gid, sid) = split_service_sid("g-1:abcdef01", None).unwrap();
    assert_eq!(gid, "g-1");
    assert_eq!(sid, "g-1:abcdef01");
}

#[test]
fn test_split_service_sid_explicit_group_wins() {
    let (gid, sid) = split_service_sid("g-1:abcdef01", Some("g-other")).unwrap();
    assert_eq!(gid, "g-other");
    assert_eq!(sid, "g-1:abcdef01");
}

#[test]
fn test_split_service_sid_rejects_unparseable_sid_without_group() {
    let err = split_service_sid("nocolonhere", None).unwrap_err();
    let msg = err.to_string();
    assert!(msg.contains("Cannot infer group"), "got: {}", msg);
    assert!(msg.contains("--group"), "got: {}", msg);
}

#[test]
fn test_parse_json_arg_supports_literal() {
    let v = parse_json_arg(r#"{"k":"v","n":42}"#).unwrap();
    assert_eq!(v.get("k").and_then(|x| x.as_str()), Some("v"));
    assert_eq!(v.get("n").and_then(|x| x.as_i64()), Some(42));
}

#[test]
fn test_parse_json_arg_reads_file_with_at_prefix() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("payload.json");
    std::fs::write(&path, r#"{"hello":"world"}"#).unwrap();

    let arg = format!("@{}", path.display());
    let v = parse_json_arg(&arg).unwrap();
    assert_eq!(v.get("hello").and_then(|x| x.as_str()), Some("world"));
}

#[test]
fn test_parse_json_arg_rejects_bare_at_sign() {
    let err = parse_json_arg("@").unwrap_err();
    assert!(err.to_string().contains("must be followed by a file path"));
}
