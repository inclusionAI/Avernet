//! Durable remote correlation, independent of Redis's expiring local run cache.
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use crate::{ServiceResult, types::a2a_bot::A2aInterface};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct A2aContextScope {
    pub bot_id: String,
    pub session_id: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub interface_url: String,
    pub tenant: Option<String>,
    /// Trusted deployment route identity, excluding credentials and the local
    /// sidecar address. Absent HTTP fields preserve legacy context-key hashes.
    #[serde(default,skip_serializing_if="Option::is_none")]
    pub route_fingerprint: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum A2aRunState { Submitting, Running, InputRequired, Completed, Failed, Canceled, Unknown }

impl A2aRunState {
    pub fn is_terminal(self) -> bool { matches!(self, Self::Completed | Self::Failed | Self::Canceled) }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct A2aRunRecord {
    pub run_id: String,
    pub scope: A2aContextScope,
    pub group_id: String,
    pub interface: A2aInterface,
    pub credential_kind: String,
    /// Negotiation frozen at submission; later Card refresh cannot change it.
    #[serde(default)]
    pub extensions: Vec<String>,
    pub message_id: String,
    pub request_digest: String,
    pub remote_task_id: Option<String>,
    pub context_id: Option<String>,
    /// Shared remote Task revision; absent until the first binding is accepted.
    #[serde(default)]
    pub task_version: Option<u64>,
    pub state: A2aRunState,
    pub deadline_ms: u64,
    /// Absent on legacy correlations and runs without input-wait support.
    #[serde(default)]
    pub budget: Option<crate::types::run_budget::RunBudgetState>,
    /// Adapter-owned, bounded validated input message. Never grants routing or
    /// credentials. Updated with confirmed receipts, not with optimistic input.
    #[serde(default)]
    pub pending_input: Option<serde_json::Value>,
    pub updated_at_ms: u64,
    pub version: u64,
}

#[async_trait]
pub trait A2aRunRepoPort: Send + Sync {
    async fn get(&self, run_id: &str) -> ServiceResult<Option<A2aRunRecord>>;
    /// Version 1, no remote Task yet. False means the run already exists.
    async fn create(&self, record: &A2aRunRecord) -> ServiceResult<bool>;
    /// Atomically updates the run binding and revision-fenced shared Task input.
    /// Multiple runs may bind the same remote Task only within the same trusted scope.
    /// Remote identity is immutable after binding. Returns the committed projection,
    /// including its Task revision; None means a run/Task CAS conflict.
    /// Context is established on first binding, not rewritten on ordinary updates.
    /// No event/token cursor is written per text delta.
    async fn compare_and_set(&self, record: &A2aRunRecord, expected_version: u64) -> ServiceResult<Option<A2aRunRecord>>;
    async fn context(&self, scope: &A2aContextScope) -> ServiceResult<Option<String>>;
    /// Fail closed before a run-scoped abort could cancel another active run's Task.
    async fn has_other_active_task_run(&self, record: &A2aRunRecord) -> ServiceResult<bool>;
    /// At most two records: enough to prove a scope cancel is unambiguous.
    async fn active(&self, scope: &A2aContextScope) -> ServiceResult<Vec<A2aRunRecord>>;
}
