//! Provider-independent assertions for the immutable workflow-revision
//! directory (plan W). One publication freezes one (scope, group) to a
//! store-assigned immutable revision: definition, compiled validation snapshot
//! and frozen binding manifest are stored as one object row, and the group's
//! current publish pointer is updated only after that object verifiably
//! exists. Sessions pin the revision resolved at freeze time and never follow
//! later publications.
//!
//! These are real callable harnesses, not proof that a backend exists. Each
//! provider supplies an isolated fixture (publication + fresh directory
//! construction + row-damage hooks) and calls these from its conformance
//! entry. The content fixtures below pass the strict-FSM publication gate, so
//! every provider proves the same publish/pin/no-fallback semantics.

use super::ContractResult;
use bcs_dist_model::*;
use bcs_domain::collaboration::{
    ProjectionPolicy, StateMachineDefaults, StateMachineDefinition, StateMachineNodeDefinition,
    StateMachineNodeKind, StateMachineTransition,
};
use bcs_service_api::port::dist_agent::{DistGroupDirectoryPort, GroupDirectoryError};
use bcs_service_api::port::repo::distributed::common::{IdempotentWrite, StoreError};
use std::collections::BTreeMap;
use std::sync::Arc;

// ---------------------------------------------------------------------------
// shared fixtures (known to pass the strict-FSM publication gate)
// ---------------------------------------------------------------------------

pub const WORKFLOW_DEFINITION_ID: &str = "wf-strict-group";

pub fn revision_scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("t-workflow-revision".into()),
        principal: PrincipalId("principal-a".into()),
    }
}

pub fn revision_group() -> GroupId {
    GroupId("group-workflow-revision".into())
}

/// A publishable group workflow: a strict BotTask DAG with one entry and one
/// designated final output node, one participant binding per node.
fn strict_group_workflow(instruction: &str) -> StateMachineDefinition {
    let node = |id: &str, targets: &[&str], final_output: bool| StateMachineNodeDefinition {
        kind: StateMachineNodeKind::BotTask,
        display_name: id.to_string(),
        loop_definition: None,
        assignee: None,
        notification: None,
        instruction: Some(format!("{instruction} {id}")),
        output_contract: None,
        node_timeout_ms: Some(600_000),
        max_attempts: None,
        transitions: if final_output {
            BTreeMap::new()
        } else {
            BTreeMap::from([(
                "complete".to_string(),
                StateMachineTransition {
                    display_name: None,
                    targets: targets.iter().map(|target| target.to_string()).collect(),
                    guard: None,
                },
            )])
        },
        action: None,
        visibility: None,
        judge: None,
        final_output,
        extensions: serde_json::Map::new(),
    };
    StateMachineDefinition {
        version: 1,
        graph_mode: bcs_domain::collaboration::StateMachineGraphMode::Acyclic,
        initial_node: None,
        input_schema: None,
        variables: BTreeMap::new(),
        events: BTreeMap::new(),
        projection: ProjectionPolicy::default(),
        defaults: StateMachineDefaults {
            node_timeout_ms: Some(600_000),
            max_attempts: 1,
        },
        human_input_channel: None,
        nodes: [("plan", vec!["work"]), ("work", vec!["final"]), ("final", vec![])]
            .into_iter()
            .map(|(id, targets)| (id.to_string(), node(id, &targets, id == "final")))
            .collect(),
        extensions: serde_json::Map::new(),
    }
}

/// The frozen per-node binding manifest published with a workflow revision.
fn bindings(manifest: &str) -> Vec<ParticipantBinding> {
    ["plan", "work", "final"]
        .into_iter()
        .map(|node| ParticipantBinding {
            node: NodeId(node.into()),
            target: AgentBinding {
                agent: AgentId(format!("agent-{manifest}-{node}")),
                binding_version: BindingVersion(format!("binding-{manifest}")),
                credential_ref: CredentialRef(format!("credential-{manifest}")),
            },
            pool: PoolId(format!("pool-{manifest}")),
            capabilities: DownstreamCapabilities {
                idempotent_start: true,
                idempotent_input: true,
                query_by_request: true,
                query_by_task: true,
                subscribe: true,
                resume_events: true,
                cancel: true,
            },
        })
        .collect()
}

