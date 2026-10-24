//! Caller container preparation orchestration (spec §10.8 application
//! consumer, wire doc "应用准备流程", P2-B second slice).
//!
//! The trusted dispatcher hands over: an ensure request plus the trusted
//! identity deadline inputs. This service validates the command, fires the
//! Gateway ensure **at most once**, re-checks the identity window after the
//! asynchronous return, validates the readback linkage (which the adapter
//! binds to the request, never to a response echo), and produces a
//! `PreparedCallerContainer` with the effective window = min(identity,
//! container). It performs no DB writes, no logging of identity material, no
//! auto-retry, no fallback.

mod routing;

use std::sync::Arc;

use thiserror::Error;

use crate::port::{
    CallerContainerDescriptor, CallerContainerError, CallerContainerGatewayPort,
    CallerContainerIsolation, CallerOpaqueRef, EnsureCallerContainer,
};

/// Prepared container fact plus its shrunk effective window. Debug of the
/// descriptor is redacted by construction (`CallerOpaqueRef`); the effective
/// deadline is non-secret coordination fact.
#[derive(Debug)]
pub enum PrepareOutcome {
    Prepared {
        descriptor: CallerContainerDescriptor,
        effective_deadline_ms: i64,
    },
}

/// Closed failure set for the preparation orchestration. No variant carries
/// URLs, gateway bodies, subjects or bindings.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum CallerPreparationError {
    #[error("invalid container preparation request")]
    InvalidRequest,
    /// The trusted identity window ended before the ensure or ended while it
    /// was in flight: the identity is consumed, fail closed, never re-issued
    /// here.
    #[error("caller identity expired during preparation")]
    IdentityExpired,
    /// The Gateway answered not-ready: no prepared container this run.
    #[error("caller container is not ready")]
    NotReady,
    /// The readback names a different target/subject than the bound request:
    /// corrupted association, never trusted as a new grant.
    #[error("caller container readback linkage mismatch")]
    LinkageMismatch,
    /// The Gateway answer cannot satisfy this request's isolation needs.
    #[error("caller container isolation below requirement")]
    IsolationMismatch,
    /// Gateway failure propagated without a retry. `unknown_outcome` marks
    /// ambiguous transport outcomes — it never proves the container was not
    /// created, so retries must reuse the command's idempotency key.
    #[error("caller container gateway failed")]
    Gateway {
        retryable: bool,
        unknown_outcome: bool,
    },
}

/// One prepare orchestration over the injected Gateway port.
pub struct CallerContainerPreparation {
    gateway: Arc<dyn CallerContainerGatewayPort>,
    provider_core: Option<Arc<dyn crate::core::ProviderCoreService>>,
    // V1 accepts only the container_bound guarantee; the requirement is
    // carried by the command, the guarantee by the readback.
    #[allow(dead_code)]
    accepted_isolation: CallerContainerIsolation,
}

impl CallerContainerPreparation {
    pub fn new(gateway: Arc<dyn CallerContainerGatewayPort>) -> Self {
        Self {
            gateway,
            provider_core: None,
            accepted_isolation: CallerContainerIsolation::ContainerBound,
        }
    }

    /// Production capability source. Read failures refuse dispatch; no HTTP
    /// error (404/timeout included) can enable subject routing implicitly.
    pub fn with_provider_core(mut self, core: Arc<dyn crate::core::ProviderCoreService>) -> Self {
        self.provider_core = Some(core);
        self
    }

