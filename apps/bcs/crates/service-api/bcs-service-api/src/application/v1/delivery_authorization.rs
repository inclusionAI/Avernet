//! `DeliveryAuthorizationService` — outbound continuous authorization
//! application contract (plan Task 15, spec §14).
//!
//! BCS pushes protected outbound frames (Workbench chat/history echoes,
//! state-machine runs, Interaction replays, run fallback) to already
//! connected Humans. A connection binds a verified identity boundary
//! (tenant/env) + the REAL User + a resource + the SELECTED view actor.
//! Each bounded dispatch batch asks this service, BEFORE enqueueing and
//! again before the actual send, whether the frame may go out. Revocations
//! take effect on the very next authorization call (spec §14.5: no
//! cross-request positive cache, no token, no TTL).
//!
//! Three-state semantics (spec §14.5/§14.6, binding for every implementation):
//! - [`DeliveryAuthorizationDecision::Deliver`] — the binding is still valid
//!   AND this specific frame is inside the audience/view scope.
//! - [`DeliveryAuthorizationDecision::SkipMessage`] — the binding is STILL
//!   valid, but this frame is one the view may not see (a
//!   [`MessageAudience::FullOnly`] artifact or a Directed frame for other
//!   actors under a `Participant` view scope). Only THIS frame is dropped;
//!   the connection/binding stays and the next public/own-directed frame
//!   still delivers.
//! - [`DeliveryAuthorizationDecision::InvalidateBinding`] — the binding's
//!   facts are gone (identity/role/membership): the human's live authority
//!   over the selected view Bot, or the view actor's resource membership.
//!   The binding must be invalidated and its protected backlog stopped;
//!   OTHER independent legal bindings are untouched.
//!
//! Ordering rule: BINDING ELIGIBILITY IS JUDGED FIRST. A genuinely revoked
//! binding is `InvalidateBinding` even when the current frame would have
//! been `SkipMessage` anyway — filtering must never mask a revocation, and
//! `SkipMessage` is never a disguise for a failed read.
//!
//! Fail-closed rules, binding for every implementation:
//! - `authorize_batch` carries at most [`DELIVERY_AUTHORIZATION_MAX_BATCH`]
//!   contexts (the delivery adapter, Task 16, splits larger events into
//!   bounded calls). A bigger batch is `Err(InvalidInput)` BEFORE any store
//!   read — the remaining targets are never treated as authorized.
//! - Decisions are POSITION-ALIGNED with the input: the same User's
//!   different views, and contexts of different env/resource/scope, resolve
//!   INDEPENDENTLY. Sharing read evidence is allowed only for IDENTICAL
//!   complete contexts — never a per-user merged boolean.
//! - Storage, decode, or authority-read failures are `Err`; they never
//!   downgrade to `SkipMessage` or empty success (spec §17.2 fail closed).
//! - The read evidence of one batch lives ONLY inside that call: no state
//!   survives for later calls (no TTL, no token, spec §14.5).

use async_trait::async_trait;
use bcs_domain::{MessageAudience, MessageVisibilityDomain};

use crate::application::v1::ApplicationError;
use crate::port::repo::bot_authority::human_actor_id;

/// One bounded dispatch batch authorizes at most this many complete
/// contexts (spec §14.5). Larger events are SPLIT by the delivery adapter
/// into multiple calls; the service rejects a bigger batch fail-closed.
pub const DELIVERY_AUTHORIZATION_MAX_BATCH: usize = 128;

pub const ERROR_DELIVERY_AUTHORIZATION_BATCH_TOO_LARGE: &str =
    "delivery_authorization_batch_too_large";
pub const ERROR_INVALID_DELIVERY_CONTEXT: &str = "invalid_delivery_context";

/// Closed enum of protectable delivery resources. Adapters can never name
/// arbitrary resource kinds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryResourceKind {
    /// A Group-scoped protected binding.
    Group,
    /// A Session-scoped protected binding.
    Session,
}

