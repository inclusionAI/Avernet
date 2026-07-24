use bcs_collaboration_runtime::{
    reject_explicit_participant_roles, validate_authoring_definition_yaml, validate_definition,
};
use bcs_domain::{CollaborationDefinition, CollaborationRuntimeDefinition, StateMachineGraphMode};
use bcs_service_api::ValidateCollaborationDefinitionYamlCommand;

const AUTHORING_YAML: &str = r#"
name: Custom collaboration workflow
participants:
  writer:
    display_name: Writer
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
        instruction: Produce the final answer.
        final_output: true
"#;

fn validate_authoring(
    yaml: &str,
    judge_available: bool,
) -> bcs_service_api::CollaborationDefinitionValidationOutcome {
    validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
        definition_yaml: yaml.to_string(),
        judge_available,
    })
}

#[test]
fn authoring_validation_returns_binding_and_graph_summary() {
    let outcome = validate_authoring(AUTHORING_YAML, false);

    assert!(outcome.valid, "{:?}", outcome.errors);
    assert_eq!(outcome.summary.participants, 1);
    assert_eq!(outcome.summary.nodes, 1);
    assert_eq!(outcome.summary.initial_nodes, vec!["answer"]);
    assert_eq!(outcome.summary.final_output_node.as_deref(), Some("answer"));
    assert_eq!(outcome.participants[0].binding, "writer");
    assert!(outcome.participants[0].required);
    assert!(outcome.participants[0].assigned);
    assert!(outcome.definition.is_some());
}

#[test]
fn authoring_validation_rejects_unknown_keys() {
    let yaml = AUTHORING_YAML.replacen(
        "name: Custom collaboration workflow",
        "verions: 1\nname: Custom collaboration workflow",
        1,
    );
    let outcome = validate_authoring(&yaml, false);

    assert!(!outcome.valid);
    assert_eq!(outcome.errors[0].code, "UNKNOWN_KEY");
    assert_eq!(outcome.errors[0].path, "$.verions");
}

#[test]
fn authoring_validation_rejects_multiple_entry_nodes() {
    let yaml = AUTHORING_YAML.replace(
        "      answer:\n",
        "      draft:\n        kind: bot_task\n        display_name: Draft\n        assignee:\n          type: bot_binding\n          binding: writer\n        instruction: Draft an answer.\n        transitions:\n          complete:\n            targets: [answer]\n      review:\n        kind: bot_task\n        display_name: Review\n        assignee:\n          type: bot_binding\n          binding: writer\n        instruction: Review the answer.\n        transitions:\n          complete:\n            targets: [answer]\n      answer:\n",
    );
    let outcome = validate_authoring(&yaml, false);

    assert!(!outcome.valid);
    assert_eq!(outcome.errors[0].code, "INVALID_DEFINITION");
    assert!(outcome.errors[0].message.contains("exactly one zero in-degree entry"));
}

#[test]
fn authoring_validation_rejects_judge_when_server_has_no_judge_provider() {
    let yaml = AUTHORING_YAML.replace(
        "        final_output: true",
        "        judge:\n          type: llm\n          criteria: [quality]\n          outcomes: [approved]\n        transitions:\n          approved:\n            targets: [publish]\n      publish:\n        kind: bot_task\n        display_name: Publish\n        assignee:\n          type: bot_binding\n          binding: writer\n        instruction: Publish the answer.\n        final_output: true",
    );
    let outcome = validate_authoring(&yaml, false);

    assert!(!outcome.valid);
    assert_eq!(outcome.errors[0].code, "UNAVAILABLE_FEATURE");
}

#[test]
fn authoring_validation_accepts_judge_when_server_has_judge_provider() {
    let yaml = AUTHORING_YAML.replace(
        "        final_output: true",
        "        judge:\n          type: llm\n          criteria: [quality]\n          outcomes: [approved]\n        transitions:\n          approved:\n            targets: [publish]\n      publish:\n        kind: bot_task\n        display_name: Publish\n        assignee:\n          type: bot_binding\n          binding: writer\n        instruction: Publish the answer.\n        final_output: true",
    );
    let outcome = validate_authoring(&yaml, true);

    assert!(outcome.valid, "{:?}", outcome.errors);
}

