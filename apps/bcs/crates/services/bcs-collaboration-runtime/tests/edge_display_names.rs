use bcs_collaboration_runtime::{fixed_loop::compile_authoring_definition, validate_authoring_definition_yaml};
use bcs_config_api::FixedLoopLimits;
use bcs_service_api::ValidateCollaborationDefinitionYamlCommand;
use serde_json::{json, Value};

fn definition() -> Value {
    let mut value: Value = serde_yaml::from_str(include_str!(
        "../../../../seeds/collaboration-templates/zh-CN/research-writing-loops.yaml"
    )).unwrap();
    let nodes = &mut value["runtime"]["state_machine"]["nodes"];
    for (id, name) in [("research_loop", "补充资料"), ("writing_loop", "继续修订")] {
        nodes[id]["loop"]["continue_display_name"] = json!(name);
        nodes[id]["transitions"]["approved"]["display_name"] = json!("通过评审");
        nodes[id]["transitions"]["exhausted"]["display_name"] = json!("处理未通过结果");
    }
    nodes["research_loop"]["loop"]["nodes"]["research"]["transitions"]["complete"]["display_name"] = json!("提交资料");
    nodes["polish"]["transitions"]["complete"]["display_name"] = json!("交付成稿");
    value
}

fn validate(value: &Value) -> bcs_service_api::CollaborationDefinitionValidationOutcome {
    validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
        definition_yaml: serde_yaml::to_string(value).unwrap(), judge_available: true,
    })
}

#[test]
fn preview_names_follow_body_continue_break_and_exhausted_routes_without_changing_outcomes() {
    let validation = validate(&definition());
    assert!(validation.valid, "{:?}", validation.errors);
    let graph = validation.graph.unwrap();
    assert_eq!(graph.loops["research_loop"].continue_display_name.as_deref(), Some("补充资料"));
    assert_eq!(graph.loops["writing_loop"].continue_display_name.as_deref(), Some("继续修订"));
    for edge in &graph.edges {
        if let Some(route) = &edge.loop_route {
            let source = graph.nodes.iter().find(|node| node.node_id == edge.source).unwrap().execution.as_ref().unwrap();
            let expected = match route.kind {
                bcs_domain::StateMachineLoopRouteKind::Continue => {
                    assert_eq!(edge.outcome, "revise");
                    if source.loop_id == "research_loop" { "补充资料" } else { "继续修订" }
                }
                bcs_domain::StateMachineLoopRouteKind::Break => {
                    assert_eq!(edge.outcome, "approved");
                    "通过评审"
                }
                bcs_domain::StateMachineLoopRouteKind::Exhausted => {
                    assert_eq!(edge.outcome, "revise");
                    assert_eq!(route.logical_outcome, "exhausted");
                    "处理未通过结果"
                }
            };
            assert_eq!(edge.display_name.as_deref(), Some(expected));
        }
    }
    assert!(graph.edges.iter().any(|edge| edge.display_name.as_deref() == Some("提交资料")));
    assert_eq!(graph.edges.iter().find(|edge| edge.source == "polish").unwrap().display_name.as_deref(), Some("交付成稿"));
}

#[test]
fn display_names_do_not_change_execution_ids_routes_or_optional_field_compatibility() {
    let named = definition();
    let mut unnamed = named.clone();
    fn strip(value: &mut Value) {
        match value {
            Value::Object(fields) => {
                fields.remove("continue_display_name");
                if fields.contains_key("targets") { fields.remove("display_name"); }
                fields.values_mut().for_each(strip);
            }
            Value::Array(values) => values.iter_mut().for_each(strip),
            _ => {}
        }
    }
    strip(&mut unnamed);
    let compile = |value: Value| {
        let mut definition: bcs_domain::CollaborationDefinition = serde_json::from_value(value).unwrap();
        definition.id = "edge-name-identity".into();
        compile_authoring_definition(definition, &FixedLoopLimits::default()).unwrap().plan.unwrap()
    };
    let old = compile(unnamed.clone());
    let new = compile(named);
    assert_eq!(old.node_metadata, new.node_metadata);
    assert_eq!(old.edge_metadata, new.edge_metadata);
    let projection = serde_json::to_value(validate(&unnamed).graph.unwrap()).unwrap();
    assert!(projection["edges"].as_array().unwrap().iter().all(|edge| edge.get("display_name").is_none()));
    assert!(projection["loops"].as_object().unwrap().values().all(|view| view.get("continue_display_name").is_none()));
    let serialized = serde_json::to_value(&old).unwrap();
    let restored: bcs_domain::StateMachineExecutionPlan = serde_json::from_value(serialized.clone()).unwrap();
    assert_eq!(serde_json::to_value(restored).unwrap(), serialized);
}

#[test]
fn rejects_invalid_names_at_precise_authoring_paths() {
    for pointer in [
        "/runtime/state_machine/nodes/research_loop/loop/continue_display_name",
        "/runtime/state_machine/nodes/research_loop/transitions/exhausted/display_name",
        "/runtime/state_machine/nodes/research_loop/loop/nodes/research/transitions/complete/display_name",
        "/runtime/state_machine/nodes/polish/transitions/complete/display_name",
    ] {
        for invalid in [json!(""), json!(" \n\t"), Value::Null, json!(42), json!({"en": "Label"})] {
            let mut value = definition();
            *value.pointer_mut(pointer).unwrap() = invalid;
            let result = validate(&value);
            assert!(!result.valid);
            assert_eq!(result.errors[0].path, format!("${}", pointer.replace('/', ".")));
        }
    }
}

#[test]
fn ordinary_dag_transition_names_are_projected_to_every_target() {
    let value = json!({"name":"Fan out", "participants":{"writer":{"required":true}},
        "runtime":{"kind":"state_machine","state_machine":{"version":1,"graph_mode":"acyclic","nodes":{
            "start":{"kind":"bot_task","display_name":"Start","assignee":{"type":"bot_binding","binding":"writer"},"instruction":"Start", "transitions":{"complete":{"targets":["a","b"],"display_name":"开始处理"}}},
            "a":{"kind":"bot_task","display_name":"A","assignee":{"type":"bot_binding","binding":"writer"},"instruction":"A", "transitions":{"complete":{"targets":["end"]}}},
            "b":{"kind":"bot_task","display_name":"B","assignee":{"type":"bot_binding","binding":"writer"},"instruction":"B", "transitions":{"complete":{"targets":["end"]}}},
            "end":{"kind":"bot_task","display_name":"End","assignee":{"type":"bot_binding","binding":"writer"},"instruction":"End","final_output":true}
        }}}});
    let result = validate(&value);
    assert!(result.valid, "{:?}", result.errors);
    let graph = result.graph.unwrap();
    assert!(graph.loops.is_empty());
    assert_eq!(graph.edges.iter().filter(|edge| edge.display_name.as_deref() == Some("开始处理")).count(), 2);
}
