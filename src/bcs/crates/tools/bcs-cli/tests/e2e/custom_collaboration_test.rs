use crate::common::{TestContext, assert_failure, assert_success};
use wiremock::{
    Mock, ResponseTemplate,
    matchers::{bearer_token, body_json, method, path},
};

const WORKFLOW_YAML: &str = r#"name: Content workflow
participants:
  planner:
    required: true
  writer:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: writer
        instruction: Answer.
        final_output: true
"#;

fn validation_response(valid: bool) -> serde_json::Value {
    serde_json::json!({
        "valid": valid,
        "errors": if valid {
            serde_json::json!([])
        } else {
            serde_json::json!([{
                "code": "INVALID_DEFINITION",
                "path": "$",
                "message": "invalid workflow"
            }])
        },
        "summary": {
            "participants": 2,
            "nodes": 1,
            "initial_nodes": ["answer"],
            "final_output_node": "answer"
        },
        "participants": [
            {"binding": "planner", "required": true, "assigned": false},
            {"binding": "writer", "required": true, "assigned": true}
        ]
    })
}

#[tokio::test]
async fn collaborate_permission_queries_current_session_with_bot_token() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");

    Mock::given(method("GET"))
        .and(path(
            "/sessions/group-1:abc12345/state-machine-permission",
        ))
        .and(bearer_token(&ctx.session.token))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "session_id": "group-1:abc12345",
            "group_id": "group-1",
            "caller_bot_id": ctx.session.bot_uuid,
            "allowed": true,
            "reason_code": "allowed",
            "message": "the caller may start a one-shot state-machine run in this session",
            "policy_version": "session_state_machine_v1",
            "group_strategy": "chat",
            "group_owner_bot_id": ctx.session.bot_uuid
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("collaborate")
        .arg("permission")
        .arg("--session")
        .arg("group-1:abc12345")
        .output()
        .expect("Failed to execute permission command");

    assert_success(&output);
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["allowed"], true);
    assert_eq!(json["reason_code"], "allowed");
    assert_eq!(json["session_id"], "group-1:abc12345");
}

#[tokio::test]
async fn collaborate_run_posts_yaml_bindings_and_input_once() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");
    let yaml_file = ctx.temp_dir.path().join("one-shot.yaml");
    std::fs::write(&yaml_file, WORKFLOW_YAML).unwrap();

    Mock::given(method("POST"))
        .and(path("/sessions/group-1:abc12345/state-machine-runs"))
        .and(bearer_token(&ctx.session.token))
        .and(body_json(serde_json::json!({
            "definition_yaml": WORKFLOW_YAML,
            "participant_bindings": {
                "planner": {"source": "manual", "bot_ids": [ctx.session.bot_uuid]},
                "writer": {"source": "manual", "bot_ids": ["bot-writer"]}
            },
            "input": {"question": "resolve it"}
        })))
        .respond_with(ResponseTemplate::new(202).set_body_json(serde_json::json!({
            "run": {
                "run_id": "run-one-shot",
                "definition_id": "definition-one-shot",
                "definition_version": 1,
                "group_id": "group-1",
                "group_version": 1,
                "session_id": "group-1:abc12345",
                "created_by": ctx.session.bot_uuid,
                "status": "running",
                "input": {"question": "resolve it"},
                "created_at": 1,
                "updated_at": 1
            },
            "nodes": [],
            "judge_outputs": []
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("collaborate")
        .arg("run")
        .arg(&yaml_file)
        .arg("--session")
        .arg("group-1:abc12345")
        .arg("--binding")
        .arg(format!("planner={}", ctx.session.bot_uuid))
        .arg("--binding")
        .arg("writer=bot-writer")
        .arg("--input")
        .arg(r#"{"question":"resolve it"}"#)
        .output()
        .expect("Failed to execute one-shot run command");

    assert_success(&output);
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["run"]["run_id"], "run-one-shot");
    assert_eq!(json["run"]["session_id"], "group-1:abc12345");
    assert_eq!(json["run"]["status"], "running");
}

#[tokio::test]
async fn collaboration_validate_calls_server_validation_api() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");
    let yaml_file = ctx.temp_dir.path().join("workflow.yaml");
    std::fs::write(&yaml_file, WORKFLOW_YAML).unwrap();

    Mock::given(method("POST"))
        .and(path("/collaboration/definitions/validate"))
        .and(body_json(serde_json::json!({
            "definition_yaml": WORKFLOW_YAML
        })))
        .respond_with(ResponseTemplate::new(200).set_body_json(validation_response(true)))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("collaboration")
        .arg("validate")
        .arg(&yaml_file)
        .output()
        .expect("Failed to execute validation command");

    assert_success(&output);
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["valid"], true);
    assert_eq!(json["participants"][1]["binding"], "writer");
}

#[tokio::test]
async fn collaboration_validate_exits_nonzero_for_invalid_report() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");
    let yaml_file = ctx.temp_dir.path().join("invalid.yaml");
    std::fs::write(&yaml_file, "invalid: true\n").unwrap();

    Mock::given(method("POST"))
        .and(path("/collaboration/definitions/validate"))
        .respond_with(ResponseTemplate::new(200).set_body_json(validation_response(false)))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("collaboration")
        .arg("validate")
        .arg(&yaml_file)
        .output()
        .expect("Failed to execute validation command");

    assert_failure(&output, Some(1));
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["valid"], false);
    assert_eq!(json["errors"][0]["code"], "INVALID_DEFINITION");
}

#[tokio::test]
async fn collaboration_create_validates_then_posts_state_machine_group() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");
    let yaml_file = ctx.temp_dir.path().join("workflow.yaml");
    std::fs::write(&yaml_file, WORKFLOW_YAML).unwrap();

    Mock::given(method("POST"))
        .and(path("/collaboration/definitions/validate"))
        .and(bearer_token(&ctx.session.token))
        .respond_with(ResponseTemplate::new(200).set_body_json(validation_response(true)))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;
    Mock::given(method("POST"))
        .and(path("/groups"))
        .and(bearer_token(&ctx.session.token))
        .and(body_json(serde_json::json!({
            "id": null,
            "label": null,
            "driver_bot": "bot-driver",
            "participants": [],
            "participant_bindings": {
                "planner": {"source": "manual", "bot_ids": ["bot-driver"]},
                "writer": {"source": "manual", "bot_ids": ["bot-writer"]}
            },
            "context": "Produce an article",
            "topic": "Article workflow",
            "group_strategy": "state_machine",
            "originator": "bot-driver",
            "collaboration_definition_yaml": WORKFLOW_YAML,
            "auto_start_on_service_invocation": false
        })))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "id": "custom-group-1",
            "driver_bot": "bot-driver",
            "participants": ["bot-driver", "bot-writer"],
            "chat_url": "http://example.test/groups/custom-group-1",
            "session_id": "custom-group-1:initial",
            "group_kind": "normal",
            "created": true
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("collaboration")
        .arg("create")
        .arg(&yaml_file)
        .arg("--driver")
        .arg("bot-driver")
        .arg("--binding")
        .arg("planner=bot-driver")
        .arg("--binding")
        .arg("writer=bot-writer")
        .arg("--context")
        .arg("Produce an article")
        .arg("--topic")
        .arg("Article workflow")
        .output()
        .expect("Failed to execute create custom group command");

    assert_success(&output);
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["id"], "custom-group-1");
    assert_eq!(
        json["participants"],
        serde_json::json!(["bot-driver", "bot-writer"])
    );
    assert_eq!(json["session_id"], "custom-group-1:initial");
}
