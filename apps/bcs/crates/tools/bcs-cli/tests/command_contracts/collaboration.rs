use super::fixture::{Cli, stdout};
use serde_json::{Value, json};
use wiremock::{Mock, ResponseTemplate, matchers::{body_json, body_partial_json, header, method, path}};

const YAML: &str = "name: Answer\nparticipants:\n  assistant:\n    required: true\nruntime:\n  kind: state_machine\n  state_machine:\n    nodes:\n      answer:\n        kind: bot_task\n        assignee: {type: bot_binding, binding: assistant}\n        instruction: Answer the question\n        final_output: true\n";
const UUID: &str = "01010101-0202-0303-0404-050505050505";
const PARENT_UUID: &str = "01010101-0202-0303-0404-060606060606";

fn workflow(cli: &Cli) -> String {
    let file = cli.home.path().join("workflow.yaml");
    std::fs::write(&file, YAML).unwrap();
    file.to_str().unwrap().to_owned()
}

fn validation() -> Value {
    json!({"valid": true, "participants": [{"binding": "assistant", "required": true}],
        "summary": {"participants": 1, "nodes": 1}, "errors": [], "warnings": []})
}

async fn validate_request(cli: &Cli, result: Value) {
    Mock::given(method("POST"))
        .and(path("/collaboration/definitions/validate"))
        .and(header("authorization", "Bearer test-command-token"))
        .and(body_json(json!({"definition_yaml": YAML})))
        .respond_with(ResponseTemplate::new(200).set_body_json(result))
        .expect(1).mount(&cli.server).await;
}

#[tokio::test]
async fn collaboration_validate_preserves_json_and_renders_human_diagnostics() {
    for human in [false, true] {
        let cli = Cli::new().await;
        let file = workflow(&cli);
        let mut result = validation();
        result["warnings"] = json!([{"code": "UNUSED", "path": "$.metadata", "message": "Unused metadata"}]);
        result["graph"] = json!({"graph_mode": "logical", "execution_graph_mode": "expanded"});
        validate_request(&cli, result.clone()).await;
        let output = cli.ok(if human { &["--no-json"] } else { &["--json"] },
            &["collaboration", "validate", &file]);
        if human {
            let text = stdout(&output);
            for expected in ["VALID", "Participants: 1", "Nodes: 1", "Graph: logical → expanded",
                "WARNING UNUSED $.metadata: Unused metadata"] {
                assert!(text.contains(expected), "{text}");
            }
        } else {
            assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result);
        }
        cli.server.verify().await;
    }
}

#[tokio::test]
async fn collaboration_invalid_validation_exits_nonzero_with_stdout_diagnostics() {
    for human in [false, true] {
        let cli = Cli::new().await;
        let file = workflow(&cli);
        let result = json!({"valid": false, "errors": [{"code": "BAD_NODE", "path": "$.runtime",
            "message": "Missing final output"}], "warnings": []});
        validate_request(&cli, result.clone()).await;
        let output = cli.command(if human { &["--no-json"] } else { &[] },
            &["collaboration", "validate", &file]).assert().failure().get_output().clone();
        if human {
            assert!(stdout(&output).contains("BAD_NODE $.runtime: Missing final output"));
        } else {
            assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result);
        }
        // Validation rejection is a documented stdout result, not a transport error.
        assert!(!String::from_utf8_lossy(&output.stderr).contains("Error:"));
        cli.server.verify().await;
    }
}

#[tokio::test]
async fn collaboration_validate_reports_http_and_decode_failures() {
    for (response, error) in [
        (ResponseTemplate::new(503).set_body_string("validator unavailable"), "validator unavailable"),
        (ResponseTemplate::new(200).set_body_string("not JSON"), "Invalid collaboration definition validation response"),
    ] {
        let cli = Cli::new().await;
        let file = workflow(&cli);
        Mock::given(method("POST")).and(path("/collaboration/definitions/validate"))
            .and(header("authorization", "Bearer test-command-token"))
            .and(body_json(json!({"definition_yaml": YAML})))
            .respond_with(response).expect(1).mount(&cli.server).await;
        cli.fail(&[], &["collaboration", "validate", &file], error);
        cli.server.verify().await;
    }
}

