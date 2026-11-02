//! Fail only in-flight requests tied to a disconnected Bot WebSocket.
use super::*;
use bcs_service_api::{StateMachineDispatchStatus, StateMachineDispatchTarget};

impl CollaborationRuntime {
    pub(super) async fn fail_disconnected_attempts(&self, bot: &str, at: u64) -> Result<(), CollaborationRuntimeError> {
        let mut cursor: Option<(String, String)> = None;
        loop {
            let nodes = self.runs.disconnected_bot_nodes(bot, at, cursor.as_ref().map(|(r, n)| (r.as_str(), n.as_str())), 64).await?;
            if nodes.is_empty() { return Ok(()); }
            for node in &nodes {
                cursor = Some((node.run_id.clone(), node.node_id.clone()));
                let Some(dispatch) = self.runs.get_node_dispatch(&node.run_id, &node.node_id, node.attempt).await? else { continue; };
                let p = &dispatch.payload;
                if p.target != StateMachineDispatchTarget::WebSocket || p.assignee_bot_id != bot
                    || p.run_id != node.run_id || p.node_id != node.node_id || p.attempt != node.attempt
                    || Some(p.delivery_request_id.as_str()) != node.delivery_request_id.as_deref()
                    || Some(p.started_at_ms) != node.started_at || p.started_at_ms > at
                    || !matches!(dispatch.status, StateMachineDispatchStatus::Delivering | StateMachineDispatchStatus::Delivered) { continue; }
                let Some(run) = self.runs.get_run(&node.run_id).await? else { continue; };
                if self.runs.fail_disconnected_bot_node(node, at).await? {
                    self.fail_run_reason(&run, "state-machine worker WebSocket disconnected during execution".into(),
                        Reason::NodeFailed, Some((&node.node_id, node.attempt))).await?;
                }
            }
            if nodes.len() < 64 { return Ok(()); }
        }
    }
}
