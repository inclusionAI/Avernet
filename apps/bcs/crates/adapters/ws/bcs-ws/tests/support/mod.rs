//! Shared WS test doubles for the final-writer delivery
//! authorization contract. Adapter-level tests replace the
//! Application service, never the authorization rules themselves
//! (the real decision matrix lives in the
//! `bcs-app-session/tests/delivery_authorization.rs` suite).

#![allow(dead_code)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, DeliveryAuthorizationService, DeliveryDecision,
    DeliveryVisibility, ProtectedDeliveryBinding,
};

/// Permit-all double: every protected frame is delivered.
pub struct AllowAllAuthorization;

#[async_trait]
impl DeliveryAuthorizationService for AllowAllAuthorization {
    async fn authorize_delivery(
        &self,
        _binding: &ProtectedDeliveryBinding,
        _message: &DeliveryVisibility,
    ) -> Result<DeliveryDecision, ApplicationError> {
        Ok(DeliveryDecision::Deliver)
    }
}

/// Fail-closed double: every protected frame invalidates its
/// binding.
pub struct InvalidateAllAuthorization;

#[async_trait]
impl DeliveryAuthorizationService for InvalidateAllAuthorization {
    async fn authorize_delivery(
        &self,
        _binding: &ProtectedDeliveryBinding,
        _message: &DeliveryVisibility,
    ) -> Result<DeliveryDecision, ApplicationError> {
        Ok(DeliveryDecision::InvalidateBinding)
    }
}

/// Records the consulted frames and returns a scripted
/// decision sequence (pop from the front when non-empty, else
/// `default`).
pub struct ScriptedAuthorization {
    pub decisions: Mutex<Vec<DeliveryDecision>>,
    pub default: DeliveryDecision,
    pub consulted: Mutex<Vec<ProtectedDeliveryBinding>>,
}

impl ScriptedAuthorization {
    pub fn new(default: DeliveryDecision) -> Self {
        Self {
            decisions: Mutex::new(Vec::new()),
            default,
            consulted: Mutex::new(Vec::new()),
        }
    }

    pub fn push(&self, decision: DeliveryDecision) {
        self.decisions
            .lock()
            .expect("scripted decisions")
            .push(decision);
    }

    pub fn consulted(&self) -> Vec<ProtectedDeliveryBinding> {
        self.consulted
            .lock()
            .expect("scripted consulted")
            .clone()
    }

    pub fn consulted_count(&self) -> usize {
        self.consulted.lock().expect("scripted consulted").len()
    }
}

#[async_trait]
impl DeliveryAuthorizationService for ScriptedAuthorization {
    async fn authorize_delivery(
        &self,
        binding: &ProtectedDeliveryBinding,
        _message: &DeliveryVisibility,
    ) -> Result<DeliveryDecision, ApplicationError> {
        self.consulted
            .lock()
            .expect("scripted consulted")
            .push(binding.clone());
        let next = self
            .decisions
            .lock()
            .expect("scripted decisions")
            .pop();
        Ok(next.unwrap_or(self.default))
    }
}

/// Synchronizing gate (barriers, no sleeps): the test closes
/// the gate to park the final writer INSIDE `authorize_delivery`
/// (between enqueue and the authorization read), scripts the
/// decision, and reopens the gate. Every gated frame pops
/// ITS OWN scripted decision in close order, so two frames
/// parked at once keep their individual control points.
pub struct GateAuthorization {
    pending: Mutex<std::collections::VecDeque<DeliveryDecision>>,
    default: DeliveryDecision,
    open_tx: tokio::sync::watch::Sender<bool>,
    open_rx: tokio::sync::watch::Receiver<bool>,
    consulted: Mutex<Vec<ProtectedDeliveryBinding>>,
}

impl GateAuthorization {
    pub fn open_with_default(default: DeliveryDecision) -> Arc<Self> {
        let (open_tx, open_rx) = tokio::sync::watch::channel(true);
        Arc::new(Self {
            pending: Mutex::new(std::collections::VecDeque::new()),
            default,
            open_tx,
            open_rx,
            consulted: Mutex::new(Vec::new()),
        })
    }

    /// Park the next authorization attempt: close the gate
    /// (the writer blocks INSIDE the application call), then
    /// set the decision observed when the gate reopens.
    pub fn close_with(&self, decision: DeliveryDecision) {
        self.pending
            .lock()
            .expect("gate pending")
            .push_back(decision);
        let _ = self.open_tx.send(false);
    }

    pub fn open(&self) {
        let _ = self.open_tx.send(true);
    }

    pub fn push(&self, decision: DeliveryDecision) {
        self.pending
            .lock()
            .expect("gate pending")
            .push_back(decision);
    }

    pub fn consulted(&self) -> Vec<ProtectedDeliveryBinding> {
        self.consulted.lock().expect("gate consulted").clone()
    }

    /// Deterministic barrier wait: poll the in-process counter
    /// with a bounded async sleep — used ONLY to observe that
    /// the final writer actually entered the authorization call.
    pub async fn wait_until_consulted(&self, expected: usize) {
        let mut waited = 0;
        while self.consulted.lock().expect("gate consulted").len() < expected {
            assert!(
                waited < 2_000,
                "final writer never consulted authorization"
            );
            tokio::time::sleep(std::time::Duration::from_millis(2)).await;
            waited += 1;
        }
    }
}

#[async_trait]
impl DeliveryAuthorizationService for GateAuthorization {
    async fn authorize_delivery(
        &self,
        binding: &ProtectedDeliveryBinding,
        _message: &DeliveryVisibility,
    ) -> Result<DeliveryDecision, ApplicationError> {
        self.consulted
            .lock()
            .expect("gate consulted")
            .push(binding.clone());
        let mut open_rx = self.open_rx.clone();
        // Race-free barrier: wait while the gate is closed.
        while !*open_rx.borrow_and_update() {
            open_rx.changed().await.expect("gate stays alive");
        }
        let scripted = self
            .pending
            .lock()
            .expect("gate pending")
            .pop_front();
        Ok(scripted.unwrap_or(self.default))
    }
}
