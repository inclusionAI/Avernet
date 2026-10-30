//! Session repository implementations: memory + mysql.

pub mod authorization_snapshot;
pub mod registry;
pub mod memory;
pub mod mysql;
pub mod memory_speaking_lease;
pub mod sql_speaking_lease;

pub use memory::MemorySessionRepo;
pub use mysql::MySqlSessionStore;
pub use memory_speaking_lease::MemorySpeakingLeaseRepo;
pub use sql_speaking_lease::SqlSpeakingLeaseRepo;