#[test]
fn authoring_validation_rejects_fields_not_supported_by_the_current_runtime() {
    let machine_fields = [
        ("input_schema", "    input_schema: {}\n    nodes:"),
        ("variables", "    variables:\n      draft: null\n    nodes:"),
        ("events", "    events:\n      approved: null\n    nodes:"),
    ];
    for (field, replacement) in machine_fields {
        let yaml = AUTHORING_YAML.replace("    nodes:", replacement);
        let outcome = validate_authoring(&yaml, false);
        assert!(!outcome.valid, "{field} must be rejected");
        assert!(
            outcome.errors[0].message.contains(field),
            "unexpected {field} diagnostic: {:?}",
            outcome.errors
        );
    }

    let node_fields = [
        (
            "output_contract",
            "        output_contract:\n          type: json\n        final_output: true",
        ),
        (
            "action",
            "        action:\n          type: tool\n        final_output: true",
        ),
    ];
    for (field, replacement) in node_fields {
        let yaml = AUTHORING_YAML.replace("        final_output: true", replacement);
        let outcome = validate_authoring(&yaml, false);
        assert!(!outcome.valid, "{field} must be rejected");
        assert!(
            outcome.errors[0].message.contains(field),
            "unexpected {field} diagnostic: {:?}",
            outcome.errors
        );
    }

    let yaml = AUTHORING_YAML.replace(
        "        final_output: true",
        "        transitions:\n          complete:\n            targets: [publish]\n            guard: approved\n      publish:\n        kind: bot_task\n        display_name: Publish\n        assignee:\n          type: bot_binding\n          binding: writer\n        instruction: Publish the answer.\n        final_output: true",
    );
    let outcome = validate_authoring(&yaml, false);
    assert!(!outcome.valid, "guard must be rejected");
    assert!(
        outcome.errors[0].message.contains("guard"),
        "unexpected guard diagnostic: {:?}",
        outcome.errors
    );
}

#[test]
fn validates_collaboration_template_seed_definitions() {
    for file_name in [
        "en-US/solution-and-risk-review.yaml",
        "en-US/single-bot-guided-answer.yaml",
        "en-US/parallel-expert-review.yaml",
        "en-US/write-and-review.yaml",
        "en-US/world-cup-preview-content-production.yaml",
        "en-US/micro-merchant-event-orchestration.yaml",
        "zh-CN/solution-and-risk-review.yaml",
        "zh-CN/single-bot-guided-answer.yaml",
        "zh-CN/parallel-expert-review.yaml",
        "zh-CN/write-and-review.yaml",
        "zh-CN/world-cup-preview-content-production.yaml",
        "zh-CN/micro-merchant-event-orchestration.yaml",
    ] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../seeds/collaboration-templates")
            .join(file_name);
        let raw = std::fs::read_to_string(&path).expect("seed yaml should be readable");
        let definition: CollaborationDefinition =
            serde_yaml::from_str(&raw).expect("seed yaml should parse");

        validate_definition(definition).expect("seed definition should validate");
    }
}

#[test]
fn default_collaboration_template_seed_documents_timeout_overrides() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../seeds/collaboration-templates/en-US/solution-and-risk-review.yaml");
    let raw = std::fs::read_to_string(&path).expect("seed yaml should be readable");
    let definition: CollaborationDefinition =
        serde_yaml::from_str(&raw).expect("seed yaml should parse");
    let state_machine = match &definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine runtime"),
    };

    assert_eq!(state_machine.defaults.node_timeout_ms, Some(120_000));
    assert_eq!(state_machine.defaults.max_attempts, 2);
    assert_eq!(
        state_machine.nodes["frame_task"].node_timeout_ms,
        Some(60_000)
    );
    assert_eq!(
        state_machine.nodes["synthesize_final_answer"].node_timeout_ms,
        Some(180_000)
    );
    assert_eq!(
        state_machine.nodes["synthesize_final_answer"].max_attempts,
        Some(3)
    );

    validate_definition(definition).expect("seed definition should validate");
}

