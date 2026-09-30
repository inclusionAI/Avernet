//! Group Context domain types — pure data, no I/O, no traits.
//!
//! The group context is a governed shared-memory infrastructure for BCS groups.
//! Every ContextEntry carries three layers:
//!   - Data layer: content + origin + time
//!   - Policy layer: flow (visibility + collection scope)
//!   - Governance layer: lineage (audit chain)

use serde::{Deserialize, Serialize};

// ── Granularity ────────────────────────────────────────────────────────────

/// The scope granularity of a context entry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Granularity {
    Run,
    Session,
    Group,
    Tenant,
}

// ── Freshness class ────────────────────────────────────────────────────────

/// Governs how a context entry may be cached or re-retrieved.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FreshnessClass {
    /// Volatile — fresh reads only; no stale cache.
    Volatile,
    /// Stable — cache for a bounded window.
    Stable,
    /// Audit — write-once, immutable; suitable for compliance records.
    Audit,
}

// ── Permission ─────────────────────────────────────────────────────────────

/// What an actor may do with a context entry governed by a template.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum Permission {
    Read,
    Write,
    #[default]
    WR,
}

// ── Origin ─────────────────────────────────────────────────────────────────

/// Framework-injected, non-forgeable origin of a context entry.
///
/// Every field is filled by BCS infrastructure — the calling bot/LLM cannot
/// override these values.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Origin {
    pub tenant_id: String,
    pub group_id: String,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
}

// ── Time ───────────────────────────────────────────────────────────────────

/// Dual-timeline for a context entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ContextTime {
    /// When the entry takes effect (epoch ms).
    pub valid_from: Option<u64>,
    /// When the entry ceases (epoch ms). Back-filled by the system when
    /// superseded.
    pub valid_to: Option<u64>,
    /// System-assigned transaction time (epoch ms).
    pub tx_time: Option<u64>,
}

// ── Visibility ─────────────────────────────────────────────────────────────

/// Which actors may see this context entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VisibleTo {
    /// Everyone in the group.
    All,
    /// Only the listed actor ids.
    Actors(Vec<String>),
    /// The origin actor only.
    SelfOnly,
}

// ── Collection scope ───────────────────────────────────────────────────────

/// From which actors may this entry be collected.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CollectFrom {
    /// Any actor in the group.
    All,
    /// Only the listed actor ids.
    Actors(Vec<String>),
}

// ── Flow ───────────────────────────────────────────────────────────────────

/// Policy-level flow constraints.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Flow {
    pub visible_to: VisibleTo,
    pub collect_from: CollectFrom,
}

// ── Consistency ────────────────────────────────────────────────────────────

/// Consistency model for a policy template.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Consistency {
    /// Latest-write-wins; no conflict detection.
    Eventual,
    /// Compare-and-swap on a provided version.
    Strong,
}

// ── ContextEntry ───────────────────────────────────────────────────────────

/// A single context entry stored in the group context.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextEntry {
    pub id: String,
    pub domain: String,
    pub key: String,
    pub content: serde_json::Value,
    pub origin: Origin,
    pub time: ContextTime,
    pub granularity: Granularity,
    pub template_id: String,
    pub version: u64,
    pub superseded_by: Option<String>,
    pub lineage: Vec<LineageHop>,
}

// ── Lineage ────────────────────────────────────────────────────────────────

/// One hop in a context entry's derivation chain.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LineageHop {
    pub op: LineageOp,
    pub from: String,
    pub at_ms: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LineageOp {
    Extract,
    Consolidate,
    Propagate,
    Supersede,
    Retrieve,
}

// ── Policy template ────────────────────────────────────────────────────────

/// A policy template that governs context entries within a domain.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolicyTemplate {
    pub id: String,
    pub name: String,
    pub domain: String,
    pub description: String,
    pub flow: Flow,
    pub consistency: Consistency,
    pub freshness: FreshnessClass,
    pub content_schema: Option<serde_json::Value>,
}

// ── Views (API response types) ─────────────────────────────────────────────

/// Returned by status / retrieve endpoints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextView {
    pub entry: ContextEntry,
    pub active: bool,
}

/// Returned by status endpoint for template info.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TemplateView {
    pub template: PolicyTemplate,
    pub active_entry_count: u64,
}

/// One item returned from a retrieve call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetrievalItem {
    pub entry: ContextEntry,
    pub relevance_score: Option<f64>,
}

// ── PolicySnapshot (subset extracted from a template) ──────────────────────

/// Projected policy fields from a template at the moment of entry creation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PolicySnapshot {
    pub template_id: String,
    pub flow: Flow,
    pub consistency: Consistency,
    pub freshness: FreshnessClass,
}