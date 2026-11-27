//! Node-isolated interaction history for frozen FSM group workflows (plan T3).
//! History belongs to one logical node inside an authorized scope, Session and
//! frozen workflow — never to a Bot: nodes bound to the same Bot keep separate
//! histories, and there is no cross-node traversal at any address here.
//! The mounted strict group runtime captures cuts at admission, freezes the
//! selected node history before dispatch publication and replays that pin on
//! takeover. HTTP handlers never access this repository.
use super::common::{IdempotentWrite, StoreError};
use super::group_round::RoundBudgetProfile;
use async_trait::async_trait;
use bcs_dist_model::{
    Digest, GroupId, InteractionId, NodeId, Payload, RoundKey, Sid, TaskScope, TerminalStatus,
    WorkflowRef,
};
use serde::{Deserialize, Serialize};
use std::num::NonZeroU64;

/// Stable store error codes of this port. Every store emits the same code for
/// the same contract breach; callers and conformance suites assert them.
pub const CODE_NODE_KEY_INVALID: &str = "node_history_key_invalid";
pub const CODE_HISTORY_CUT_INVALID: &str = "history_cut_invalid";
pub const CODE_NODE_INPUT_PIN_CONFLICT: &str = "node_input_pin_conflict";
pub const CODE_NODE_INPUT_PIN_RELEASED: &str = "node_input_pin_released";
pub const CODE_NODE_INPUT_PIN_ABSENT: &str = "node_input_pin_absent";
pub const CODE_NODE_HISTORY_CORRUPT: &str = "node_history_corrupt";

/// Address of one logical node's interaction history. Scope, Session, frozen
/// workflow and node isolate the log; no Bot dimension exists, so binding two
/// nodes to the same Bot never merges their histories. A new workflow or a
/// new binding is a new key.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NodeHistoryKey {
    pub scope: TaskScope,
    pub group: GroupId,
    pub sid: Sid,
    pub workflow_ref: WorkflowRef,
    pub node_id: NodeId,
}

impl NodeHistoryKey {
    /// Identity hashing must length-prefix these UTF-8 components and use the
    /// first entry as the domain separator. Not a client-authorized scope.
    pub fn components(&self) -> Vec<&str> {
        vec![
            "fsm-node-history:v1",
            &self.scope.tenant.0,
            &self.scope.principal.0,
            &self.group.0,
            &self.sid.0,
            &self.workflow_ref.definition_id,
            &self.workflow_ref.revision,
            &self.workflow_ref.digest.0,
            &self.node_id.0,
        ]
    }
}

pub use bcs_dist_model::DeliveryState;

/// Role of one persisted interaction.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum NodeHistoryKind {
    /// Input actually dispatched to the bound node.
    NodeInput { delivery: DeliveryState },
    /// The node's actually recorded response.
    NodeResponse,
}

/// One real interaction of the owning round. Entries are append-only: they
/// never change once written, so a committed boundary stays valid forever.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NodeHistoryEntry {
    /// Admission-consistent index (checkpoint generation) of the round that
    /// produced this interaction inside the Sid.
    pub round_generation: NonZeroU64,
    pub kind: NodeHistoryKind,
    /// Inline content, or an immutable digest-checked reference for large
    /// content (the reference is the immutable artifact, not an inline copy).
    pub content: Payload,
    /// Terminal label of the owning round, present when the interaction is
    /// recorded at a failed/cancelled round's bounded finalization. Failed and
    /// cancelled rounds keep their real interactions under this label; they
    /// are never disguised as success artifacts. `None` means the owning round
    /// was still open when this entry was appended.
    pub round_outcome: Option<TerminalStatus>,
}

/// A stored interaction with its stable identity. The identity dedupes
/// retries: one interaction records once.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NodeHistoryRecord {
    pub interaction_id: InteractionId,
    pub entry: NodeHistoryEntry,
}

/// Opaque, self-certifying boundary of ONE node's committed history, captured
/// by the admission path at round admission. The digest covers the embedded
/// history key and the boundary in order (see `components`), so a cut
/// captured for one node — or one boundary — cannot be presented for another.
/// Stores validate the digest and never re-derive the boundary from live
/// state: the token is the admission handoff, and read/freeze operations only
/// honor entries committed at or before it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HistoryCut {
    pub history: NodeHistoryKey,
    /// Last committed row boundary at capture time; 0 means no entry yet.
    pub boundary: u64,
    pub digest: Digest,
}

impl HistoryCut {
    /// Digest input components in order. Implementations must recompute and
    /// verify this before any read or freeze at the cut.
    pub fn components(&self) -> Vec<String> {
        self.history
            .components()
            .iter()
            .map(|field| (*field).to_owned())
            .chain([self.boundary.to_string()])
            .collect()
    }
}

