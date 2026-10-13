//! Pure bounded fixed-Loop authoring compilation to the ordinary execution DAG.

use std::collections::{BTreeMap, BTreeSet};

use bcs_config_api::FixedLoopLimits;
use bcs_domain::{
    CollaborationDefinition, CollaborationRuntimeDefinition,
    CompiledArtifactProjection, CompiledEdgeMetadata, CompiledNodeMetadata, FixedLoopDefinition,
    StateMachineExecutionPlan, StateMachineGraphMode, StateMachineLoopFrame, StateMachineLoopRoute,
    StateMachineLoopRouteKind, StateMachineNodeDefinition, StateMachineNodeKind,
    StateMachineTransition,
};
use bcs_service_api::{CollaborationDefinitionValidationDiagnostic, StateMachineLoopCompileRejection,
    StateMachineLoopInstrumentationHook, StateMachineLoopMetric};
use sha2::{Digest, Sha256};

use crate::definition::{CompiledStateMachine, ensure_acyclic, validate_definition};

pub const FIXED_LOOP_COMPILER_VERSION: &str = "bcs.fixed-loop.compiler/v1";
pub const NESTED_FIXED_LOOP_COMPILER_VERSION: &str = "bcs.fixed-loop.compiler/v2";
const MACHINE_PATH: &str = "$.runtime.state_machine";
const LOOP_FEATURES: [&str; 5] = [
    "state_machine.version.2",
    "state_machine.graph_mode.hierarchical",
    "state_machine.node.kind.loop",
    "state_machine.loop.mode.fixed",
    "state_machine.loop.previous_result",
];
const LOOP_NESTING_FEATURE: &str = "state_machine.loop.nesting";

pub struct CompiledAuthoringDefinition {
    pub definition: CollaborationDefinition,
    pub execution: CompiledStateMachine,
    pub plan: Option<StateMachineExecutionPlan>,
}

struct CompileFailure {
    diagnostic: CollaborationDefinitionValidationDiagnostic,
    reason: StateMachineLoopCompileRejection,
}

type CompileResult<T> = Result<T, CompileFailure>;

pub fn compile_authoring_definition(
    definition: CollaborationDefinition,
    limits: &FixedLoopLimits,
) -> Result<CompiledAuthoringDefinition, CollaborationDefinitionValidationDiagnostic> {
    compile_definition(definition, limits).map_err(|failure| failure.diagnostic)
}

pub(crate) fn compile_authoring_definition_with_instrumentation(
    definition: CollaborationDefinition,
    limits: &FixedLoopLimits,
    hook: Option<&dyn StateMachineLoopInstrumentationHook>,
) -> Result<CompiledAuthoringDefinition, CollaborationDefinitionValidationDiagnostic> {
    let is_v2 = matches!(&definition.runtime,
        CollaborationRuntimeDefinition::StateMachine(machine) if machine.version == 2);
    compile_definition(definition, limits).map_err(|failure| {
        if is_v2 {
            tracing::warn!(target: "bcs_observation", reason = ?failure.reason,
                diagnostic_code = %failure.diagnostic.code, authoring_path = %failure.diagnostic.path,
                "state_machine: loop compile rejected");
            if let Some(hook) = hook {
                hook.record(StateMachineLoopMetric::CompileRejected { reason: failure.reason });
            }
        }
        failure.diagnostic
    })
}

