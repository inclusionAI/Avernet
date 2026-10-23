//! Caller admission orchestration (spec §7.5.1, P2-A write side).
//!
//! The single seam a trusted route calls with the strict-authentication
//! ingress: it resolves the target bot's caller readiness, reserves a
//! handoff slot, runs the ordinary Managed admission with the non-secret
//! `caller_required: true` projection marker, and publishes the identity to
//! the handoff **only against a fresh Queued receipt**. Duplicates keep the
//! original association without identity; failures release the reservation.
//! Nothing caller-derived is ever persisted.
//!
//! Fail-closed rules:
//! - target not configured/never-configured/bad data/unreadable → the caller
//!   path refuses before any side effect;
//! - disabled callers don't "fall back" to an ordinary owner dispatch here;
//!   the route decides its own ordinary path without calling this seam;
//! - the admission projection is marked by this orchestration itself — a
//!   request body can never inject or clear the marker.

use std::sync::Arc;

use bcs_service_api::application::caller_execution::{
    HandoffError, HandoffScope, HumanCallerDeliveryHandoffService, QueuedAdmissionReceipt,
};
use bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress;
use bcs_service_api::application::message_delivery::ManagedMessageDeliveryService;
use bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries;
use bcs_service_api::{BotControlPlaneCoreService, ManagedDeliveryError};

/// Facts the trusted route supplies about one caller admission.
#[derive(Debug)]
pub struct CallerMessageAdmission {
    /// The primary caller-ENABLED target's scope. None when the routed
    /// delivery carries no caller-enabled target at all (spec §7.2.1): the
    /// message then rides the ordinary Owner execution for every target,
    /// with only the delegation-only lineage association below.
    pub scope: Option<HandoffScope>,
    /// Additional per-target scopes for group dispatch (spec §6.2: one
    /// canonical message, one identity, per-target dispatch). Every scope
    /// receives the same identity atomically; empty in DM.
    pub extra_scopes: Vec<HandoffScope>,
    /// Delegation-only scopes (spec §7.2.1, review batch-3 §3.1): one per
    /// caller-DISABLED routed target of a lease-admitted caller group.
    /// These targets keep the ordinary Owner execution — no
    /// `caller_required` marker — their projections carry the
    /// `caller_lineage: "delegation_only"` association marker and the
    /// identity parks once per target for the runtime's delegation-only
    /// root registration.
    pub delegation_scopes: Vec<HandoffScope>,
    /// The handoff park fails if the identity's trusted window already ended.
    pub ingress: AuthenticatedHumanCallerIngress,
    /// The ordinary admission command; its target projections receive the
    /// `caller_required` marker injected here, never by the request body.
    pub admission: AdmitMessageDeliveries,
}

/// Closed refusal set for the caller admission path.
#[derive(Debug, thiserror::Error)]
pub enum CallerAdmissionError {
    #[error("caller target is not caller-enabled")]
    TargetNotCallerEnabled,
    #[error("caller identity mismatch")]
    IdentityMismatch,
    #[error("caller identity expired before admission")]
    IdentityExpired,
    #[error("caller identity handoff refused the slot")]
    HandoffRefused,
    #[error("caller delivery admission failed: {0}")]
    Admission(String),
}

pub struct CallerAdmissionDeps {
    pub handoff: Arc<dyn HumanCallerDeliveryHandoffService>,
    pub admission: Arc<dyn ManagedMessageDeliveryService>,
    pub control_plane: Arc<dyn BotControlPlaneCoreService>,
    /// Advisory reservation window; never extends identity validity.
    pub reserve_ttl_ms: i64,
}

/// Result of a successful caller admission: the ordinary admission outcome
/// plus the fact that the identity is parked for exactly one claim by the
/// committed dispatch attempt.
pub struct CallerAdmissionOutcome {
    pub result: bcs_service_api::port::repo::message_delivery::DeliveryAdmissionResult,
}

impl CallerAdmissionError {
    fn from_handoff(_error: HandoffError) -> Self {
        // Closed refusal; the handoff taxonomy already told the story.
        CallerAdmissionError::HandoffRefused
    }
}

