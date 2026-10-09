#![allow(dead_code)]

//! Shared fixtures for the Workbench WS protected-delivery suites.
//!
//! Everything here drives the REAL adapter machinery: the real
//! `WorkbenchConnectionRegistry`, the real `RunChannelManager`, and the real
//! `WorkbenchOutbound` queue items. Only the application-layer continuous
//! authorization hook is a test double (`RecordingDeliveryAuthorization`),
//! mirroring Task 15's application-level map double: it records authorize
//! reads and decides from a mutable decision-state map that the test can
//! revoke "from another instance" mid-flight, exactly like a second BCS
//! instance committing a revoke to the shared authority store.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_domain::{HumanMessageView, MessageAudience, MessageViewScope, MessageVisibilityDomain};
use bcs_service_api::application::v1::delivery_authorization::{
    DeliveryAuthorizationContext, DeliveryAuthorizationDecision, DeliveryResourceKind,
    DeliveryAuthorizationService,
};
use bcs_service_api::application::v1::ApplicationError;
use bcs_ws::web::protected_delivery::{
    DequeuedDecision, ProtectedDeliveryBinding, ProtectedDeliveryGate, WorkbenchOutbound,
};
use tokio::sync::{Notify, mpsc};
use std::sync::Mutex;

/// Decision-state entry for one protected binding, keyed by the complete
/// trusted context identity (`user_id \0 view_actor_id \0 resource`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BindingFacts {
    /// The binding is valid; the view's participant scope decides per-frame
    /// visibility exactly like the real application service does.
    Valid {
        scope: MessageViewScope,
        allow_legacy_unclassified_chat: bool,
    },
    /// The binding's authority facts are gone (revoked in the authority
    /// store "from another instance").
    Revoked,
}

impl BindingFacts {
    pub fn full() -> Self {
        BindingFacts::Valid {
            scope: MessageViewScope::Full,
            allow_legacy_unclassified_chat: true,
        }
    }

    pub fn participant() -> Self {
        BindingFacts::Valid {
            scope: MessageViewScope::Participant,
            allow_legacy_unclassified_chat: true,
        }
    }
}

#[derive(Default)]
struct RecordingState {
    facts: HashMap<String, BindingFacts>,
    /// authorize_batch calls, one entry per call recording the batch size.
    batch_sizes: Vec<usize>,
    /// authorize reads per decision-key, both positions combined.
    reads_by_key: HashMap<String, usize>,
    /// When armed, authorize_batch fails closed with an Err for every
    /// context (`ApplicationError` is not `Clone`; the error is constructed
    /// at return time).
    armed_err: bool,
    /// When armed, authorize_batch publishes on `entry` and then waits for
    /// `resume` — used to pin a call in-flight deterministically.
    pause: Option<Arc<Notify>>,
    entry_pause: Option<Arc<Notify>>,
}

/// Application double: unregistered contexts are treated as invalid bindings
/// (a fact the adapter never gets to invent).
pub struct RecordingDeliveryAuthorization {
    state: Mutex<RecordingState>,
    authorize_calls: AtomicUsize,
}

impl RecordingDeliveryAuthorization {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new(RecordingState::default()),
            authorize_calls: AtomicUsize::new(0),
        })
    }

    pub fn register(
        &self,
        user_id: &str,
        view_actor_id: &str,
        resource: &str,
        facts: BindingFacts,
    ) {
        self.state
            .lock()
            .unwrap()
            .facts
            .insert(decision_key(user_id, view_actor_id, resource), facts);
    }

    pub fn revoke(&self, user_id: &str, view_actor_id: &str, resource: &str) {
        self.state.lock().unwrap().facts.insert(
            decision_key(user_id, view_actor_id, resource),
            BindingFacts::Revoked,
        );
    }

    pub fn arm_err(&self) {
        self.state.lock().unwrap().armed_err = true;
    }

    /// Makes the next authorize call announce itself on the returned entry
    /// Notify and then block until the companion `resume` Notify fires.
    pub fn arm_pause(self: &Arc<Self>) -> (Arc<Notify>, Arc<Notify>) {
        let entry = Arc::new(Notify::new());
        let resume = Arc::new(Notify::new());
        let mut state = self.state.lock().unwrap();
        state.pause = Some(resume.clone());
        state.entry_pause = Some(entry.clone());
        (entry, resume)
    }

    pub fn batch_sizes(&self) -> Vec<usize> {
        self.state.lock().unwrap().batch_sizes.clone()
    }

    pub fn total_authorize_reads(&self) -> usize {
        self.authorize_calls.load(Ordering::SeqCst)
    }

    pub fn authorize_reads_for(&self, user_id: &str, view: &str, resource: &str) -> usize {
        *self
            .state
            .lock()
            .unwrap()
            .reads_by_key
            .get(&decision_key(user_id, view, resource))
            .unwrap_or(&0)
    }

    fn record_read(&self, contexts: &[DeliveryAuthorizationContext]) {
        let mut state = self.state.lock().unwrap();
        state.batch_sizes.push(contexts.len());
        for context in contexts {
            let key = decision_key(
                &context.user_id,
                &context.view_actor_id,
                &context.resource_id,
            );
            *state.reads_by_key.entry(key).or_default() += 1;
        }
    }
}

fn decision_key(user_id: &str, view_actor_id: &str, resource: &str) -> String {
    format!("{user_id}\u{0}{view_actor_id}\u{0}{resource}")
}

