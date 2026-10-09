//! Outbound continuous-authorization application implementation
//! (plan Task 15, spec §14).
//!
//! `DeliveryAuthorizationServiceImpl` answers the three-state delivery
//! question for one bounded batch of complete trusted contexts. Query
//! budget (spec §17.2, asserted by the Task 15 conformance suite's real
//! counters): per bound batch, ONE strict authority batch read
//! (`BotAuthorityCoreService::roles_for`, the Task 3 no-N+1 lane, pairs
//! deduplicated) plus ONE snapshot read per DISTINCT
//! (tenant, env, kind, resource) combination — never one per context.
//! Everything else is in-memory composition; authority and membership are
//! re-read on EVERY call — nothing survives the call (no TTL, no token).
//!
//! Layering: this application service reads through
//! [`bcs_service_api::core::BotAuthorityCoreService`] (never the authority
//! repo port directly), the [`GroupCoreService`] core contract and the
//! [`bcs_service_api::port::repo::SessionRepoPort`] snapshot read (the same
//! resource-read dependencies this facade's session lane already uses).
//!
//! Decision order (spec §14.6, binding): binding eligibility FIRST
//! (identity shape already validated fail-closed; then the live role over
//! the view Bot, then the view actor's resource membership) — a genuinely
//! revoked binding is `InvalidateBinding` even when the frame would have
//! been skipped anyway; the visibility filter runs only for still-valid
//! bindings, reusing the CANONICAL history visibility matrix
//! (`HumanMessageView::allows_artifact`) so live delivery and history
//! reads can never drift apart.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::HumanMessageView;
use bcs_service_api::application::v1::{
    ApplicationError, DeliveryAuthorizationContext, DeliveryAuthorizationDecision,
    DeliveryAuthorizationService, DeliveryResourceKind, DELIVERY_AUTHORIZATION_MAX_BATCH,
    ERROR_DELIVERY_AUTHORIZATION_BATCH_TOO_LARGE, ERROR_INVALID_DELIVERY_CONTEXT,
};
use bcs_service_api::core::{BotAuthorityCoreService, GroupCoreService};
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_service_api::port::repo::bot_authority::human_actor_id;
use bcs_service_api::types::{Participant, ServiceError};
use bcs_service_api::{Group, Session};

use crate::authorization::map_authority_hook_error;

/// Production implementation of [`DeliveryAuthorizationService`] over the
/// injected strict authority Core plus the Group/Session snapshot reads.
pub struct DeliveryAuthorizationServiceImpl {
    authority: Arc<dyn BotAuthorityCoreService>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionRepoPort>,
}

impl DeliveryAuthorizationServiceImpl {
    pub fn new(
        authority: Arc<dyn BotAuthorityCoreService>,
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionRepoPort>,
    ) -> Self {
        Self {
            authority,
            groups,
            sessions,
        }
    }

    /// Fail-closed structural validation of one trusted context: shape
    /// defects are `Err(InvalidInput)` BEFORE any store read; they are
    /// never treated as an authorization answer.
    fn validate_context(
        context: &DeliveryAuthorizationContext,
    ) -> Result<(), ApplicationError> {
        let invalid = |detail: String| {
            ApplicationError::invalid(ERROR_INVALID_DELIVERY_CONTEXT, detail)
        };
        if context.env.trim().is_empty() {
            return Err(invalid("the trusted env boundary must not be blank".into()));
        }
        if context.user_id.trim().is_empty() {
            return Err(invalid("the trusted user_id must not be blank".into()));
        }
        if context.resource_id.trim().is_empty() {
            return Err(invalid("the resource_id must not be blank".into()));
        }
        if context.view_actor_id.trim().is_empty() {
            return Err(invalid("the view_actor_id must not be blank".into()));
        }
        // A Human-shaped view actor may only ever be the caller's OWN self
        // view (D11 id-by-prefix): viewing as another Human is not an
        // authorizable construction at all — a structural defect, Err.
        if context.view_actor_id.starts_with("human_")
            && context.view_actor_id != human_actor_id(&context.user_id)
        {
            return Err(invalid(
                "a human view actor must be the authenticated user's own self view".into(),
            ));
        }
        if let Some(audience) = &context.audience {
            if let Err(detail) = audience.validate() {
                return Err(invalid(format!("invalid audience: {detail}")));
            }
        }
        Ok(())
    }

