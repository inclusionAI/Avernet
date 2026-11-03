use super::super::{
    Arc,
    BTreeMap,
    CollaborationDefinition,
    MemoryCollaborationStore,
    Mutex,
    ResolvedParticipantBinding,
    ServiceResult,
    StateMachineDefinitionRepoPort,
    StateMachineRun,
    async_trait
};

pub(in super::super) fn assert_inferred_default_requires(definition: &CollaborationDefinition) {
    let requires = definition
        .requires
        .as_ref()
        .expect("requires should be inferred");
    assert!(
        requires
            .server_features
            .contains(&"state_machine.graph_mode.acyclic".to_string())
    );
    assert!(
        requires
            .server_features
            .contains(&"state_machine.node.kind.bot_task".to_string())
    );
    assert!(
        requires
            .server_features
            .contains(&"state_machine.transitions.complete".to_string())
    );
    assert!(
        requires
            .bot_runtime_features
            .contains(&"delivery.chat_send_task_compat".to_string())
    );
}

#[derive(Default)]
pub(in super::super) struct CountingDefinitionRepo {
    inner: Arc<MemoryCollaborationStore>,
    upsert_calls: Mutex<usize>,
}

impl CountingDefinitionRepo {
    pub(in super::super) fn new(inner: Arc<MemoryCollaborationStore>) -> Self {
        Self {
            inner,
            upsert_calls: Mutex::new(0),
        }
    }

    pub(in super::super) async fn upsert_calls(&self) -> usize {
        *self.upsert_calls.lock().await
    }
}

#[async_trait]
impl StateMachineDefinitionRepoPort for CountingDefinitionRepo {
    async fn upsert(&self, definition: CollaborationDefinition) -> ServiceResult<()> {
        *self.upsert_calls.lock().await += 1;
        StateMachineDefinitionRepoPort::upsert(&*self.inner, definition).await
    }

    async fn get(&self, id: &str, version: i32) -> ServiceResult<Option<CollaborationDefinition>> {
        StateMachineDefinitionRepoPort::get(&*self.inner, id, version).await
    }

    async fn save_run_snapshot(
        &self,
        run: &StateMachineRun,
        group_version: i32,
        definition: &CollaborationDefinition,
        resolved_participant_bindings: Option<&BTreeMap<String, ResolvedParticipantBinding>>,
        execution_plan: Option<&bcs_service_api::port::repo::StateMachineExecutionPlanSnapshot>,
    ) -> ServiceResult<()> {
        StateMachineDefinitionRepoPort::save_run_snapshot(
            &*self.inner,
            run,
            group_version,
            definition,
            resolved_participant_bindings,
            execution_plan,
        )
        .await
    }

    async fn get_run_snapshot(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<bcs_service_api::port::repo::StateMachineRunSnapshot>> {
        StateMachineDefinitionRepoPort::get_run_snapshot(&*self.inner, run_id).await
    }
}

pub(in super::super) fn one_shot_authoring_yaml() -> String {
    r#"
name: One Shot
participants:
  writer:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: writer
        instruction: Answer the current question.
        final_output: true
"#
    .to_string()
}

pub(in super::super) fn one_shot_human_input_authoring_yaml() -> String {
    r#"
name: One Shot Human Review
participants:
  driver:
    required: false
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: human_input
        display_name: Review
        instruction: 请在前端给出你的意见。
        node_timeout_ms: 60000
"#
    .to_string()
}

pub(in super::super) fn single_node_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: single_node
version: 1
name: Single Node
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    defaults:
      node_timeout_ms: 120000
      max_attempts: 2
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        node_timeout_ms: 60000
        max_attempts: 3
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer the question.
        final_output: true
"#
    .to_string()
}

pub(in super::super) fn human_input_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: human_input_single
version: 1
name: Human Review
participants: {}
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    human_input_channel:
      channel_type: dingtalk
      fixed_group:
        conversation_type: group
        conversation_id: cid-review
    nodes:
      review:
        kind: human_input
        display_name: Review
        assignee:
          type: runtime_actor
          actor: human_1001
        notification:
          mode: fixed_group
        instruction: 请用自然语言给出你的意见。
        node_timeout_ms: 60000
"#
    .to_string()
}

pub(in super::super) fn frontend_human_input_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: frontend_human_input
version: 1
name: Frontend Human Review
participants: {}
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: human_input
        display_name: Review
        instruction: 请在前端给出你的意见。
        node_timeout_ms: 60000
"#
    .to_string()
}

