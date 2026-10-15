//! Read-only logical descriptors; execution topology and identities remain in the plan.

use std::collections::BTreeMap;
use bcs_domain::{CollaborationDefinition, CollaborationRuntimeDefinition};
use bcs_service_api::StateMachineLoopGraphView;

pub(crate) fn loop_graph_descriptors(definition: &CollaborationDefinition) -> BTreeMap<String, StateMachineLoopGraphView> {
    let CollaborationRuntimeDefinition::StateMachine(machine) = &definition.runtime else {
        return BTreeMap::new();
    };
    let mut loops = BTreeMap::new();
    collect_loop_descriptors(&machine.nodes, "", &mut loops);
    loops
}

fn collect_loop_descriptors(
    nodes: &BTreeMap<String, bcs_domain::StateMachineNodeDefinition>,
    parent_path: &str,
    loops: &mut BTreeMap<String, StateMachineLoopGraphView>,
) {
    for (id, node) in nodes {
        let Some(body) = node.loop_definition.as_ref() else { continue; };
        let path = if parent_path.is_empty() { id.clone() } else { format!("{parent_path}/{id}") };
        loops.insert(path.clone(), StateMachineLoopGraphView {
            display_name: node.display_name.clone(),
            continue_display_name: body.continue_display_name.clone(),
            max_iterations: body.max_iterations,
            entry_node_id: body.entry_node.clone(),
            result_node_id: body.result_node.clone(),
            body_node_ids: body.nodes.keys().cloned().collect(),
            continue_outcomes: body.continue_outcomes.clone(),
            break_outcomes: body.break_outcomes.clone(),
            exhausted_outcome: body.exhausted_outcome.clone(),
        });
        collect_loop_descriptors(&body.nodes, &path, loops);
    }
}
