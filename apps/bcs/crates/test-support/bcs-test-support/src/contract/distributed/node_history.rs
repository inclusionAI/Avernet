//! Provider-independent assertions for the node-isolated interaction history
//! (plan T3). The history of one logical FSM node is addressed by its own key:
//! nodes bound to the same Bot never share, merge or traverse each other's
//! entries. Failed/cancelled rounds keep their real interactions labeled with
//! the owning round's outcome, never disguised as success artifacts.
//!
//! These are real callable harnesses, not proof that a backend exists. Each
//! provider supplies an isolated empty scope and calls these from its
//! conformance entry. Inputs, identity fields and budgets are fixed by the
//! harness so every provider proves the same node-privacy cut semantics.

use super::ContractResult;
use bcs_dist_model::{
    Digest, GroupId, InteractionId, NodeId, Payload, PrincipalId, RoundKey, Sid, TaskScope,
    TenantId, TerminalStatus, Tid, WorkflowRef,
};
use bcs_service_api::port::repo::distributed::{
    common::{IdempotentWrite, StoreError},
    group_round::RoundBudgetProfile,
    node_history::{
        CODE_HISTORY_CUT_INVALID, CODE_NODE_INPUT_PIN_ABSENT, CODE_NODE_INPUT_PIN_CONFLICT,
        CODE_NODE_INPUT_PIN_RELEASED, CODE_NODE_KEY_INVALID, DeliveryState, HistoryPage,
        NodeHistoryEntry, NodeHistoryKind, NodeHistoryKey, NodeHistoryRepo, NodeHistoryRecord,
        PinnedHistory,
    },
};
use std::num::NonZeroU64;

// ---------------------------------------------------------------------------
// shared fixtures
// ---------------------------------------------------------------------------

/// Bounded-read budget with an explicit small page; production values come
/// from `RoundBudgetProfile::v1()` and are never silently exceeded.
fn budget(rows: u32) -> RoundBudgetProfile {
    let mut profile = RoundBudgetProfile::v1();
    assert!(rows > 0, "read budget must allow at least one row");
    profile.max_page_rows = rows;
    profile
}

fn scope(tenant: &str) -> TaskScope {
    TaskScope {
        tenant: TenantId(tenant.into()),
        principal: PrincipalId("principal-a".into()),
    }
}

fn workflow() -> WorkflowRef {
    WorkflowRef {
        definition_id: "wf-fsm-demo".into(),
        revision: "r1".into(),
        digest: Digest("sha256:workflow-fixture".into()),
    }
}

fn history(scope: &TaskScope, sid: &str, node: &str) -> NodeHistoryKey {
    NodeHistoryKey {
        scope: scope.clone(),
        group: GroupId("group-a2a".into()),
        sid: Sid(sid.into()),
        workflow_ref: workflow(),
        node_id: NodeId(node.into()),
    }
}

fn round(scope: &TaskScope, sid: &str, generation: u64) -> RoundKey {
    RoundKey {
        scope: scope.clone(),
        group: GroupId("group-a2a".into()),
        sid: Sid(sid.into()),
        tid: Tid(format!("tid-gen-{generation}")),
        generation: NonZeroU64::new(generation).expect("round generation starts at one"),
    }
}

fn interaction(id: &str) -> InteractionId {
    InteractionId(id.into())
}

fn generation(value: u64) -> NonZeroU64 {
    NonZeroU64::new(value).expect("round generation starts at one")
}

/// One real input interaction actually dispatched to the node. `delivery`
/// states whether the node provably received it, provably did not, or the
/// delivery outcome is still unknown.
fn input_entry(text: &str, round: u64, outcome: Option<TerminalStatus>) -> NodeHistoryEntry {
    NodeHistoryEntry {
        round_generation: generation(round),
        kind: NodeHistoryKind::NodeInput {
            delivery: DeliveryState::Delivered,
        },
        content: Payload::Inline(serde_json::json!(text)),
        round_outcome: outcome,
    }
}