/// Closed enum of the outbound actions that can be authorized. Adapters can
/// never invent arbitrary action strings. Every action is judged with the
/// same semantics — replay/fallback is a NEW dispatch that re-authorizes
/// against current committed facts and never reuses an earlier decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DeliveryAction {
    /// Deliver one protected outbound frame to a bound connection (the
    /// ordinary queue drain and the writer's pre-send recheck).
    #[default]
    DeliverFrame,
    /// Re-dispatch of a previously queued frame (Interaction replay /
    /// run-fallback reinsert).
    ReplayFrame,
}

/// One complete trusted authorization context (spec §14.5 decision key):
/// the verified identity boundary, the REAL User, the resource, the
/// SELECTED view actor, the outbound action and the frame's
/// visibility/audience facts. Adapters build it ONLY through
/// [`DeliveryAuthorizationContextBuilder`] from values they verified —
/// `tenant` may be `None` (the trusted identity contract allows a missing
/// tenant) and NONE of tenant/env is inferred from the others.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeliveryAuthorizationContext {
    /// Trusted tenant boundary of the verified connection identity; may be
    /// `None` per the identity contract. Carried for decision-key
    /// completeness — never inferred, never defaulted.
    pub tenant: Option<String>,
    /// Trusted environment boundary. Never inferred from tenant or user.
    pub env: String,
    /// The verified REAL User id (never a request-body value).
    pub user_id: String,
    /// The protectable resource (closed enum + id).
    pub resource_kind: DeliveryResourceKind,
    pub resource_id: String,
    /// The SELECTED view actor: `human_<user_id>` for the Human's own
    /// participation view, or a Bot actor id viewed through the user's
    /// live owner/manager authority. A revoked view is NEVER auto-switched
    /// to another actor.
    pub view_actor_id: String,
    /// The outbound action (closed enum).
    pub action: DeliveryAction,
    /// The frame's visibility domain.
    pub visibility_domain: MessageVisibilityDomain,
    /// The frame's audience (`None` fails closed for Participant-scope
    /// ManagerWorker/StateMachine frames, exactly like the history matrix).
    pub audience: Option<MessageAudience>,
}

/// The only sanctioned builder for [`DeliveryAuthorizationContext`] inputs.
/// Builds from trusted values the adapter already verified; the defaults are
/// the ordinary outbound frame (`DeliveryAction::DeliverFrame`, a Chat
/// domain, no audience) — every non-default fact is set explicitly.
#[derive(Debug, Clone)]
pub struct DeliveryAuthorizationContextBuilder {
    tenant: Option<String>,
    env: String,
    user_id: String,
    resource_kind: DeliveryResourceKind,
    resource_id: String,
    view_actor_id: String,
    action: DeliveryAction,
    visibility_domain: MessageVisibilityDomain,
    audience: Option<MessageAudience>,
}

impl DeliveryAuthorizationContextBuilder {
    /// The Human's OWN participation view (`human_<user_id>`, D11
    /// id-by-prefix). Eligibility rests on the Human's own resource
    /// membership — no Bot authority involved.
    pub fn self_view(
        env: impl Into<String>,
        user_id: impl Into<String>,
        resource_kind: DeliveryResourceKind,
        resource_id: impl Into<String>,
    ) -> Self {
        let user_id = user_id.into();
        Self::for_view(
            env,
            user_id.clone(),
            resource_kind,
            resource_id,
            human_actor_id(&user_id),
        )
    }

    /// A selected-Bot view. The caller must hold live owner/manager
    /// authority over this EXACT Bot — enforced at authorize time from
    /// committed facts, never by the builder.
    pub fn bot_view(
        env: impl Into<String>,
        user_id: impl Into<String>,
        resource_kind: DeliveryResourceKind,
        resource_id: impl Into<String>,
        view_bot_id: impl Into<String>,
    ) -> Self {
        Self::for_view(
            env,
            user_id,
            resource_kind,
            resource_id,
            view_bot_id.into(),
        )
    }

    fn for_view(
        env: impl Into<String>,
        user_id: impl Into<String>,
        resource_kind: DeliveryResourceKind,
        resource_id: impl Into<String>,
        view_actor_id: impl Into<String>,
    ) -> Self {
        Self {
            tenant: None,
            env: env.into(),
            user_id: user_id.into(),
            resource_kind,
            resource_id: resource_id.into(),
            view_actor_id: view_actor_id.into(),
            action: DeliveryAction::default(),
            visibility_domain: MessageVisibilityDomain::Chat,
            audience: None,
        }
    }

