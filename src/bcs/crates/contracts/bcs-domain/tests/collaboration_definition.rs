use bcs_domain::{
    CollaborationDefinition, CollaborationRuntimeDefinition, StateMachineNodeKind,
    StateMachineNodeRun, StateMachineNodeStatus,
};

#[test]
fn risk_review_yaml_deserializes_transition_based_state_machine() {
    let yaml = r#"
api_version: bcs.collaboration/v1
id: risk_review
version: 1
name: 风控专家会诊
metadata:
  description: 对风控方案进行多专家评估、冲突对齐和结论汇总。
  labels:
    scenario: risk_review
participants:
  driver:
    bot_id: risk_driver_bot
    display_name: 风控负责人
    description: 负责理解材料并汇总专家结论。
    required: true
  strategy:
    bot_id: risk_strategy_bot
    display_name: 策略专家
    description: 负责从策略角度评审方案。
    required: true
  compliance:
    bot_id: risk_compliance_bot
    display_name: 合规专家
    description: 负责从合规角度识别风险。
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
"#;

    let definition: CollaborationDefinition =
        serde_yaml::from_str(yaml).expect("risk review yaml should parse");

    assert_eq!(definition.id, "risk_review");
    assert!(definition.participants.contains_key("driver"));
    assert_eq!(
        definition.participants["driver"].display_name.as_deref(),
        Some("风控负责人")
    );
    assert_eq!(
        definition.participants["driver"].description.as_deref(),
        Some("负责理解材料并汇总专家结论。")
    );
    let state_machine = match definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine runtime"),
    };
    assert_eq!(
        state_machine.nodes["understand"].kind,
        StateMachineNodeKind::BotTask
    );
    assert_eq!(
        state_machine.nodes["understand"].transitions["complete"].targets,
        vec![
            "strategy_review".to_string(),
            "compliance_review".to_string()
        ]
    );
    assert!(state_machine.nodes["synthesize"].final_output);
}

#[test]
fn human_input_yaml_deserializes_without_bot_assignee() {
    let yaml = r#"
api_version: bcs.collaboration/v1
id: human_review
version: 1
name: Human Review
participants: {}
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: human_input
        display_name: 人工评审
        instruction: 请直接回复自然语言评审意见。
        node_timeout_ms: 60000
        judge:
          type: llm
          criteria: [是否可以发布]
          outcomes: [approved, rejected]
        transitions:
          approved: { targets: [] }
          rejected: { targets: [] }
"#;

    let definition: CollaborationDefinition =
        serde_yaml::from_str(yaml).expect("human input yaml should parse");
    let state_machine = match definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => panic!("expected state machine runtime"),
    };
    let review = &state_machine.nodes["review"];

    assert_eq!(review.kind, StateMachineNodeKind::HumanInput);
    assert!(review.assignee.is_none());
    assert_eq!(review.node_timeout_ms, Some(60_000));
    assert_eq!(
        review.judge.as_ref().expect("judge").outcomes,
        vec!["approved".to_string(), "rejected".to_string()]
    );
}

#[test]
fn human_node_run_serializes_output_and_responder_metadata() {
    let node = StateMachineNodeRun {
        run_id: "run-1".to_string(),
        node_id: "review".to_string(),
        status: StateMachineNodeStatus::Running,
        attempt: 0,
        node_timeout_ms: Some(60_000),
        timeout_deadline_ms: Some(61_000),
        max_attempts: 1,
        assignee_bot_id: None,
        outcome: Some("approved".to_string()),
        responded_by: Some("human_123".to_string()),
        delivery_request_id: None,
        bot_delivery_run_id: None,
        artifact_text: None,
        error: None,
        started_at: Some(1_000),
        completed_at: None,
    };

    let serialized = serde_json::to_value(&node).expect("node should serialize");

    assert_eq!(serialized["outcome"], "approved");
    assert_eq!(serialized["responded_by"], "human_123");
    assert!(serialized.get("assignee_bot_id").is_none());
}
