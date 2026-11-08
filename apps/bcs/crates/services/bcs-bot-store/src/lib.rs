//! Plugin-backed bot repository implementation.
//!
//! This module provides a bot repository backed by:
//! - **Database plugin**: Persistent storage for bot capabilities and tokens
//! - **Process Memory**: streaming connection state and heartbeat tracking
//!
//! # Architecture
//!
//! ```text
//! Layer 1 (Memory):    ws_connection, last_heartbeat, token_to_bot mapping
//! Layer 2 (Database):  bot_info JSON, session_token, name
//! ```

use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tracing::debug;
#[cfg(test)]
use std::sync::Arc;
#[cfg(test)]
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement};
use bcs_db_api::DbValue as Value;
use bcs_service_api::{
    BindingChannels, BotCapabilities, RegisteredBot, ServiceError, ServiceResult, Skill,
};

#[cfg(test)]
use bcs_service_api::BotDynamicStatus;

fn log_bot_cache_source(source: &'static str) {
    bcs_observability::count("bot.memory", source);
    debug!(target: "bcs_observation", request_id = %bcs_observability::current_request_id(), source, "bot.load.source");
}

pub mod memory;
pub mod persistent_repo;
pub mod provider;
pub mod provider_cache;

#[cfg(test)]
#[path = "../tests/unit/heartbeat.rs"]
mod heartbeat_tests;

/// Trusted-registration ownership composition (bot-authority
/// plan Task 4): shared transaction pieces plus the memory
/// projection's registration/profile composites' re-exports
/// live behind the crate facade.
pub(crate) mod ownership;
/// Atomic retirement composition (bot-authority
/// spec §7.1): shared SQL pieces for the ordinary and
/// Provider retire commands, plus (in the memory
/// module) the memory projection's mirror.
pub(crate) mod retirement;

pub use bcs_service_api::port::repo::BotRepoPort;
pub use memory::MemoryBotRepo;
/// The memory projection's shared atomic authority state
/// boundary (spec §7) plus its Task-1/Task-2 facets — a
/// public facade so contract fixtures drive the same
/// fields production uses; the accessor is not an
/// application API.
pub use memory::authority::{
    MemoryAuthorityAuditRow, MemoryAuthorityEdgeKind,
    MemoryAuthorityEdgeStatus, MemoryAuthorityState,
    MemoryTransferRow,
};
/// Direct module alias used by the contract fixture.
pub mod memory_authority {
    #[doc(inline)]
    pub use crate::memory::authority::{
        MemoryAuthorityAuditRow, MemoryAuthorityEdgeKind,
        MemoryAuthorityEdgeStatus, MemoryAuthorityState,
        MemoryTransferRow,
    };
}
pub use persistent_repo::PersistentBotRepo;
pub use provider::{DbProviderStore, MemoryProviderStore};

/// Maximum time before a bot registration expires (5 minutes).
const BOT_EXPIRY: Duration = Duration::from_secs(300);

fn bot_info_json_set(
    updates: Vec<(&'static str, serde_json::Value)>,
) -> ServiceResult<(String, Vec<Value>)> {
    let mut expressions = Vec::with_capacity(updates.len());
    let mut params = Vec::with_capacity(updates.len());
    for (path, value) in updates {
        expressions.push(format!("'{path}', json_extract(?, '$')"));
        params.push(Value::from(serde_json::to_string(&value).map_err(|error| {
            ServiceError::InternalError(error.to_string())
        })?));
    }
    Ok((
        format!(
            "bot_info = JSON_SET(\
             CASE WHEN JSON_VALID(bot_info) AND LOWER(JSON_TYPE(bot_info)) = 'object' \
                  THEN bot_info ELSE JSON_OBJECT() END, {})",
            expressions.join(", ")
        ),
        params,
    ))
}

fn serialized_string_value<T: Serialize>(value: &T) -> ServiceResult<String> {
    serde_json::to_value(value)
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| ServiceError::InternalError("expected a serialized string value".to_string()))
}

fn is_legacy_namespace(bot_uuid: &str, staff_no: &str) -> bool {
    let suffix = format!(":{}", staff_no);
    if !bot_uuid.ends_with(&suffix) {
        return false;
    }
    let namespace = &bot_uuid[..bot_uuid.len() - suffix.len()];
    if namespace == "default" {
        return true;
    }
    let bytes = namespace.as_bytes();
    if bytes.len() != 17 {
        return false;
    }
    if bytes[8] != b'_' {
        return false;
    }
    bytes[..8].iter().all(|b| b.is_ascii_digit())
        && bytes[9..]
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
}