    async fn load_resource(
        &self,
        kind: DeliveryResourceKind,
        resource_id: &str,
    ) -> Result<Option<Vec<Participant>>, ApplicationError> {
        // Snapshot read only: the service never mutates the resource and
        // fetches participants only — no message/wave reads. `try_get`
        // separates storage failures (`Err`, fail-closed for the batch)
        // from a missing resource (`None` → the binding's membership is
        // gone → InvalidateBinding for the contexts of that exact resource).
        match kind {
            DeliveryResourceKind::Group => {
                let group: Option<Group> = self
                    .groups
                    .try_get(resource_id)
                    .await
                    .map_err(map_resource_read_error)?;
                Ok(group.map(|group| group.participants))
            }
            DeliveryResourceKind::Session => {
                let session: Option<Session> = self
                    .sessions
                    .try_get(resource_id)
                    .await
                    .map_err(map_resource_read_error)?;
                Ok(session.map(|session| session.participants))
            }
        }
    }
}

/// A resource/authority evidence read failure is `Err` for the whole batch —
/// never a per-context Skip nor an empty-success (spec §17.2). Typed
/// authority branches keep their fixed codes; storage/decode failures stay
/// internal with no SQL leakage.
fn map_resource_read_error(error: ServiceError) -> ApplicationError {
    match error {
        authority @ ServiceError::Authority(_) => map_authority_hook_error(authority),
        other => ApplicationError::internal(other.to_string()),
    }
}

#[async_trait]
impl DeliveryAuthorizationService for DeliveryAuthorizationServiceImpl {
    async fn authorize_batch(
        &self,
        contexts: Vec<DeliveryAuthorizationContext>,
    ) -> Result<Vec<DeliveryAuthorizationDecision>, ApplicationError> {
        // 1. Validate trusted contexts and size — BEFORE any store read:
        //    an oversized batch fails closed without touching the stores
        //    (the ADAPTER splits bigger events, Task 16), and the remaining
        //    targets are never treated as authorized.
        if contexts.len() > DELIVERY_AUTHORIZATION_MAX_BATCH {
            return Err(ApplicationError::invalid(
                ERROR_DELIVERY_AUTHORIZATION_BATCH_TOO_LARGE,
                format!(
                    "a bounded delivery batch authorizes at most {} contexts \
                     (got {}); the delivery adapter must split larger events",
                    DELIVERY_AUTHORIZATION_MAX_BATCH,
                    contexts.len(),
                ),
            ));
        }
        for context in &contexts {
            Self::validate_context(context)?;
        }

        // 2. Fetch the EXACT authority pairs once per bounded batch: every
        //    distinct (tenant, env, user, view Bot) key maps to one slot in
        //    ONE `roles_for` call. Evidence is shared only between
        //    identical identity boundaries — never merged by user.
        //    (Keys are bounded by the batch size, so a linear probe list is
        //    both simple and strictly bounded.)
        let mut role_keys: Vec<(Option<String>, String, String, String)> = Vec::new();
        let mut role_pairs: Vec<(String, String)> = Vec::new();
        for context in &contexts {
            if context.view_actor_id.starts_with("human_") {
                // Self view: eligibility is the Human's own membership; no
                // Bot authority pair exists (and the context contract
                // already pins `human_<user_id>` to the trusted caller).
                continue;
            }
            let key = (
                context.tenant.clone(),
                context.env.clone(),
                context.user_id.clone(),
                context.view_actor_id.clone(),
            );
            if role_keys.iter().any(|existing| existing == &key) {
                continue;
            }
            role_keys.push(key);
            role_pairs.push((context.user_id.clone(), context.view_actor_id.clone()));
        }
        let roles = if role_pairs.is_empty() {
            Vec::new()
        } else {
            self.authority
                .roles_for(&role_pairs)
                .await
                .map_err(map_authority_hook_error)?
        };

        // 3. Load each DISTINCT resource snapshot once (first occurrence
        //    order), keyed by the complete identity boundary: contexts of
        //    different tenant/env/kind/resource never share evidence. A
        //    snapshot read failure fails the whole batch fail-closed.
        let mut resource_keys: Vec<(Option<String>, String, DeliveryResourceKind, String)> =
            Vec::new();
        let mut resource_members: Vec<Option<Vec<Participant>>> = Vec::new();
        for context in &contexts {
            let key = (
                context.tenant.clone(),
                context.env.clone(),
                context.resource_kind,
                context.resource_id.clone(),
            );
            if resource_keys.iter().any(|existing| existing == &key) {
                continue;
            }
            let members = self
                .load_resource(context.resource_kind, &context.resource_id)
                .await?;
            resource_keys.push(key);
            resource_members.push(members);
        }

        // 4. Decide per context, position-aligned. Binding eligibility
        //    FIRST, so an out-of-scope frame can never mask a revocation.
        let mut decisions = Vec::with_capacity(contexts.len());
        for context in contexts {
            let resource_key = (
                context.tenant.clone(),
                context.env.clone(),
                context.resource_kind,
                context.resource_id.clone(),
            );
            let members = resource_keys
                .iter()
                .position(|existing| existing == &resource_key)
                .and_then(|slot| resource_members[slot].as_ref());

            match binding_check(&context, members, &role_keys, &roles) {
                Ok(participant) => {
                    // Valid binding: judge THIS frame through the same
                    // visibility matrix every history read uses — independence
                    // per context, since the scope fact comes from the
                    // participant row and the audience from the input.
                    let visible = HumanMessageView {
                        actor_id: context.view_actor_id.clone(),
                        scope: participant.message_view_scope,
                        // The frame being delivered NOW always carries a
                        // concrete visibility domain in its context, so the
                        // legacy-unclassified leniency flag does not apply.
                        allow_legacy_unclassified_chat: false,
                    }
                    .allows_artifact(context.visibility_domain, context.audience.as_ref());
                    decisions.push(if visible {
                        DeliveryAuthorizationDecision::Deliver
                    } else {
                        DeliveryAuthorizationDecision::SkipMessage
                    });
                }
                Err(invalid_binding) => {
                    // The only `Err` this step produces — an ineligible
                    // binding is a decision, not a failure (failures have
                    // already failed the whole batch above).
                    decisions.push(invalid_binding);
                }
            }
        }

        // 5. Position-aligned results for this call only — nothing is
        //    cached beyond returning them.
        Ok(decisions)
    }
}