/// The node's actually recorded response.
fn response_entry(text: &str, round: u64) -> NodeHistoryEntry {
    NodeHistoryEntry {
        round_generation: generation(round),
        kind: NodeHistoryKind::NodeResponse,
        content: Payload::Inline(serde_json::json!(text)),
        round_outcome: None,
    }
}

/// Comparison text per entry, independent of provider byte layouts.
fn page_text(page: &HistoryPage) -> String {
    page.entries
        .iter()
        .map(|record| match &record.entry.content {
            Payload::Inline(value) => value.to_string(),
            Payload::Ref(reference) => reference.key.clone(),
        })
        .collect::<Vec<_>>()
        .join("|")
}

fn pinned_entries_bytes(pinned: &PinnedHistory) -> Vec<u8> {
    serde_json::to_vec(&pinned.entries).expect("pinned entries are serializable")
}

fn assert_not_applied<T: std::fmt::Debug>(actual: Result<T, StoreError>, code: &str) {
    match actual {
        Err(StoreError::NotApplied { code: actual_code }) => assert_eq!(actual_code, code),
        other => panic!("expected NotApplied({code}), got {other:?}"),
    }
}

fn assert_invalid<T: std::fmt::Debug>(actual: Result<T, StoreError>, code: &str) {
    match actual {
        Err(StoreError::Invalid { code: actual_code }) => assert_eq!(actual_code, code),
        other => panic!("expected Invalid({code}), got {other:?}"),
    }
}

fn record_ids(records: &[NodeHistoryRecord]) -> Vec<&str> {
    records.iter().map(|r| r.interaction_id.0.as_str()).collect()
}

// ---------------------------------------------------------------------------
// central contract suite (R25 entry: node_history_repo_contract_tests)
// ---------------------------------------------------------------------------