#[test]
fn defaults_optional_definition_identity_and_state_machine_fields() {
    let definition: CollaborationDefinition = serde_yaml::from_str(
        r#"
name: 最小状态机
participants:
  driver:
    required: true
runtime:
  kind: state_machine
  state_machine:
    nodes:
      answer:
        kind: bot_task
        display_name: 回答
        assignee:
          type: bot_binding
          binding: driver
        instruction: 输出最终回答。
        final_output: true
"#,
    )
    .expect("minimal yaml should parse with defaults");

    assert_eq!(definition.api_version, "bcs.collaboration/v1");
    assert_eq!(definition.version, 1);
    uuid::Uuid::parse_str(&definition.id).expect("missing id should default to uuid");
    let state_machine = match &definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine runtime"),
    };
    assert_eq!(state_machine.version, 1);
    assert_eq!(state_machine.graph_mode, StateMachineGraphMode::Acyclic);

    let compiled = validate_definition(definition).expect("minimal definition should validate");
    assert_eq!(compiled.initial_nodes, vec!["answer".to_string()]);
    let requires = compiled
        .definition
        .requires
        .expect("requires should be inferred");
    assert!(
        requires
            .server_features
            .contains(&"state_machine.graph_mode.acyclic".to_string())
    );
    assert!(
        requires
            .server_features
            .contains(&"state_machine.node.kind.bot_task".to_string())
    );
    assert!(
        requires
            .server_features
            .contains(&"state_machine.transitions.complete".to_string())
    );
    assert!(
        requires
            .bot_runtime_features
            .contains(&"delivery.chat_send_task_compat".to_string())
    );
}

#[test]
fn validates_transition_based_risk_review_definition() {
    let definition = risk_review_definition();

    let compiled = validate_definition(definition).expect("definition should validate");

    assert_eq!(compiled.initial_nodes, vec!["understand".to_string()]);
    assert_eq!(
        compiled.upstreams["synthesize"],
        vec![
            "compliance_review".to_string(),
            "strategy_review".to_string()
        ]
    );
}

#[test]
fn validates_template_participants_without_bot_id() {
    let mut definition = risk_review_definition();
    for participant in definition.participants.values_mut() {
        participant.bot_id = None;
    }

    let compiled = validate_definition(definition).expect("template definition should validate");

    assert_eq!(compiled.initial_nodes, vec!["understand".to_string()]);
}

#[test]
fn validate_definition_ignores_legacy_bcs_participant_role() {
    let mut definition = risk_review_definition();
    definition
        .participants
        .get_mut("driver")
        .expect("driver participant")
        .bcs_participant_role = Some(bcs_domain::ParticipantRole::Driver);

    validate_definition(definition).expect("legacy role should be ignored during read/execute");
}

#[test]
fn reject_explicit_participant_roles_rejects_new_input() {
    let mut definition = risk_review_definition();
    definition
        .participants
        .get_mut("driver")
        .expect("driver participant")
        .bcs_participant_role = Some(bcs_domain::ParticipantRole::Driver);

    let error = reject_explicit_participant_roles(&definition)
        .expect_err("explicit role should be rejected for new input");

    assert!(error.to_string().contains("bcs_participant_role"));
}

#[test]
fn rejects_custom_outcome_transition_without_judge() {
    let mut definition = risk_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine
        .nodes
        .get_mut("understand")
        .expect("node")
        .transitions
        .insert(
            "approved".to_string(),
            bcs_domain::StateMachineTransition {
                targets: vec!["synthesize".to_string()],
                guard: None,
            },
        );

    let error = validate_definition(definition).expect_err("custom outcomes are not executable");
    assert!(error.to_string().contains("transitions.complete"));
}

