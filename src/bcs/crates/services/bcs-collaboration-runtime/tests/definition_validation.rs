use bcs_collaboration_runtime::{reject_explicit_participant_roles, validate_definition};
use bcs_domain::{CollaborationDefinition, CollaborationRuntimeDefinition, StateMachineGraphMode};

#[test]
fn validates_collaboration_template_seed_definitions() {
    for file_name in [
        "en-US/solution-and-risk-review.yaml",
        "en-US/single-bot-guided-answer.yaml",
        "en-US/parallel-expert-review.yaml",
        "en-US/write-and-review.yaml",
        "en-US/world-cup-preview-media-copy.yaml",
        "en-US/micro-merchant-event-orchestration.yaml",
        "zh-CN/solution-and-risk-review.yaml",
        "zh-CN/single-bot-guided-answer.yaml",
        "zh-CN/parallel-expert-review.yaml",
        "zh-CN/write-and-review.yaml",
        "zh-CN/world-cup-preview-media-copy.yaml",
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
    let requires = compiled.definition.requires.expect("requires should be inferred");
    assert!(requires.server_features.contains(&"state_machine.graph_mode.acyclic".to_string()));
    assert!(requires.server_features.contains(&"state_machine.node.kind.bot_task".to_string()));
    assert!(requires.server_features.contains(&"state_machine.transitions.complete".to_string()));
    assert!(requires.bot_runtime_features.contains(&"delivery.chat_send_task_compat".to_string()));
}

#[test]
fn validates_transition_based_risk_review_definition() {
    let definition = risk_review_definition();

    let compiled = validate_definition(definition).expect("definition should validate");

    assert_eq!(compiled.initial_nodes, vec!["understand".to_string()]);
    assert_eq!(
        compiled.upstreams["synthesize"],
        vec!["compliance_review".to_string(), "strategy_review".to_string()]
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
fn rejects_custom_outcome_transition_in_mvp() {
    let mut definition = risk_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine.nodes.get_mut("understand").expect("node").transitions.insert(
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
"#,
    )
    .expect("fixture should parse");

    let compiled = validate_definition(definition).expect("judge definition should validate");

    assert_eq!(compiled.initial_nodes, vec!["synthesize".to_string()]);
    assert_eq!(compiled.upstreams["publish"], vec!["synthesize".to_string()]);
    assert_eq!(compiled.upstreams["revise"], vec!["synthesize".to_string()]);
    let requires = compiled.definition.requires.expect("requires should be inferred");
    assert!(requires.server_features.contains(&"state_machine.node.judge".to_string()));
    assert!(requires.server_features.contains(&"state_machine.outcome_transitions".to_string()));
}

#[test]
fn infers_judge_requires_without_capability_declaration() {
    let mut definition = risk_review_definition();
    let state_machine = match &mut definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine"),
    };
    state_machine.nodes.get_mut("understand").expect("node").judge =
        Some(bcs_domain::JudgePolicy {
            judge_type: Some("llm".to_string()),
            criteria: vec!["是否完成理解".to_string()],
            outcomes: vec!["complete".to_string()],
            extensions: Default::default(),
        });

    let compiled = validate_definition(definition).expect("judge requires should be inferred");
    let requires = compiled.definition.requires.expect("requires should be inferred");

    assert!(requires.server_features.contains(&"state_machine.node.judge".to_string()));
    assert!(requires.server_features.contains(&"state_machine.outcome_transitions".to_string()));
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
