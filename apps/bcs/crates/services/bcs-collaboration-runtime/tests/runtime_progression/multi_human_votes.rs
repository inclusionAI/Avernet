//! Two Present Humans vote in parallel on frontend HumanInput nodes bound to
//! each of them (issue #2609). Binding scopes pending lists, prompts and
//! submissions; ballot text is not withheld server-side in this phase.

use super::*;
use bcs_service_api::PendingHumanNodeView;

const HUMAN_A: &str = "human_1001";
const HUMAN_B: &str = "human_2002";
const BALLOT_A: &str = "我投2号";

fn parallel_vote_yaml() -> String {
    format!(
        r#"
api_version: bcs.collaboration/v1
id: parallel_bound_votes
version: 1
name: Parallel Bound Votes
participants: {{}}
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      vote_start:
        kind: human_input
        display_name: 开投
        assignee: {{ type: runtime_actor, actor: {HUMAN_A} }}
        instruction: 确认开始投票。
        node_timeout_ms: 600000
        transitions:
          complete: {{ targets: [vote_a, vote_b] }}
      vote_a:
        kind: human_input
        display_name: A 投票
        assignee: {{ type: runtime_actor, actor: {HUMAN_A} }}
        instruction: A 请投票。
        node_timeout_ms: 600000
        transitions:
          complete: {{ targets: [reveal] }}
      vote_b:
        kind: human_input
        display_name: B 投票
        assignee: {{ type: runtime_actor, actor: {HUMAN_B} }}
        instruction: B 请投票。
        node_timeout_ms: 600000
        transitions:
          complete: {{ targets: [reveal] }}
      reveal:
        kind: human_input
        display_name: 揭晓
        assignee: {{ type: runtime_actor, actor: {HUMAN_A} }}
        instruction: 确认揭晓。
        node_timeout_ms: 600000
"#
    )
}

struct VoteHarness {
    runtime: CollaborationRuntime,
    run_id: String,
    session_id: String,
}

impl VoteHarness {
    async fn start() -> Self {
        let group = Arc::new(GroupStore::new());
        let seeded_group = state_machine_test_group();
        group.upsert(seeded_group.clone()).await.expect("seed group");
        let sessions = test_sessions();
        let mut a = Participant::human(HUMAN_A, ParticipantRole::Observer);
        a.mode = Some(ParticipantMode::Present);
        let mut b = Participant::human(HUMAN_B, ParticipantRole::Observer);
        b.mode = Some(ParticipantMode::Present);
        let session = sessions
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id: seeded_group.id.clone(),
                session_id: None,
                params: NewSessionParams {
                    session_kind: SessionKind::ServiceInvocation,
                    participants: vec![a, b],
                    ..Default::default()
                },
            })
            .await
            .expect("seed session")
            .session;
        let store = Arc::new(MemoryCollaborationStore::new());
        let runtime = test_runtime!(
            store.clone(),
            store.clone(),
            store.clone(),
            store,
            group,
            sessions,
            Arc::new(RecordingDelivery::default()),
            noop_judge(),
        );
        let started = runtime
            .start_state_machine_run(StartStateMachineRunCommand {
                group_id: seeded_group.id,
                session_id: Some(session.id.clone()),
                definition_yaml: Some(parallel_vote_yaml()),
                definition: None,
                definition_ref: None,
                participant_bindings: None,
                opening_message_override: None,
                input: json!({"round": 1}),
                caller_id: Some("bot_driver".to_string()),
                authenticated_human: None,
            })
            .await
            .expect("start parallel vote run");
        Self { runtime, run_id: started.view.run.run_id, session_id: session.id }
    }

    async fn pending(&self, actor: &str) -> Vec<PendingHumanNodeView> {
        self.runtime
            .list_pending_human_nodes(ListPendingHumanNodesCommand {
                run_id: self.run_id.clone(),
                caller_actor_id: actor.to_string(),
            })
            .await
            .expect("list pending")
    }

    async fn respond(&self, actor: &str, node: &str, content: &str) -> Result<RespondHumanNodeOutcome, CollaborationRuntimeError> {
        self.runtime
            .respond_human_node(RespondHumanNodeCommand {
                run_id: self.run_id.clone(),
                node_id: node.to_string(),
                caller_actor_id: actor.to_string(),
                content: content.to_string(),
                source: HumanResponseSource::Http,
            })
            .await
    }

    fn access(&self, actor: &str) -> HumanRunAccessCommand {
        HumanRunAccessCommand { run_id: self.run_id.clone(), caller_actor_id: actor.to_string() }
    }

    async fn node_artifact(&self, actor: &str, node: &str) -> Option<String> {
        self.runtime
            .get_state_machine_node_run_for_human(self.access(actor), node)
            .await
            .expect("read node")
            .expect("node exists")
            .node
            .artifact_text
    }

    async fn run_view_artifact(&self, actor: &str, node: &str) -> Option<String> {
        self.runtime
            .get_state_machine_run_for_human(self.access(actor))
            .await
            .expect("read run")
            .expect("run exists")
            .nodes
            .into_iter()
            .find(|run| run.node_id == node)
            .expect("node in run view")
            .artifact_text
    }

    async fn legacy_access_artifact(&self, actor: &str, node: &str) -> Option<String> {
        self.runtime
            .get_state_machine_node_run_with_access(
                StateMachineRunAccessCommand {
                    run_id: self.run_id.clone(),
                    authenticated_human: Some(AuthenticatedHumanCaller {
                        actor_id: actor.to_string(),
                        display_name: None,
                    }),
                },
                node,
            )
            .await
            .expect("read node with access")
            .expect("node exists")
            .node
            .artifact_text
    }

    async fn history_contents(&self, view: Option<HumanMessageView>) -> Vec<String> {
        let history = match view {
            Some(view) => self
                .runtime
                .get_state_machine_session_history_for_view(&self.session_id, 50, None, view)
                .await,
            None => self
                .runtime
                .get_state_machine_session_history(&self.session_id, 50, None)
                .await,
        };
        history
            .expect("read history")
            .expect("history exists")
            .messages
            .into_iter()
            .map(|message| message.content)
            .collect()
    }
}

