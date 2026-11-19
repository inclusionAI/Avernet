//! Bound metadata by serialized JSON bytes, including escaping and quotes.
use bcs_dist_model::*;
use bcs_service_api::core::dist_projection::ProjectionError;
pub(super) fn identity(value: &str, limits: &OutputLimits) -> Result<(), ProjectionError> {
    if value.is_empty()
        || super::encoding::encoded_bytes(&value, u64::from(limits.max_identity_bytes)).is_none()
    {
        return Err(ProjectionError::InvalidLimits {
            code: "metadata_identity_bytes",
        });
    }
    Ok(())
}
pub(super) fn session(spec: &SessionSpec, limits: &OutputLimits) -> Result<(), ProjectionError> {
    super::limits::validate(limits)?;
    for value in [
        &spec.sid.0,
        &spec.scope.tenant.0,
        &spec.scope.principal.0,
        &spec.group.0,
        &spec.workflow.definition_id,
        &spec.workflow.revision,
        &spec.workflow.digest.0,
        &spec.digest.0,
    ] {
        identity(value, limits)?;
    }
    if spec.schema_version != 1
        || spec.participants.len() > PHASE_ONE_OUTPUT_LIMITS.max_dispatches as usize
        || spec.limits.max_queued_tasks == 0
        || spec.limits.max_queued_tasks > PHASE_ONE_OUTPUT_LIMITS.max_queued_tasks
        || spec.limits.max_task_steps == 0
        || spec.limits.task_timeout_millis == 0
        || i64::try_from(spec.limits.task_timeout_millis).is_err()
        || spec.limits.max_inline_bytes == 0
        || spec.limits.max_inline_bytes > PHASE_ONE_OUTPUT_LIMITS.inline_threshold_bytes
    {
        return Err(ProjectionError::InvalidLimits {
            code: "session_capacity_profile",
        });
    }
    for binding in &spec.participants {
        for value in [
            &binding.node.0,
            &binding.target.agent.0,
            &binding.target.binding_version.0,
            &binding.target.credential_ref.0,
            &binding.pool.0,
        ] {
            identity(value, limits)?;
        }
    }
    Ok(())
}
