use super::{
    Cli, Commands, ErrorKind, GroupContextCommands, Parser, group_context_add_body,
    group_context_insert_optional_timestamp, group_context_query, group_context_update_body,
    resolve_group_context_scope_levels,
};

#[test]
fn group_context_add_parses_valid_range_flags() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "group-context",
        "add",
        "--group-id",
        "game-room-7",
        "--name",
        "game_rule",
        "--scope-level",
        "group",
        "--content",
        "rules",
        "--collect-from",
        r#"{"userIds":["judge_bot"]}"#,
        "--valid-from",
        "2026-10-12T14:56:08+08:00",
        "--valid-to",
        "1791797768000",
    ])
    .unwrap();

    match cli.command {
        Commands::GroupContext {
            command:
                GroupContextCommands::Add {
                    valid_from,
                    valid_to,
                    ..
                },
            ..
        } => {
            assert_eq!(valid_from.as_deref(), Some("2026-10-12T14:56:08+08:00"));
            assert_eq!(valid_to.as_deref(), Some("1791797768000"));
        }
        _ => panic!("expected group-context add command"),
    }
}

#[test]
fn group_context_add_rejects_change_reason_flag() {
    let err = match Cli::try_parse_from([
        "bcs-cli",
        "group-context",
        "add",
        "--group-id",
        "game-room-7",
        "--name",
        "game_rule",
        "--scope-level",
        "group",
        "--content",
        "rules",
        "--collect-from",
        r#"{"userIds":["judge_bot"]}"#,
        "--change-reason",
        "not allowed",
    ]) {
        Ok(_) => panic!("expected parse error"),
        Err(err) => err,
    };
    assert_eq!(err.kind(), ErrorKind::UnknownArgument);
}

#[test]
fn group_context_update_requires_change_reason_flag() {
    let err = match Cli::try_parse_from([
        "bcs-cli",
        "group-context",
        "update",
        "--group-id",
        "game-room-7",
        "--name",
        "player_state",
        "--scope-level",
        "session",
        "--content",
        "updated",
    ]) {
        Ok(_) => panic!("expected parse error"),
        Err(err) => err,
    };
    assert_eq!(err.kind(), ErrorKind::MissingRequiredArgument);
}

#[test]
fn group_context_list_parses_plural_scope_levels_flag() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "group-context",
        "list",
        "--group-id",
        "game-room-7",
        "--scope-levels",
        "group,session",
    ])
    .unwrap();

    match cli.command {
        Commands::GroupContext {
            command:
                GroupContextCommands::List {
                    scope_level,
                    scope_levels,
                    ..
                },
            ..
        } => {
            assert!(scope_level.is_empty());
            assert_eq!(scope_levels, vec!["group", "session"]);
        }
        _ => panic!("expected group-context list command"),
    }
}

#[test]
fn group_context_query_omits_missing_and_empty_group_id() {
    for group_id in [None, Some("")] {
        let query = group_context_query(group_id, Some("sess-001"), None);
        assert!(!query.iter().any(|(key, _)| key == "groupId"));
    }
}

#[test]
fn group_context_query_includes_non_empty_group_id() {
    let query = group_context_query(Some("game-room-7"), None, None);
    assert_eq!(
        query,
        vec![("groupId".to_string(), "game-room-7".to_string())]
    );
}

#[test]
fn group_context_commands_accept_non_group_scope_without_group_id() {
    for (operation, args) in [
        (
            "add",
            vec![
                "group-context",
                "add",
                "--session-id",
                "sess-001",
                "--name",
                "game_rule",
                "--scope-level",
                "session",
                "--content",
                "rules",
                "--collect-from",
                r#"{"userIds":["judge_bot"]}"#,
            ],
        ),
        (
            "update",
            vec![
                "group-context",
                "update",
                "--session-id",
                "sess-001",
                "--name",
                "player_state",
                "--scope-level",
                "session",
                "--change-reason",
                "metadata",
            ],
        ),
        (
            "list",
            vec![
                "group-context",
                "list",
                "--session-id",
                "sess-001",
                "--scope-level",
                "session",
            ],
        ),
        (
            "get",
            vec![
                "group-context",
                "get",
                "--session-id",
                "sess-001",
                "--name",
                "player_state",
                "--scope-level",
                "session",
            ],
        ),
    ] {
        let mut input = vec!["bcs-cli"];
        input.extend(args);
        let cli = Cli::try_parse_from(input)
            .unwrap_or_else(|error| panic!("failed to parse {operation}: {error}"));
        let group_id = match cli.command {
            Commands::GroupContext {
                command:
                    GroupContextCommands::Add { group_id, .. }
                    | GroupContextCommands::Update { group_id, .. }
                    | GroupContextCommands::List { group_id, .. }
                    | GroupContextCommands::Get { group_id, .. },
                ..
            } => group_id,
            _ => panic!("expected group-context {operation} command"),
        };
        assert_eq!(group_id, None, "unexpected group id for {operation}");
    }
}