/// The binding-eligibility leg of one context. `Ok(participant)` = a still
/// valid binding with the view actor's participant row (its
/// `message_view_scope` feeds the scope filter); `Err(InvalidateBinding)`
/// = the binding's facts are gone; other Errs are impossible here by
/// construction (failures already failed the batch above).
fn binding_check<'a>(
    context: &'a DeliveryAuthorizationContext,
    members: Option<&'a Vec<Participant>>,
    role_keys: &[(Option<String>, String, String, String)],
    roles: &[Option<bcs_domain::BotAccessRelation>],
) -> Result<&'a Participant, DeliveryAuthorizationDecision> {
    let Some(members) = members else {
        // Missing resource: the membership fact is gone with it.
        return Err(DeliveryAuthorizationDecision::InvalidateBinding);
    };
    // The view actor's own membership row.
    let Some(participant) = members
        .iter()
        .find(|participant| participant.bot_uuid == context.view_actor_id)
    else {
        return Err(DeliveryAuthorizationDecision::InvalidateBinding);
    };
    if context.view_actor_id.starts_with("human_") {
        // Self view: the trusted user's own membership row IS the binding.
        return Ok(participant);
    }
    // Bot view: the user must hold a live owner/manager role over the
    // selected view Bot (Task 3 strict batch read; `None` is an ordinary
    // deny → the binding is invalid, not an error).
    let key = (
        context.tenant.clone(),
        context.env.clone(),
        context.user_id.clone(),
        context.view_actor_id.clone(),
    );
    let Some(slot) = role_keys.iter().position(|existing| existing == &key) else {
        // Structurally unreachable: every bot-view context contributed a key.
        return Err(DeliveryAuthorizationDecision::InvalidateBinding);
    };
    match roles.get(slot) {
        // `None` (no live role) is an ordinary deny: the binding is invalid.
        Some(Some(_)) => Ok(participant),
        _ => Err(DeliveryAuthorizationDecision::InvalidateBinding),
    }
}