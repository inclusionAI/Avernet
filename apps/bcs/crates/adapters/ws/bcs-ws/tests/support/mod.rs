//! Shared WS test doubles for the operation-boundary delivery
//! admission contract (bot-authority spec §8.2 — 已准入输出不追溯).
//! Adapter-level tests replace the Application admission resolver,
//! never the permit/predicate rules themselves (the real resolution
//! matrix lives in `bcs-app-session/tests/delivery_admission.rs`).

#![allow(dead_code)]

use std::collections::BTreeSet;
use std::sync::Mutex;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    AdmittedRecipient, ApplicationError, DeliveryAdmissionService, DeliveryScope,
    ProtectedDeliveryBinding,
};

/// Permit-all double: every candidate is admitted with full view.
pub struct AllowAllAdmission;

#[async_trait]
impl DeliveryAdmissionService for AllowAllAdmission {
    async fn resolve_recipient_views(
        &self,
        _scope: &DeliveryScope,
        candidates: &[ProtectedDeliveryBinding],
    ) -> Result<Vec<AdmittedRecipient>, ApplicationError> {
        Ok(candidates
            .iter()
            .map(|candidate| AdmittedRecipient {
                connection_instance_id: candidate.connection_instance_id,
                view_actor_id: candidate.view_actor_id.clone(),
                scope: bcs_domain::MessageViewScope::Full,
                allow_legacy_unclassified_chat: true,
            })
            .collect())
    }
}

/// Deny-all double: admits nobody (a scope's whole candidate set
/// failing at resolution time).
pub struct DenyAllAdmission;

#[async_trait]
impl DeliveryAdmissionService for DenyAllAdmission {
    async fn resolve_recipient_views(
        &self,
        _scope: &DeliveryScope,
        _candidates: &[ProtectedDeliveryBinding],
    ) -> Result<Vec<AdmittedRecipient>, ApplicationError> {
        Ok(Vec::new())
    }
}

/// Always-failing double: every resolution is the desensitized
/// application read failure.
pub struct FailingAdmission;

#[async_trait]
impl DeliveryAdmissionService for FailingAdmission {
    async fn resolve_recipient_views(
        &self,
        _scope: &DeliveryScope,
        _candidates: &[ProtectedDeliveryBinding],
    ) -> Result<Vec<AdmittedRecipient>, ApplicationError> {
        Err(ApplicationError::internal("delivery_admission_read_failed"))
    }
}

/// Records EVERY resolution call (scope + candidate instance ids)
/// and answers with full-view permits. The frame-count-independence
/// proof: no matter how many frames an operation emits, this mock
/// is consulted only at operation/event boundaries.
pub struct RecordingAdmission {
    pub calls: Mutex<Vec<(DeliveryScope, Vec<u64>)>>,
    /// When set, only these candidate instance ids are admitted —
    /// the rest are absent from the result.
    pub admit_only: Mutex<Option<BTreeSet<u64>>>,
    /// When true, the NEXT call fails once (then clears the latch).
    pub fail_next: Mutex<bool>,
    /// The frozen scope every permit carries (Full by default; set
    /// Participant to pin enqueue-boundary filtering).
    pub scope: Mutex<bcs_domain::MessageViewScope>,
}

impl RecordingAdmission {
    pub fn new() -> Self {
        Self {
            calls: Mutex::new(Vec::new()),
            admit_only: Mutex::new(None),
            fail_next: Mutex::new(false),
            scope: Mutex::new(bcs_domain::MessageViewScope::Full),
        }
    }

    pub fn set_scope(&self, scope: bcs_domain::MessageViewScope) {
        *self.scope.lock().expect("scope latch") = scope;
    }

    pub fn call_count(&self) -> usize {
        self.calls.lock().expect("recording calls").len()
    }

    pub fn scopes(&self) -> Vec<DeliveryScope> {
        self.calls
            .lock()
            .expect("recording calls")
            .iter()
            .map(|(scope, _)| scope.clone())
            .collect()
    }

    pub fn admit_only_instances(&self, instances: &[u64]) {
        *self.admit_only.lock().expect("admit-only latch") =
            Some(instances.iter().copied().collect());
    }

    pub fn fail_next_call(&self) {
        *self.fail_next.lock().expect("fail latch") = true;
    }
}

#[async_trait]
impl DeliveryAdmissionService for RecordingAdmission {
    async fn resolve_recipient_views(
        &self,
        scope: &DeliveryScope,
        candidates: &[ProtectedDeliveryBinding],
    ) -> Result<Vec<AdmittedRecipient>, ApplicationError> {
        self.calls.lock().expect("recording calls").push((
            scope.clone(),
            candidates
                .iter()
                .map(|candidate| candidate.connection_instance_id)
                .collect(),
        ));
        {
            let mut fail_next = self.fail_next.lock().expect("fail latch");
            if *fail_next {
                *fail_next = false;
                return Err(ApplicationError::internal("delivery_admission_read_failed"));
            }
        }
        let admit_only = self.admit_only.lock().expect("admit-only latch").clone();
        let scope = *self.scope.lock().expect("scope latch");
        Ok(candidates
            .iter()
            .filter(|candidate| {
                admit_only
                    .as_ref()
                    .is_none_or(|only| only.contains(&candidate.connection_instance_id))
            })
            .map(|candidate| AdmittedRecipient {
                connection_instance_id: candidate.connection_instance_id,
                view_actor_id: candidate.view_actor_id.clone(),
                scope,
                allow_legacy_unclassified_chat: true,
            })
            .collect())
    }
}