#[test]
fn group_context_scope_level_flags_are_mutually_exclusive() {
    let err = resolve_group_context_scope_levels(
        vec!["group".to_string()],
        vec!["session".to_string()],
    )
    .unwrap_err();
    assert!(
        err.to_string()
            .contains("--scope-level and --scope-levels cannot be used together")
    );
}

#[test]
fn group_context_add_body_includes_valid_fields_only_when_provided() {
    let with_valid = group_context_add_body(
        "game_rule".to_string(),
        "group".to_string(),
        "rules".to_string(),
        r#"{"userIds":[]}"#.to_string(),
        Some(r#"{"userIds":["judge_bot"]}"#.to_string()),
        Some("2026-10-12T14:56:08+08:00".to_string()),
        Some("1791797768000".to_string()),
    )
    .unwrap();
    assert_eq!(with_valid["valid_from"], "2026-10-12T14:56:08+08:00");
    assert_eq!(with_valid["valid_to"], serde_json::json!(1791797768000i64));

    let without_valid = group_context_add_body(
        "game_rule".to_string(),
        "group".to_string(),
        "rules".to_string(),
        r#"{"userIds":[]}"#.to_string(),
        Some(r#"{"userIds":["judge_bot"]}"#.to_string()),
        None,
        None,
    )
    .unwrap();
    let object = without_valid.as_object().unwrap();
    assert!(!object.contains_key("valid_from"));
    assert!(!object.contains_key("valid_to"));

    let without_collect_from = group_context_add_body(
        "game_rule".to_string(),
        "group".to_string(),
        "rules".to_string(),
        r#"{"userIds":[]}"#.to_string(),
        None,
        None,
        None,
    )
    .unwrap();
    let object = without_collect_from.as_object().unwrap();
    assert!(!object.contains_key("collect_from"));
    assert!(!object.contains_key("valid_from"));
    assert!(!object.contains_key("valid_to"));
}

#[test]
fn group_context_add_cli_accepts_missing_collect_from() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "group-context",
        "add",
        "--group-id",
        "game-room-7",
        "--name",
        "game_rule",
        "--scope-level",
        "group",
        "--content",
        "rules",
    ])
    .expect("collect_from is optional");

    match cli.command {
        Commands::GroupContext {
            command: GroupContextCommands::Add { collect_from, .. },
            ..
        } => assert_eq!(collect_from, None),
        _ => panic!("expected group-context add command"),
    }
}

#[test]
fn group_context_update_body_omits_unprovided_patch_fields() {
    let body = group_context_update_body(
        "player_state".to_string(),
        "session".to_string(),
        None,
        None,
        None,
        None,
        Some("2026-10-12 14:56:08".to_string()),
        None,
        Some("extend validity".to_string()),
    )
    .unwrap();
    let object = body.as_object().unwrap();
    assert_eq!(object.get("valid_from").unwrap(), "2026-10-12 14:56:08");
    assert_eq!(object.get("change_reason").unwrap(), "extend validity");
    assert!(!object.contains_key("content"));
    assert!(!object.contains_key("visible_to"));
    assert!(!object.contains_key("collect_from"));
    assert!(!object.contains_key("owners"));
    assert!(!object.contains_key("valid_to"));
}

