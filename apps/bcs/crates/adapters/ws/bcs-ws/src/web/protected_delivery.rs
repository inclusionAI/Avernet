//! Workbench WS protected-delivery queue contract (plan Task 16, spec
//! §14.5/§17.2): the adapter's internal typed queue and the two continuous-
//! authorization positions around it.
//!
//! The queue between the broadcast/run-channel layers and the per-connection
//! socket writer carries [`WorkbenchOutbound`] items — an ADAPTER-INTERNAL
//! type; the client frame schema never changes. A protected item keeps the
//! COMPLETE trusted authorization context and the delivery binding's
//! generation id, so the writer can re-judge against CURRENT committed
//! authority right before the actual send.
//!
//! Two explicit authorization positions (spec §14.5):
//! - Position 1 (pre-enqueue): the registry/run-channel layer takes a bounded
//!   snapshot of event-related targets, releases the registry lock, asks the
//!   application hook in ≤128-context chunks, re-checks conn id / binding
//!   generation / closed state, and enqueues ONLY items judged `Deliver`.
//!   `SkipMessage` skips just this frame (no unsubscribe, no close);
//!   `InvalidateBinding` and any `Err` invalidate the binding and drop its
//!   protected backlog — and the registry deregisters the connection's
//!   protected subscription in the same event, so broadcasts stop and NO
//!   later event queries the dead binding (spec §17.2). An enqueue decision
//!   is NEVER a writer pass.
//! - Position 2 (pre-send): the writer re-authorizes the dequeued frame with
//!   a single-context batch against current committed authority and only
//!   then starts the socket send. No positive caching, no TTL (spec §14.5).
//!
//! Unwired hook = fail-closed: with no `DeliveryAuthorizationService`
//! attached, every protected context answers `InvalidateBinding` (Task 15's
//! Noop contract), so an assembly can never leak a protected frame.
//!
//! Anonymous/legacy connections without a verified real User cannot carry a
//! binding at all; their items stay on the legacy [`WorkbenchOutbound::
//! PublicControl`] lane and preserve the pre-existing visibility behavior.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};

use bcs_domain::{MessageAudience, MessageVisibilityDomain};
use bcs_service_api::application::v1::delivery_authorization::{
    DELIVERY_AUTHORIZATION_MAX_BATCH, DeliveryAction, DeliveryAuthorizationContext,
    DeliveryAuthorizationContextBuilder, DeliveryAuthorizationDecision,
    DeliveryAuthorizationService, DeliveryResourceKind,
};
use bcs_service_api::port::repo::bot_authority::human_actor_id;
use tokio::sync::mpsc;

/// The single item type of every per-connection outbound queue (the mpsc the
/// registry, run channels, and dispatcher responses feed, and the socket
/// writer drains). Client frame schemas are untouched.
#[derive(Debug, Clone)]
pub enum WorkbenchOutbound {
    /// A frame carrying no protected payload and no binding:
    /// request/response frames, pongs, close notifications — plus every
    /// legacy-lane frame for connections without a verified binding.
    PublicControl(String),
    /// A protected frame plus the complete trusted context and the binding
    /// generation that authorized its enqueue. The writer MUST re-authorize
    /// `context` before sending; the enqueue decision is not a pass.
    Protected {
        payload: String,
        context: DeliveryAuthorizationContext,
        binding_id: u64,
    },
}

/// One protected delivery binding: the trusted identity facts captured at
/// subscribe/registration time — the REAL User (never the registry's legacy
/// actor slot), the verified tenant/env boundary, the protectable resource,
/// and the SELECTED view actor. Frame-level facts (action + visibility/
/// audience) are stamped per dispatch; the binding itself never changes
/// for the lifetime of a connection subscription.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtectedDeliveryBinding {
    /// Trusted tenant boundary of the verified connection identity; may be
    /// `None` per the identity contract. Never inferred.
    pub tenant: Option<String>,
    /// Trusted environment boundary; never taken from client input.
    pub env: String,
    /// The verified REAL User id — the operator, never a view actor id.
    pub user_id: String,
    /// The protectable resource (closed enum + id).
    pub resource_kind: DeliveryResourceKind,
    pub resource_id: String,
    /// The SELECTED view actor (`human_<user>` self view or a Bot view
    /// under live owner/manager authority). Revoked views never auto-switch.
    pub view_actor_id: String,
}

impl ProtectedDeliveryBinding {
    /// Build the complete context for one outbound frame of this binding,
    /// through the ONLY sanctioned builder, from values the adapter
    /// verified at subscribe/registration time (never client frame input).
    pub fn context(
        &self,
        action: DeliveryAction,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
    ) -> DeliveryAuthorizationContext {
        let mut builder = if self.view_actor_id == human_actor_id(&self.user_id) {
            DeliveryAuthorizationContextBuilder::self_view(
                &self.env,
                &self.user_id,
                self.resource_kind,
                &self.resource_id,
            )
        } else {
            DeliveryAuthorizationContextBuilder::bot_view(
                &self.env,
                &self.user_id,
                self.resource_kind,
                &self.resource_id,
                &self.view_actor_id,
            )
        };
        builder = builder.tenant(self.tenant.clone());
        builder
            .action(action)
            .message(visibility_domain, audience.cloned())
            .build()
    }
}