/// Full port contract over one isolated node key. Providers pass an empty,
/// private scope; every vector below invents its own identifiers.
pub async fn node_history_repo_contract_tests(repo: &dyn NodeHistoryRepo) -> ContractResult {
    let tenant = scope("t-contract");
    let key = history(&tenant, "sid-contract", "node-alpha");

    // A fresh node key has an empty committed history and a legal cut at zero.
    let cut_empty = repo.committed_cut(&key).await?;
    assert_eq!(repo.read_at(&key, &cut_empty, &budget(10)).await?,
        HistoryPage { entries: Vec::new(), truncated: false });

    // Stable interaction identity: the first append applies, an identical
    // retry applies only once, and different content under the same identity
    // is a conflict, never a second record.
    let e1 = input_entry("dispatch-turn-1", 1, None);
    assert_eq!(repo.append_once(&key, &interaction("input-1"), &e1).await?, IdempotentWrite::Applied);
    assert_eq!(repo.append_once(&key, &interaction("input-1"), &e1).await?, IdempotentWrite::AlreadyApplied);
    let e_conflict = response_entry("different-content-same-identity", 1);
    assert_eq!(repo.append_once(&key, &interaction("input-1"), &e_conflict).await?,
        IdempotentWrite::ContentConflict);
    let cut1 = repo.committed_cut(&key).await?;

    // Entries appended after a cut are invisible at that cut, even when the
    // owning round retries within the same Sid.
    assert_eq!(repo.append_once(&key, &interaction("reply-1"), &response_entry("answer-turn-1", 1)).await?,
        IdempotentWrite::Applied);
    let page = repo.read_at(&key, &cut1, &budget(10)).await?;
    assert_eq!(page_text(&page), "\"dispatch-turn-1\"");
    assert!(!page.truncated);

    // The budget bounds the batch: the newest entries within the cut are
    // returned in append order and `truncated` flags the dropped older tail.
    // The page never compensates by reading entries beyond the cut.
    assert_eq!(repo.append_once(&key, &interaction("reply-2"), &response_entry("answer-turn-2", 2)).await?,
        IdempotentWrite::Applied);
    let cut2 = repo.committed_cut(&key).await?;
    let page = repo.read_at(&key, &cut2, &budget(1)).await?;
    assert_eq!(page_text(&page), "\"answer-turn-2\"");
    assert!(page.truncated);
    let page = repo.read_at(&key, &cut2, &budget(10)).await?;
    assert_eq!(page_text(&page), "\"dispatch-turn-1\"|\"answer-turn-1\"|\"answer-turn-2\"");
    assert!(!page.truncated);

    // Undelivered and unknown delivery proofs are distinguishable states of
    // the recorded input, never collapse into each other.
    let kinds = [
        (DeliveryState::Delivered, "input-delivered"),
        (DeliveryState::Undelivered, "input-undelivered"),
        (DeliveryState::Unknown, "input-unknown"),
    ];
    for (delivery, id) in kinds {
        let entry = NodeHistoryEntry {
            round_generation: generation(2),
            kind: NodeHistoryKind::NodeInput { delivery },
            content: Payload::Inline(serde_json::json!(id)),
            round_outcome: None,
        };
        assert_eq!(repo.append_once(&key, &interaction(id), &entry).await?, IdempotentWrite::Applied);
    }
    let cut_kinds = repo.committed_cut(&key).await?;
    let page = repo.read_at(&key, &cut_kinds, &budget(10)).await?;
    let states: Vec<DeliveryState> = page
        .entries
        .iter()
        .filter(|r| r.interaction_id.0.starts_with("input-"))
        .map(|r| match r.entry.kind {
            NodeHistoryKind::NodeInput { delivery } => delivery,
            NodeHistoryKind::NodeResponse => panic!("input rows never replay as responses"),
        })
        .collect();
    assert!(states.contains(&DeliveryState::Delivered));
    assert!(states.contains(&DeliveryState::Undelivered));
    assert!(states.contains(&DeliveryState::Unknown));
    assert_eq!(states.len(), 4);

    // Failed and cancelled rounds keep their real interactions, labeled with
    // the owning round's outcome, never as success artifacts.
    let e_fail = input_entry("turn-that-failed", 3, Some(TerminalStatus::Failed));
    assert_eq!(repo.append_once(&key, &interaction("input-failed"), &e_fail).await?, IdempotentWrite::Applied);
    let e_cancel = input_entry("turn-that-was-cancelled", 3, Some(TerminalStatus::Canceled));
    assert_eq!(repo.append_once(&key, &interaction("input-cancelled"), &e_cancel).await?, IdempotentWrite::Applied);
    let cut3 = repo.committed_cut(&key).await?;
    let page = repo.read_at(&key, &cut3, &budget(10)).await?;
    let failed = page.entries.iter().find(|r| r.interaction_id.0 == "input-failed")
        .expect("failed-round interaction retained");
    assert_eq!(failed.entry.round_outcome, Some(TerminalStatus::Failed));
    assert_ne!(failed.entry.round_outcome, Some(TerminalStatus::Completed));
    let cancelled = page.entries.iter().find(|r| r.interaction_id.0 == "input-cancelled")
        .expect("cancelled-round interaction retained");
    assert_eq!(cancelled.entry.round_outcome, Some(TerminalStatus::Canceled));

    // Cut binding: a cut captured for another node key is rejected, and a
    // tampered boundary digest never degrades to a live-state read.
    let other = history(&tenant, "sid-contract", "node-beta");
    assert_invalid(repo.read_at(&other, &cut3, &budget(10)).await, CODE_HISTORY_CUT_INVALID);
    let mut tampered = cut3.clone();
    tampered.digest = Digest("sha256:not-the-admitted-boundary".into());
    assert_invalid(repo.read_at(&key, &tampered, &budget(10)).await, CODE_HISTORY_CUT_INVALID);

    // Invalid keys fail fast instead of scattering rows across a broken
    // composite (no Bot dimension exists to fall back to).
    let mut broken = key.clone();
    broken.node_id = NodeId(String::new());
    assert_invalid(repo.committed_cut(&broken).await, CODE_NODE_KEY_INVALID);

    // Freeze pins the exact cut-bounded input for one round+node.
    let round3 = round(&tenant, "sid-contract", 3);
    let pinned = repo.freeze_node_input(&round3, &key.node_id, &cut3, &budget(2)).await?;
    assert_eq!(record_ids(&pinned.entries), vec!["input-failed", "input-cancelled"]);
    assert!(pinned.truncated);
    assert!(pinned.ordered_digest.0.starts_with("sha256:"));

    // Re-freezing with the same arguments replays the exact pinned version:
    // bytes, order, digest and truncation flag are unchanged.
    let replay = repo.freeze_node_input(&round3, &key.node_id, &cut3, &budget(2)).await?;
    assert_eq!(pinned_entries_bytes(&pinned), pinned_entries_bytes(&replay));
    assert_eq!(pinned, replay);

    // Same-round retry records appended after the freeze stay invisible to
    // the replayed input; they are raw history for later cuts only.
    let retry = input_entry("retry-after-freeze", 3, None);
    assert_eq!(repo.append_once(&key, &interaction("retry-3"), &retry).await?, IdempotentWrite::Applied);
    let replay = repo.freeze_node_input(&round3, &key.node_id, &cut3, &budget(2)).await?;
    assert_eq!(pinned, replay);

    // A different budget over the same frozen round input is a conflict: the
    // pinned version is immutable, never recomputed to please a new caller.
    assert_invalid(repo.freeze_node_input(&round3, &key.node_id, &cut3, &budget(10)).await,
        CODE_NODE_INPUT_PIN_CONFLICT);

    // Release after a terminal round is explicit and idempotent.
    assert_eq!(repo.release_node_input(&round3, &key.node_id).await?, IdempotentWrite::Applied);
    assert_eq!(repo.release_node_input(&round3, &key.node_id).await?, IdempotentWrite::AlreadyApplied);

    // A released pin never falls back to a bare read at the same cut.
    assert_not_applied(repo.freeze_node_input(&round3, &key.node_id, &cut3, &budget(2)).await,
        CODE_NODE_INPUT_PIN_RELEASED);

    // Releasing a round that never froze an input pin is a visible caller bug.
    let round_never = round(&tenant, "sid-contract", 42);
    assert_not_applied(repo.release_node_input(&round_never, &key.node_id).await, CODE_NODE_INPUT_PIN_ABSENT);

    Ok(())
}