fn compile_definition(
    mut definition: CollaborationDefinition,
    limits: &FixedLoopLimits,
) -> CompileResult<CompiledAuthoringDefinition> {
    let machine = match &definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(machine) => machine,
        _ => return Err(invalid("$.runtime.kind", "must be state_machine")),
    };
    if machine.version != 2 {
        let execution = validate_definition(definition)
            .map_err(|error| invalid("$", error.to_string()))?;
        return Ok(CompiledAuthoringDefinition {
            definition: execution.definition.clone(),
            execution,
            plan: None,
        });
    }
    limits.validate().map_err(|error| invalid(MACHINE_PATH, error))?;
    if machine.graph_mode != StateMachineGraphMode::Hierarchical {
        return Err(invalid(format!("{MACHINE_PATH}.graph_mode"), "version 2 requires hierarchical"));
    }
    let mut count = 0usize;
    let mut max_loop_depth = 0u32;
    for (id, node) in &machine.nodes {
        let path = format!("{MACHINE_PATH}.nodes.{id}");
        validate_id(id, &path)?;
        let added = if node.kind == StateMachineNodeKind::Loop {
            let (cost, depth) = validate_loop(node, &machine.nodes, limits, 1, &path)?;
            max_loop_depth = max_loop_depth.max(depth);
            Some(cost)
        } else {
            if node.loop_definition.is_some() {
                return Err(invalid(format!("{path}.loop"), "only kind: loop may define loop"));
            }
            Some(1)
        };
        count = added.and_then(|added| count.checked_add(added))
            .ok_or_else(|| resource_limit(&path, "compiled node count overflow"))?;
        if count > limits.max_compiled_state_machine_nodes {
            return Err(resource_limit(&path, "compiled node count exceeds max_compiled_state_machine_nodes"));
        }
    }
    let finals = machine.nodes.values().filter(|node| node.final_output).count();
    if finals != 1 {
        return Err(invalid(format!("{MACHINE_PATH}.nodes"), "must have exactly one outer final_output node"));
    }

    let compiler_version = if max_loop_depth >= 2 {
        NESTED_FIXED_LOOP_COMPILER_VERSION
    } else {
        FIXED_LOOP_COMPILER_VERSION
    };
    let mut expansion = Expansion {
        definition: &definition,
        compiler_version,
        nodes: BTreeMap::new(),
        metadata: BTreeMap::new(),
        edges: Vec::new(),
        budget: LimitedWriter {
        written: 0,
        limit: limits.max_compiled_state_machine_bytes,
        },
    };
    for (id, node) in &machine.nodes {
        if node.kind == StateMachineNodeKind::Loop {
            expansion.expand_loop(id, node, &machine.nodes, &[], &format!("{MACHINE_PATH}.nodes.{id}"))?;
        } else {
            expansion.expand_ordinary(id, node, &machine.nodes, &[], None,
                &format!("{MACHINE_PATH}.nodes.{id}"))?;
        }
    }
    let Expansion { nodes, metadata, mut edges, .. } = expansion;
    let mut executable_definition = definition.clone();
    let mut executable_machine = machine.clone();
    executable_machine.version = 1;
    executable_machine.graph_mode = StateMachineGraphMode::Acyclic;
    executable_machine.nodes = nodes;
    executable_definition.runtime = CollaborationRuntimeDefinition::StateMachine(executable_machine.clone());
    if let Some(requires) = &mut executable_definition.requires {
        requires.server_features.retain(|feature| {
            !LOOP_FEATURES.contains(&feature.as_str()) && feature != LOOP_NESTING_FEATURE
        });
    }
    let mut execution = validate_definition(executable_definition).map_err(|error| {
        // Preserve the authoring location for errors emitted by the reused DAG validator.
        let message = error.to_string();
        let path = metadata.values().find(|meta| meta.loop_id.is_some() && message.contains(&meta.execution_node_id))
            .map(|meta| match &meta.loop_id {
                Some(id) => format!("{MACHINE_PATH}.nodes.{id}.loop.nodes.{}", meta.definition_node_id),
                None => format!("{MACHINE_PATH}.nodes.{}", meta.definition_node_id),
            }).unwrap_or_else(|| format!("{MACHINE_PATH}.nodes"));
        invalid(path, message)
    })?;
    let mut requires = execution.definition.requires.clone().unwrap_or_default();
    let mut features: BTreeSet<_> = requires.server_features.into_iter().collect();
    features.extend(LOOP_FEATURES.map(str::to_string));
    if max_loop_depth >= 2 {
        features.insert(LOOP_NESTING_FEATURE.into());
    }
    requires.server_features = features.into_iter().collect();
    definition.requires = Some(requires);
    edges.sort_by(|a, b| (&a.source_execution_node_id, &a.outcome, &a.target_execution_node_id)
        .cmp(&(&b.source_execution_node_id, &b.outcome, &b.target_execution_node_id)));
    let plan = StateMachineExecutionPlan {
        compiler_version: compiler_version.into(),
        state_machine: executable_machine,
        node_metadata: metadata,
        edge_metadata: edges,
    };
    // Serialize through a bounded writer so rejecting a large plan does not allocate its full JSON.
    let mut writer = LimitedWriter {
        written: 0,
        limit: limits.max_compiled_state_machine_bytes,
    };
    serde_json::to_writer(&mut writer, &plan)
        .map_err(|_| resource_limit(MACHINE_PATH, "execution plan exceeds max_compiled_state_machine_bytes"))?;
    execution.execution_plan = Some(plan.clone());
    Ok(CompiledAuthoringDefinition {
        definition,
        execution,
        plan: Some(plan),
    })
}

