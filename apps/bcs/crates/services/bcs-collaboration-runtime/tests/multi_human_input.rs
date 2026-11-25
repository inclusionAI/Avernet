//! Frontend HumanInput bound to Humans (issue #2609).
//!
//! A frontend HumanInput may carry a `runtime_actor` assignee, and HumanInput
//! nodes bound to different actors may wait at the same time. Everything else
//! keeps the original "explicit dependency order" rule.

use bcs_collaboration_runtime::validate_authoring_definition_yaml;
use bcs_service_api::ValidateCollaborationDefinitionYamlCommand;

/// One voting round: a Bot opens the vote, two Humans and one Bot vote in
/// parallel, the referee tallies. Mirrors the undercover vote graph.
const VOTE_ROUND_YAML: &str = r#"
name: Undercover vote round
participants:
  referee:
    required: true
  bot_player:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      vote_start:
        kind: bot_task
        display_name: 开投
        assignee: { type: bot_binding, binding: bot_player }
        instruction: 确认开始投票。
        transitions:
          complete: { targets: [vote_1, vote_2, vote_3] }
      vote_1:
        kind: human_input
        display_name: 1号投票
        assignee: { type: runtime_actor, actor: human_a }
        instruction: 投出你怀疑的座位号。
        node_timeout_ms: 600000
        transitions:
          complete: { targets: [tally] }
      vote_2:
        kind: human_input
        display_name: 2号投票
        assignee: { type: runtime_actor, actor: human_b }
        instruction: 投出你怀疑的座位号。
        node_timeout_ms: 600000
        transitions:
          complete: { targets: [tally] }
      vote_3:
        kind: bot_task
        display_name: 3号投票
        assignee: { type: bot_binding, binding: bot_player }
        instruction: 投出你怀疑的座位号。
        transitions:
          complete: { targets: [tally] }
      tally:
        kind: bot_task
        display_name: 计票
        assignee: { type: bot_binding, binding: referee }
        instruction: 汇总票面并计票。
        final_output: true
"#;

fn validate(yaml: &str) -> bcs_service_api::CollaborationDefinitionValidationOutcome {
    validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
        definition_yaml: yaml.to_string(),
        judge_available: false,
    })
}

#[test]
fn concurrent_human_votes_with_distinct_assignees_validate() {
    let outcome = validate(VOTE_ROUND_YAML);
    assert!(outcome.valid, "{:?}", outcome.errors);
}

#[test]
fn concurrent_human_votes_without_distinct_assignees_are_rejected() {
    // Same Human on both nodes: keep the original ordering rule.
    let same = VOTE_ROUND_YAML.replace("actor: human_b", "actor: human_a");
    let outcome = validate(&same);
    assert!(!outcome.valid);
    assert!(format!("{:?}", outcome.errors).contains("may wait concurrently"));

    // One unbound frontend node is open to every Present Human, so it may
    // still collide with the bound one.
    let mixed = VOTE_ROUND_YAML.replace(
        "        assignee: { type: runtime_actor, actor: human_b }\n",
        "",
    );
    let outcome = validate(&mixed);
    assert!(!outcome.valid);
    assert!(format!("{:?}", outcome.errors).contains("may wait concurrently"));
}

#[test]
fn single_unbound_frontend_human_input_still_validates() {
    // The pre-#2609 single-Human shape: no assignee anywhere.
    let legacy = VOTE_ROUND_YAML
        .replace("        assignee: { type: runtime_actor, actor: human_a }\n", "")
        .replace(
            "          complete: { targets: [vote_1, vote_2, vote_3] }",
            "          complete: { targets: [vote_1, vote_3] }",
        );
    let legacy = legacy[..legacy.find("      vote_2:").unwrap()].to_string()
        + &legacy[legacy.find("      vote_3:").unwrap()..];
    let outcome = validate(&legacy);
    assert!(outcome.valid, "{:?}", outcome.errors);
}
