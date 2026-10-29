//! Centralized BCS contract test harnesses.
//!
//! Each public trait has a stable harness entry point. Concrete implementations
//! call these functions from `tests/conformance_*.rs`.

pub mod application;
pub mod bot_authority_contract;
pub mod bot_authority_core;
pub mod bot_authority_service;
pub mod bot_mine_contract;
pub mod bot_ownership_sync_contract;
pub mod bot_transfer_contract;
pub mod collaboration_draft;
pub mod delivery_admission;
pub mod session_authorization;
pub mod core;
pub mod interceptor;
pub mod lifecycle;
pub mod plugin;
pub mod port;
pub mod repo;
pub mod provider_registration_core;
pub mod bot_registration_create;
pub mod bot_registration_chain;
pub mod bot_provider;
pub mod provider_slug;
pub mod bot_self;
pub mod agent_bots;
pub mod group_human_notify;