fn participant_view(actor: &str) -> HumanMessageView {
    HumanMessageView {
        actor_id: actor.to_string(),
        scope: MessageViewScope::Participant,
        allow_legacy_unclassified_chat: false,
    }
}

#[tokio::test]
async fn parallel_votes_are_scoped_to_their_assignee() {
    let h = VoteHarness::start().await;
    assert!(h.pending(HUMAN_B).await.is_empty(), "B does not see A's vote_start");
    h.respond(HUMAN_A, "vote_start", "开始").await.expect("A opens the vote");

    let a_pending = h.pending(HUMAN_A).await;
    let b_pending = h.pending(HUMAN_B).await;
    assert_eq!(a_pending.iter().map(|node| node.node_id.as_str()).collect::<Vec<_>>(), ["vote_a"]);
    assert_eq!(b_pending.iter().map(|node| node.node_id.as_str()).collect::<Vec<_>>(), ["vote_b"]);
    assert!(b_pending.iter().all(|node| !node.instruction.contains("A 请投票")));

    let error = h.respond(HUMAN_B, "vote_a", "我替 A 投").await.expect_err("B cannot answer A's node");
    assert!(matches!(error, CollaborationRuntimeError::Forbidden(_)));
}

#[tokio::test]
async fn participant_history_keeps_other_humans_prompt_and_ballot_out() {
    let h = VoteHarness::start().await;
    h.respond(HUMAN_A, "vote_start", "开始").await.unwrap();
    h.respond(HUMAN_A, "vote_a", BALLOT_A).await.expect("A votes");

    let b_history = h.history_contents(Some(participant_view(HUMAN_B))).await;
    assert!(!b_history.iter().any(|text| text.contains(BALLOT_A)), "{b_history:?}");
    assert!(!b_history.iter().any(|text| text.contains("A 请投票")), "{b_history:?}");
    assert!(b_history.iter().any(|text| text.contains("B 请投票")), "B sees its own prompt");

    let a_history = h.history_contents(Some(participant_view(HUMAN_A))).await;
    assert!(a_history.iter().any(|text| text.contains(BALLOT_A)), "A sees its own ballot");
}

/// Known limitation accepted for this phase (see the #2609 RFC): the node
/// detail and run views keep the run-level access rule, so a Present Human
/// calling them directly can read another Human's ballot before the tally.
/// If a later phase adds server-side withholding, flip these assertions.
#[tokio::test]
async fn known_limitation_direct_node_reads_return_unpublished_ballot() {
    let h = VoteHarness::start().await;
    h.respond(HUMAN_A, "vote_start", "开始").await.unwrap();
    h.respond(HUMAN_A, "vote_a", BALLOT_A).await.expect("A votes");

    assert_eq!(h.node_artifact(HUMAN_B, "vote_a").await.as_deref(), Some(BALLOT_A));
    assert_eq!(h.run_view_artifact(HUMAN_B, "vote_a").await.as_deref(), Some(BALLOT_A));
    assert_eq!(h.legacy_access_artifact(HUMAN_B, "vote_a").await.as_deref(), Some(BALLOT_A));
}
