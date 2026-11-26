//! Host fixtures of the published group: the two-node workflow, its
//! frozen definitions, the D0 policy, the in-process peer transport, and
//! the fast-stop-deadline variant used by the quarantine vector. (Shared
//! items come from `super::*`.)
use super::*;
// ---------------------------------------------------------------------------
// The published group catalog: a two-node workflow, its frozen definition,
// and the D0 host policy that publishes it.
// ---------------------------------------------------------------------------

fn node(id: &str, targets: &[&str], final_output: bool) -> StateMachineNodeDefinition {
    let transitions = if final_output {
        BTreeMap::new()
    } else {
        BTreeMap::from([("complete".to_string(), StateMachineTransition {
            display_name: None,
            targets: targets.iter().map(|t| t.to_string()).collect(),
            guard: None,
        })])
    };
    StateMachineNodeDefinition {
        kind: StateMachineNodeKind::BotTask,
        display_name: id.to_string(),
        loop_definition: None,
        assignee: None,
        notification: None,
        instruction: Some(format!("work on {id}")),
        output_contract: None,
        node_timeout_ms: Some(120_000),
        max_attempts: None,
        transitions,
        action: None,
        visibility: None,
        judge: None,
        final_output,
        extensions: serde_json::Map::new(),
    }
}

fn workflow() -> StateMachineDefinition {
    StateMachineDefinition {
        version: 1,
        graph_mode: StateMachineGraphMode::Acyclic,
        initial_node: None,
        input_schema: None,
        variables: BTreeMap::new(),
        events: BTreeMap::new(),
        projection: ProjectionPolicy::default(),
        defaults: StateMachineDefaults {
            node_timeout_ms: Some(120_000),
            max_attempts: 1,
        },
        human_input_channel: None,
        nodes: BTreeMap::from([
            (NODE_RESEARCH.to_string(), node(NODE_RESEARCH, &[NODE_REVIEW], false)),
            (NODE_REVIEW.to_string(), node(NODE_REVIEW, &[], true)),
        ]),
        extensions: serde_json::Map::new(),
    }
}

/// The no-dedupe-replay target profile: an uncertain first send cannot
/// rely on a negotiated dedupe proof, so the Connector's rules take the
/// AskAgent path — the entry of the real short-term-exhaustion handoff
/// into the verification lane.
fn handoff_prone_capabilities() -> DownstreamCapabilities {
    let mut caps = capabilities();
    caps.persistent_dedupe = false;
    caps.idempotent_start = false;
    caps
}

/// The quarantine-vector catalog: the same published two-node group, but
/// every node's stop-verification deadline is seconds (not the default
/// two minutes), so the verification lane's `Quarantined` record lands
/// inside a test's wall time.
pub struct FastDeadlineCatalog;

fn fast_workflow() -> bcs_domain::collaboration::StateMachineDefinition {
    let mut definition = workflow();
    for node in definition.nodes.values_mut() {
        node.node_timeout_ms = Some(5_000);
    }
    definition.defaults.node_timeout_ms = Some(5_000);
    definition
}

#[async_trait]
impl DistGroupDirectoryPort for FastDeadlineCatalog {
    async fn resolve(
        &self,
        _: &TaskScope,
        group: &GroupId,
    ) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        if group.0 != GROUP {
            return Err(GroupDirectoryError::NotFound);
        }
        Ok(FrozenGroupDefinition {
            group: group.clone(),
            workflow: workflow_ref(),
            participants: vec![
                binding_with(NODE_RESEARCH, handoff_prone_capabilities()),
                binding_with(NODE_REVIEW, handoff_prone_capabilities()),
            ],
            limits: SessionLimits {
                max_queued_tasks: 16,
                max_task_steps: 512,
                task_timeout_millis: 600_000,
                max_inline_bytes: 65_536,
            },
        })
    }
    async fn load_workflow(
        &self,
        _: &TaskScope,
        _: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        Ok(fast_workflow())
    }
}

fn capabilities() -> DownstreamCapabilities {
    DownstreamCapabilities {
        idempotent_start: true,
        idempotent_input: true,
        query_by_request: true,
        query_by_task: true,
        // The companion drives the dispatch legs through the by-task /
        // by-request query polls; subscriptions stay off so the engine
        // never parks on a stream event the journal would have to serve.
        subscribe: false,
        resume_events: false,
        cancel: true,
        isolated_memory: true,
        persistent_dedupe: true,
    }
}

fn binding(node: &str) -> ParticipantBinding {
    binding_with(node, capabilities())
}