fn limits() -> SessionLimits {
    SessionLimits {
        max_queued_tasks: 32,
        max_task_steps: 512,
        task_timeout_millis: 3_600_000,
        max_inline_bytes: 65_536,
    }
}

/// One publishable frozen definition: workflow, binding manifest and limits.
#[derive(Debug, Clone)]
pub struct WorkflowPublication {
    pub definition: StateMachineDefinition,
    pub participants: Vec<ParticipantBinding>,
    pub limits: SessionLimits,
}

pub fn workflow_publication_v1() -> WorkflowPublication {
    WorkflowPublication {
        definition: strict_group_workflow("research and answer"),
        participants: bindings("v1"),
        limits: limits(),
    }
}

/// Different content under the same node names: different instruction bytes
/// and a different binding manifest, so a pinned v1 session must keep its
/// original bindings while new contexts see v2.
pub fn workflow_publication_v2() -> WorkflowPublication {
    WorkflowPublication {
        definition: strict_group_workflow("research deeper and answer"),
        participants: bindings("v2"),
        limits: limits(),
    }
}

fn definition_value(publication: &WorkflowPublication) -> serde_json::Value {
    serde_json::to_value(&publication.definition).expect("definition serializes")
}

fn participants_value(publication: &WorkflowPublication) -> serde_json::Value {
    serde_json::to_value(&publication.participants).expect("participants serialize")
}

// ---------------------------------------------------------------------------
// provider-neutral fixture contract
// ---------------------------------------------------------------------------

/// How the fixture damages a persisted object row to prove no-fallback
/// semantics. Row-level access is provider plumbing, not product API.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RevisionDamage {
    /// The object row disappears (external deletion).
    Deleted,
    /// The stored definition bytes change but stay parseable, so only the
    /// digest comparison can catch the tamper.
    Corrupted,
    /// The row's stored format marker is unknown to this code (old format).
    OldFormat,
}

/// Outcome of attempting the forbidden write: different content under an
/// existing revision identity.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SameRevisionWrite {
    Rejected,
    Overwritten,
}

/// Where an injected publication fault lands.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PublishFault {
    /// The durable layer fails at the pointer write that follows the object
    /// save, inside the publication's atomicity boundary.
    AtPointerWrite,
}

/// Provider fixture for the workflow-revision conformance vectors. `publish`
/// runs the real publication path, `directory` returns a fresh `DistGroupDirectoryPort`
/// instance over the same durable state (`generation` > 0 simulates a process
/// restart with cleared caches), and the damage hooks mutate raw rows.
#[async_trait::async_trait]
pub trait WorkflowRevisionFixture: Send + Sync {
    async fn publish(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        definition_id: &str,
        publication: &WorkflowPublication,
    ) -> Result<(WorkflowRef, IdempotentWrite), StoreError>;

    async fn publish_with_fault(
        &self,
        fault: PublishFault,
        scope: &TaskScope,
        group: &GroupId,
        definition_id: &str,
        publication: &WorkflowPublication,
    ) -> Result<(WorkflowRef, IdempotentWrite), StoreError>;

    async fn directory(&self, generation: u64) -> Arc<dyn DistGroupDirectoryPort>;

    async fn damage(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        reference: &WorkflowRef,
        damage: RevisionDamage,
    );

    async fn try_overwrite_same_revision(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        reference: &WorkflowRef,
        publication: &WorkflowPublication,
    ) -> SameRevisionWrite;
}

fn assert_invalid_revision<T: std::fmt::Debug>(actual: Result<T, GroupDirectoryError>) {
    match actual {
        Err(GroupDirectoryError::InvalidRevision) => {}
        other => panic!(
            "expected InvalidRevision, never a fallback to current config: got {other:?}"
        ),
    }
}

// ---------------------------------------------------------------------------
// central contract suite (R25 entry: these vectors + the directory port
// harness in `services.rs` run against the real provider)
// ---------------------------------------------------------------------------