pub fn execution_plan_content_hash(
    plan: &StateMachineExecutionPlan,
) -> Result<String, serde_json::Error> {
    let mut hasher = Sha256::new();
    serde_json::to_writer(&mut hasher, plan)?;
    Ok(format!("{:x}", hasher.finalize()))
}

struct Expansion<'a> {
    definition: &'a CollaborationDefinition,
    compiler_version: &'static str,
    nodes: BTreeMap<String, StateMachineNodeDefinition>,
    metadata: BTreeMap<String, CompiledNodeMetadata>,
    edges: Vec<CompiledEdgeMetadata>,
    budget: LimitedWriter,
}

struct LoopLeafRole<'a> {
    loop_id: &'a str,
    body: &'a FixedLoopDefinition,
    iteration: u32,
}

impl Expansion<'_> {
    fn expand_loop(
        &mut self,
        loop_id: &str,
        loop_node: &StateMachineNodeDefinition,
        parent_scope: &BTreeMap<String, StateMachineNodeDefinition>,
        parent_frames: &[StateMachineLoopFrame],
        node_path: &str,
    ) -> CompileResult<()> {
        let body = loop_node.loop_definition.as_ref().expect("validated Loop");
        for iteration in 1..=body.max_iterations {
            let mut frames = parent_frames.to_vec();
            frames.push(StateMachineLoopFrame {
                loop_id: loop_id.into(), iteration, max_iterations: body.max_iterations,
            });
            for (body_id, body_node) in &body.nodes {
                let body_path = format!("{node_path}.loop.nodes.{body_id}");
                if body_node.kind == StateMachineNodeKind::Loop {
                    self.expand_loop(body_id, body_node, &body.nodes, &frames, &body_path)?;
                } else if body_id == &body.result_node {
                    self.expand_loop_result(
                        body_id, body_node, loop_node, parent_scope, parent_frames,
                        &frames, LoopLeafRole { loop_id, body, iteration }, &body_path,
                    )?;
                } else {
                    self.expand_ordinary(
                        body_id, body_node, &body.nodes, &frames,
                        Some(LoopLeafRole { loop_id, body, iteration }), &body_path,
                    )?;
                }
            }
        }
        Ok(())
    }

    fn expand_ordinary(
        &mut self,
        definition_node_id: &str,
        node: &StateMachineNodeDefinition,
        scope: &BTreeMap<String, StateMachineNodeDefinition>,
        frames: &[StateMachineLoopFrame],
        role: Option<LoopLeafRole<'_>>,
        path: &str,
    ) -> CompileResult<()> {
        let edge_start = self.edges.len();
        let execution_id = self.execution_node_id(frames, definition_node_id);
        let mut compiled_node = node.clone();
        compiled_node.transitions.clear();
        for (outcome, transition) in &node.transitions {
            let targets = transition.targets.iter()
                .map(|target| self.target_execution_id(scope, frames, target)).collect();
            add_transition(&mut compiled_node, &mut self.edges, &execution_id, outcome, targets,
                CompiledArtifactProjection::Artifact, None, transition.display_name.clone());
            compiled_node.transitions.get_mut(outcome).expect("inserted transition").guard = transition.guard.clone();
        }
        self.insert_leaf(execution_id, definition_node_id, compiled_node, frames, role, edge_start, path)
    }

    fn expand_loop_result(
        &mut self,
        definition_node_id: &str,
        node: &StateMachineNodeDefinition,
        loop_node: &StateMachineNodeDefinition,
        parent_scope: &BTreeMap<String, StateMachineNodeDefinition>,
        parent_frames: &[StateMachineLoopFrame],
        frames: &[StateMachineLoopFrame],
        role: LoopLeafRole<'_>,
        path: &str,
    ) -> CompileResult<()> {
        let edge_start = self.edges.len();
        let execution_id = self.execution_node_id(frames, definition_node_id);
        let mut compiled_node = node.clone();
        compiled_node.transitions.clear();
        for outcome in &role.body.continue_outcomes {
            let (targets, kind, logical, projection, display_name) =
                if role.iteration < role.body.max_iterations {
                    let mut next_frames = frames.to_vec();
                    next_frames.last_mut().expect("Loop frame").iteration += 1;
                    (
                        vec![self.target_execution_id(&role.body.nodes, &next_frames, &role.body.entry_node)],
                        StateMachineLoopRouteKind::Continue,
                        outcome.clone(),
                        CompiledArtifactProjection::ControlOnly,
                        role.body.continue_display_name.clone(),
                    )
                } else {
                    let transition = &loop_node.transitions[&role.body.exhausted_outcome];
                    (
                        transition.targets.iter().map(|target|
                            self.target_execution_id(parent_scope, parent_frames, target)).collect(),
                        StateMachineLoopRouteKind::Exhausted,
                        role.body.exhausted_outcome.clone(),
                        CompiledArtifactProjection::Artifact,
                        transition.display_name.clone(),
                    )
                };
            add_transition(&mut compiled_node, &mut self.edges, &execution_id, outcome, targets,
                projection, Some(StateMachineLoopRoute { kind, logical_outcome: logical }), display_name);
        }
        for outcome in &role.body.break_outcomes {
            let transition = &loop_node.transitions[outcome];
            let targets = transition.targets.iter().map(|target|
                self.target_execution_id(parent_scope, parent_frames, target)).collect();
            add_transition(&mut compiled_node, &mut self.edges, &execution_id, outcome, targets,
                CompiledArtifactProjection::Artifact,
                Some(StateMachineLoopRoute {
                    kind: StateMachineLoopRouteKind::Break, logical_outcome: outcome.clone(),
                }), transition.display_name.clone());
        }
        self.insert_leaf(execution_id, definition_node_id, compiled_node, frames, Some(role), edge_start, path)
    }

    fn insert_leaf(
        &mut self,
        execution_id: String,
        definition_node_id: &str,
        compiled_node: StateMachineNodeDefinition,
        frames: &[StateMachineLoopFrame],
        role: Option<LoopLeafRole<'_>>,
        edge_start: usize,
        path: &str,
    ) -> CompileResult<()> {
        let (loop_id, iteration, max_iterations, is_loop_entry, is_loop_result, previous_result_node_id) =
            match role {
                Some(role) => {
                    let is_entry = definition_node_id == role.body.entry_node;
                    let is_result = definition_node_id == role.body.result_node;
                    let previous = if is_entry && role.iteration > 1 {
                        let mut previous_frames = frames.to_vec();
                        previous_frames.last_mut().expect("Loop frame").iteration -= 1;
                        Some(self.execution_node_id(&previous_frames, &role.body.result_node))
                    } else { None };
                    (Some(role.loop_id.into()), Some(role.iteration), Some(role.body.max_iterations),
                        is_entry, is_result, previous)
                }
                None => (None, None, None, false, false, None),
            };
        let metadata = CompiledNodeMetadata {
            execution_node_id: execution_id.clone(),
            definition_node_id: definition_node_id.into(),
            loop_id,
            iteration,
            max_iterations,
            loop_path: (self.compiler_version == NESTED_FIXED_LOOP_COMPILER_VERSION)
                .then(|| frames.to_vec()).filter(|path| !path.is_empty()),
            is_loop_entry,
            is_loop_result,
            previous_result_node_id,
        };
        if self.nodes.contains_key(&execution_id) || self.metadata.contains_key(&execution_id) {
            return Err(invalid(path, "generated execution ID collision"));
        }
        check_expansion_budget(&mut self.budget, &compiled_node, &metadata, &self.edges[edge_start..])?;
        self.metadata.insert(execution_id.clone(), metadata);
        self.nodes.insert(execution_id, compiled_node);
        Ok(())
    }

    fn target_execution_id(
        &self,
        scope: &BTreeMap<String, StateMachineNodeDefinition>,
        frames: &[StateMachineLoopFrame],
        target: &str,
    ) -> String {
        match scope.get(target).and_then(|node| node.loop_definition.as_ref()) {
            Some(body) => {
                let mut target_frames = frames.to_vec();
                target_frames.push(StateMachineLoopFrame {
                    loop_id: target.into(), iteration: 1, max_iterations: body.max_iterations,
                });
                self.execution_node_id(&target_frames, &body.entry_node)
            }
            None => self.execution_node_id(frames, target),
        }
    }

    fn execution_node_id(&self, frames: &[StateMachineLoopFrame], body_id: &str) -> String {
        if frames.is_empty() {
            return body_id.into();
        }
        let mut hasher = Sha256::new();
        if self.compiler_version == FIXED_LOOP_COMPILER_VERSION {
            let frame = frames.last().expect("Loop frame");
            hasher.update(b"bcs.fixed-loop.compiler/v1/execution-node\0");
            let tuple = (&self.definition.id, self.definition.version,
                &frame.loop_id, frame.iteration, body_id);
            serde_json::to_writer(&mut hasher, &tuple).expect("string/integer tuple serializes");
        } else {
            hasher.update(b"bcs.fixed-loop.compiler/v2/execution-node\0");
            let path: Vec<_> = frames.iter().map(|frame| (&frame.loop_id, frame.iteration)).collect();
            let tuple = (&self.definition.id, self.definition.version, path, body_id);
            serde_json::to_writer(&mut hasher, &tuple).expect("string/integer tuple serializes");
        }
        let hash = format!("{:x}", hasher.finalize());
        format!("ln-{}", &hash[..32])
    }
}

