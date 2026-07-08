//! Channel persistence stores.
//!
//! Repo traits come from `bcs-service-api::port::repo`; this crate provides
//! local memory/JSON implementations. DB-backed stores are added separately.

pub mod db;
pub mod memory;

pub use db::{
    ChannelSqlFlavor, DbChannelBindingStore, DbConversationSessionStore, DbImParticipantStore,
};
pub use memory::{
    MemoryChannelBindingRepo, MemoryConversationSessionRepo, MemoryImParticipantRepo,
};
