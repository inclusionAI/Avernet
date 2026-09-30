//! Group Context repository port — persistence contract for context entries
//! and policy templates.

use async_trait::async_trait;
use bcs_domain::group_context::{
    ContextEntry, ContextView, PolicyTemplate, RetrievalItem, TemplateView,
};

// ── Scope key ──────────────────────────────────────────────────────────────

/// Identifies the scope for context lookups.
#[derive(Debug, Clone)]
pub struct ScopeKey {
    pub tenant_id: String,
    pub group_id: String,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
}

// ── Supersede ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct SupersedeRequest {
    pub entry_id: String,
    pub new_version: u64,
    pub prev_version: u64,
    pub new_entry_id: String,
    pub tx_time_ms: u64,
    pub actor_id: String,
}

#[derive(Debug, Clone)]
pub struct SupersedeOutcome {
    pub old_entry_id: String,
    pub new_entry_id: String,
}

// ── Insert ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct InsertContextRequest {
    pub entry: ContextEntry,
}

// ── Stored entry ───────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct StoredEntry {
    pub entry: ContextEntry,
    pub active: bool,
}

// ── Audit ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct AuditEntry {
    pub entry_id: String,
    pub actor_id: String,
    pub op: String,
    pub tx_time_ms: u64,
}

// ── Repo trait ─────────────────────────────────────────────────────────────

#[async_trait]
pub trait GroupContextRepoPort: Send + Sync {
    /// Find active entries within a scope, optionally filtered by domain.
    async fn find_active_in_scope(
        &self,
        scope: &ScopeKey,
        domain: Option<&str>,
        limit: u32,
    ) -> Result<Vec<ContextView>, String>;

    /// Insert a new context entry.
    async fn insert_context(
        &self,
        request: InsertContextRequest,
    ) -> Result<ContextEntry, String>;

    /// Atomically supersede an existing entry (CAS on version).
    async fn supersede(
        &self,
        request: SupersedeRequest,
    ) -> Result<SupersedeOutcome, String>;

    /// Find entries by domain and key (for update).
    async fn find_entries_by_domain(
        &self,
        scope: &ScopeKey,
        domain: &str,
        key: Option<&str>,
        active_only: bool,
        limit: u32,
    ) -> Result<Vec<ContextEntry>, String>;

    /// Find candidate entries for a retrieve operation.
    async fn find_candidates_for_retrieve(
        &self,
        scope: &ScopeKey,
        domain: Option<&str>,
        query: Option<&str>,
        limit: u32,
    ) -> Result<Vec<RetrievalItem>, String>;

    /// List active contexts visible to a given actor.
    async fn list_active_contexts_for_actor(
        &self,
        scope: &ScopeKey,
        actor_id: &str,
        domain: Option<&str>,
        limit: u32,
    ) -> Result<Vec<ContextView>, String>;

    /// List policy templates the given actor may use to create entries.
    async fn list_creatable_templates_for_actor(
        &self,
        scope: &ScopeKey,
        actor_id: &str,
        domain: Option<&str>,
        limit: u32,
    ) -> Result<Vec<TemplateView>, String>;

    /// Look up a single policy template by id.
    async fn find_policy_template(
        &self,
        template_id: &str,
    ) -> Result<Option<PolicyTemplate>, String>;

    /// List all policy templates, optionally filtered by domain.
    async fn list_policy_templates(
        &self,
        domain: Option<&str>,
        limit: u32,
    ) -> Result<Vec<PolicyTemplate>, String>;

    /// Record a retrieve audit entry.
    async fn write_retrieve_audit(&self, audit: AuditEntry) -> Result<(), String>;
}