fn binding_with(node: &str, capabilities: DownstreamCapabilities) -> ParticipantBinding {
    ParticipantBinding {
        node: NodeId(node.into()),
        target: AgentBinding {
            agent: AgentId(format!("agent-{node}")),
            binding_version: BindingVersion("1".into()),
            credential_ref: CredentialRef("companion-credential".into()),
        },
        pool: PoolId(POOL.into()),
        capabilities,
    }
}

pub(super) fn workflow_ref() -> WorkflowRef {
    WorkflowRef {
        definition_id: WORKFLOW_ID.into(),
        revision: WORKFLOW_REVISION.into(),
        digest: Digest("companion-workflow".into()),
    }
}

/// The host-registered group catalog (directory port): the frozen
/// publication of the two-node group and its workflow definition resolve
/// through the same seam the composition root injects.
pub struct Catalog;

#[async_trait]
impl DistGroupDirectoryPort for Catalog {
    async fn resolve(
        &self,
        _: &TaskScope,
        group: &GroupId,
    ) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        if group.0 != GROUP {
            return Err(GroupDirectoryError::NotFound);
        }
        Ok(FrozenGroupDefinition {
            group: group.clone(),
            workflow: workflow_ref(),
            participants: vec![binding(NODE_RESEARCH), binding(NODE_REVIEW)],
            limits: SessionLimits {
                max_queued_tasks: 16,
                max_task_steps: 512,
                task_timeout_millis: 600_000,
                max_inline_bytes: 65_536,
            },
        })
    }
    async fn load_workflow(
        &self,
        _: &TaskScope,
        _: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        Ok(workflow())
    }
}

/// The host-registered D0 driver: this group is published, invocable,
/// readable and cancelable for the companion's caller.
pub struct AllowPolicy;

#[async_trait]
impl GroupGrantPolicy for AllowPolicy {
    async fn grants(&self, _: &GatewayCaller, _: &GroupId) -> GroupRoundGrants {
        GroupRoundGrants {
            published: true,
            invoke: true,
            read: true,
            cancel: true,
        }
    }
}


// ---------------------------------------------------------------------------
// In-process Worker peer transport of the handoff lane (the same
// Weak-registry the Singlebox worker story uses; peers hand off through
// the runtime application service of the registered incarnation).
// ---------------------------------------------------------------------------

#[derive(Default)]
pub struct Peers(Mutex<HashMap<String, Weak<bcs_dist_worker::WorkerRuntime>>>);

impl Peers {
    fn peer_of(&self, incarnation: &str) -> Option<Arc<bcs_dist_worker::WorkerRuntime>> {
        self.0
            .lock()
            .unwrap()
            .get(incarnation)
            .and_then(Weak::upgrade)
    }
    pub fn register(&self, runtime: &Arc<bcs_dist_worker::WorkerRuntime>) {
        self.0.lock().unwrap().insert(
            runtime.instance().incarnation.clone(),
            Arc::downgrade(runtime),
        );
    }
}

#[async_trait]
impl bcs_service_api::port::dist_runtime::DistWorkerPeerPort for Peers {
    async fn handoff(
        &self,
        target: &MemberRecord,
        request: HandoffRequest,
    ) -> Result<HandoffOutcome, bcs_service_api::types::dist_runtime::RuntimeError> {
        use bcs_service_api::application::dist_runtime::DistWorkerRuntimeService;
        let runtime = self
            .peer_of(&target.instance.incarnation)
            .ok_or_else(|| bcs_service_api::types::dist_runtime::RuntimeError {
                code: "peer_offline".into(),
            })?;
        let application = bcs_app_dist_runtime::WorkerRuntimeApplication::new(runtime);
        DistWorkerRuntimeService::accept_handoff(
            &application,
            request.from.owner.instance.clone(),
            request,
        )
        .await
    }
    async fn source_done(
        &self,
        target: &MemberRecord,
        source: Instance,
        claim: ShardClaim,
        id: RequestId,
    ) -> Result<bool, bcs_service_api::types::dist_runtime::RuntimeError> {
        use bcs_service_api::application::dist_runtime::DistWorkerRuntimeService;
        let runtime = self
            .peer_of(&target.instance.incarnation)
            .ok_or_else(|| bcs_service_api::types::dist_runtime::RuntimeError {
                code: "peer_offline".into(),
            })?;
        let application = bcs_app_dist_runtime::WorkerRuntimeApplication::new(runtime);
        DistWorkerRuntimeService::handoff_done(&application, source, claim, id).await
    }
}