fn add_transition(
    node: &mut StateMachineNodeDefinition,
    edges: &mut Vec<CompiledEdgeMetadata>,
    source: &str,
    outcome: &str,
    targets: Vec<String>,
    projection: CompiledArtifactProjection,
    route: Option<StateMachineLoopRoute>,
    display_name: Option<String>,
) {
    for target in &targets {
        edges.push(CompiledEdgeMetadata {
            source_execution_node_id: source.into(),
            outcome: outcome.into(),
            target_execution_node_id: target.clone(),
            artifact_projection: projection,
            loop_route: route.clone(),
        });
    }
    node.transitions.insert(outcome.into(), StateMachineTransition { targets, guard: None, display_name });
}

fn validate_loop(
    node: &StateMachineNodeDefinition,
    outer: &BTreeMap<String, StateMachineNodeDefinition>,
    limits: &FixedLoopLimits,
    depth: u32,
    node_path: &str,
) -> CompileResult<(usize, u32)> {
    let path = format!("{node_path}.loop");
    if depth > limits.max_fixed_loop_depth {
        return Err(resource_limit(&path, "fixed Loop nesting exceeds max_fixed_loop_depth"));
    }
    let body = node.loop_definition.as_ref().ok_or_else(|| invalid(&path, "loop is required"))?;
    if body.continue_display_name.as_ref().is_some_and(|name| name.trim().is_empty()) {
        return Err(invalid(format!("{path}.continue_display_name"), "must be a nonblank string"));
    }
    if node.display_name.trim().is_empty() {
        return Err(invalid(format!("{node_path}.display_name"), "must not be empty"));
    }
    if node.assignee.is_some() || node.instruction.is_some() || node.notification.is_some()
        || node.judge.is_some() || node.action.is_some() || node.output_contract.is_some()
        || node.node_timeout_ms.is_some() || node.max_attempts.is_some() || node.visibility.is_some() || node.final_output
    {
        return Err(invalid(node_path, "loop must not contain executable node fields"));
    }
    if body.max_iterations == 0 || body.max_iterations > limits.max_fixed_loop_iterations {
        let mut error = invalid(format!("{path}.max_iterations"), "must be positive and within max_fixed_loop_iterations");
        if body.max_iterations > limits.max_fixed_loop_iterations { error.reason = StateMachineLoopCompileRejection::ResourceLimit; }
        return Err(error);
    }
    if body.nodes.is_empty() || body.nodes.len() > limits.max_fixed_loop_body_nodes {
        let mut error = invalid(format!("{path}.nodes"), "must be nonempty and within max_fixed_loop_body_nodes");
        if body.nodes.len() > limits.max_fixed_loop_body_nodes { error.reason = StateMachineLoopCompileRejection::ResourceLimit; }
        return Err(error);
    }
    for (field, value) in [("entry_node", &body.entry_node), ("result_node", &body.result_node)] {
        if value.trim().is_empty() || !body.nodes.contains_key(value) {
            return Err(invalid(format!("{path}.{field}"), "must reference a body node"));
        }
        if body.nodes[value].kind == StateMachineNodeKind::Loop {
            return Err(invalid(format!("{path}.{field}"), "must reference a direct executable node, not a child Loop"));
        }
    }
    let continues = outcomes(&body.continue_outcomes, &format!("{path}.continue_outcomes"), false)?;
    let breaks = outcomes(&body.break_outcomes, &format!("{path}.break_outcomes"), true)?;
    if !continues.is_disjoint(&breaks) {
        return Err(invalid(format!("{path}.break_outcomes"), "continue and break outcomes must be disjoint"));
    }
    if body.exhausted_outcome.trim().is_empty() || continues.contains(&body.exhausted_outcome)
        || breaks.contains(&body.exhausted_outcome)
    {
        return Err(invalid(format!("{path}.exhausted_outcome"), "must be nonempty and distinct from result outcomes"));
    }
    let result = &body.nodes[&body.result_node];
    let partition: BTreeSet<_> = continues.union(&breaks).cloned().collect();
    match &result.judge {
        Some(judge) => {
            let declared = outcomes(&judge.outcomes, &format!("{path}.nodes.{}.judge.outcomes", body.result_node), false)?;
            if partition != declared {
                return Err(invalid(&path, "continue/break must partition all result Judge outcomes"));
            }
        }
        None if body.continue_outcomes != ["complete"] || !body.break_outcomes.is_empty() => {
            return Err(invalid(&path, "result without Judge requires continue_outcomes: [complete] and break_outcomes: []"));
        }
        None => {}
    }
    let expected: BTreeSet<_> = breaks.into_iter().chain([body.exhausted_outcome.clone()]).collect();
    for outcome in &expected {
        let transition_path = format!("{node_path}.transitions.{outcome}");
        let transition = node.transitions.get(outcome).ok_or_else(|| invalid(format!("{transition_path}.targets"), "must be a nonempty list"))?;
        if transition.targets.is_empty() {
            return Err(invalid(format!("{transition_path}.targets"), "must be a nonempty list"));
        }
        if transition.guard.is_some() {
            return Err(invalid(format!("{transition_path}.guard"), "guarded transitions are not supported"));
        }
        for target in &transition.targets {
            if !outer.contains_key(target) {
                return Err(invalid(format!("{transition_path}.targets"), format!("outer target not found: {target}")));
            }
        }
    }
    if node.transitions.keys().cloned().collect::<BTreeSet<_>>() != expected {
        return Err(invalid(format!("{node_path}.transitions"), "keys must equal break outcomes plus exhausted outcome"));
    }
    let (body_cost, max_depth) = validate_body(body, &path, limits, depth)?;
    let cost = body_cost.checked_mul(body.max_iterations as usize)
        .ok_or_else(|| resource_limit(&path, "compiled node count overflow"))?;
    Ok((cost, max_depth))
}