#[test]
fn validates_judge_outcome_transitions_with_inferred_requires() {
    let definition: CollaborationDefinition = serde_yaml::from_str(
        r#"
api_version: bcs.collaboration/v1
id: judge_review
version: 1
name: Judge Review
participants:
  driver:
    bot_id: risk_driver_bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      synthesize:
        kind: bot_task
        display_name: 汇总结论
        assignee:
          type: bot_binding
          binding: driver
        instruction: 汇总所有专家意见。
        judge:
          type: llm
          criteria:
            - 是否覆盖策略意见
          outcomes: [approved, rejected]
        transitions:
          approved:
            targets: [publish]
          rejected:
            targets: [revise]
      publish:
        kind: bot_task
        display_name: 发布
        assignee:
          type: bot_binding
          binding: driver
        instruction: 输出最终结论。
        final_output: true
      revise:
        kind: bot_task
        display_name: 修订
        assignee:
          type: bot_binding
          binding: driver
        instruction: 说明需要修订的内容。
        transitions:
          complete:
            targets: [publish]
"#,
    )
    .expect("fixture should parse");

    let compiled = validate_definition(definition).expect("judge definition should validate");

    assert_eq!(compiled.initial_nodes, vec!["synthesize".to_string()]);
    assert_eq!(
        compiled.upstreams["publish"],
        vec!["revise".to_string(), "synthesize".to_string()]
    );
    assert_eq!(compiled.upstreams["revise"], vec!["synthesize".to_string()]);
    let requires = compiled
        .definition
        .requires
        .expect("requires should be inferred");
    assert!(
        requires
            .server_features
            .contains(&"state_machine.node.judge".to_string())
    );
    assert!(
        requires
            .server_features
            .contains(&"state_machine.outcome_transitions".to_string())
    );
}

#[test]
fn infers_judge_requires_without_capability_declaration() {
    let mut definition = risk_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine
        .nodes
        .get_mut("understand")
        .expect("node")
        .judge = Some(bcs_domain::JudgePolicy {
        judge_type: Some("llm".to_string()),
        criteria: vec!["是否完成理解".to_string()],
        outcomes: vec!["complete".to_string()],
        extensions: Default::default(),
    });

    let compiled = validate_definition(definition).expect("judge requires should be inferred");
    let requires = compiled
        .definition
        .requires
        .expect("requires should be inferred");

    assert!(
        requires
            .server_features
            .contains(&"state_machine.node.judge".to_string())
    );
    assert!(
        requires
            .server_features
            .contains(&"state_machine.outcome_transitions".to_string())
    );
}

#[test]
fn validates_human_input_with_natural_language_judge_outcomes() {
    let compiled = validate_definition(human_review_definition())
        .expect("human input definition should validate");

    assert_eq!(compiled.initial_nodes, vec!["review".to_string()]);
    let requires = compiled
        .definition
        .requires
        .expect("requires should be inferred");
    assert!(
        requires
            .server_features
            .contains(&"state_machine.node.kind.human_input".to_string())
    );
    assert!(
        requires
            .server_features
            .contains(&"state_machine.outcome_transitions".to_string())
    );
}

#[test]
fn validates_human_input_without_judge_uses_complete_transition() {
    let mut definition = human_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine.nodes.remove("revise");
    let review = state_machine.nodes.get_mut("review").expect("review node");
    review.judge = None;
    review.transitions.clear();
    review.transitions.insert(
        "complete".to_string(),
        bcs_domain::StateMachineTransition {
            targets: vec!["publish".to_string()],
            guard: None,
        },
    );

    validate_definition(definition).expect("judge-less human input should validate");
}

#[test]
fn validates_multiple_human_inputs_with_explicit_dependency_order() {
    let definition: CollaborationDefinition = serde_yaml::from_str(
        r#"
name: Sequential Human Review
participants:
  driver:
    bot_id: risk_driver_bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    nodes:
      first_review:
        kind: human_input
        display_name: 第一次评审
        instruction: 请完成第一次评审。
        node_timeout_ms: 60000
        transitions:
          complete: { targets: [second_review] }
      second_review:
        kind: human_input
        display_name: 第二次评审
        instruction: 请完成第二次评审。
        node_timeout_ms: 60000
        transitions:
          complete: { targets: [publish] }
      publish:
        kind: bot_task
        display_name: 发布
        assignee:
          type: bot_binding
          binding: driver
        instruction: 发布结果。
        final_output: true
"#,
    )
    .expect("fixture should parse");

    validate_definition(definition).expect("ordered HumanInput nodes should validate");
}