    /// The trusted tenant boundary; `None` stays `None` (/legal per the
    /// identity contract) and nothing is inferred.
    pub fn tenant(mut self, tenant: Option<impl Into<String>>) -> Self {
        self.tenant = tenant.map(Into::into);
        self
    }

    /// The outbound action.
    pub fn action(mut self, action: DeliveryAction) -> Self {
        self.action = action;
        self
    }

    /// The frame's visibility facts.
    pub fn message(
        mut self,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<MessageAudience>,
    ) -> Self {
        self.visibility_domain = visibility_domain;
        self.audience = audience;
        self
    }

    /// Build the context. Structural validation happens at authorize time;
    /// the builder only carries trusted values.
    pub fn build(self) -> DeliveryAuthorizationContext {
        DeliveryAuthorizationContext {
            tenant: self.tenant,
            env: self.env,
            user_id: self.user_id,
            resource_kind: self.resource_kind,
            resource_id: self.resource_id,
            view_actor_id: self.view_actor_id,
            action: self.action,
            visibility_domain: self.visibility_domain,
            audience: self.audience,
        }
    }
}

/// The per-frame decision (spec §14.5/§14.6 table). `SkipMessage` is not
/// "delivery failed" and never falls back to run/session retry for the same
/// target; `InvalidateBinding` stops this binding's protected backlog
/// without touching other independent legal bindings.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryAuthorizationDecision {
    /// Valid binding + this frame inside the audience/view scope.
    Deliver,
    /// Valid binding, but this frame is not visible to the participant
    /// view (a FullOnly artifact or another actor's Directed frame): drop
    /// only this frame, keep the connection/binding.
    SkipMessage,
    /// The binding's identity/authority/membership is gone: invalidate
    /// this binding and stop its protected backlog.
    InvalidateBinding,
}

/// Outbound continuous-authorization application contract. Called by the
/// delivery adapter for every bounded dispatch batch (the pre-enqueue
/// check) and again by the writer before the actual send (the pre-send
/// recheck) — each call decides from the CURRENT committed facts with no
/// memory of earlier calls.
#[async_trait]
pub trait DeliveryAuthorizationService: Send + Sync {
    /// Authorize one bounded batch of complete trusted contexts.
    ///
    /// - Results are position-aligned, one decision per input context.
    /// - At most [`DELIVERY_AUTHORIZATION_MAX_BATCH`] inputs; a larger batch
    ///   is `Err(InvalidInput)` (the ADAPTER splits, Task 16).
    /// - The batch reads its bounded evidence ONCE (the scoped authority
    ///   pairs through one Task 3 `roles_for` batch read and each distinct
    ///   resource snapshot once); queries are never issued per context.
    /// - Any storage/decode/authority read failure fails the whole batch
    ///   fail-closed (`Err`), never a per-context Skip.
    async fn authorize_batch(
        &self,
        contexts: Vec<DeliveryAuthorizationContext>,
    ) -> Result<Vec<DeliveryAuthorizationDecision>, ApplicationError>;
}

/// Fail-closed Noop of the continuous-authorization contract: every
/// context is reported as an invalid binding. An assembly that forgot to
/// wire the real implementation can therefore NEVER leak a protected frame;
/// the delivery adapter treats [`DeliveryAuthorizationDecision::InvalidateBinding`]
/// as "stop this binding's protected backlog".
pub struct NoopDeliveryAuthorizationService;

#[async_trait]
impl DeliveryAuthorizationService for NoopDeliveryAuthorizationService {
    async fn authorize_batch(
        &self,
        contexts: Vec<DeliveryAuthorizationContext>,
    ) -> Result<Vec<DeliveryAuthorizationDecision>, ApplicationError> {
        Ok(contexts
            .into_iter()
            .map(|_| DeliveryAuthorizationDecision::InvalidateBinding)
            .collect())
    }
}