fn validate_body(
    body: &FixedLoopDefinition,
    path: &str,
    limits: &FixedLoopLimits,
    depth: u32,
) -> CompileResult<(usize, u32)> {
    let mut upstreams: BTreeMap<String, Vec<String>> = body.nodes.keys().map(|id| (id.clone(), Vec::new())).collect();
    let mut cost = 0usize;
    let mut max_depth = depth;
    for (id, node) in &body.nodes {
        let node_path = format!("{path}.nodes.{id}");
        validate_id(id, &node_path)?;
        let node_cost = match node.kind {
            StateMachineNodeKind::Loop => {
                let next_depth = depth.checked_add(1)
                    .ok_or_else(|| resource_limit(&node_path, "fixed Loop nesting depth overflow"))?;
                let (node_cost, node_depth) = validate_loop(
                    node, &body.nodes, limits, next_depth, &node_path,
                )?;
                max_depth = max_depth.max(node_depth);
                node_cost
            }
            StateMachineNodeKind::BotTask | StateMachineNodeKind::HumanInput => {
                if node.loop_definition.is_some() {
                    return Err(invalid(format!("{node_path}.loop"), "only kind: loop may define loop"));
                }
                1
            }
            _ => return Err(invalid(&node_path, "Loop body only supports bot_task, human_input, or loop nodes")),
        };
        cost = cost.checked_add(node_cost)
            .ok_or_else(|| resource_limit(&node_path, "compiled node count overflow"))?;
        if node.final_output {
            return Err(invalid(format!("{node_path}.final_output"), "body nodes must not be final_output"));
        }
        if id == &body.result_node {
            if !node.transitions.is_empty() {
                return Err(invalid(format!("{node_path}.transitions"), "result node must not define body transitions"));
            }
        } else if node.transitions.is_empty() {
            return Err(invalid(format!("{node_path}.transitions"), "only result_node may be terminal"));
        }
        for (outcome, transition) in &node.transitions {
            if transition.targets.is_empty() {
                return Err(invalid(format!("{node_path}.transitions.{outcome}.targets"), "must be nonempty"));
            }
            for target in &transition.targets {
                upstreams.get_mut(target).ok_or_else(|| invalid(format!("{node_path}.transitions.{outcome}.targets"), "target must belong to the same body"))?.push(id.clone());
            }
        }
    }
    ensure_acyclic(&body.nodes, &upstreams).map_err(|error| invalid(format!("{path}.nodes"), error.to_string()))?;
    let entries: Vec<_> = upstreams.iter().filter(|(_, parents)| parents.is_empty()).map(|(id, _)| id).collect();
    if entries != [&body.entry_node] {
        return Err(invalid(format!("{path}.entry_node"), "must be the only zero in-degree body node"));
    }
    // In a finite DAG, one root and one terminal imply every node lies on a root-to-terminal path.
    Ok((cost, max_depth))
}