#[test]
fn group_context_update_body_includes_owners_when_provided() {
    let body = group_context_update_body(
        "player_state".to_string(),
        "session".to_string(),
        Some("v2".to_string()),
        None,
        None,
        Some(r#"{"userIds":["bot-a","bot-b"]}"#.to_string()),
        None,
        None,
        Some("transfer ownership".to_string()),
    )
    .unwrap();
    assert_eq!(
        body["owners"],
        serde_json::json!({"userIds": ["bot-a", "bot-b"]})
    );
}

#[test]
fn group_context_add_rejects_owners_flag() {
    let err = match Cli::try_parse_from([
        "bcs-cli",
        "group-context",
        "add",
        "--group-id",
        "game-room-7",
        "--name",
        "game_rule",
        "--scope-level",
        "group",
        "--content",
        "rules",
        "--collect-from",
        r#"{"userIds":["judge_bot"]}"#,
        "--owners",
        r#"{"userIds":["judge_bot"]}"#,
    ]) {
        Ok(_) => panic!("expected parse error"),
        Err(err) => err,
    };
    assert_eq!(err.kind(), ErrorKind::UnknownArgument);
}

#[test]
fn group_context_update_parses_owners_flag() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "group-context",
        "update",
        "--group-id",
        "game-room-7",
        "--name",
        "player_state",
        "--scope-level",
        "session",
        "--content",
        "v2",
        "--owners",
        r#"{"userIds":["bot-a","bot-b"]}"#,
        "--change-reason",
        "transfer ownership",
    ])
    .unwrap();
    match cli.command {
        Commands::GroupContext {
            command:
                GroupContextCommands::Update {
                    owners,
                    change_reason,
                    ..
                },
            ..
        } => {
            assert_eq!(owners.as_deref(), Some(r#"{"userIds":["bot-a","bot-b"]}"#));
            assert_eq!(change_reason, "transfer ownership");
        }
        _ => panic!("expected group-context update command"),
    }
}

#[test]
fn group_context_update_rejects_invalid_owners_shapes() {
    for (raw, expected_error) in [
        ("[]", "--owners must be a JSON object"),
        (
            r#"{"foo":"bar"}"#,
            "--owners must be a JSON object containing a non-empty userIds array",
        ),
        (
            r#"{"userIds":"bot-a"}"#,
            "--owners must be a JSON object containing a non-empty userIds array",
        ),
        (r#"{"userIds":[]}"#, "--owners.userIds must not be empty"),
        (
            r#"{"userIds":["", " "]}"#,
            "--owners.userIds must contain non-empty string actor ids",
        ),
        (
            r#"{"userIds":[1]}"#,
            "--owners.userIds must contain non-empty string actor ids",
        ),
    ] {
        let error = group_context_update_body(
            "player_state".to_string(),
            "session".to_string(),
            None,
            None,
            None,
            Some(raw.to_string()),
            None,
            None,
            Some("transfer ownership".to_string()),
        )
        .unwrap_err();
        let error = error.to_string();
        assert!(
            error.contains(expected_error),
            "owners={raw}, expected {expected_error}, got {error}"
        );
    }
}

#[test]
fn group_context_insert_optional_timestamp_converts_numeric_strings() {
    let mut body = serde_json::Map::new();
    group_context_insert_optional_timestamp(&mut body, "valid_from", Some("1791797768".into()));
    group_context_insert_optional_timestamp(
        &mut body,
        "valid_to",
        Some("2026-10-12T14:56:08+08:00".into()),
    );
    group_context_insert_optional_timestamp(&mut body, "missing", None);

    assert_eq!(body["valid_from"], serde_json::json!(1791797768i64));
    assert_eq!(
        body["valid_to"],
        serde_json::json!("2026-10-12T14:56:08+08:00")
    );
    assert!(!body.contains_key("missing"));
}

#[test]
fn group_context_add_body_sends_timestamps_as_numbers() {
    let body = group_context_add_body(
        "test".into(),
        "session".into(),
        "content".into(),
        r#"{"userIds":["bot1"]}"#.into(),
        Some(r#"{"userIds":["bot2"]}"#.to_string()),
        Some("1791797768".into()),
        Some("2026-10-12T14:56:08+08:00".into()),
    )
    .expect("build add body");

    assert_eq!(body["valid_from"], serde_json::json!(1791797768i64));
    assert_eq!(
        body["valid_to"],
        serde_json::json!("2026-10-12T14:56:08+08:00")
    );
}