/// A run channel's protected anchor: the binding facts plus the binding
/// generation the channel was registered under, plus the shared gate. Run
/// fallback / replay / re-dispatch all re-authorize through this exact
/// identity (spec §14.5: no decision reuse across dispatches).
#[derive(Clone)]
pub struct ProtectedChannelRef {
    pub user_id: String,
    pub view_actor_id: String,
    pub tenant: Option<String>,
    pub env: String,
    pub resource_kind: DeliveryResourceKind,
    pub resource_id: String,
    pub binding_id: u64,
    /// Shared gate: authorization hook + binding liveness for enqueue
    /// re-checks on this lane.
    pub gate: Arc<ProtectedDeliveryGate>,
}

impl ProtectedChannelRef {
    pub fn binding(&self) -> ProtectedDeliveryBinding {
        ProtectedDeliveryBinding {
            tenant: self.tenant.clone(),
            env: self.env.clone(),
            user_id: self.user_id.clone(),
            resource_kind: self.resource_kind,
            resource_id: self.resource_id.clone(),
            view_actor_id: self.view_actor_id.clone(),
        }
    }
}

impl std::fmt::Debug for ProtectedChannelRef {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ProtectedChannelRef")
            .field("user_id", &self.user_id)
            .field("view_actor_id", &self.view_actor_id)
            .field("resource_id", &self.resource_id)
            .field("binding_id", &self.binding_id)
            .finish_non_exhaustive()
    }
}

/// What the writer must do after the pre-send re-check of one dequeued
/// protected frame.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DequeuedDecision {
    /// Binding valid + frame visible: start the socket send.
    Send(String),
    /// Drop this frame only — either the binding is dead (its backlog is
    /// discarded, further frames of this binding cost no authority read) or
    /// the frame is out of the view's scope (SkipMessage: the binding and
    /// connection stay alive) or authority failed fail-closed for it.
    /// The producer-visible difference is handled inside the gate.
    Drop,
}

static NEXT_BINDING_ID: AtomicU64 = AtomicU64::new(1);

/// The shared continuous-authorization state for the whole WS adapter: the
/// application hook plus the active binding-generation set. The registry
/// (position 1) and the per-connection writer (position 2) both resolve
/// through this gate; decisions live only inside single calls.
pub struct ProtectedDeliveryGate {
    /// Application hook; when absent every protected context fails closed
    /// as `InvalidateBinding` (Task 15's Noop contract).
    authorization: Mutex<Option<Arc<dyn DeliveryAuthorizationService>>>,
    /// Active bindings: binding_id -> owning conn_id. Presence means the
    /// binding is live; absence (retired or invalidated) means queued frames
    /// of this binding are dropped by the writer without any authority read.
    bindings: Mutex<HashMap<u64, u64>>,
}

impl ProtectedDeliveryGate {
    pub fn new() -> Self {
        Self {
            authorization: Mutex::new(None),
            bindings: Mutex::new(HashMap::new()),
        }
    }

    /// Assembly wiring (bootstrap) and tests attach the real application
    /// service here.
    pub fn set_authorization(&self, service: Arc<dyn DeliveryAuthorizationService>) {
        *self.authorization.lock().unwrap() = Some(service);
    }

    /// Whether a real application hook is attached (diagnostics only;
    /// an unwired gate keeps answering every context fail-closed).
    pub(crate) fn hook_configured(&self) -> bool {
        self.authorization.lock().unwrap().is_some()
    }

    /// Register a fresh binding generation for `conn_id`; the returned id is
    /// globally unique and never reused, so a replaced connection can never
    /// inherit a previous generation's queued frames.
    pub fn register_binding(&self, conn_id: u64) -> u64 {
        let binding_id = NEXT_BINDING_ID.fetch_add(1, Ordering::Relaxed);
        self.bindings.lock().unwrap().insert(binding_id, conn_id);
        binding_id
    }

    /// Retire the binding on unsubscribe/connection close. Queued protected
    /// frames of this binding are dropped by the writer with no re-read.
    pub fn retire_binding(&self, binding_id: u64) {
        self.bindings.lock().unwrap().remove(&binding_id);
    }

    /// Invalidate a binding after a revoke / failed authority read: its
    /// protected backlog stops (drop-on-drain) while the connection itself
    /// stays open — its broadcast subscription was already deregistered by
    /// the registry, so frames simply go silent and only direct
    /// dispatcher-originated PublicControl responses keep flowing.
    pub fn invalidate_binding(&self, binding_id: u64) {
        self.bindings.lock().unwrap().remove(&binding_id);
    }