#[tokio::test]
async fn collaboration_preview_is_offline_verbatim_and_has_explicit_json_opt_in() {
    for flags in [vec![], vec!["--no-json"], vec!["--json"], vec!["--json", "--no-json"]] {
        let cli = Cli::new().await;
        let file = workflow(&cli);
        let output = cli.ok(&flags, &["collaboration", "preview", &file, "--title", "Answer's plan",
            "--document-id", UUID, "--snapshot-id", UUID, "--parent-snapshot-id", PARENT_UUID]);
        let text = stdout(&output);
        let message = if flags == ["--json"] {
            let value: Value = serde_json::from_str(&text).unwrap();
            assert_eq!(value["snapshot"]["definitionYaml"], YAML);
            assert_eq!(value["snapshot"]["documentId"], UUID);
            assert_eq!(value["snapshot"]["snapshotId"], UUID);
            assert_eq!(value["snapshot"]["parentSnapshotId"], PARENT_UUID);
            assert_eq!(value["snapshot"]["title"], "Answer's plan");
            value["message"].as_str().unwrap().to_owned()
        } else { text.trim_end().to_owned() };
        let params = message.strip_prefix("<AixUI type=\"card\" component=\"bcsPanel.CollaborationDefinitionCard\" params='")
            .unwrap().strip_suffix("' />").unwrap();
        let snapshot: Value = serde_json::from_str(params).unwrap();
        assert_eq!(snapshot["definitionYaml"], YAML);
        assert_eq!(snapshot["schemaVersion"], 1);
        assert!(!params.contains("Answer's"), "attribute apostrophe must be escaped");
        assert!(cli.server.received_requests().await.unwrap().is_empty());
    }
}

#[tokio::test]
async fn collaboration_preview_rejects_invalid_metadata_without_http() {
    let cli = Cli::new().await;
    let file = workflow(&cli);
    for (extra, error) in [
        (vec!["--title", "  "], "--title must not be empty or blank"),
        (vec!["--document-id", "not-a-uuid"], "--document-id must be a standard hyphenated UUID"),
        (vec!["--parent-snapshot-id", UUID], "requires an explicit --document-id"),
        (vec!["--document-id", UUID, "--snapshot-id", UUID, "--parent-snapshot-id", UUID],
            "--parent-snapshot-id must not equal --snapshot-id"),
    ] {
        let mut args = vec!["collaboration", "preview", &file];
        args.extend(extra);
        cli.fail(&[], &args, error);
    }
    assert!(cli.server.received_requests().await.unwrap().is_empty());
}

#[tokio::test]
async fn collaboration_permission_json_and_human_allowed_denied_fallbacks() {
    for (flags, result, expected) in [
        (vec![], json!({"allowed": true, "session_id": "session-1", "reason_code": "owner"}), None),
        (vec!["--no-json"], json!({"allowed": true, "session_id": "session-1", "reason_code": "owner",
            "message": "Session owner"}), Some("State-machine permission: ALLOWED\n  Session: session-1\n  Reason: owner\n  Message: Session owner\n")),
        (vec!["--no-json"], json!({}), Some("State-machine permission: DENIED\n  Session: session-1\n  Reason: unknown\n")),
    ] {
        let cli = Cli::new().await;
        Mock::given(method("GET")).and(path("/sessions/session-1/state-machine-permission"))
            .and(header("authorization", "Bearer test-command-token"))
            .respond_with(ResponseTemplate::new(200).set_body_json(result.clone()))
            .expect(1).mount(&cli.server).await;
        let output = cli.ok(&flags, &["collaborate", "permission", "--session", "session-1"]);
        if let Some(expected) = expected { assert_eq!(stdout(&output), expected); }
        else { assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result); }
        cli.server.verify().await;
    }
}

#[tokio::test]
async fn collaboration_run_sends_bindings_input_and_optional_panel_in_both_modes() {
    for human in [false, true] {
        let cli = Cli::new().await;
        let file = workflow(&cli);
        let input_file = cli.home.path().join("input.json");
        std::fs::write(&input_file, r#"{"question":"Why?"}"#).unwrap();
        let input_arg = format!("@{}", input_file.display());
        let mut body = json!({"definition_yaml": YAML, "participant_bindings": {
            "assistant": {"source": "manual", "bot_ids": ["bot-source"]}}, "input": {"question": "Why?"}});
        let mut args = vec!["collaborate", "run", &file, "--session", "session-1",
            "--binding", "assistant=bot-source", "--input", &input_arg];
        if human {
            args.extend(["--panel-component", "bcsPanel.Result", "--panel-params", r#"{"question":"Why?"}"#,
                "--panel-tab-id", "answer", "--panel-tab-title", "Answer", "--panel-tab-closable", "false"]);
            body["opening_message"] = json!({"type": "panel", "component": "bcsPanel.Result",
                "params": {"question": "Why?"}, "tab": {"id": "answer", "title": "Answer", "closable": false}});
        }
        let result = json!({"run": {"run_id": "run-1", "session_id": "session-1", "status": "running"},
            "nodes": [{"node_id": "answer", "display_name": "Answer", "status": "running", "attempt": 1}],
            "node_execution_metadata": {"answer": {"definition_node_id": "answer"}}});
        Mock::given(method("POST")).and(path("/sessions/session-1/state-machine-runs"))
            .and(header("authorization", "Bearer test-command-token")).and(body_json(body))
            .respond_with(ResponseTemplate::new(202).set_body_json(result.clone()))
            .expect(1).mount(&cli.server).await;
        let output = cli.ok(if human { &["--no-json"] } else { &[] }, &args);
        if human {
            let text = stdout(&output);
            for expected in ["State-machine run started:", "Run: run-1", "Session: session-1", "Status: running",
                "Run run-1 · running", "Node answer · Answer · running · attempt 1"] {
                assert!(text.contains(expected), "{text}");
            }
        } else { assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result); }
        cli.server.verify().await;
    }
}

