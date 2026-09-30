//! SQLite migration registry: struct, versioned list, and checksum.
//!
//! The registry is the source of truth for which versioned migrations exist
//! (and therefore which target version the runner applies). The runner and
//! checks in `report` consume this list; tests reach in here when they need
//! the canonical version of an entry's checksum.

use sha2::{Digest, Sha256};

#[derive(Debug, Clone, Copy)]
pub(super) struct SqliteMigration {
    pub(super) version: i64,
    pub(super) name: &'static str,
}

pub(super) const SQLITE_VERSIONED_MIGRATIONS: &[SqliteMigration] = &[
    SqliteMigration {
        version: 1,
        name: "init_schema",
    },
    SqliteMigration {
        version: 2,
        name: "channel_binding_audit_timestamps",
    },
    SqliteMigration {
        version: 3,
        name: "add_organizations",
    },
    SqliteMigration {
        version: 4,
        name: "add_session_collection",
    },
    SqliteMigration {
        version: 5,
        name: "add_session_collection_timestamp",
    },
    SqliteMigration {
        version: 6,
        name: "session_files",
    },
    SqliteMigration {
        version: 7,
        name: "human_input_output_metadata",
    },
    SqliteMigration {
        version: 8,
        name: "human_input_im_requests",
    },
    SqliteMigration {
        version: 9,
        name: "eventing",
    },
    SqliteMigration {
        version: 10,
        name: "eventing_plaintext_endpoint",
    },
    SqliteMigration {
        version: 11,
        name: "group_opening_message",
    },
    SqliteMigration {
        version: 12,
        name: "add_bot_task_modes",
    },
    SqliteMigration {
        version: 13,
        name: "edge_permission",
    },
    SqliteMigration {
        version: 14,
        name: "add_bot_internal_attributes",
    },
    SqliteMigration {
        version: 15,
        name: "group_participant_tags",
    },
    SqliteMigration {
        version: 16,
        name: "expand_session_ids",
    },
    SqliteMigration {
        version: 17,
        name: "session_callback_lease",
    },
    SqliteMigration {
        version: 18,
        name: "state_machine_rerun_lineage",
    },
    SqliteMigration {
        version: 19,
        name: "one_shot_opening_message_override",
    },
    SqliteMigration {
        version: 20,
        name: "invite_code_id",
    },
    SqliteMigration {
        version: 21,
        name: "human_participant_message_visibility",
    },
    SqliteMigration {
        version: 22,
        name: "message_deliveries",
    },
    SqliteMigration { version: 23, name: "message_delivery_policy" },
    SqliteMigration { version: 24, name: "delivery_worker_queries" },
    SqliteMigration { version: 25, name: "delivery_context_selection" },
    SqliteMigration { version: 26, name: "delivery_pending_abort" },
    SqliteMigration { version: 27, name: "run_reply_segments" },
    SqliteMigration { version: 28, name: "provider_bot_webhook" },
    SqliteMigration { version: 29, name: "fixed_loop_runtime" },
    SqliteMigration { version: 30, name: "bot_provider_storage" },
    SqliteMigration {
        version: 31,
        name: "group_human_mention_notify_mode",
    },
    // Auth-session versioning (this branch): renumbered from its draft 28
    // to 32 after dev merged provider_bot_webhook (28), fixed_loop_runtime
    // (29), bot_provider_storage (30) and group_human_mention_notify_mode
    // (31) first.
    SqliteMigration {
        version: 32,
        name: "auth_session_version",
    },
];

pub fn sqlite_target_version() -> i64 {
    SQLITE_VERSIONED_MIGRATIONS
        .last()
        .map(|migration| migration.version)
        .unwrap_or(0)
}

#[allow(dead_code)]
pub fn sqlite_migration_count() -> usize {
    SQLITE_VERSIONED_MIGRATIONS.len()
}

pub(super) fn sqlite_migration_checksum(migration: &SqliteMigration) -> String {
    let mut hasher = Sha256::new();
    hasher.update(migration.version.to_string().as_bytes());
    hasher.update(b"\n");
    hasher.update(migration.name.as_bytes());
    hasher.update(b"\n");
    hex::encode(hasher.finalize())
}