    /// "The binding still matches and was not cancelled": the writer's first
    /// pre-send check, and the registry's post-authorize recheck.
    pub fn binding_active(&self, binding_id: u64) -> bool {
        self.bindings.lock().unwrap().contains_key(&binding_id)
    }

    pub fn conn_id_of(&self, binding_id: u64) -> Option<u64> {
        self.bindings.lock().unwrap().get(&binding_id).copied()
    }

    /// Position 1 (pre-enqueue): authorize one bounded, event-related target
    /// snapshot. The ADAPTER does the chunking (ceil(K/128)); a hook-failed
    /// chunk fails the whole event's batch fail-closed — never enqueue
    /// partially verified targets, never treat the remains as authorized.
    /// With no hook attached, every context is `InvalidateBinding`.
    pub async fn authorize_targets(
        &self,
        contexts: Vec<DeliveryAuthorizationContext>,
    ) -> Result<Vec<DeliveryAuthorizationDecision>, ()> {
        let hook = self.authorization.lock().unwrap().clone();
        let Some(hook) = hook else {
            return Ok(contexts
                .iter()
                .map(|_| DeliveryAuthorizationDecision::InvalidateBinding)
                .collect());
        };
        let mut decisions = Vec::with_capacity(contexts.len());
        for chunk in contexts.chunks(DELIVERY_AUTHORIZATION_MAX_BATCH) {
            match hook
                .authorize_batch(chunk.to_vec())
                .await
            {
                Ok(chunk_decisions) => decisions.extend(chunk_decisions),
                Err(_) => return Err(()),
            }
        }
        Ok(decisions)
    }

    /// Position 2 (pre-send): the writer re-checks the dequeued frame.
    /// `SkipMessage` only drops the frame (binding stays); `InvalidateBinding`
    /// or any error drops the frame AND invalidates the binding, stopping its
    /// protected drain. The enqueue decision is never consulted.
    pub async fn authorize_dequeued(
        &self,
        binding_id: u64,
        context: &DeliveryAuthorizationContext,
        payload: String,
    ) -> DequeuedDecision {
        // Dead binding: backlog discard — no authority read, no fallback.
        if !self.binding_active(binding_id) {
            return DequeuedDecision::Drop;
        }
        let decision = match self
            .authorize_single(context.clone())
            .await
        {
            Ok(decision) => decision,
            Err(()) => {
                self.invalidate_binding(binding_id);
                return DequeuedDecision::Drop;
            }
        };
        match decision {
            DeliveryAuthorizationDecision::Deliver => DequeuedDecision::Send(payload),
            DeliveryAuthorizationDecision::SkipMessage => DequeuedDecision::Drop,
            DeliveryAuthorizationDecision::InvalidateBinding => {
                self.invalidate_binding(binding_id);
                DequeuedDecision::Drop
            }
        }
    }

    /// One bounded authorize call (batch of one is a legal batch — spec
    /// §17.2 counts it as the dequeue/redispatch read, never as reuse).
    async fn authorize_single(
        &self,
        context: DeliveryAuthorizationContext,
    ) -> Result<DeliveryAuthorizationDecision, ()> {
        let hook = self.authorization.lock().unwrap().clone();
        let Some(hook) = hook else {
            return Ok(DeliveryAuthorizationDecision::InvalidateBinding);
        };
        hook.authorize_batch(vec![context])
            .await
            .map(|mut decisions| decisions.pop().unwrap_or(DeliveryAuthorizationDecision::InvalidateBinding))
            .map_err(|_| ())
    }
}

/// Enqueue one protected frame after a position-1 authorize for the single
/// target lanes (interaction replay at connect, dispatcher-originated re-
/// dispatches). Only `Deliver` enqueues; `SkipMessage` skips the frame alone;
/// `InvalidateBinding`/`Err` invalidate the binding (spec §14.5).
pub(crate) async fn enqueue_single_protected(
    gate: &Arc<ProtectedDeliveryGate>,
    tx: &mpsc::Sender<WorkbenchOutbound>,
    binding: &ProtectedDeliveryBinding,
    binding_id: u64,
    payload: String,
    action: DeliveryAction,
    visibility_domain: MessageVisibilityDomain,
    audience: Option<&MessageAudience>,
) {
    let context = binding.context(action, visibility_domain, audience);
    match gate
        .authorize_targets(vec![context.clone()])
        .await
    {
        Ok(decisions) => match decisions.as_slice() {
            [DeliveryAuthorizationDecision::Deliver] => {
                if tx
                    .send(WorkbenchOutbound::Protected {
                        payload,
                        context,
                        binding_id,
                    })
                    .await
                    .is_err()
                {
                    gate.retire_binding(binding_id);
                }
            }
            [DeliveryAuthorizationDecision::SkipMessage] => {}
            _ => gate.invalidate_binding(binding_id),
        },
        Err(()) => gate.invalidate_binding(binding_id),
    }
}