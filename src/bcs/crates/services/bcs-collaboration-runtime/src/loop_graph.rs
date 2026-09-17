//! Read-only logical descriptors; execution topology and identities remain in the plan.

use std::collections::BTreeMap;
use bcs_domain::{CollaborationDefinition, CollaborationRuntimeDefinition};
use bcs_service_api::StateMachineLoopGraphView;

pub(crate) fn loop_graph_descriptors(definition: &CollaborationDefinition) -> BTreeMap<String, StateMachineLoopGraphView> {
    let CollaborationRuntimeDefinition::StateMachine(machine) = &definition.runtime else {
        return BTreeMap::new();
    };
    machine.nodes.iter().filter_map(|(id, node)| {
        let body = node.loop_definition.as_ref()?;
        Some((id.clone(), StateMachineLoopGraphView {
            display_name: node.display_name.clone(),
            max_iterations: body.max_iterations,
            entry_node_id: body.entry_node.clone(),
            result_node_id: body.result_node.clone(),
            body_node_ids: body.nodes.keys().cloned().collect(),
            continue_outcomes: body.continue_outcomes.clone(),
            break_outcomes: body.break_outcomes.clone(),
            exhausted_outcome: body.exhausted_outcome.clone(),
        }))
    }).collect()
}
