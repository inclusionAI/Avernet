//! Bot-owned A2A configuration; no credentials or SDK/framework types.
use async_trait::async_trait;
pub use crate::types::a2a_bot::*;
use crate::ServiceResult;

#[async_trait]
pub trait A2aBotRepoPort: Send + Sync {
    /// At most 256 identities, undeleted rows in this environment only. Does
    /// not expose credentials, selected routes or management metadata.
    async fn cards_for_bots(&self, bot_uuids: &[String]) -> ServiceResult<std::collections::BTreeMap<String, serde_json::Value>>;
    /// Absent key: legacy Bot. None value: A2A Bot whose Card does not match.
    /// Only matching Cards load their full payload; never fall back to legacy
    /// capabilities for a nonmatching A2A Bot.
    async fn discovery_cards(&self, bot_uuids: &[String], query: Option<&str>, skills: &[String])
        -> ServiceResult<std::collections::BTreeMap<String, Option<serde_json::Value>>>;
    async fn get(&self, bot_uuid: &str) -> ServiceResult<Option<A2aBotRecord>>;
    /// Up to 256 requested IDs. Absent rows are non-A2A; invalid A2A rows
    /// remain present with ready=false so callers cannot fall back to webhook.
    async fn availability_many(&self, bot_uuids: &[String]) -> ServiceResult<Vec<A2aBotAvailability>>;
    /// Replace all configuration atomically on an existing, active Provider Bot.
    /// revision must be expected_revision + 1. False means stale/absent binding.
    async fn compare_and_set(&self, record: &A2aBotRecord, expected_revision: u64) -> ServiceResult<bool>;
    /// Bounded keyset page of URL-backed Cards, at most 16 full snapshots.
    /// Inline Cards are excluded; callers keep their refresh-round cursor.
    async fn refreshable(&self, after_bot_uuid: Option<&str>, limit: u32) -> ServiceResult<Vec<A2aBotRecord>>;
}
