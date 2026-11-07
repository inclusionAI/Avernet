//! Shared-stream projection policy (spec §6.4 / P4 L225): the Bot×session
//! shared stream derives from each TARGET Bot's own routing/producer
//! projection — never from the Human's visibility/audience queries — and
//! directed task / Workflow / private MCP content is never broadcast
//! group-wide. Pure decisions over declared facts; callers apply them at
//! the fan-out seam.

/// What kind of content is being considered for shared-stream delivery.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SharedContentKind {
    /// Ordinary group-participant chat (broadcast-visible to members).
    OpenChat,
    /// A task addressed to specific target bot(s) by the producer's own
    /// routing — the directed projection of the owning bot.
    DirectedTask { addressed_bot_ids: Vec<String> },
    /// Private MCP / tool-call intermediate material.
    PrivateMcpContext,
    /// A Workflow node's execution content / data predecessor output.
    WorkflowNodeContent {
        /// The PRODUCING node's run id — the specific result being shared
        /// (spec §8.5: "节点 A 完成后…数据前驱=RA" — every causal statement
        /// binds to one concrete run, never to "some predecessor").
        producer_node_run_id: String,
        /// The producer node's own declared causal predecessor runs.
        predecessor_node_run_ids: Vec<String>,
        /// Per-observer trusted links from the orchestrator's own topology
        /// (the ONLY the-observer-declared source): each entry binds one
        /// observing node to the runs it consumes.
        consumer_links: Vec<WorkflowConsumerLink>,
    },
}

/// One observing node's trusted causal link (spec §8.5, review P1-4): the
/// orchestrator declares WHICH node consumes the producer's run — a
/// message carrying a non-empty predecessor list but no link for THIS
/// observer never shares to it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkflowConsumerLink {
    /// The observing Bot the link authorizes.
    pub observer_bot_id: String,
    /// The observing node's own run id — the consuming node identity.
    pub observer_node_run_id: String,
    /// The predecessor runs this node declared consuming; every id must
    /// sit in the producer's own causal graph, else the link is refused.
    pub consumed_node_run_ids: Vec<String>,
}

/// Closed denial reasons — actionable, no identity material.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SharedStreamDenial {
    /// A directed task never reaches non-addressed observers: directed
    /// projection must not be re-broadcast (§6.4, L225).
    DirectedOutsideAddressees,
    /// private-MCP/tool material is never shared to any other container.
    PrivateContextNeverShared,
    /// Workflow content shares only along explicit causal-predecessor
    /// links declared for the observing node (§8.5). Anything broader is
    /// a directed broadcast, not a shared-stream fact.
    WorkflowNotCausallyLinked,
}

/// Whether `observer` may receive this content on its Bot×session shared
/// stream. The candidate's routing projection is evaluated per-observer:
/// the same content can be eligible for one target and denied for another.
pub fn shared_stream_eligibility(
    kind: &SharedContentKind,
    observer_bot_id: &str,
) -> Result<(), SharedStreamDenial> {
    match kind {
        SharedContentKind::OpenChat => Ok(()),
        SharedContentKind::DirectedTask { addressed_bot_ids } => {
            if addressed_bot_ids.iter().any(|bot| bot == observer_bot_id) {
                Ok(())
            } else {
                Err(SharedStreamDenial::DirectedOutsideAddressees)
            }
        }
        SharedContentKind::PrivateMcpContext => Err(SharedStreamDenial::PrivateContextNeverShared),
        SharedContentKind::WorkflowNodeContent {
            producer_node_run_id,
            predecessor_node_run_ids,
            consumer_links,
        } => {
            // The trusted association (review P1-4): WHICH observer consumes
            // WHICH predecessor run is per-observer link data from the
            // orchestrator's own topology — a non-empty predecessor list
            // alone authorizes nobody. THIS observer needs a declared link
            // binding its node to specific consumed runs, every consumed
            // run must sit in the producer's own causal graph, and the
            // producer/observer node identities must name concrete runs.
            if producer_node_run_id.trim().is_empty() || predecessor_node_run_ids.is_empty() {
                return Err(SharedStreamDenial::WorkflowNotCausallyLinked);
            }
            let Some(link) = consumer_links
                .iter()
                .find(|link| link.observer_bot_id == observer_bot_id)
            else {
                return Err(SharedStreamDenial::WorkflowNotCausallyLinked);
            };
            if link.observer_node_run_id.trim().is_empty()
                || link.consumed_node_run_ids.is_empty()
                || link
                    .consumed_node_run_ids
                    .iter()
                    .any(|run| !predecessor_node_run_ids.contains(run))
            {
                return Err(SharedStreamDenial::WorkflowNotCausallyLinked);
            }
            Ok(())
        }
    }
}