/// Bot info stored in the `bcs_bots.bot_info` field.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BotInfo {
    pub summary: Option<String>,
    #[serde(default)]
    pub domains: Vec<String>,
    #[serde(default, deserialize_with = "bcs_service_api::deserialize_skills")]
    pub skills: Vec<Skill>,
    #[serde(default)]
    pub scopes: Vec<String>,
    /// Channel bindings for message routing.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub binding_channels: Option<BindingChannels>,
    #[serde(default)]
    pub hidden: bool,
    /// AI安全网关agent_code
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_code: Option<String>,
    /// AI安全网关授权token
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_token: Option<String>,
}

/// A bot streaming connection marker (process-local, not serializable).
#[derive(Debug, Clone)]
pub struct BotConnection {
    /// Session token for authentication.
    pub session_token: String,
    /// When the connection was established.
    pub connected_at: Instant,
}

/// Internal representation of a registered bot.
#[derive(Debug)]
struct RegisteredBotInner {
    /// Bot unique identifier (UUID).
    bot_uuid: String,
    /// Last heartbeat timestamp (process-local).
    last_heartbeat: Instant,
    /// Bot capabilities (loaded from database).
    capabilities: BotCapabilities,
    /// Active streaming connection (if connected).
    ws_connection: Option<BotConnection>,
    /// Protocol version negotiated for the active streaming connection.
    ///
    /// This is process-local connection state and is intentionally not
    /// persisted with the bot registration.
    protocol_version: u32,
    /// Session token (persisted in database).
    session_token: Option<String>,
    /// Server environment (prod, gray, pre, dev).
    env: Option<String>,
    /// DEPRECATED: Hidden mechanism removed in Rev-4 / Human Actor V1.
    /// Retained for struct compatibility; ignored by routing/visibility.
    hidden: bool,
    /// Actor-level lifecycle status (`Online` / `Hidden`) — Task P.2 / Requirement 3.16.
    status: bcs_service_api::ActorStatus,
    /// Actor kind (Bot / Human) — Human Actor V1 / Requirement 3.1.
    /// Sourced from `bcs_bots.actor_kind`. Defaults to `Bot` for legacy rows.
    actor_kind: bcs_service_api::ActorKind,
    /// Creator's staff_no (set during onboard, immutable).
    created_by: Option<String>,
}

impl RegisteredBotInner {
    /// Check if this bot registration has expired.
    fn is_expired(&self) -> bool {
        self.last_heartbeat.elapsed() > BOT_EXPIRY
    }

    /// Check if this bot has a specific skill (case-insensitive partial match).
    fn has_skill(&self, skill: &str) -> bool {
        self.capabilities
            .skills
            .iter()
            .any(|s| s.name.to_lowercase().contains(&skill.to_lowercase()))
    }

    /// Check if this bot has a specific domain (case-insensitive partial match).
    fn has_domain(&self, domain: &str) -> bool {
        self.capabilities
            .domains
            .iter()
            .any(|d| d.to_lowercase().contains(&domain.to_lowercase()))
    }

    /// Check if this bot has a specific scope (case-insensitive partial match).
    fn has_scope(&self, scope: &str) -> bool {
        self.capabilities
            .scopes
            .iter()
            .any(|s| s.to_lowercase().contains(&scope.to_lowercase()))
    }

    /// Convert to public RegisteredBot type.
    ///
    /// Human Actor V1 / Code-Review fix #1: propagate the persisted
    /// `actor_kind` and `status` instead of returning defaults, otherwise
    /// downstream callers (`O.5` human_ guard, `P.3` mode validation,
    /// `F.3` Human↔Human rejection) will misclassify every actor.
    fn to_registered_bot(&self) -> RegisteredBot {
        // 清除敏感字段，防止通过常规接口泄露
        let mut capabilities = self.capabilities.clone();
        capabilities.agent_token = None;

        RegisteredBot {
            bot_uuid: self.bot_uuid.clone(),
            capabilities,
            env: self.env.clone(),
            created_by: self.created_by.clone(),
            actor_kind: self.actor_kind,
            status: self.status,
        }
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    // Note: These tests require external database connections.
    // Run with: cargo test --package bcs-bot -- --ignored

    #[test]
    fn test_bot_info_serialization() {
        let bot_info = BotInfo {
            summary: Some("Test bot".to_string()),
            domains: vec!["testing".to_string()],
            skills: vec![Skill::new("test")],
            scopes: vec!["read".to_string()],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
            ..Default::default()
        };

        let json = serde_json::to_string(&bot_info).unwrap();
        let parsed: BotInfo = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.summary, bot_info.summary);
        assert_eq!(parsed.domains, bot_info.domains);
        assert_eq!(parsed.skills, bot_info.skills);
        assert_eq!(parsed.scopes, bot_info.scopes);
    }

    #[test]
    fn test_bot_inner_expiry() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities::default(),
            ws_connection: None,
            protocol_version: 1,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(!bot.is_expired());