/// The plan-W recovery vector: publish v1, freeze a session S on it, publish
/// v2, restart with cleared caches; S keeps loading its pinned v1 bytes and
/// bindings, new contexts resolve v2, and deleting or tampering the pinned
/// object surfaces an explicit error, never a fallback to v2.
pub async fn publish_v2_restart_loads_pinned_v1(
    fixture: &dyn WorkflowRevisionFixture,
) -> ContractResult {
    let scope = revision_scope();
    let group = revision_group();
    let v1 = workflow_publication_v1();
    let v2 = workflow_publication_v2();

    // Publish v1; the session freezes exactly what resolve hands it.
    let (first, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v1)
        .await?;
    assert_eq!(outcome, IdempotentWrite::Applied);
    let session = fixture.directory(0).await;
    let frozen_v1 = session.resolve(&scope, &group).await?;
    assert_eq!(frozen_v1.group, group);
    assert_eq!(frozen_v1.workflow, first);
    assert_eq!(
        serde_json::to_value(&frozen_v1.participants)?,
        participants_value(&v1)
    );
    assert_eq!(frozen_v1.limits, v1.limits);

    // v2 is published while the session runs.
    let (second, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v2)
        .await?;
    assert_eq!(outcome, IdempotentWrite::Applied);
    assert_ne!(second.revision, first.revision);

    // Process restart with cleared caches: a fresh directory instance over
    // the same durable state.
    let restarted = fixture.directory(1).await;

    // New contexts resolve v2 with the v2 binding manifest.
    let frozen_v2 = restarted.resolve(&scope, &group).await?;
    assert_eq!(frozen_v2.workflow, second);
    assert_eq!(
        serde_json::to_value(&frozen_v2.participants)?,
        participants_value(&v2)
    );
    assert_ne!(
        serde_json::to_value(&frozen_v2.participants)?,
        participants_value(&v1)
    );

    // Session S keeps its pinned revision: byte- and digest-equal v1.
    let pinned = restarted.load_workflow(&scope, &frozen_v1.workflow).await?;
    assert_eq!(serde_json::to_value(&pinned)?, definition_value(&v1));
    assert_ne!(serde_json::to_value(&pinned)?, definition_value(&v2));

    // Tamper the pinned object: parseable but different bytes, so only the
    // digest check catches it. S's load fails closed, never falls back to v2.
    fixture
        .damage(&scope, &group, &frozen_v1.workflow, RevisionDamage::Corrupted)
        .await;
    assert_invalid_revision(restarted.load_workflow(&scope, &frozen_v1.workflow).await);

    // Delete the pinned object outright: S's load still fails closed and the
    // current publication stays untouched and reachable.
    fixture
        .damage(&scope, &group, &frozen_v1.workflow, RevisionDamage::Deleted)
        .await;
    assert_invalid_revision(restarted.load_workflow(&scope, &frozen_v1.workflow).await);
    let current = restarted.resolve(&scope, &group).await?;
    assert_eq!(current.workflow, second);
    Ok(())
}

/// Identical content republishes to the same revision; moving content moves
/// the pointer; old content republished later gets a NEW store-assigned
/// revision instead of rewinding identity (content digests are not unique per
/// revision, revision identity is publication order).
pub async fn idempotent_republish_returns_the_same_revision(
    fixture: &dyn WorkflowRevisionFixture,
) -> ContractResult {
    let scope = revision_scope();
    let group = revision_group();
    let v1 = workflow_publication_v1();
    let v2 = workflow_publication_v2();
    let directory = fixture.directory(0).await;

    // First publication applies; an identical retry republishes the SAME
    // revision (AlreadyApplied) and moves nothing.
    let (first, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v1)
        .await?;
    assert_eq!(outcome, IdempotentWrite::Applied);
    let (retry, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v1)
        .await?;
    assert_eq!(outcome, IdempotentWrite::AlreadyApplied);
    assert_eq!(retry, first);
    let frozen = directory.resolve(&scope, &group).await?;
    assert_eq!(frozen.workflow, first);

    // Different content publishes as a new revision and moves the pointer.
    let (second, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v2)
        .await?;
    assert_eq!(outcome, IdempotentWrite::Applied);
    assert_ne!(second.revision, first.revision);
    assert_eq!(directory.resolve(&scope, &group).await?.workflow, second);

    // Republishing the older content is a NEW publication: a fresh
    // store-assigned revision carrying the same digest, not a rewind.
    let (third, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v1)
        .await?;
    assert_eq!(outcome, IdempotentWrite::Applied);
    assert_ne!(third.revision, first.revision);
    assert_ne!(third.revision, second.revision);
    assert_eq!(third.digest, first.digest);
    let current = directory.resolve(&scope, &group).await?;
    assert_eq!(current.workflow, third);

    // The previously pinned revision still loads its original bytes.
    let pinned = directory.load_workflow(&scope, &first).await?;
    assert_eq!(serde_json::to_value(&pinned)?, definition_value(&v1));
    Ok(())
}