pub(in super::super) fn judged_human_input_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: human_input_judged
version: 1
name: Human Review With Judge
participants: {}
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    human_input_channel:
      channel_type: dingtalk
      fixed_group:
        conversation_type: group
        conversation_id: cid-review
    nodes:
      review:
        kind: human_input
        display_name: Review
        assignee:
          type: runtime_actor
          actor: human_1001
        notification:
          mode: fixed_group
        instruction: 请用自然语言给出你的意见。
        node_timeout_ms: 60000
        transitions:
          approved:
            targets: []
          rejected:
            targets: []
        judge:
          type: llm
          criteria:
            - 是否明确批准或拒绝
          outcomes:
            - approved
            - rejected
"#
    .to_string()
}

pub(in super::super) fn single_node_authoring_yaml(name: &str) -> String {
    format!(
        r#"
api_version: bcs.collaboration/v1
name: {name}
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer the question.
        final_output: true
"#
    )
}

pub(in super::super) fn single_node_template_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: single_node_template
version: 1
name: Single Node Template
participants:
  driver:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer the question.
        final_output: true
"#
    .to_string()
}

pub(in super::super) fn join_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: join_graph
version: 1
name: Join Graph
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      start:
        kind: bot_task
        display_name: Start
        assignee:
          type: bot_binding
          binding: driver
        instruction: Start.
        transitions:
          complete:
            targets: [branch_b, branch_c]
      branch_b:
        kind: bot_task
        display_name: Branch B
        assignee:
          type: bot_binding
          binding: driver
        instruction: B.
        transitions:
          complete:
            targets: [join]
      branch_c:
        kind: bot_task
        display_name: Branch C
        assignee:
          type: bot_binding
          binding: driver
        instruction: C.
        transitions:
          complete:
            targets: [join]
      join:
        kind: bot_task
        display_name: Join
        assignee:
          type: bot_binding
          binding: driver
        instruction: Join.
        final_output: true
"#
    .to_string()
}

pub(in super::super) fn judge_branch_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: judge_branch
version: 1
name: Judge Branch
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: bot_task
        display_name: Review
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce a candidate artifact.
        judge:
          type: llm
          criteria:
            - Is the answer good enough?
          outcomes: [approved, rejected]
        transitions:
          approved:
            targets: [publish]
          rejected:
            targets: [revise]
      publish:
        kind: bot_task
        display_name: Publish
        assignee:
          type: bot_binding
          binding: driver
        instruction: Publish final answer.
        final_output: true
      revise:
        kind: bot_task
        display_name: Revise
        assignee:
          type: bot_binding
          binding: driver
        instruction: Revise answer.
        transitions:
          complete:
            targets: [manual_review]
      manual_review:
        kind: bot_task
        display_name: Manual Review
        assignee:
          type: bot_binding
          binding: driver
        instruction: Review revised answer.
        transitions:
          complete:
            targets: [publish]
"#
    .to_string()
}

pub(in super::super) fn judge_timeout_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: judge_timeout
version: 1
name: Judge Timeout
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: bot_task
        display_name: Review
        node_timeout_ms: 1000
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce a candidate artifact.
        judge:
          type: llm
          criteria:
            - Is the answer good enough?
          outcomes: [approved, rejected]
        transitions:
          approved:
            targets: [publish]
          rejected:
            targets: []
      publish:
        kind: bot_task
        display_name: Publish
        assignee:
          type: bot_binding
          binding: driver
        instruction: Publish final answer.
        final_output: true
"#
    .to_string()
}

pub(in super::super) fn judge_shared_merge_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: judge_shared_merge
version: 1
name: Judge Shared Merge
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      review:
        kind: bot_task
        display_name: Review
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce a candidate artifact.
        judge:
          type: llm
          criteria:
            - Is the answer good enough?
          outcomes: [approved, rejected]
        transitions:
          approved:
            targets: [fast]
          rejected:
            targets: [slow]
      fast:
        kind: bot_task
        display_name: Fast Path
        assignee:
          type: bot_binding
          binding: driver
        instruction: Continue approved answer.
        transitions:
          complete:
            targets: [merge]
      slow:
        kind: bot_task
        display_name: Slow Path
        assignee:
          type: bot_binding
          binding: driver
        instruction: Revise rejected answer.
        transitions:
          complete:
            targets: [merge]
      merge:
        kind: bot_task
        display_name: Merge
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce final answer.
        final_output: true
"#
    .to_string()
}