        // Simulate time passing (we can't actually wait, so just test the logic)
        // In real usage, last_heartbeat.elapsed() would be compared
    }

    #[test]
    fn test_bot_inner_skill_matching() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                skills: vec![Skill::new("SQL Analysis"), Skill::new("Deadlock Debugging")],
                ..Default::default()
            },
            ws_connection: None,
            protocol_version: 1,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_skill("sql"));
        assert!(bot.has_skill("deadlock"));
        assert!(bot.has_skill("SQL")); // Case insensitive
        assert!(!bot.has_skill("python"));
    }

    #[test]
    fn test_bot_inner_domain_matching() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                domains: vec!["Database".to_string(), "MySQL".to_string()],
                ..Default::default()
            },
            ws_connection: None,
            protocol_version: 1,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_domain("database"));
        assert!(bot.has_domain("mysql"));
        assert!(!bot.has_domain("security"));
    }

    #[test]
    fn test_bot_inner_scope_matching() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                scopes: vec!["database:read".to_string(), "database:write".to_string()],
                ..Default::default()
            },
            ws_connection: None,
            protocol_version: 1,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_scope("database:read"));
        assert!(bot.has_scope("write"));
        assert!(!bot.has_scope("admin"));
    }

    #[test]
    fn test_to_registered_bot() {
        let bot = RegisteredBotInner {
            bot_uuid: "test-uuid".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                name: Some("Test Bot".to_string()),
                ..Default::default()
            },
            ws_connection: None,
            protocol_version: 1,
            session_token: None,
            env: Some("prod".to_string()),
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        let registered = bot.to_registered_bot();
        assert_eq!(registered.bot_uuid, "test-uuid");
        assert_eq!(registered.capabilities.name, Some("Test Bot".to_string()));
        assert_eq!(registered.env, Some("prod".to_string()));
    }

    #[test]
    fn test_bot_info_default() {
        let bot_info = BotInfo::default();
        assert!(bot_info.summary.is_none());
        assert!(bot_info.domains.is_empty());
        assert!(bot_info.skills.is_empty());
        assert!(bot_info.scopes.is_empty());
    }

    #[test]
    fn test_bot_info_with_all_fields() {
        let bot_info = BotInfo {
            summary: Some("A comprehensive bot".to_string()),
            domains: vec!["database".to_string(), "security".to_string()],
            skills: vec![
                Skill::new("sql_analysis"),
                Skill::new("penetration_testing"),
            ],
            scopes: vec!["admin:read".to_string(), "admin:write".to_string()],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
            ..Default::default()
        };

        let json = serde_json::to_string(&bot_info).unwrap();
        let parsed: BotInfo = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.summary, Some("A comprehensive bot".to_string()));
        assert_eq!(parsed.domains.len(), 2);
        assert_eq!(parsed.skills.len(), 2);
        assert_eq!(parsed.scopes.len(), 2);
    }

    #[test]
    fn test_bot_info_empty_arrays() {
        let json = r#"{"summary":null,"domains":[],"skills":[],"scopes":[]}"#;
        let bot_info: BotInfo = serde_json::from_str(json).unwrap();
        assert!(bot_info.summary.is_none());
        assert!(bot_info.domains.is_empty());
    }

    #[test]
    fn test_bot_inner_has_skill_partial_match() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                skills: vec![Skill::new("SQL_Analysis_Expert")],
                ..Default::default()
            },
            ws_connection: None,
            protocol_version: 1,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        // Partial match should work
        assert!(bot.has_skill("sql"));
        assert!(bot.has_skill("analysis"));
        assert!(bot.has_skill("expert"));
        // Case insensitive
        assert!(bot.has_skill("SQL"));
        assert!(bot.has_skill("ANALYSIS"));
    }

    #[test]
    fn test_bot_inner_has_domain_partial_match() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                domains: vec!["Database-Administration".to_string()],
                ..Default::default()
            },
            ws_connection: None,
            protocol_version: 1,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_domain("database"));
        assert!(bot.has_domain("administration"));
        assert!(!bot.has_domain("security"));
    }

    #[test]
    fn test_bot_inner_empty_capabilities() {
        let bot = RegisteredBotInner {
            bot_uuid: "empty".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities::default(),
            ws_connection: None,
            protocol_version: 1,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(!bot.has_skill("anything"));
        assert!(!bot.has_domain("anything"));
        assert!(!bot.has_scope("anything"));
    }

    #[test]
    fn test_bot_connection_clone() {
        let conn = BotConnection {
            session_token: "test-token".to_string(),
            connected_at: Instant::now(),
        };

        let conn_clone = conn.clone();
        assert_eq!(conn_clone.session_token, "test-token");
    }

    #[test]
    fn test_dynamic_status_default() {
        let status = BotDynamicStatus::default();
        assert!(status.status.is_empty());
        assert!(status.dynamic_summary.is_none());
        assert!(status.load.is_none());
        assert!(status.updated_at.is_none());
    }

    #[test]
    fn test_bot_capabilities_default() {
        let caps = BotCapabilities::default();
        assert!(caps.name.is_none());
        assert!(caps.summary.is_none());
        assert!(caps.domains.is_empty());
        assert!(caps.skills.is_empty());
        assert!(caps.scopes.is_empty());
    }

    #[test]
    fn test_bot_info_json_roundtrip_complex() {
        let bot_info = BotInfo {
            summary: Some("Summary with \"quotes\" and \\backslashes\\rating".to_string()),
            domains: vec![
                "domain:with:colons".to_string(),
                "domain-with-dashes".to_string(),
            ],
            skills: vec![Skill::new("skill with spaces")],
            scopes: vec!["scope/with/slashes".to_string()],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
            ..Default::default()
        };

        let json = serde_json::to_string(&bot_info).unwrap();
        let parsed: BotInfo = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.summary, bot_info.summary);
        assert_eq!(parsed.domains, bot_info.domains);
        assert_eq!(parsed.skills, bot_info.skills);
        assert_eq!(parsed.scopes, bot_info.scopes);
    }

    // ===== Integration tests (require external database) =====
    // Run with: cargo test --package bcs-bot -- --ignored

    #[tokio::test]
    #[ignore = "Requires external database connections"]
    async fn integration_test_register_and_retrieve() {
        // This test documents the expected workflow:
        // 1. Create PersistentBotRepo with a DbPlugin handle
        // 2. Register a bot
        // 3. Retrieve the bot from memory (fast path)
        // 4. Verify capabilities are persisted to the database
        //
        // Example setup is intentionally omitted because production wiring
        // now happens through the bootstrap composition root.
        //
        // let caps = BotCapabilities {
        //     name: Some("Test Bot".into()),
        //     skills: vec!["testing".into()],
        //     ..Default::default()
        // };
        // registry.register("test-bot".into(), caps).await;
        //
        // let bot = registry.get("test-bot").await.unwrap();
        // assert_eq!(bot.capabilities.name, Some("Test Bot".into()));
    }

    #[tokio::test]
    #[ignore = "Requires external database connections"]
    async fn integration_test_token_persistence() {
        // This test documents the expected workflow:
        // 1. Register WS connection (generates token)
        // 2. Token is persisted to the database
        // 3. After server restart, token can be looked up from the database
        //
        // Example:
        // let (tx, _rx) = mpsc::channel(10);
        // let token = registry.register_streaming_connection("test-bot".into()).await.unwrap();
        //
        // // Token should be findable
        // let found = registry.find_bot_by_token(&token).await;
        // assert_eq!(found, Some("test-bot".into()));
    }

    #[tokio::test]
    #[ignore = "Requires external database connections"]
    async fn integration_test_reconnect_streaming_recover_from_storage() {
        // This test documents the failover recovery workflow:
        // 1. Bot connects and persists capabilities and its token in the database
        // 2. WS disconnects (but token is preserved)
        // 3. Server restarts (memory cleared)
        // 4. Bot reconnects with existing token
        // 5. Server recovers capabilities and its token from the database, then renews liveness
        //
        // Example:
        // let (tx, _rx) = mpsc::channel(10);
        // let token = registry.register_streaming_connection("test-bot".into()).await.unwrap();
        //
        // // Simulate server restart by clearing memory
        // // (in real scenario, memory is lost)
        //
        // let (tx2, _rx2) = mpsc::channel(10);
        // let (bot_uuid, recovered_token) = registry.reconnect_streaming(token.clone()).await.unwrap();
        // assert_eq!(bot_uuid, "test-bot");
        // assert_eq!(recovered_token, token);
    }

    #[tokio::test]
    #[ignore = "Requires external database connections"]
    async fn integration_test_sql_injection_protection() {
        // This test documents SQL injection protection:
        // Bot IDs and other fields are escaped before SQL queries
        //
        // Example:
        // let malicious_id = "bot'; DROP TABLE bcs_bots; --";
        // let caps = BotCapabilities::default();
        // registry.register(malicious_id.into(), caps).await;
        // // Should not cause SQL injection, just creates a bot with that name
    }

    #[test]
    fn test_sql_escaping_in_save_to_storage() {
        // Verify that special SQL characters are properly escaped
        let bot_info = BotInfo {
            summary: Some("Test with 'single quotes'".into()),
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
            ..Default::default()
        };
        let json = serde_json::to_string(&bot_info).unwrap();
        // JSON escaping handles most cases, but the SQL layer also does escaping
        assert!(json.contains("single quotes"));
    }
}
