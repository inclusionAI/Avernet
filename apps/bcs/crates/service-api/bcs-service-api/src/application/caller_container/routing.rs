//! Provider capability selection for caller container preparation.
//! A subject-routed descriptor certifies routing readiness, NOT an existing
//! physical container or history readiness. Provider must lazily resolve/create
//! the isolated target and satisfy the history barrier before accepting work.
use super::*;
use bcs_domain::caller_identity::{CallerContainerRouting, ProviderCallerCapabilities};
use crate::port::CallerObserveTarget;

pub(super) fn subject_routed_descriptor(
    caps: &ProviderCallerCapabilities,
    command: &EnsureCallerContainer,
) -> Result<Option<CallerContainerDescriptor>, CallerPreparationError> {
    if !caps.supports_caller {
        return Err(CallerPreparationError::IsolationMismatch);
    }
    if caps.container_routing == CallerContainerRouting::Ensure {
        return Ok(None);
    }
    // No binding was read back: never fabricate a binding/generation or token.
    // The explicit mode promises Human×Bot container + session isolation.
    let expires_at_ms = command.deadline_ms.min(command.now_ms.saturating_add(command.container_ttl_ms));
    Ok(Some(CallerContainerDescriptor {
        sandbox_container_id: None,
        target_bot_id: command.target_bot_id.clone(),
        caller_subject_ref: command.caller_subject_ref.clone(),
        ready: true,
        isolation: CallerContainerIsolation::ContainerBound,
        binding_ref: None,
        runtime_generation: None,
        expires_at_ms,
        observe_target: caps.supports_observe_inject.then(|| CallerObserveTarget {
            caller_subject: command.caller_subject_ref.clone(),
            caller_ref: None,
            target_epoch: None,
            target_bot_id: command.target_bot_id.clone(),
            session_id: command.session_id.clone(),
            expires_at_ms,
        }),
    }))
}