#[tokio::test]
async fn collaboration_local_input_errors_do_not_submit_or_validate() {
    let cli = Cli::new().await;
    let missing = cli.home.path().join("missing.yaml").to_str().unwrap().to_owned();
    for operation in ["validate", "create", "run"] {
        let mut args = vec!["collaboration", operation, &missing];
        if operation == "create" { args.extend(["--driver", "bot-source", "--binding", "assistant=bot-source"]); }
        if operation == "run" { args.extend(["--session", "session-1", "--binding", "assistant=bot-source"]); }
        cli.fail(&[], &args, "Failed to read YAML file");
    }
    let file = workflow(&cli);
    for (extra, error) in [
        (vec!["--binding", "bad"], "expected ROLE=BOT_UUID"),
        (vec!["--binding", "=bot-source"], "role and bot UUID must not be empty"),
        (vec!["--binding", "assistant=bot-source", "--binding", "assistant=bot-other"], "Duplicate --binding role"),
        (vec!["--binding", "assistant=bot-source", "--input", "not-json"], "Invalid JSON literal"),
        (vec!["--binding", "assistant=bot-source", "--panel-component", "Panel", "--panel-params", "[]"],
            "--panel-params must be a JSON object"),
    ] {
        let mut args = vec!["collaboration", "run", &file, "--session", "session-1"];
        args.extend(extra);
        cli.fail(&[], &args, error);
    }
    assert!(cli.server.received_requests().await.unwrap().is_empty());
}

#[tokio::test]
async fn collaboration_query_routes_run_node_graph_pending_and_renders_each_projection() {
    let node = json!({"node_id": "approval:1", "display_name": "Review", "status": "pending", "attempt": 1,
        "response_ref": "review-ref", "instruction": "Approve the answer"});
    for (extra, suffix, result, expected) in [
        (vec![], "", json!({"run": {"run_id": "run-1", "status": "running"}, "nodes": [node.clone()]}),
            "Run run-1 · running"),
        (vec!["--node", "approval:1"], "/nodes/approval:1", json!({"node": node.clone()}),
            "Node approval:1 · Review · pending · attempt 1"),
        (vec!["--graph"], "/graph", json!({"definition": {"graph_mode": "logical", "execution_graph_mode": "expanded"}}),
            "Graph: logical → expanded"),
        (vec!["--pending"], "/pending-human-nodes", json!([node]), "Reply using --node approval:1"),
    ] {
        for human in [false, true] {
            let cli = Cli::new().await;
            Mock::given(method("GET")).and(path(format!("/state-machine-runs/run-1{suffix}")))
                .and(header("authorization", "Bearer test-command-token"))
                .respond_with(ResponseTemplate::new(200).set_body_json(json!({"data": result})))
                .expect(1).mount(&cli.server).await;
            let mut args = vec!["collaboration", "query", "--run", "run-1"];
            args.extend(extra.iter().copied());
            let output = cli.ok(if human { &["--no-json"] } else { &[] }, &args);
            if human { assert!(stdout(&output).contains(expected), "{}", stdout(&output)); }
            else { assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result); }
            cli.server.verify().await;
        }
    }
    let cli = Cli::new().await;
    cli.fail(&[], &["collaboration", "query", "--run", "run-1", "--graph", "--pending"], "cannot be used with");
    assert!(cli.server.received_requests().await.unwrap().is_empty());
}