fn validate_id(id: &str, path: &str) -> CompileResult<()> {
    let mut chars = id.chars();
    if !matches!(chars.next(), Some(ch) if ch.is_ascii_alphabetic())
        || !chars.all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-') || id.starts_with("ln-") || id.len() > 128
    {
        return Err(invalid(path, "node ID must be valid, at most 128 bytes, and outside the reserved ln- prefix"));
    }
    Ok(())
}

fn outcomes(
    values: &[String],
    path: &str,
    allow_empty: bool,
) -> CompileResult<BTreeSet<String>> {
    let unique: BTreeSet<_> = values.iter().cloned().collect();
    if (!allow_empty && values.is_empty()) || unique.len() != values.len() || values.iter().any(|value| value.trim().is_empty()) {
        return Err(invalid(path, "outcomes must be nonblank, unique and satisfy the required list cardinality"));
    }
    Ok(unique)
}

fn invalid(
    path: impl Into<String>,
    message: impl Into<String>,
) -> CompileFailure {
    CompileFailure { reason: StateMachineLoopCompileRejection::InvalidDefinition, diagnostic: CollaborationDefinitionValidationDiagnostic {
        code: "INVALID_DEFINITION".into(),
        path: path.into(),
        message: message.into(),
        hint: None,
    } }
}

