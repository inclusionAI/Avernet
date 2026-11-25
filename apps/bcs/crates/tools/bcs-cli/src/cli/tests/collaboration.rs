use super::{
    Cli, CollaborationCommands, Commands, Parser, PathBuf, build_panel_opening_message,
    parse_custom_group_bindings, validate_custom_group_bindings,
};

#[test]
fn collaboration_validate_command_parses_yaml_path() {
    let cli =
        Cli::try_parse_from(["bcs-cli", "collaboration", "validate", "/tmp/workflow.yaml"])
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
fn collaborate_permission_alias_parses_current_session() {
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
fn collaborate_run_alias_parses_yaml_session_bindings_and_input() {
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
fn collaborate_run_parses_panel_arguments() {
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
fn collaborate_run_requires_panel_component_for_dependent_arguments() {
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
fn panel_opening_message_requires_object_params() {
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
fn collaboration_create_command_parses_repeated_bindings() {
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
fn custom_group_binding_parser_preserves_bot_uuid_colons() {
    let bindings = parse_custom_group_bindings(&[
        "planner=bot-driver".to_string(),
        "writer=20260412_abc:100005".to_string(),
    ])
    .unwrap();

    assert_eq!(bindings["planner"].bot_ids, vec!["bot-driver"]);
    assert_eq!(bindings["writer"].bot_ids, vec!["20260412_abc:100005"]);
}

#[test]
fn custom_group_binding_validation_requires_assigned_slots_and_driver() {
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
    let error =
        validate_custom_group_bindings(&missing, &validation, "bot-driver").unwrap_err();
    assert!(error.to_string().contains("writer"));
}