#[test]
fn rejects_human_inputs_that_may_wait_concurrently() {
    let definition: CollaborationDefinition = serde_yaml::from_str(
        r#"
name: Parallel Human Review
participants:
  driver:
    bot_id: risk_driver_bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    nodes:
      prepare:
        kind: bot_task
        display_name: 准备材料
        assignee:
          type: bot_binding
          binding: driver
        instruction: 准备评审材料。
        transitions:
          complete: { targets: [review_a, review_b] }
      review_a:
        kind: human_input
        display_name: A 评审
        instruction: 完成 A 评审。
        node_timeout_ms: 60000
        transitions:
          complete: { targets: [publish] }
      review_b:
        kind: human_input
        display_name: B 评审
        instruction: 完成 B 评审。
        node_timeout_ms: 60000
        transitions:
          complete: { targets: [publish] }
      publish:
        kind: bot_task
        display_name: 发布
        assignee:
          type: bot_binding
          binding: driver
        instruction: 发布结果。
        final_output: true
"#,
    )
    .expect("fixture should parse");

    let error = validate_definition(definition)
        .expect_err("parallel HumanInput nodes must be rejected in MVP");
    assert!(error.to_string().contains("may wait concurrently"));
}

#[test]
fn rejects_human_input_bot_assignee() {
    let mut definition = human_review_definition();
    human_node_mut(&mut definition).assignee = Some(bcs_domain::StateMachineAssignee::BotBinding {
        binding: "driver".to_string(),
    });

    let error = validate_definition(definition).expect_err("human assignee must be rejected");
    assert!(error.to_string().contains("must not define assignee"));
}

#[test]
fn rejects_human_input_without_explicit_timeout() {
    let mut definition = human_review_definition();
    human_node_mut(&mut definition).node_timeout_ms = None;

    let error = validate_definition(definition).expect_err("human timeout is required");
    assert!(error.to_string().contains("node_timeout_ms is required"));
}

#[test]
fn rejects_human_input_attempts_and_final_output() {
    let mut with_attempts = human_review_definition();
    human_node_mut(&mut with_attempts).max_attempts = Some(2);
    let error = validate_definition(with_attempts).expect_err("human attempts must be rejected");
    assert!(error.to_string().contains("must not define max_attempts"));

    let mut final_output = human_review_definition();
    human_node_mut(&mut final_output).final_output = true;
    let error = validate_definition(final_output).expect_err("human final output must be rejected");
    assert!(error.to_string().contains("must not be final_output"));
}

#[test]
fn rejects_duplicate_or_mismatched_human_judge_outcomes() {
    let mut duplicate = human_review_definition();
    human_node_mut(&mut duplicate)
        .judge
        .as_mut()
        .expect("judge")
        .outcomes
        .push("approved".to_string());
    let error = validate_definition(duplicate).expect_err("duplicate outcome must be rejected");
    assert!(error.to_string().contains("duplicate judge outcome"));

    let mut missing_transition = human_review_definition();
    human_node_mut(&mut missing_transition)
        .transitions
        .remove("rejected");
    let error =
        validate_definition(missing_transition).expect_err("missing transition must be rejected");
    assert!(
        error
            .to_string()
            .contains("judge outcome has no transition")
    );

    let mut undeclared_transition = human_review_definition();
    human_node_mut(&mut undeclared_transition)
        .transitions
        .insert(
            "manual".to_string(),
            bcs_domain::StateMachineTransition::default(),
        );
    let error = validate_definition(undeclared_transition)
        .expect_err("undeclared transition must be rejected");
    assert!(
        error
            .to_string()
            .contains("transition outcome is not declared")
    );
}

#[test]
fn human_input_does_not_bypass_cycle_rejection() {
    let mut definition = human_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine
        .nodes
        .get_mut("revise")
        .expect("revise")
        .transitions
        .insert(
            "complete".to_string(),
            bcs_domain::StateMachineTransition {
                targets: vec!["review".to_string()],
                guard: None,
            },
        );

    let error = validate_definition(definition).expect_err("cycle must still be rejected");
    assert!(error.to_string().contains("must be acyclic"));
}

fn human_node_mut(
    definition: &mut CollaborationDefinition,
) -> &mut bcs_domain::StateMachineNodeDefinition {
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine.nodes.get_mut("review").expect("review node")
}