#[tokio::test]
async fn collaboration_respond_checks_pending_identity_and_posts_exact_content() {
    for human in [false, true] {
        let cli = Cli::new().await;
        let result = json!({"run": {"run_id": "run-1", "status": "running"}});
        Mock::given(method("GET")).and(path("/state-machine-runs/run-1/pending-human-nodes"))
            .and(header("authorization", "Bearer test-command-token"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"data": [{"node_id": "approval:1"}]})))
            .expect(1).mount(&cli.server).await;
        Mock::given(method("POST")).and(path("/state-machine-runs/run-1/nodes/approval:1/respond"))
            .and(header("authorization", "Bearer test-command-token"))
            .and(body_json(json!({"content": "Approved, keep the details."})))
            .respond_with(ResponseTemplate::new(200).set_body_json(result.clone()))
            .expect(1).mount(&cli.server).await;
        let output = cli.ok(if human { &["--no-json"] } else { &[] }, &["collaboration", "respond",
            "--run", "run-1", "--node", "approval:1", "--content", "Approved, keep the details."]);
        if human { assert_eq!(stdout(&output), "Run run-1 · running\n"); }
        else { assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result); }
        cli.server.verify().await;
    }
}

#[tokio::test]
async fn collaboration_respond_rejects_blank_or_non_pending_node_without_posting() {
    let cli = Cli::new().await;
    cli.fail(&[], &["collaboration", "respond", "--run", "run-1", "--node", "approval:1", "--content", "  "],
        "Human response must not be empty");
    assert!(cli.server.received_requests().await.unwrap().is_empty());
    Mock::given(method("GET")).and(path("/state-machine-runs/run-1/pending-human-nodes"))
        .and(header("authorization", "Bearer test-command-token"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!([{"node_id": "other:1"}])))
        .expect(1).mount(&cli.server).await;
    cli.fail(&[], &["collaboration", "respond", "--run", "run-1", "--node", "approval:1", "--content", "Approve"],
        "Execution node is not pending for the authenticated Human");
    assert_eq!(cli.server.received_requests().await.unwrap().len(), 1);
    cli.server.verify().await;
}

#[tokio::test]
async fn collaboration_create_validates_then_sends_explicit_group_options() {
    for human in [false, true] {
        let cli = Cli::new().await;
        let file = workflow(&cli);
        validate_request(&cli, validation()).await;
        let result = json!({"id": "group-1", "driver_bot": "bot-source", "participants": ["bot-source"],
            "chat_url": "https://example.test/chat/group-1", "session_id": "session-1", "group_kind": "normal", "created": true});
        Mock::given(method("POST")).and(path("/groups"))
            .and(header("authorization", "Bearer test-command-token"))
            .and(body_partial_json(json!({"id": "group-1", "driver_bot": "bot-source", "originator": "bot-source",
                "participants": [], "participant_bindings": {"assistant": {"source": "manual", "bot_ids": ["bot-source"]}},
                "collaboration_definition_yaml": YAML, "group_strategy": "state_machine", "create_initial_session": true,
                "auto_start_on_service_invocation": true, "context": "Answer question", "topic": "Answer"})))
            .respond_with(ResponseTemplate::new(200).set_body_json(result.clone()))
            .expect(1).mount(&cli.server).await;
        let output = cli.ok(if human { &["--no-json"] } else { &[] }, &["collaboration", "create", &file,
            "--id", "group-1", "--driver", "bot-source", "--binding", "assistant=bot-source", "--context", "Answer question",
            "--topic", "Answer", "--auto-start-on-service-invocation"]);
        if human {
            for expected in ["Custom collaboration group created:", "ID: group-1", "Driver: bot-source",
                "Participants: bot-source", "Chat URL: https://example.test/chat/group-1", "Session: session-1"] {
                assert!(stdout(&output).contains(expected), "{}", stdout(&output));
            }
        } else { assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result); }
        cli.server.verify().await;
        let requests = cli.server.received_requests().await.unwrap();
        assert_eq!(requests[0].url.path(), "/collaboration/definitions/validate");
        assert_eq!(requests[1].url.path(), "/groups");
    }
}

#[tokio::test]
async fn collaboration_create_no_session_requires_server_to_honor_the_flag() {
    for session in [Value::Null, json!("unexpected-session")] {
        let cli = Cli::new().await;
        let file = workflow(&cli);
        validate_request(&cli, validation()).await;
        Mock::given(method("POST")).and(path("/groups"))
            .and(header("authorization", "Bearer test-command-token"))
            .and(body_partial_json(json!({"create_initial_session": false, "auto_start_on_service_invocation": false,
                "participant_bindings": {"assistant": {"source": "manual", "bot_ids": ["bot-source"]}},
                "collaboration_definition_yaml": YAML})))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"id": "group-1", "driver_bot": "bot-source",
                "participants": ["bot-source"], "session_id": session})))
            .expect(1).mount(&cli.server).await;
        let args = ["collaboration", "create", &file, "--driver", "bot-source", "--binding", "assistant=bot-source", "--no-session"];
        if session.is_null() {
            let output = cli.ok(&["--no-json"], &args);
            assert!(stdout(&output).contains("ID: group-1"));
            assert!(!stdout(&output).contains("Session:"));
            assert!(!stdout(&output).contains("Chat URL:"));
        } else {
            cli.fail(&[], &args, "server did not honor --no-session");
        }
        cli.server.verify().await;
        assert_eq!(cli.server.received_requests().await.unwrap().len(), 2, "must not attempt rollback");
    }
}

