//! Shared deterministic display transform. See the public core contract.
mod encoding;
mod limits;

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

    fn validate_task_limits(
        &self,
        snapshot: &TaskSnapshot,
        frozen: &OutputLimits,
    ) -> Result<(), ProjectionError> {
        limits::validate(frozen)?;
        if snapshot.output_limits != *frozen {
            return Err(ProjectionError::FrozenLimitsChanged);
        }
        if snapshot.output_usage.cumulative_bytes > frozen.max_total_output_bytes {
            return Err(invalid_snapshot("usage_exceeds_frozen_limit"));
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
    if node.0.len() > limits.max_identity_bytes as usize
        || artifact.0.len() > limits.max_identity_bytes as usize
    {
        return Err(quota(OutputBudget::IdentityBytes));
    }
    Ok(())
}

fn validate_snapshot(
    snapshot: &DisplaySnapshot,
    limits: &OutputLimits,
) -> Result<(u64, u64), ProjectionError> {
    if snapshot.format_version != limits.display_format {
        return Err(ProjectionError::UnsupportedFormat {
            format: snapshot.format_version,
        });
    }
    if snapshot.units.len() > limits.max_artifacts as usize {
        return Err(invalid_snapshot("too_many_artifacts"));
    }
    let mut identities = BTreeSet::new();
    let mut content_bytes = 0;
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
        content_bytes += encoded_bytes(&unit.content, u64::from(limits.max_unit_bytes))
            .ok_or_else(|| invalid_snapshot("unit_bytes"))?;
    }
    let total = encoded_bytes(snapshot, u64::from(limits.max_snapshot_bytes))
        .ok_or_else(|| invalid_snapshot("snapshot_bytes"))?;
    Ok((total, content_bytes))
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
    let (mut snapshot_bytes, current_content_bytes) = validate_snapshot(current, limits)?;
    if let Some(usage) = usage {
        if usage.cumulative_bytes > limits.max_total_output_bytes {
            return Err(invalid_snapshot("usage_exceeds_frozen_limit"));
        }
        if usage.cumulative_bytes < current_content_bytes {
            return Err(invalid_snapshot("usage_below_display"));
        }
    }
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
        let bytes = encoded_bytes(value, u64::from(limits.max_unit_bytes))
            .ok_or_else(|| quota(OutputBudget::UnitBytes))?;
        // Thinking remains bounded as an individual event but never enters the
        // durable projection, cumulative accounting or publication cadence.
        if change.class == OutputClass::Thinking {
            continue;
        }
        if usage.is_some() {
            result.usage.cumulative_bytes = result
                .usage
                .cumulative_bytes
                .checked_add(bytes)
                .filter(|n| *n <= limits.max_total_output_bytes)
                .ok_or_else(|| quota(OutputBudget::CumulativeBytes))?;
        }
        apply(
            &mut result.snapshot,
            change,
            value,
            bytes,
            &mut snapshot_bytes,
            limits,
        )?;
        result.display_events += 1;
        result.charged_bytes += bytes;
    }
    Ok(result)
}

fn apply(
    snapshot: &mut DisplaySnapshot,
    change: &ArtifactChange,
    value: &Value,
    payload_bytes: u64,
    snapshot_bytes: &mut u64,
    limits: &OutputLimits,
) -> Result<(), ProjectionError> {
    let index = snapshot.units.iter().position(|unit| {
        unit.node == change.node
            && unit.artifact_id == change.artifact_id
            && unit.class == change.class
    });
    if let Some(index) = index {
        let unit = &mut snapshot.units[index];
        let old_bytes = encoded_bytes(&unit.content, u64::from(limits.max_unit_bytes))
            .ok_or_else(|| invalid_snapshot("unit_bytes"))?;
        let new_bytes = match &change.update {
            ArtifactUpdate::Upsert { .. } => payload_bytes,
            ArtifactUpdate::AppendText { .. } => {
                if !unit.content.is_string() {
                    return Err(invalid_artifact("append_requires_text"));
                }
                // Both values are encoded strings; concatenation removes the
                // two middle quotation marks and preserves all escaping.
                old_bytes + payload_bytes - 2
            }
        };
        if new_bytes > u64::from(limits.max_unit_bytes) {
            return Err(quota(OutputBudget::UnitBytes));
        }
        *snapshot_bytes = *snapshot_bytes - old_bytes + new_bytes;
        if *snapshot_bytes > u64::from(limits.max_snapshot_bytes) {
            return Err(quota(OutputBudget::SnapshotBytes));
        }
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
        let added = encoded_bytes(&unit, u64::from(limits.max_snapshot_bytes))
            .ok_or_else(|| quota(OutputBudget::SnapshotBytes))?;
        *snapshot_bytes += added + u64::from(!snapshot.units.is_empty());
        if *snapshot_bytes > u64::from(limits.max_snapshot_bytes) {
            return Err(quota(OutputBudget::SnapshotBytes));
        }
        snapshot.units.push(unit);
    }
    Ok(())
}
