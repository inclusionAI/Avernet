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