fn human_review_definition() -> CollaborationDefinition {
    serde_yaml::from_str(
        r#"
api_version: bcs.collaboration/v1
id: human_review
version: 1
name: Human Review
participants:
  driver:
    bot_id: risk_driver_bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    defaults:
      node_timeout_ms: 120000
      max_attempts: 3
    nodes:
      review:
        kind: human_input
        display_name: 人工评审
        instruction: 请直接回复自然语言评审意见。
        node_timeout_ms: 60000
        judge:
          type: llm
          criteria:
            - 是否可以发布
          outcomes: [approved, rejected]
        transitions:
          approved: { targets: [publish] }
          rejected: { targets: [revise] }
      publish:
        kind: bot_task
        display_name: 发布
        assignee:
          type: bot_binding
          binding: driver
        instruction: 发布结果。
        final_output: true
      revise:
        kind: bot_task
        display_name: 修订
        assignee:
          type: bot_binding
          binding: driver
        instruction: 根据人工意见修订一次。
        transitions:
          complete: { targets: [publish] }
"#,
    )
    .expect("human review fixture should parse")
}

#[test]
fn rejects_fields_not_supported_by_the_current_runtime() {
    let mut definition = risk_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine.input_schema = Some(serde_json::json!({"type": "object"}));
    let error = validate_definition(definition).expect_err("input_schema must be rejected");
    assert!(error.to_string().contains("input_schema"));

    let mut definition = risk_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine
        .variables
        .insert("draft".to_string(), serde_json::Value::Null);
    let error = validate_definition(definition).expect_err("variables must be rejected");
    assert!(error.to_string().contains("variables"));

    let mut definition = risk_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine
        .events
        .insert("approved".to_string(), serde_json::Value::Null);
    let error = validate_definition(definition).expect_err("events must be rejected");
    assert!(error.to_string().contains("events"));

    let mut definition = risk_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine
        .nodes
        .get_mut("understand")
        .expect("node")
        .output_contract = Some(bcs_domain::OutputContract::default());
    let error = validate_definition(definition).expect_err("output_contract must be rejected");
    assert!(error.to_string().contains("output_contract"));

    let mut definition = risk_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine.nodes.get_mut("understand").expect("node").action =
        Some(bcs_domain::StateMachineAction::default());
    let error = validate_definition(definition).expect_err("action must be rejected");
    assert!(error.to_string().contains("action"));

    let mut definition = risk_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine
        .nodes
        .get_mut("understand")
        .expect("node")
        .transitions
        .get_mut("complete")
        .expect("transition")
        .guard = Some("approved".to_string());
    let error = validate_definition(definition).expect_err("guard must be rejected");
    assert!(error.to_string().contains("guard"));
}

fn risk_review_definition() -> CollaborationDefinition {
    serde_yaml::from_str(
        r#"
api_version: bcs.collaboration/v1
id: risk_review
version: 1
name: 风控专家会诊
participants:
  driver:
    bot_id: risk_driver_bot
    required: true
  strategy:
    bot_id: risk_strategy_bot
    required: true
  compliance:
    bot_id: risk_compliance_bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      understand:
        kind: bot_task
        display_name: 材料理解
        assignee:
          type: bot_binding
          binding: driver
        instruction: 理解用户问题，输出一段文本。
        transitions:
          complete:
            targets: [strategy_review, compliance_review]
      strategy_review:
        kind: bot_task
        display_name: 策略评审
        assignee:
          type: bot_binding
          binding: strategy
        instruction: 从策略角度输出一段文本。
        transitions:
          complete:
            targets: [synthesize]
      compliance_review:
        kind: bot_task
        display_name: 合规评审
        assignee:
          type: bot_binding
          binding: compliance
        instruction: 从合规角度输出一段文本。
        transitions:
          complete:
            targets: [synthesize]
      synthesize:
        kind: bot_task
        display_name: 汇总结论
        assignee:
          type: bot_binding
          binding: driver
        instruction: 汇总所有专家意见。
        final_output: true
"#,
    )
    .expect("fixture should parse")
}