// ---------------------------------------------------------------------------
// brief-required vectors
// ---------------------------------------------------------------------------

/// Research and review both bind the SAME Bot; each writes private markers
/// into its own node history. Neither history ever contains the other's
/// marker, and a brand-new Sid starts empty. The node key carries no Bot
/// dimension on purpose: aggregation by Bot is not an address here.
pub async fn same_bot_two_nodes_remain_private(repo: &dyn NodeHistoryRepo) -> ContractResult {
    let tenant = scope("t-same-bot");
    // Both nodes address the same frozen workflow, group and Sid; only the
    // logical node differs. In production both are bound to the same Bot X.
    let research = history(&tenant, "sid-private", "node-research");
    let review = history(&tenant, "sid-private", "node-review");

    assert_eq!(repo.append_once(&research, &interaction("research-write"),
        &input_entry("research-private-marker", 1, None)).await?, IdempotentWrite::Applied);
    assert_eq!(repo.append_once(&review, &interaction("review-write"),
        &input_entry("review-private-marker", 1, None)).await?, IdempotentWrite::Applied);

    let research_cut = repo.committed_cut(&research).await?;
    let review_cut = repo.committed_cut(&review).await?;
    let research_page = repo.read_at(&research, &research_cut, &budget(10)).await?;
    let review_page = repo.read_at(&review, &review_cut, &budget(10)).await?;

    assert_eq!(page_text(&research_page), "\"research-private-marker\"");
    assert_eq!(page_text(&review_page), "\"review-private-marker\"");

    // The brief's literal assertion: the review history must not contain the
    // research marker (checked in both directions because the store exposes
    // no cross-node traversal at all).
    assert!(!review_page.entries.iter().any(|r| r.entry.content
        == Payload::Inline(serde_json::json!("research-private-marker"))));
    assert!(!research_page.entries.iter().any(|r| r.entry.content
        == Payload::Inline(serde_json::json!("review-private-marker"))));

    // A third node of the same Bot in the same Sid stays empty until its own
    // interactions are appended.
    let sibling = history(&tenant, "sid-private", "node-sibling");
    let sibling_cut = repo.committed_cut(&sibling).await?;
    assert_eq!(repo.read_at(&sibling, &sibling_cut, &budget(10)).await?,
        HistoryPage { entries: Vec::new(), truncated: false });

    // New Sid: empty history, never a recycled node page.
    let fresh = history(&tenant, "sid-new", "node-research");
    let fresh_cut = repo.committed_cut(&fresh).await?;
    assert_eq!(repo.read_at(&fresh, &fresh_cut, &budget(10)).await?,
        HistoryPage { entries: Vec::new(), truncated: false });

    // Cross-key cut reuse and broken composites fail closed.
    assert_invalid(repo.read_at(&fresh, &research_cut, &budget(10)).await, CODE_HISTORY_CUT_INVALID);
    let mut hollow = research.clone();
    hollow.group = GroupId(String::new());
    assert_invalid(repo.committed_cut(&hollow).await, CODE_NODE_KEY_INVALID);

    // Failed and cancelled rounds of a private node keep their real
    // interactions labeled with the owning round's outcome, not success
    // artifacts, and a retry of the same interaction still appends once.
    let failed_round = input_entry("real-interaction-failed-round", 2, Some(TerminalStatus::Failed));
    assert_eq!(repo.append_once(&review, &interaction("review-failed"), &failed_round).await?,
        IdempotentWrite::Applied);
    assert_eq!(repo.append_once(&review, &interaction("review-failed"), &failed_round).await?,
        IdempotentWrite::AlreadyApplied);
    let review_cut2 = repo.committed_cut(&review).await?;
    let page = repo.read_at(&review, &review_cut2, &budget(10)).await?;
    let labeled = page.entries.iter().find(|r| r.interaction_id.0 == "review-failed")
        .expect("real interaction of the failed round is retained");
    assert_eq!(labeled.entry.round_outcome, Some(TerminalStatus::Failed));
    assert_ne!(labeled.entry.round_outcome, Some(TerminalStatus::Completed));
    assert_eq!(page.entries.iter().filter(|r| r.interaction_id.0 == "review-failed").count(), 1);

    Ok(())
}