/// Contents of the strict round's immutable history boundary blob. Its
/// digest is stored in `RoundInput` before any node can become runnable.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HistoryManifest {
    pub round: RoundKey,
    pub cuts: Vec<HistoryCut>,
}

impl HistoryManifest {
    pub const KEY_PREFIX: &'static str = "fsm-node-cuts:v1:";

    pub fn dispatch_cut(
        &self,
        spec: &bcs_dist_model::DispatchSpec,
        pin: &bcs_dist_model::DispatchHistory,
    ) -> Result<&HistoryCut, StoreError> {
        if self.round == pin.round && pin.round.sid == spec.sid && pin.round.tid == spec.tid
            && spec.capabilities.isolated_memory
            && let Some(cut) = self.cuts.iter().find(|c| c.history.node_id == spec.node)
            && cut.history.workflow_ref == pin.workflow
        {
            return Ok(cut);
        }
        Err(StoreError::Invalid { code: "node_history_dispatch_binding".into() })
    }
}

/// Bounded batch page of the entries committed at or before a cut, in append
/// order. `truncated` flags that older entries were dropped by the budget —
/// the page never compensates by reading newer entries beyond the cut.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HistoryPage {
    pub entries: Vec<NodeHistoryRecord>,
    pub truncated: bool,
}

/// The exact dispatch input of one (round, node), materialized from the
/// cut-bounded history and pinned immutably: selected entries/content
/// references, an ordered digest over them and the truncation flag. Later
/// dispatches of the round replay this exact version.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PinnedHistory {
    pub entries: Vec<NodeHistoryRecord>,
    /// Domain-separated digest folded over the selected entries in order.
    pub ordered_digest: Digest,
    pub truncated: bool,
}

#[async_trait]
pub trait NodeHistoryRepo: Send + Sync {
    /// Capture the current committed boundary of one node's history as an
    /// opaque admission cut. One consistency observation: the log is
    /// append-only, so every entry at or before the returned boundary is
    /// already immutable and stays visible under this cut; entries appended
    /// later are invisible to it by construction. Admission wiring that turns
    /// this into a round input happens in plan T2/T9, not here.
    async fn committed_cut(&self, history: &NodeHistoryKey) -> Result<HistoryCut, StoreError>;

    /// Append one interaction, deduplicated by the stable interaction
    /// identity within this node history: an identical retry records once
    /// (`AlreadyApplied`), the same identity with different content is a
    /// `ContentConflict`, never a second record. Write failures propagate.
    async fn append_once(
        &self,
        history: &NodeHistoryKey,
        interaction_id: &InteractionId,
        entry: &NodeHistoryEntry,
    ) -> Result<IdempotentWrite, StoreError>;

    /// Bounded batch read of the entries committed at or before `cut`,
    /// newest-first selection of `budget.max_page_rows` entries returned in
    /// append order with `truncated` flagging the dropped older tail. Never
    /// reads beyond the cut for compensation. Once a round's input is frozen,
    /// this is an inspection read only: dispatch must load the frozen version.
    async fn read_at(
        &self,
        history: &NodeHistoryKey,
        cut: &HistoryCut,
        budget: &RoundBudgetProfile,
    ) -> Result<HistoryPage, StoreError>;

    /// Materialize and pin the exact input of `(round, node)`: a bounded read
    /// at `cut`, persisted with immutable entries' content references, the
    /// ordered digest and the truncation flag. Repeat calls with the same
    /// arguments replay the identical pinned version — entries appended for
    /// the same round after the freeze stay invisible, because after a freeze
    /// the pinned version is the only dispatch input source. A different
    /// budget over an existing pin is a conflict, and a released pin answers
    /// with an explicit error: a freeze failure never falls back to a bare
    /// query at the same cut.
    async fn freeze_node_input(
        &self,
        round: &RoundKey,
        node: &NodeId,
        cut: &HistoryCut,
        budget: &RoundBudgetProfile,
    ) -> Result<PinnedHistory, StoreError>;

    /// Load a previously frozen input without consulting raw history or
    /// creating a new pin. Missing and released pins both refuse dispatch.
    async fn load_node_input(
        &self,
        round: &RoundKey,
        node: &NodeId,
        cut: &HistoryCut,
        budget: &RoundBudgetProfile,
    ) -> Result<PinnedHistory, StoreError>;

    /// Release the input pin after the owning round reached a terminal state
    /// with no replayable dispatch left. The caller proves that precondition;
    /// this only records the release (idempotently), so a late freeze of the
    /// same round input fails explicitly instead of silently re-reading
    /// history. Releasing a round that never froze a pin is a visible error.
    async fn release_node_input(
        &self,
        round: &RoundKey,
        node: &NodeId,
    ) -> Result<IdempotentWrite, StoreError>;
}
