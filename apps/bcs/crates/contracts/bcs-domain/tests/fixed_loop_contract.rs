use bcs_domain::{CollaborationDefinition, LoopContext, StateMachineNodeDefinition};
use serde_json::json;

#[test]
fn ordinary_nodes_omit_loop_and_require_loop_lists_when_present() {
    let node: StateMachineNodeDefinition = serde_json::from_value(json!({
        "kind": "bot_task", "display_name": "Answer", "instruction": "Answer"
    })).unwrap();
    assert!(serde_json::to_value(node).unwrap().get("loop").is_none());
    let value = json!({
        "kind": "loop", "display_name": "Rounds", "loop": {
            "mode": "fixed", "max_iterations": 2, "entry_node": "entry", "result_node": "entry",
            "continue_outcomes": ["complete"], "break_outcomes": [], "exhausted_outcome": "exhausted",
            "nodes": { "entry": {"kind": "bot_task", "display_name": "Entry"} }
        }
    });
    let loop_node: StateMachineNodeDefinition = serde_json::from_value(value.clone()).unwrap();
    assert!(serde_json::to_value(loop_node).unwrap().get("final_output").is_none());
    for missing in ["continue_outcomes", "break_outcomes", "max_iterations"] {
        let mut invalid = value.clone();
        invalid["loop"].as_object_mut().unwrap().remove(missing);
        assert!(serde_json::from_value::<StateMachineNodeDefinition>(invalid).is_err(), "{missing}");
    }
    let mut unknown = value;
    unknown["loop"]["while"] = json!(true);
    assert!(serde_json::from_value::<StateMachineNodeDefinition>(unknown).is_err());
}

#[test]
fn loop_context_distinguishes_explicit_null_from_missing_previous_result() {
    let mut first = json!({"loop_id": "rounds", "iteration": 1, "max_iterations": 3, "previous_result": null});
    let context: LoopContext = serde_json::from_value(first.clone()).unwrap();
    assert_eq!(serde_json::to_value(context).unwrap(), first);
    first.as_object_mut().unwrap().remove("previous_result");
    assert!(serde_json::from_value::<LoopContext>(first).is_err());
    let second = json!({"loop_id": "rounds", "iteration": 2, "max_iterations": 3, "previous_result": {
        "iteration": 1, "result_node_id": "decision", "execution_node_id": "ln-result", "outcome": "continue",
        "output": "Persisted result", "completed_at": 123
    }});
    assert_eq!(serde_json::to_value(serde_json::from_value::<LoopContext>(second.clone()).unwrap()).unwrap(), second);
}

#[test]
fn judge_capability_includes_loop_body() {
    let definition: CollaborationDefinition = serde_json::from_value(json!({
        "name": "Loop", "runtime": {"kind": "state_machine", "state_machine": {"version": 2, "nodes": {
            "rounds": {"kind": "loop", "display_name": "Rounds", "loop": {
                "mode": "fixed", "max_iterations": 2, "entry_node": "entry", "result_node": "entry",
                "continue_outcomes": ["continue"], "break_outcomes": [], "exhausted_outcome": "exhausted",
                "nodes": {"entry": {"kind": "bot_task", "display_name": "Entry", "judge": {"criteria": ["Check"], "outcomes": ["continue"]}}}
            }}
        }}}
    })).unwrap();
    assert!(definition.uses_judge());
}
