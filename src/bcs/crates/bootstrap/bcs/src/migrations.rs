//! SQLite schema initialization and local upgrade runner (facade).
//!
//! This file is the module root / facade. After the Task 1 split:
//! - [`registry`] owns the `SqliteMigration` struct, the versioned list
//!   (`SQLITE_VERSIONED_MIGRATIONS`), and the version/count/checksum helpers.
//! - [`report`] owns the runner (`run_sqlite_migrations`, the report types,
//!   and the apply/check helpers).
//! - [`repairs`] owns the idempotent schema repair / additive migration
//!   routines (`ensure_*`, `add_sqlite_*`, `migrate_sqlite_*`).
//! - [`baseline_identity`], [`baseline_collaboration`], and
//!   [`baseline_delivery`] hold the baseline DDL `&[&str]` groups, organized
//!   by domain so later tasks can edit them in isolation.
//!
//! The facade still publishes the original public API: `run_sqlite_migrations`,
//! `run_sqlite_migrations_with_report`, `check_sqlite_migrations`,
//! `run_sqlite_bootstrap_tables`, `run_sqlite_bootstrap_indexes`,
//! `run_sqlite_versioned_migrations`, `SqliteMigrationReport`,
//! `SqliteMigrationPlan`, `sqlite_target_version`, and
//! `sqlite_migration_count`.
//!
//! The DDL execution order is preserved by the master `SQLITE_DDL_STATEMENTS`
//! below — a list of baseline group references chained in the same order as
//! the original single-array constant. The runner `report`'s `for ddl in
//! super::SQLITE_DDL_STATEMENTS.iter().copied().flatten()` iterates each
//! statement one at a time, exactly as before.

#[path = "migrations/baseline_collaboration.rs"]
mod baseline_collaboration;
#[path = "migrations/baseline_delivery.rs"]
mod baseline_delivery;
#[path = "migrations/baseline_identity.rs"]
mod baseline_identity;
#[path = "migrations/registry.rs"]
mod registry;
#[path = "migrations/repairs.rs"]
mod repairs;
#[path = "migrations/report.rs"]
mod report;

#[allow(unused_imports)]
pub use registry::{sqlite_migration_count, sqlite_target_version};
#[allow(unused_imports)]
pub use report::{
    SqliteMigrationPlan, SqliteMigrationReport, check_sqlite_migrations,
    run_sqlite_bootstrap_indexes, run_sqlite_bootstrap_tables, run_sqlite_migrations,
    run_sqlite_migrations_with_report, run_sqlite_versioned_migrations,
};

/// Master DDL ordered list. Each entry is a `&[&str]` from one of the
/// baseline files; the runner iterates this in order so the schema evolves
/// through the same `CREATE` statements in the original sequence.
///
/// Note: this `&[&[&str]]` replaces the historical flat `&[&str]`; the
/// statements themselves are untouched, only regrouped by responsibility.
/// Runtime execution order is identical to the legacy single-array form.
const SQLITE_DDL_STATEMENTS: &[&[&str]] = &[
    baseline_identity::SCHEMA_MIGRATIONS,
    baseline_identity::BOTS,
    baseline_identity::FRIENDSHIPS,
    baseline_identity::FRIEND_REQUESTS,
    baseline_identity::INVITE_CODES,
    baseline_identity::ACTOR_RELATIONS,
    baseline_identity::PROVIDERS,
    baseline_identity::ORGANIZATIONS,
    baseline_identity::ORGANIZATION_MEMBERS,
    baseline_identity::PROVIDER_BOT_BINDINGS,
    baseline_delivery::CHANNEL_BINDINGS,
    baseline_delivery::CHANNEL_CONVERSATIONS,
    baseline_delivery::CHANNEL_IM_PARTICIPANTS,
    baseline_delivery::HUMAN_INPUT_IM_REQUESTS,
    baseline_identity::PROVIDER_CREDENTIALS,
    baseline_identity::USER_IDENTITIES,
    baseline_collaboration::GROUPS,
    baseline_collaboration::CHAT_RUNS,
    baseline_collaboration::GROUP_PARTICIPANTS,
    baseline_collaboration::GROUP_SESSIONS,
    baseline_collaboration::SESSION_PARTICIPANTS,
    baseline_delivery::MESSAGES,
    baseline_collaboration::COLLABORATION_DEFINITIONS,
    baseline_collaboration::COLLABORATION_DEFINITION_BLOBS,
    baseline_collaboration::COLLABORATION_EVENTS,
    baseline_collaboration::COLLABORATION_TEMPLATES,
    baseline_collaboration::COLLABORATION_TEMPLATE_CONTENTS,
    baseline_collaboration::COLLABORATION_TEMPLATE_TAGS,
    baseline_collaboration::STATE_MACHINE_RUNS,
    baseline_collaboration::STATE_MACHINE_NODE_RUNS,
    baseline_collaboration::STATE_MACHINE_DELIVERY_CORRELATIONS,
    baseline_collaboration::STATE_MACHINE_DEFINITION_SNAPSHOTS,
    baseline_collaboration::GROUP_RUNTIME_BINDINGS,
    baseline_identity::IDENTITY_LINKS,
    baseline_collaboration::SERVICE_GROUP_TEMPLATES,
    baseline_collaboration::SERVICE_GROUP_INSTANCES,
    baseline_delivery::SESSION_FILES,
    baseline_delivery::PUBLIC_EVENTING,
];

// Re-export items the inline test modules historically reached through
// `use super::*;` against the original single-file migrations module. The
// cfg(test) re-export keeps the behavior-preserving split from forcing
// edits inside every test file.
#[cfg(test)]
#[allow(unused_imports)]
use {
    bcs_db_api::{DbError, DbPlugin, DbResult, DbStatement, DbTransactionStep, DbValue, db_get_column},
    report::*,
    repairs::*,
    registry::*,
};

#[cfg(test)]
#[path = "migrations/tests_core.rs"]
mod tests_core;
#[cfg(test)]
#[path = "migrations/tests_eventing.rs"]
mod tests_eventing;
#[cfg(test)]
#[path = "migrations/tests_upgrade.rs"]
mod tests_upgrade;