#[async_trait]
impl DeliveryAuthorizationService for RecordingDeliveryAuthorization {
    async fn authorize_batch(
        &self,
        contexts: Vec<DeliveryAuthorizationContext>,
    ) -> Result<Vec<DeliveryAuthorizationDecision>, ApplicationError> {
        self.authorize_calls.fetch_add(1, Ordering::SeqCst);
        self.record_read(&contexts);
        let (armed, entry, resume) = {
            // Snapshot the decision inputs under the std lock and drop the
            // guard BEFORE any await: the future must stay Send. The pause is
            // TAKEN (not cloned) so exactly one authorize call pins, and the
            // next call never waits behind it.
            let mut state = self.state.lock().unwrap();
            (state.armed_err, state.entry_pause.take(), state.pause.take())
        };
        if armed {
            return Err(ApplicationError::InvalidInput {
                code: "recording_authority_store_failed".to_string(),
                message: "recording fixture: authority store failing".to_string(),
            });
        }
        if let Some(entry) = entry {
            entry.notify_waiters();
        }
        if let Some(resume) = resume {
            let _ = tokio::time::timeout(std::time::Duration::from_secs(60), resume.notified())
                .await;
        }
        Ok(contexts
            .iter()
            .map(|context| {
                let state = self.state.lock().unwrap();
                let key = decision_key(
                    &context.user_id,
                    &context.view_actor_id,
                    &context.resource_id,
                );
                match state.facts.get(&key).cloned() {
                    Some(BindingFacts::Revoked) | None => {
                        DeliveryAuthorizationDecision::InvalidateBinding
                    }
                    Some(BindingFacts::Valid {
                        scope,
                        allow_legacy_unclassified_chat,
                    }) => {
                        let view = HumanMessageView {
                            actor_id: context.view_actor_id.clone(),
                            scope,
                            allow_legacy_unclassified_chat,
                        };
                        if view.allows_artifact(
                            context.visibility_domain,
                            context.audience.as_ref(),
                        ) {
                            DeliveryAuthorizationDecision::Deliver
                        } else {
                            DeliveryAuthorizationDecision::SkipMessage
                        }
                    }
                }
            })
            .collect())
    }
}

/// The test-side socket writer: drives the SAME pre-send re-authorization the
/// production writer in `handler.rs` performs (the position-2 code is the
/// shared `ProtectedDeliveryGate::authorize_dequeued`; the loop mirrors the
/// handler's tiny drain loop). Sent payloads land on `out`. Returns after
/// dequeuing exactly `expected_frames` items, so every fixture below stages
/// "enqueue — revoke — dequeue" with Barriers/Notify and no sleeps.
pub async fn drain_into_socket(
    rx: &mut mpsc::Receiver<WorkbenchOutbound>,
    gate: Arc<ProtectedDeliveryGate>,
    out: mpsc::Sender<String>,
    expected_frames: usize,
) {
    let mut processed = 0usize;
    while processed < expected_frames {
        let item = rx.recv().await.expect("writer drained before all staged frames");
        processed += 1;
        match item {
            WorkbenchOutbound::PublicControl(payload) => {
                let _ = out.send(payload).await;
            }
            WorkbenchOutbound::Protected {
                payload,
                context,
                binding_id,
            } => match gate.authorize_dequeued(binding_id, &context, payload).await {
                DequeuedDecision::Send(delivered_payload) => {
                    let _ = out.send(delivered_payload).await;
                }
                DequeuedDecision::Drop => {}
            },
        }
    }
}

/// One subscribed protected connection plus the collector playing its socket.
pub struct ProtectedSocket {
    pub tx: mpsc::Sender<WorkbenchOutbound>,
    pub rx: mpsc::Receiver<WorkbenchOutbound>,
    pub socket_tx: mpsc::Sender<String>,
    pub socket: mpsc::Receiver<String>,
    pub conn_id: u64,
    pub binding_id: u64,
}

impl ProtectedSocket {
    pub async fn connect(
        registry: &bcs_ws::web::WorkbenchConnectionRegistry,
        hook: &RecordingDeliveryAuthorization,
        session: &str,
        user_id: &str,
        view_actor_id: &str,
        facts: BindingFacts,
    ) -> Self {
        hook.register(user_id, view_actor_id, session, facts);
        let (tx, rx) = mpsc::channel(256);
        let (socket_tx, socket) = mpsc::channel(256);
        let binding = ProtectedDeliveryBinding {
            tenant: Some("tenant-test".to_string()),
            env: "env-test".to_string(),
            user_id: user_id.to_string(),
            resource_kind: DeliveryResourceKind::Session,
            resource_id: session.to_string(),
            view_actor_id: view_actor_id.to_string(),
        };
        let (conn_id, binding_id) = registry
            .subscribe_bound(
                session.to_string(),
                tx.clone(),
                Some(view_actor_id.to_string()),
                None,
                Some(binding),
                tokio_util::sync::CancellationToken::new(),
            )
            .await
            .expect("protected subscribe");
        Self {
            tx,
            rx,
            socket_tx,
            socket,
            conn_id,
            binding_id,
        }
    }
}

/// The audience facts used by the plain-chat fixtures.
pub fn chat_public() -> (MessageVisibilityDomain, Option<MessageAudience>) {
    (MessageVisibilityDomain::Chat, Some(MessageAudience::Public))
}