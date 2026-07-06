//! `bcs-relation` — social / relation graph core implementation for BCS.

pub mod core;

pub use bcs_relation_store::{DbRelationStore, MemoryRelationRepo, RelationSqlFlavor};
pub use core::{MemoryRelationStore, RelationCore, RelationStore};
