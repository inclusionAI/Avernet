use bcs_dist_model::*;
use bcs_service_api::core::dist_projection::ProjectionError;

pub(super) fn validate(limits: &OutputLimits) -> Result<(), ProjectionError> {
    let invalid = |code| ProjectionError::InvalidLimits { code };
    if limits.display_format != DISPLAY_FORMAT_VERSION {
        return Err(ProjectionError::UnsupportedFormat {
            format: limits.display_format,
        });
    }
    if super::encoding::encoded_bytes(
        &DisplaySnapshot {
            format_version: DISPLAY_FORMAT_VERSION,
            units: vec![],
        },
        u64::from(limits.max_snapshot_bytes),
    )
    .is_none()
    {
        return Err(invalid("empty_snapshot_bytes"));
    }
    let maxima = PHASE_ONE_OUTPUT_LIMITS;
    for (value, maximum) in [
        (
            u64::from(limits.max_unit_bytes),
            u64::from(maxima.max_unit_bytes),
        ),
        (limits.max_total_output_bytes, maxima.max_total_output_bytes),
        (
            u64::from(limits.max_snapshot_bytes),
            u64::from(maxima.max_snapshot_bytes),
        ),
        (
            u64::from(limits.inline_threshold_bytes),
            u64::from(maxima.inline_threshold_bytes),
        ),
        (
            u64::from(limits.max_checkpoint_bytes),
            u64::from(maxima.max_checkpoint_bytes),
        ),
        (
            u64::from(limits.max_outbox_bytes),
            u64::from(maxima.max_outbox_bytes),
        ),
        (u64::from(limits.max_actions), u64::from(maxima.max_actions)),
        (
            u64::from(limits.max_artifacts),
            u64::from(maxima.max_artifacts),
        ),
        (
            u64::from(limits.max_queued_tasks),
            u64::from(maxima.max_queued_tasks),
        ),
        (
            u64::from(limits.max_dispatches),
            u64::from(maxima.max_dispatches),
        ),
        (
            u64::from(limits.max_identity_bytes),
            u64::from(maxima.max_identity_bytes),
        ),
        (
            u64::from(limits.max_reference_metadata_bytes),
            u64::from(maxima.max_reference_metadata_bytes),
        ),
        (
            u64::from(limits.max_runner_state_bytes),
            u64::from(maxima.max_runner_state_bytes),
        ),
        (
            u64::from(limits.snapshots.max_pending_events),
            u64::from(maxima.snapshots.max_pending_events),
        ),
        (
            limits.snapshots.max_pending_bytes,
            maxima.snapshots.max_pending_bytes,
        ),
        (
            limits.snapshots.max_delay_millis,
            maxima.snapshots.max_delay_millis,
        ),
    ] {
        if value == 0 || value > maximum {
            return Err(invalid("outside_phase_one_profile"));
        }
    }
    if limits.inline_threshold_bytes > limits.max_unit_bytes
        || u64::from(limits.max_unit_bytes) > limits.max_total_output_bytes
        || limits.max_outbox_bytes > limits.max_checkpoint_bytes
    {
        return Err(invalid("inconsistent_byte_budgets"));
    }
    if limits.checkpoint_finalization_reserve < maxima.checkpoint_finalization_reserve
        || limits.outbox_finalization_reserve < maxima.outbox_finalization_reserve
        || limits.outbox_finalization_reserve >= limits.max_outbox_bytes
        || u64::from(limits.checkpoint_finalization_reserve)
            + u64::from(limits.max_outbox_bytes)
            + u64::from(limits.max_runner_state_bytes)
            >= u64::from(limits.max_checkpoint_bytes)
    {
        return Err(invalid("finalization_reserve"));
    }
    Ok(())
}
