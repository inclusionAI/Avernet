//! Shared doubles for the V1 Group facade integration tests.
//!
//! The production-style hook wraps the strict authority core implemented by
//! `bcs-edge-permission` over the memory bot repo (the same wiring bootstrap
//! performs); the recording double PROVES facade decisions actually resolve
//! through the hook and never through `created_by` (spec §12.4).

// Shared across several integration crates: each binary uses a different
// subset of the double's surface, so unused-member lints are expected.
#![allow(dead_code)]

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::application::v1::BotAuthorityHook;

/// The production authority chain over one memory repo: the strict Core
/// implemented by `bcs-edge-permission`, wrapped by the application hook —
/// the same wiring bootstrap performs.
pub fn repo_authority_hook(repo: &Arc<MemoryBotRepo>) -> Arc<dyn BotAuthorityHook> {
    Arc::new(RepoAuthorityHook {
        core: Arc::new(bcs_edge_permission::authority::BotAuthorityCoreServiceImpl::new(
            repo.clone(),
        )),
    })
}

struct RepoAuthorityHook {
    core: Arc<dyn bcs_service_api::core::BotAuthorityCoreService>,
}

#[async_trait]
impl BotAuthorityHook for RepoAuthorityHook {
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
        Ok(self.core.role(user_id, bot_id).await?.is_some())
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        match self.core.role(user_id, bot_id).await? {
            Some(bcs_service_api::types::BotAccessRelation::Owner) => Ok(()),
            _ => Err(ServiceError::Forbidden(format!(
                "user '{user_id}' is not the owner of bot '{bot_id}'"
            ))),
        }
    }
}

/// Recording double: every hook question is ANSWERED (no silent fallthrough
/// to created_by) and RECORED so tests can assert the hook was actually
/// consulted, and can flip a verdict to prove live re-verification.
#[derive(Default)]
pub struct RecordingAuthorityHook {
    questions: Mutex<Vec<(String, String)>>,
    verdicts: Mutex<BTreeMap<(String, String), bool>>,
    failure: Mutex<Option<ServiceError>>,
    default: bool,
}

impl RecordingAuthorityHook {
    pub fn new(default: bool) -> Self {
        Self {
            default,
            ..Self::default()
        }
    }

    /// Arm a one-shot propagated backend failure for EVERY question, so a
    /// suite can prove the facade propagates authority-hook failures
    /// instead of degrading them to a plain deny.
    pub fn arm_failure(&self, error: ServiceError) {
        *self.failure.lock().unwrap() = Some(error);
    }

    pub fn set(&self, user_id: &str, bot_id: &str, verdict: bool) {
        self.verdicts.lock().unwrap().insert(
            (user_id.to_string(), bot_id.to_string()),
            verdict,
        );
    }

    pub fn questions(&self) -> Vec<(String, String)> {
        self.questions.lock().unwrap().clone()
    }

    pub fn asked_about(&self, user_id: &str, bot_id: &str) -> bool {
        self.questions
            .lock()
            .unwrap()
            .iter()
            .any(|(user, bot)| user == user_id && bot == bot_id)
    }
}

#[async_trait]
impl BotAuthorityHook for RecordingAuthorityHook {
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
        self.questions
            .lock()
            .unwrap()
            .push((user_id.to_string(), bot_id.to_string()));
        if let Some(error) = self.failure.lock().unwrap().take() {
            return Err(error);
        }
        Ok(self
            .verdicts
            .lock()
            .unwrap()
            .get(&(user_id.to_string(), bot_id.to_string()))
            .copied()
            .unwrap_or(self.default))
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        let allowed = self.can_manage(user_id, bot_id).await?;
        if allowed {
            Ok(())
        } else {
            Err(ServiceError::Forbidden(format!(
                "user '{user_id}' is not the owner of bot '{bot_id}'"
            )))
        }
    }
}