/// The brief's takeover vector. Two entries are selected within the read
/// budget; the dispatch has not committed when the holder crashes. While a
/// retention actor releases the previous round's terminal input pin, the
/// taker replays the frozen input exactly: bytes, order, ordered digest and
/// truncation flag are unchanged, and same-round retry records written after
/// the freeze stay invisible to the replay. A later new round DOES see them
/// (inside its own bounded, honestly-flagged trim). A released pin never
/// degrades into a bare query at the same cut.
pub async fn trim_during_takeover_replays_exact_input(repo: &dyn NodeHistoryRepo) -> ContractResult {
    let tenant = scope("t-takeover");
    let node = history(&tenant, "sid-takeover", "node-driver");
    let previous_round = round(&tenant, "sid-takeover", 1);
    let inflight_round = round(&tenant, "sid-takeover", 2);

    // Round 1 (previous, now terminal): its input was frozen at its own
    // admitted cut, before the newer interactions exist.
    assert_eq!(repo.append_once(&node, &interaction("base-1"),
        &input_entry("takeover-base-1", 1, None)).await?, IdempotentWrite::Applied);
    assert_eq!(repo.append_once(&node, &interaction("base-2"),
        &input_entry("takeover-base-2", 1, None)).await?, IdempotentWrite::Applied);
    let previous_cut = repo.committed_cut(&node).await?;
    repo.freeze_node_input(&previous_round, &node.node_id, &previous_cut, &budget(10)).await?;

    // Round 2 (in flight): the round-1 tail is appended before its admission.
    let terminal_tail = input_entry("takeover-base-3-terminal", 1, Some(TerminalStatus::Completed));
    assert_eq!(repo.append_once(&node, &interaction("base-3-terminal"), &terminal_tail).await?,
        IdempotentWrite::Applied);
    let takeoff_cut = repo.committed_cut(&node).await?;

    // Two entries are selected within the budget and the dispatch input is
    // frozen; the holder then crashes BEFORE the dispatch commits.
    let pinned = repo.freeze_node_input(&inflight_round, &node.node_id, &takeoff_cut, &budget(2)).await?;
    assert_eq!(record_ids(&pinned.entries), vec!["base-2", "base-3-terminal"]);
    assert!(pinned.truncated);

    // Retention releases the finished round's terminal pin CONCURRENTLY with
    // the taker's replay of the in-flight round's frozen input.
    let (retention, takeover) = tokio::join!(
        async {
            // Retention actor: round 1 is terminal with no replayable dispatch
            // left.
            repo.release_node_input(&previous_round, &node.node_id).await
        },
        async { repo.freeze_node_input(&inflight_round, &node.node_id, &takeoff_cut, &budget(2)).await }
    );
    assert_eq!(retention?, IdempotentWrite::Applied);
    let replay = takeover?;

    // Bytes, order, ordered digest and truncation flag are unchanged.
    assert_eq!(pinned_entries_bytes(&pinned), pinned_entries_bytes(&replay));
    assert_eq!(pinned, replay);

    // Same-round retry records written after the freeze are invisible to the
    // replayed input: the frozen PinnedHistory is the only dispatch input
    // source after the freeze.
    assert_eq!(repo.append_once(&node, &interaction("retry-same-round"),
        &input_entry("takeover-same-round-retry", 2, None)).await?, IdempotentWrite::Applied);
    let replay = repo.freeze_node_input(&inflight_round, &node.node_id, &takeoff_cut, &budget(2)).await?;
    assert_eq!(pinned, replay);
    let same_cut_page = repo.read_at(&node, &takeoff_cut, &budget(10)).await?;
    assert!(!same_cut_page.entries.iter().any(|r| r.interaction_id.0 == "retry-same-round"),
        "the same-cut page sees only entries committed before the cut");

    // The next round's new cut DOES see the retry record and the trim: its
    // bounded page returns the newest entries with truncated=true, never a
    // silent substitute from beyond its own budget.
    let next_cut = repo.committed_cut(&node).await?;
    let next_page = repo.read_at(&node, &next_cut, &budget(2)).await?;
    assert!(next_page.truncated);
    assert_eq!(next_page.entries.len(), 2);
    assert!(next_page.entries.iter().any(|r| r.interaction_id.0 == "retry-same-round"));

    // Once the in-flight round reaches terminal state with no replayable
    // dispatch, its pin is released, and a repeat freeze answers with an
    // explicit error — never a bare read at the same cut.
    assert_eq!(repo.release_node_input(&inflight_round, &node.node_id).await?, IdempotentWrite::Applied);
    assert_not_applied(
        repo.freeze_node_input(&inflight_round, &node.node_id, &takeoff_cut, &budget(2)).await,
        CODE_NODE_INPUT_PIN_RELEASED,
    );
    // The retention actor's own released round is equally closed.
    assert_not_applied(
        repo.freeze_node_input(&previous_round, &node.node_id, &previous_cut, &budget(10)).await,
        CODE_NODE_INPUT_PIN_RELEASED,
    );

    Ok(())
}