fn resource_limit(path: impl Into<String>, message: impl Into<String>) -> CompileFailure {
    CompileFailure { reason: StateMachineLoopCompileRejection::ResourceLimit, ..invalid(path, message) }
}

struct LimitedWriter {
    written: usize,
    limit: usize,
}

fn check_expansion_budget(
    budget: &mut LimitedWriter,
    node: &StateMachineNodeDefinition,
    metadata: &CompiledNodeMetadata,
    edges: &[CompiledEdgeMetadata],
) -> CompileResult<()> {
    // Count disjoint values in the final plan during expansion. Container/key
    // overhead is checked by the final serialization, without double counting.
    serde_json::to_writer(&mut *budget, node)
        .and_then(|_| serde_json::to_writer(&mut *budget, metadata))
        .map_err(|_| resource_limit(MACHINE_PATH, "execution plan exceeds max_compiled_state_machine_bytes"))?;
    for edge in edges {
        serde_json::to_writer(&mut *budget, edge)
            .map_err(|_| resource_limit(MACHINE_PATH, "execution plan exceeds max_compiled_state_machine_bytes"))?;
    }
    Ok(())
}

impl std::io::Write for LimitedWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.limit.saturating_sub(self.written) {
            return Err(std::io::Error::other("plan size limit"));
        }
        self.written += bytes.len();
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