pub async fn admit_caller_message(
    deps: &CallerAdmissionDeps,
    caller: CallerMessageAdmission,
    now_ms: i64,
) -> Result<CallerAdmissionOutcome, CallerAdmissionError> {
    let CallerMessageAdmission { scope, extra_scopes, delegation_scopes, ingress, mut admission } = caller;

    // 1) Identity window: re-checked at the seam — the route may have raced.
    if ingress.is_expired(now_ms) {
        return Err(CallerAdmissionError::IdentityExpired);
    }

    // Recheck the canonical message and every target scope before reserving
    // or persisting. Callers of this seam need not pass through web_send.
    if admission.message.sender_id != ingress.actor().actor_id
        || admission.message.sender_type != bcs_domain::SenderType::Human
        || scope.iter().chain(extra_scopes.iter()).chain(delegation_scopes.iter())
            .any(|scope| scope.env != ingress.env())
    {
        return Err(CallerAdmissionError::IdentityMismatch);
    }

    // §7.2.1 shape gate: either a caller-protected target exists, or this is
    // a delegation-only admission carrying at least one Owner association
    // scope. With neither there is nothing the caller seam owns — refuse.
    if scope.is_none() && extra_scopes.is_empty() && delegation_scopes.is_empty() {
        return Err(CallerAdmissionError::Admission(
            "caller admission requires one target scope".into(),
        ));
    }

    // 2) Target readiness (the caller-protected path only): enabled only.
    //    Unconfigured, disabled, bad data or unreadable all refuse the
    //    caller path before side effects. Delegation-only scopes are
    //    deliberately NOT readiness-checked: a caller-disabled target is
    //    exactly the §7.2.1 shape they exist for.
    if let Some(scope) = scope.as_ref() {
        let config = deps
            .control_plane
            .get_caller_configuration(&scope.target_bot_id, &scope.env)
            .await
            .map_err(|_| CallerAdmissionError::TargetNotCallerEnabled)?
            .ok_or(CallerAdmissionError::TargetNotCallerEnabled)?;
        match config.snapshot {
            Some(snapshot) if snapshot.caller_identity.enabled => {}
            _ => return Err(CallerAdmissionError::TargetNotCallerEnabled),
        }
    }

    // 3) Reserve the bounded slots before admission; release on any failure.
    // A reserve hitting a published or claimed slot is a duplicate replay of
    // the same canonical message: it proceeds to admission (which resolves
    // the duplicate) but never touches the parked identity.
    let mut slot_already_bound = false;
    // Reserve primary (if any).
    if let Some(scope) = scope.as_ref() {
        match deps.handoff.reserve(scope, deps.reserve_ttl_ms, now_ms).await {
            Ok(()) => {}
            Err(HandoffError::AlreadyPublished) | Err(HandoffError::AlreadyClaimed) => {
                slot_already_bound = true;
            }
            Err(error) => return Err(CallerAdmissionError::from_handoff(error)),
        }
    }
    // Group dispatch: every extra per-target scope reserves before admission;
    // any already-bound extra marks the whole message as a duplicate replay.
    for extra in &extra_scopes {
        match deps.handoff.reserve(extra, deps.reserve_ttl_ms, now_ms).await {
            Ok(()) => {}
            Err(HandoffError::AlreadyPublished) | Err(HandoffError::AlreadyClaimed) => {
                slot_already_bound = true;
            }
            Err(error) => {
                // Release what this call reserved before bubbling out.
                if let Some(scope) = scope.as_ref() {
                    let _ = deps.handoff.release(scope).await;
                }
                for extra in extra_scopes.iter().take(extra_scopes.len()) {
                    let _ = deps.handoff.release(extra).await;
                }
                return Err(CallerAdmissionError::from_handoff(error));
            }
        }
    }
    // Delegation-only scopes (§7.2.1): the identity parks for the Owner
    // association exactly like a caller target's — the runtime's ordinary
    // Started row claims it to register the delegation-only root lineage.
    for delegation in &delegation_scopes {
        match deps.handoff.reserve(delegation, deps.reserve_ttl_ms, now_ms).await {
            Ok(()) => {}
            Err(HandoffError::AlreadyPublished) | Err(HandoffError::AlreadyClaimed) => {
                slot_already_bound = true;
            }
            Err(error) => {
                if let Some(scope) = scope.as_ref() {
                    let _ = deps.handoff.release(scope).await;
                }
                for extra in extra_scopes.iter().take(extra_scopes.len()) {
                    let _ = deps.handoff.release(extra).await;
                }
                for released in delegation_scopes.iter().take(delegation_scopes.len()) {
                    let _ = deps.handoff.release(released).await;
                }
                return Err(CallerAdmissionError::from_handoff(error));
            }
        }
    }

    // 4) Mark the ordinary admission's projections; the markers and the
    //    protected dispatch facts come from this trusted producer alone —
    //    a request body can never inject or clear them. `caller_task` is
    //    the compliance-projected text the protected dispatch will carry
    //    (the digest's text input, spec §7.5.1): the same text already
    //    inside the canonical message, non-secret and safe to persist.
    let caller_task = if scope.is_some() {
        match admission
            .message
            .content
            .get("text")
            .and_then(serde_json::Value::as_str)
            .filter(|text| !text.is_empty())
        {
            Some(text) => text.to_string(),
            None => {
                // Plain-text protected dispatch material only (v1): without
                // it the wire digest has no text input; reservations release.
                if let Some(scope) = scope.as_ref() {
                    let _ = deps.handoff.release(scope).await;
                }
                for extra in &extra_scopes {
                    let _ = deps.handoff.release(extra).await;
                }
                for delegation in &delegation_scopes {
                    let _ = deps.handoff.release(delegation).await;
                }
                return Err(CallerAdmissionError::Admission(
                    "caller dispatch requires protected text".into(),
                ));
            }
        }
    } else {
        String::new()
    };
    for target in admission.targets.iter_mut() {
        // Mixed-group per-target filter (review P1.III.3): the caller
        // markers and the protected dispatch facts mark ONLY the targets
        // this trusted producer parked a caller identity for (scope +
        // extra scopes). An Owner middle-hop target keeps the ordinary
        // projection and the ordinary execution semantics — a caller mark
        // without a parked identity would send the Owner target into the
        // protected branch and fail it closed instead of running as
        // Owner. The §7.2.1 delegation-only line is SEPARATE: the
        // `caller_lineage` association marker never routes and never
        // grants execution material; the row stays an ordinary open_task.
        let scope_covered = scope
            .as_ref()
            .map(|scope| {
                target.target_bot_id == scope.target_bot_id
                    || extra_scopes
                        .iter()
                        .any(|extra| extra.target_bot_id == target.target_bot_id)
            })
            .unwrap_or(false);
        if scope_covered {
            let mut projection = match target.semantic_projection_json {
                serde_json::Value::Object(ref map) => map.clone(),
                _ => serde_json::Map::new(),
            };
            projection.insert("caller_required".to_string(), serde_json::json!(true));
            projection.insert("caller_task".to_string(), serde_json::json!(caller_task));
            target.semantic_projection_json = serde_json::Value::Object(projection);
            continue;
        }
        let delegation_covered = delegation_scopes
            .iter()
            .any(|delegation| delegation.target_bot_id == target.target_bot_id);
        if delegation_covered {
            let mut projection = match target.semantic_projection_json {
                serde_json::Value::Object(ref map) => map.clone(),
                _ => serde_json::Map::new(),
            };
            projection
                .insert("caller_lineage".to_string(), serde_json::json!("delegation_only"));
            target.semantic_projection_json = serde_json::Value::Object(projection);
        }
    }

    // 5) Ordinary atomic admission (canonical message + queued delivery +
    //    marker all in one transaction).
    let result = match deps.admission.admit(admission).await {
        Ok(result) => result,
        Err(error) => {
            // Release the reservations on failure; identity never gets
            // published.
            if let Some(scope) = scope.as_ref() {
                let _ = deps.handoff.release(scope).await;
            }
            for extra in &extra_scopes {
                let _ = deps.handoff.release(extra).await;
            }
            for delegation in &delegation_scopes {
                let _ = deps.handoff.release(delegation).await;
            }
            return Err(CallerAdmissionError::Admission(managed_error_text(error)));
        }
    };

    // 6) Publish the identity only for a fresh, non-duplicate receipt on a
    //    slot this call actually reserved. Replays never re-park identity.
    if result.duplicate || slot_already_bound {
        // Duplicates keep the original association: no identity, no TTL.
        // A Published/Claimed reservation is not ours to release.
        if let Some(scope) = scope.as_ref() {
            let _ = deps.handoff.release(scope).await;
        }
        for extra in &extra_scopes {
            let _ = deps.handoff.release(extra).await;
        }
        for extra in &delegation_scopes {
            let _ = deps.handoff.release(extra).await;
        }
    } else {
        // One identity, one atomic multi-target publication: every scope's
        // receipt pairs with its OWN delivery row (its target's root run).
        // Caller scopes and §7.2.1 delegation-only scopes publish the SAME
        // identity object through the same atomic multi-target handoff.
        let receipt_for = |delivery: &bcs_domain::message_delivery::PersistedMessageDelivery| {
            QueuedAdmissionReceipt::new(
                delivery.delivery_id.clone(),
                delivery.state.state_version,
                now_ms,
            )
        };
        let mut wanted: Vec<&HandoffScope> = Vec::new();
        if let Some(scope) = scope.as_ref() {
            wanted.push(scope);
        }
        wanted.extend(extra_scopes.iter());
        wanted.extend(delegation_scopes.iter());
        let mut targets = Vec::with_capacity(wanted.len());
        let mut release_all = false;
        for slot in &wanted {
            match result
                .deliveries
                .iter()
                .find(|d| d.target_bot_id == slot.target_bot_id)
            {
                Some(delivery) => targets.push(((*slot).clone(), receipt_for(delivery))),
                None => {
                    release_all = true;
                    break;
                }
            }
        }
        if release_all {
            if let Some(scope) = scope.as_ref() {
                let _ = deps.handoff.release(scope).await;
            }
            for extra in &extra_scopes {
                let _ = deps.handoff.release(extra).await;
            }
            for delegation in &delegation_scopes {
                let _ = deps.handoff.release(delegation).await;
            }
            return Err(CallerAdmissionError::Admission(
                "caller admission target missing from the receipt".into(),
            ));
        }
        use bcs_service_api::application::caller_execution::HumanCallerDeliveryHandoffService as _Handoff;
        let publish_result = _Handoff::publish_for_targets(&*deps.handoff, ingress, &targets).await;
        if let Err(error) = publish_result {
            return Err(CallerAdmissionError::from_handoff(error));
        }
    }
    Ok(CallerAdmissionOutcome { result })
}

fn managed_error_text(error: ManagedDeliveryError) -> String {
    // Sanitized, non-payload error text only.
    error.to_string()
}