//! Shared deterministic display transform. See the public core contract.
mod encoding;
mod limits;
mod metadata;

use bcs_dist_model::*;
use bcs_service_api::core::dist_projection::*;
use encoding::encoded_bytes;
use serde_json::Value;
use std::collections::BTreeSet;

#[derive(Debug, Default)]
pub struct OutputProjectionService;

impl OutputProjectionCoreService for OutputProjectionService {
    fn validate_limits(&self, limits: &OutputLimits) -> Result<(), ProjectionError> {
        limits::validate(limits)
    }

    fn validate_session_limits(
        &self,
        spec: &SessionSpec,
        limits: &OutputLimits,
    ) -> Result<(), ProjectionError> {
        metadata::session(spec, limits)
    }

    fn validate_task_limits(
        &self,
        snapshot: &TaskSnapshot,
        frozen: &OutputLimits,
    ) -> Result<(), ProjectionError> {
        limits::validate(frozen)?;
        for value in [
            &snapshot.sid.0,
            &snapshot.tid.0,
            &snapshot.scope.tenant.0,
            &snapshot.scope.principal.0,
        ] {
            metadata::identity(value, frozen)?;
        }
        if snapshot.output_limits != *frozen {
            return Err(ProjectionError::FrozenLimitsChanged);
        }
        Ok(())
    }

    fn project_candidate(
        &self,
        current: &DisplaySnapshot,
        usage: OutputUsage,
        changes: &[ArtifactChange],
        limits: &OutputLimits,
    ) -> Result<OutputProjection, ProjectionError> {
        project(current, Some(usage), changes, limits)
    }

    fn fold_committed(
        &self,
        current: &DisplaySnapshot,
        changes: &[ArtifactChange],
        limits: &OutputLimits,
    ) -> Result<DisplaySnapshot, ProjectionError> {
        project(current, None, changes, limits).map(|projected| projected.snapshot)
    }
}

fn quota(budget: OutputBudget) -> ProjectionError {
    ProjectionError::OutputQuotaExceeded { budget }
}
fn invalid_snapshot(code: &'static str) -> ProjectionError {
    ProjectionError::InvalidSnapshot { code }
}
fn invalid_artifact(code: &'static str) -> ProjectionError {
    ProjectionError::InvalidArtifact { code }
}

fn identity(
    node: &NodeId,
    artifact: &ArtifactId,
    limits: &OutputLimits,
) -> Result<(), ProjectionError> {
    if node.0.is_empty() || artifact.0.is_empty() {
        return Err(invalid_artifact("empty_artifact_identity"));
    }
    if encoded_bytes(&node.0, u64::from(limits.max_identity_bytes)).is_none()
        || encoded_bytes(&artifact.0, u64::from(limits.max_identity_bytes)).is_none()
    {
        return Err(quota(OutputBudget::IdentityBytes));
    }
    Ok(())
}

fn validate_snapshot(
    snapshot: &DisplaySnapshot,
    limits: &OutputLimits,
) -> Result<(), ProjectionError> {
    if snapshot.format_version != limits.display_format {
        return Err(ProjectionError::UnsupportedFormat {
            format: snapshot.format_version,
        });
    }
    if snapshot.units.len() > limits.max_artifacts as usize {
        return Err(invalid_snapshot("too_many_artifacts"));
    }
    let mut identities = BTreeSet::new();
    for unit in &snapshot.units {
        identity(&unit.node, &unit.artifact_id, limits)
            .map_err(|_| invalid_snapshot("artifact_identity"))?;
        let class = match unit.class {
            OutputClass::Output => 0,
            OutputClass::Tool => 1,
            OutputClass::Thinking => return Err(invalid_snapshot("thinking_in_snapshot")),
        };
        if !identities.insert((&unit.node.0, &unit.artifact_id.0, class)) {
            return Err(invalid_snapshot("duplicate_artifact_identity"));
        }
    }
    Ok(())
}

fn project(
    current: &DisplaySnapshot,
    usage: Option<OutputUsage>,
    changes: &[ArtifactChange],
    limits: &OutputLimits,
) -> Result<OutputProjection, ProjectionError> {
    limits::validate(limits)?;
    if changes.len() > limits.max_actions as usize {
        return Err(quota(OutputBudget::Actions));
    }
    validate_snapshot(current, limits)?;
    let mut result = OutputProjection {
        snapshot: current.clone(),
        usage: usage.unwrap_or_default(),
        display_events: 0,
        charged_bytes: 0,
    };
    for change in changes {
        identity(&change.node, &change.artifact_id, limits)?;
        let payload = match &change.update {
            ArtifactUpdate::Upsert { content } => content,
            ArtifactUpdate::AppendText { delta } => delta,
        };
        let Payload::Inline(value) = payload else {
            return Err(ProjectionError::UnresolvedReference);
        };
        if matches!(change.update, ArtifactUpdate::AppendText { .. }) && !value.is_string() {
            return Err(invalid_artifact("append_requires_text"));
        }
        // Thinking never enters the durable display or output accounting.
        if change.class == OutputClass::Thinking {
            continue;
        }
        let bytes = encoded_bytes(value, u64::MAX)
            .ok_or_else(|| invalid_artifact("output_encoding"))?;
        if usage.is_some() {
            result.usage.cumulative_bytes = result.usage.cumulative_bytes.saturating_add(bytes);
        }
        apply(&mut result.snapshot, change, value, limits)?;
        result.display_events += 1;
        result.charged_bytes = result.charged_bytes.saturating_add(bytes);
    }
    Ok(result)
}

fn apply(
    snapshot: &mut DisplaySnapshot,
    change: &ArtifactChange,
    value: &Value,
    limits: &OutputLimits,
) -> Result<(), ProjectionError> {
    let index = snapshot.units.iter().position(|unit| {
        unit.node == change.node
            && unit.artifact_id == change.artifact_id
            && unit.class == change.class
    });
    if let Some(index) = index {
        let unit = &mut snapshot.units[index];
        match (&change.update, &mut unit.content, value) {
            (ArtifactUpdate::AppendText { .. }, Value::String(current), Value::String(delta)) => {
                current.push_str(delta)
            }
            (ArtifactUpdate::Upsert { .. }, current, next) => *current = next.clone(),
            _ => return Err(invalid_artifact("append_requires_text")),
        }
    } else {
        if snapshot.units.len() == limits.max_artifacts as usize {
            return Err(quota(OutputBudget::Artifacts));
        }
        let unit = DisplayUnit {
            node: change.node.clone(),
            artifact_id: change.artifact_id.clone(),
            class: change.class,
            content: value.clone(),
        };
        snapshot.units.push(unit);
    }
    Ok(())
}