    /// Validate → select declared routing → ensure if required → shrink the window.
    /// `post_now_ms` is the explicit clock read after the asynchronous
    /// ensure return; the command carries its own pre-ensure clock. Target
    /// and service credential come from the trusted dispatcher — this
    /// orchestration never discovers or authorizes them itself.
    pub async fn prepare(
        &self,
        target: &bcs_domain::BotDeliveryTarget,
        credential: &bcs_domain::RedactedToken,
        command: EnsureCallerContainer,
        post_now_ms: i64,
    ) -> Result<PrepareOutcome, CallerPreparationError> {
        // 1) Structural validation before any side effect.
        if command.target_bot_id.is_empty()
            || command.session_id.is_empty()
            || command.environment.is_empty()
            || command.idempotency_key.is_empty()
            || command.run.run_id.is_empty()
            || command.run.root_run_id.is_empty()
            || command.run.message_turn_id.is_empty()
            || command.caller_subject_ref.expose().is_empty()
            || command.container_ttl_ms <= 0
            || command.deadline_ms <= 0
            || command.now_ms <= 0
            || post_now_ms <= 0
        {
            return Err(CallerPreparationError::InvalidRequest);
        }
        // 2) Identity still live at the ensure clock.
        if command.now_ms >= command.deadline_ms {
            return Err(CallerPreparationError::IdentityExpired);
        }
        // Capture the trusted linkage facts before handing the command over
        // to the Gateway — the post-readback validation compares against
        // these, never against response echoes.
        let bound_target_bot_id = command.target_bot_id.clone();
        let bound_subject_ref = CallerOpaqueRef::new(command.caller_subject_ref.expose().to_string());
        let bound_deadline_ms = command.deadline_ms;
        // 3) One ensure. Target and service credential come from the trusted
        //    dispatcher; this orchestration never discovers or authorizes.
        let deferred = if let Some(core) = &self.provider_core {
            let bcs_domain::BotDeliveryTarget::HttpProvider { provider_id, bot_id, .. } = target else {
                return Err(CallerPreparationError::InvalidRequest);
            };
            if bot_id != &command.target_bot_id {
                return Err(CallerPreparationError::LinkageMismatch);
            }
            let caps = core.get_caller_capabilities(provider_id).await
                .map_err(|_| CallerPreparationError::Gateway { retryable: true, unknown_outcome: false })?
                .ok_or(CallerPreparationError::IsolationMismatch)?;
            routing::subject_routed_descriptor(&caps, &command)?
        } else {
            // Legacy/test compositions still require a real ensure call.
            None
        };
        let descriptor = if let Some(descriptor) = deferred {
            descriptor
        } else {
            match self.gateway.ensure_container(target, credential, command).await {
                Ok(descriptor) => descriptor,
                Err(CallerContainerError::InvalidRequest) => {
                    return Err(CallerPreparationError::InvalidRequest)
                }
                Err(CallerContainerError::GatewayRejected { retryable }) => {
                    return Err(CallerPreparationError::Gateway { retryable, unknown_outcome: false });
                }
                Err(CallerContainerError::ProviderUnavailable { retryable }) => {
                    return Err(CallerPreparationError::Gateway { retryable, unknown_outcome: true });
                }
            }
        };
        // 4) Post-return identity window check (the §10.13 rule: re-read the
        //    clock after every await before consuming identity material).
        if post_now_ms >= bound_deadline_ms {
            return Err(CallerPreparationError::IdentityExpired);
        }
        // 5) Linkage: the adapter binds the readback to the request's own
        //    target/subject; a mismatch names corruption, never a new grant.
        if descriptor.target_bot_id != bound_target_bot_id
            || descriptor.caller_subject_ref.expose() != bound_subject_ref.expose()
        {
            return Err(CallerPreparationError::LinkageMismatch);
        }
        // 6) This subset accepts only ready, container_bound answers.
        if !descriptor.ready {
            return Err(CallerPreparationError::NotReady);
        }
        if !matches!(descriptor.isolation, CallerContainerIsolation::ContainerBound) {
            return Err(CallerPreparationError::IsolationMismatch);
        }
        // 7) Effective window = min(identity, container); container TTL never
        //    extends the identity deadline, and an already-past window is a
        //    hard loss rather than a future promise.
        let effective_deadline_ms = bound_deadline_ms.min(descriptor.expires_at_ms);
        if post_now_ms >= effective_deadline_ms {
            return Err(CallerPreparationError::IdentityExpired);
        }
        Ok(PrepareOutcome::Prepared {
            descriptor,
            effective_deadline_ms,
        })
    }
}