/// Classify one delivered row's flow for shared-stream eligibility (the
/// fan-out call site's producer side). Group chat rows are OpenChat — the
/// broadcast-visible group conversation. Every other flow stays
/// UNCLASSIFIED: directed task / workflow / system content is not deduced
/// from its flow kind alone, and unclassified content never broadcasts
/// (its future producers declare the kind when their flows land).
pub fn shared_kind_for_delivery(
    flow_kind: crate::message_delivery::DeliveryFlowKind,
) -> Option<SharedContentKind> {
    match flow_kind {
        crate::message_delivery::DeliveryFlowKind::Group => Some(SharedContentKind::OpenChat),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn open_chat_is_shared_group_wide_through_each_bots_own_projection() {
        assert_eq!(shared_stream_eligibility(&SharedContentKind::OpenChat, "bot-b"), Ok(()));
        assert_eq!(shared_stream_eligibility(&SharedContentKind::OpenChat, "bot-z"), Ok(()));
    }

    #[test]
    fn directed_task_stays_inside_its_addressees() {
        let task = SharedContentKind::DirectedTask { addressed_bot_ids: vec!["bot-b".into()] };
        assert_eq!(shared_stream_eligibility(&task, "bot-b"), Ok(()));
        assert_eq!(
            shared_stream_eligibility(&task, "bot-c"),
            Err(SharedStreamDenial::DirectedOutsideAddressees)
        );
        // Multiple addressees all see it; everyone else never.
        let multi = SharedContentKind::DirectedTask {
            addressed_bot_ids: vec!["bot-b".into(), "bot-c".into()],
        };
        assert_eq!(shared_stream_eligibility(&multi, "bot-b"), Ok(()));
        assert_eq!(shared_stream_eligibility(&multi, "bot-c"), Ok(()));
        assert_eq!(
            shared_stream_eligibility(&multi, "bot-z"),
            Err(SharedStreamDenial::DirectedOutsideAddressees)
        );
    }

    #[test]
    fn private_mcp_and_unlinked_workflow_content_are_never_shared() {
        assert_eq!(
            shared_stream_eligibility(&SharedContentKind::PrivateMcpContext, "bot-b"),
            Err(SharedStreamDenial::PrivateContextNeverShared)
        );
        let unlinked = SharedContentKind::WorkflowNodeContent {
            producer_node_run_id: "node-a-1".into(),
            predecessor_node_run_ids: vec![],
            consumer_links: vec![],
        };
        assert_eq!(
            shared_stream_eligibility(&unlinked, "bot-b"),
            Err(SharedStreamDenial::WorkflowNotCausallyLinked)
        );
        // A NON-EMPTY predecessor list alone authorizes NOBODY (review
        // P1-4: the old branch passed any observer on a non-empty list —
        // that was a directed broadcast, not a causal check).
        let declared_but_unlinked = SharedContentKind::WorkflowNodeContent {
            producer_node_run_id: "node-a-1".into(),
            predecessor_node_run_ids: vec!["node-a-1".into()],
            consumer_links: vec![],
        };
        assert_eq!(
            shared_stream_eligibility(&declared_but_unlinked, "bot-b"),
            Err(SharedStreamDenial::WorkflowNotCausallyLinked)
        );
        assert_eq!(
            shared_stream_eligibility(&declared_but_unlinked, "bot-z"),
            Err(SharedStreamDenial::WorkflowNotCausallyLinked)
        );
        // The trusted per-observer link: THIS observer's node declared
        // consuming a run that sits in the producer's own causal graph.
        let linked = SharedContentKind::WorkflowNodeContent {
            producer_node_run_id: "node-a-1".into(),
            predecessor_node_run_ids: vec!["node-a-1".into()],
            consumer_links: vec![WorkflowConsumerLink {
                observer_bot_id: "bot-b".into(),
                observer_node_run_id: "node-b-1".into(),
                consumed_node_run_ids: vec!["node-a-1".into()],
            }],
        };
        assert_eq!(shared_stream_eligibility(&linked, "bot-b"), Ok(()));
        // Every OTHER observer still refuses — the link is per-observer.
        assert_eq!(
            shared_stream_eligibility(&linked, "bot-c"),
            Err(SharedStreamDenial::WorkflowNotCausallyLinked)
        );
        // A link consuming runs OUTSIDE the producer's causal graph is
        // corrupt material: refused, never trimmed to fit.
        let corrupt = SharedContentKind::WorkflowNodeContent {
            producer_node_run_id: "node-a-1".into(),
            predecessor_node_run_ids: vec!["node-a-1".into()],
            consumer_links: vec![WorkflowConsumerLink {
                observer_bot_id: "bot-b".into(),
                observer_node_run_id: "node-b-1".into(),
                consumed_node_run_ids: vec!["node-x-9".into()],
            }],
        };
        assert_eq!(
            shared_stream_eligibility(&corrupt, "bot-b"),
            Err(SharedStreamDenial::WorkflowNotCausallyLinked)
        );
    }

    #[test]
    fn group_rows_classify_as_open_chat_and_every_other_flow_stays_unclassified() {
        use crate::message_delivery::DeliveryFlowKind;
        assert_eq!(
            shared_kind_for_delivery(DeliveryFlowKind::Group),
            Some(SharedContentKind::OpenChat)
        );
        for kind in [
            DeliveryFlowKind::Task,
            DeliveryFlowKind::DirectA2a,
            DeliveryFlowKind::System,
            DeliveryFlowKind::StateMachine,
        ] {
            assert_eq!(
                shared_kind_for_delivery(kind),
                None,
                "unclassified content never broadcasts"
            );
        }
    }
}