/// One revision identity may never carry different content: the durable
/// layer itself rejects the second row, the pinned bytes stay unchanged, and
/// a caller presenting the right revision with a foreign digest gets an
/// explicit error instead of the other content.
pub async fn same_revision_rejects_different_content(
    fixture: &dyn WorkflowRevisionFixture,
) -> ContractResult {
    let scope = revision_scope();
    let group = revision_group();
    let v1 = workflow_publication_v1();
    let v2 = workflow_publication_v2();
    let directory = fixture.directory(0).await;

    let (first, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v1)
        .await?;
    assert_eq!(outcome, IdempotentWrite::Applied);

    // The forbidden write: different content under the existing revision
    // identity. The durable layer rejects it, so v1 bytes survive.
    assert_eq!(
        fixture
            .try_overwrite_same_revision(&scope, &group, &first, &v2)
            .await,
        SameRevisionWrite::Rejected
    );
    let pinned = directory.load_workflow(&scope, &first).await?;
    assert_eq!(serde_json::to_value(&pinned)?, definition_value(&v1));

    // The caller-facing view: a reference claiming v1's revision with v2's
    // digest is rejected as an invalid revision, never answered with v2.
    let (second, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v2)
        .await?;
    assert_eq!(outcome, IdempotentWrite::Applied);
    let mut forged = first.clone();
    forged.digest = second.digest.clone();
    assert_invalid_revision(directory.load_workflow(&scope, &forged).await);
    Ok(())
}

/// resolve never hands out a revision whose object is unreadable: a
/// publication fault between the object save and the pointer update fails
/// honestly and leaves the previous publication in place, and an old-format
/// or otherwise unreadable current object makes resolve fail closed instead
/// of returning a definition whose load would break.
pub async fn pointer_never_references_an_unreadable_object(
    fixture: &dyn WorkflowRevisionFixture,
) -> ContractResult {
    let scope = revision_scope();
    let group = revision_group();
    let v1 = workflow_publication_v1();
    let v2 = workflow_publication_v2();

    let (first, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v1)
        .await?;
    assert_eq!(outcome, IdempotentWrite::Applied);
    let directory = fixture.directory(0).await;
    assert_eq!(directory.resolve(&scope, &group).await?.workflow, first);

    // Fault injected at the pointer write that follows the object save: the
    // publication fails honestly (never swallowed) and the pointer stays on
    // the previous, verifiable publication.
    let faulted = fixture
        .publish_with_fault(
            PublishFault::AtPointerWrite,
            &scope,
            &group,
            WORKFLOW_DEFINITION_ID,
            &v2,
        )
        .await;
    assert!(faulted.is_err(), "pointer-write failure must propagate");
    let frozen = directory.resolve(&scope, &group).await?;
    assert_eq!(frozen.workflow, first);
    let pinned = directory.load_workflow(&scope, &first).await?;
    assert_eq!(serde_json::to_value(&pinned)?, definition_value(&v1));

    // The retried publication succeeds and the pointer advances.
    let (second, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v2)
        .await?;
    assert_eq!(outcome, IdempotentWrite::Applied);
    let frozen = directory.resolve(&scope, &group).await?;
    assert_eq!(frozen.workflow, second);

    // An old-format current object is an explicit error at BOTH entry points:
    // resolve never returns a definition whose load would fail closed.
    fixture
        .damage(&scope, &group, &second, RevisionDamage::OldFormat)
        .await;
    assert_invalid_revision(directory.resolve(&scope, &group).await);
    assert_invalid_revision(directory.load_workflow(&scope, &second).await);
    Ok(())
}