#[tokio::test]
async fn collaboration_create_stops_before_group_write_on_invalid_definition_or_bindings() {
    for (result, binding, driver, error) in [
        (json!({"valid": false, "errors": [{"code": "INVALID", "message": "Bad definition"}]}),
            "assistant=bot-source", "bot-source", None),
        (validation(), "unknown=bot-source", "bot-source", Some("undeclared participant role: unknown")),
        (json!({"valid": true, "participants": [{"binding": "assistant", "required": true},
            {"binding": "reviewer", "assigned": true}]}), "assistant=bot-source", "bot-source",
            Some("Missing --binding for participant role: reviewer")),
        (validation(), "assistant=bot-other", "bot-source", Some("Driver bot must appear in at least one --binding")),
        (json!({"valid": true}), "assistant=bot-source", "bot-source", Some("Validation response is missing participants")),
    ] {
        let cli = Cli::new().await;
        let file = workflow(&cli);
        validate_request(&cli, result.clone()).await;
        let args = ["collaboration", "create", &file, "--driver", driver, "--binding", binding];
        if let Some(error) = error { cli.fail(&[], &args, error); }
        else {
            let output = cli.command(&[], &args).assert().failure().get_output().clone();
            assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result);
        }
        cli.server.verify().await;
        assert_eq!(cli.server.received_requests().await.unwrap().len(), 1);
    }
}

#[tokio::test]
async fn collaboration_http_errors_propagate_with_context_instead_of_success() {
    for (operation, route, verb, extra, error) in [
        ("permission", "/sessions/session-1/state-machine-permission", "GET", vec!["--session", "session-1"],
            "Query session state-machine permission failed"),
        ("query", "/state-machine-runs/run-1", "GET", vec!["--run", "run-1"], "State-machine request failed"),
        ("run", "/sessions/session-1/state-machine-runs", "POST", vec!["--session", "session-1", "--binding", "assistant=bot-source"],
            "Start session state-machine run failed"),
        ("create", "/groups", "POST", vec!["--driver", "bot-source", "--binding", "assistant=bot-source"],
            "Create custom collaboration group failed"),
    ] {
        let cli = Cli::new().await;
        let file = workflow(&cli);
        if operation == "create" { validate_request(&cli, validation()).await; }
        Mock::given(method(verb)).and(path(route)).and(header("authorization", "Bearer test-command-token"))
            .respond_with(ResponseTemplate::new(403).set_body_string("not authorized"))
            .expect(1).mount(&cli.server).await;
        let mut args = vec!["collaboration", operation];
        if matches!(operation, "run" | "create") { args.push(&file); }
        args.extend(extra);
        let output = cli.command(&[], &args).assert().failure().get_output().clone();
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(stderr.contains(error), "{stderr}");
        assert!(stderr.contains("not authorized"), "{stderr}");
        assert!(stdout(&output).is_empty());
        cli.server.verify().await;
    }
}

#[tokio::test]
async fn collaboration_draft_dispatch_preserves_success_json_and_failure_exit_even_in_human_mode() {
    for success in [true, false] {
        let cli = Cli::new().await;
        Mock::given(method("DELETE")).and(path(format!("/collaboration/drafts/{UUID}")))
            .and(header("authorization", "Bearer test-command-token"))
            .respond_with(ResponseTemplate::new(if success { 204 } else { 403 }))
            .expect(1).mount(&cli.server).await;
        let mut command = cli.command(&["--no-json"], &["collaboration", "draft", "delete", UUID]);
        let output = if success { command.assert().success().get_output().clone() }
            else { command.assert().failure().get_output().clone() };
        let value: Value = serde_json::from_str(&stdout(&output)).unwrap();
        if success { assert_eq!(value, json!({"draft_id": UUID, "deleted": true})); }
        else {
            assert!(!value.to_string().contains("\"deleted\":true"));
            assert!(String::from_utf8_lossy(&output.stderr).contains("draft workflow did not produce a successful result"));
        }
        cli.server.verify().await;
    }
}
