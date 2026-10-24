//! Pure SQL step builders for the Bot lifecycle registration
//! composites (bot-authority plan Task 4, spec §10.2).
//!
//! This module builds `DbStatement`s ONLY — it never touches a
//! plugin, never runs query/execute and never keeps state. The
//! calling stores (`bcs-bot-store`'s Bot/Provider composites) own
//! the transaction: they splice these statements together with
//! their own Bot-row, binding and guard steps and run them as one
//! `DbPlugin::transaction_with_options`, so the whole registration
//! (Bot, Human actor, owner edge, ownership version, default
//! runtime profile, initialization audit) commits or rolls back
//! atomically. There is deliberately NO runtime grant logic here
//! and no dependency upward from this crate to any Bot store.
//!
//! All identity comparisons stay byte-exact through the same
//! `CAST(... AS BINARY)` pattern the authority files use on MySQL.

use bcs_db_api::{DbSqlFlavor, DbStatement, DbValue};

use super::audit::CLOSING_SYSTEM_ACTOR;

/// Audit action for the one-shot lifecycle initialization written by
/// a trusted registration or a governance backfill (spec §4).
pub const ACTION_INITIALIZE: &str = "initialize";

/// The FIXED system decision marker for automatic
/// closures (spec §4.4) — exposed to the memory
/// projection so both drivers audit the same id.
pub fn system_transfer_close_actor() -> &'static str {
    CLOSING_SYSTEM_ACTOR
}

/// Audit sequence reserved for the single `initialize` row of one
/// mutation. Distinct rows of one mutation stay free for other
/// lifecycle verbs.
pub const INITIALIZE_SEQUENCE: u64 = 0;

/// Wildcard-allow rules JSON seeded as every Bot's default runtime
/// profile. MUST stay byte-identical with the SQL
/// `ensure_default_profile` seed (`DbPermissionProfileStore`,
/// D12 rule 2) so a registered Bot's profile and a later
/// `ensure_default_profile` share one record.
pub const TRUSTED_DEFAULT_RULES: &str =
    r#"[{"tool":"*","specifier":"*","effect":"allow"}]"#;

/// Profile name used for the seeded default profile.
pub const TRUSTED_DEFAULT_PROFILE_NAME: &str = "default";

/// Digest of [`TRUSTED_DEFAULT_RULES`] (identical hashing to the
/// SQL store's `ensure_default_profile`).
pub fn default_rules_digest() -> String {
    crate::sha256_hex(TRUSTED_DEFAULT_RULES)
}

/// Status JSON of the initialization audit row. Shared verbatim
/// with the memory projection so both drivers audit the same.
pub fn initialize_audit_state(
    owner_actor_id: &str,
    version: u64,
) -> String {
    format!("{{\"owner\":\"{owner_actor_id}\",\"version\":{version}}}")
}

fn insert_or_ignore(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => "INSERT IGNORE INTO",
        DbSqlFlavor::Sqlite => "INSERT OR IGNORE INTO",
    }
}

/// Byte-exact predicate on a TEXT column (`CAST AS BINARY`
/// keeps case-distinct principals distinct on MySQL).
fn binary_eq(flavor: DbSqlFlavor, column: &str) -> String {
    match flavor {
        DbSqlFlavor::Mysql => format!("CAST({column} AS BINARY) = ?"),
        DbSqlFlavor::Sqlite => format!("{column} = ?"),
    }
}

/// Approved-unitary ownership predicate, shared with the strict
/// authority reads: one approved owner edge per (env, bot).
fn approved_owner_predicate() -> &'static str {
    "grant_kind = 'owner' AND grant_ref_id = 0 AND rules IS NULL \
     AND status = 'approved'"
}

/// Ensure the Human ACTOR row (`bcs_bots`, `actor_kind = 'human'`)
/// for the registration owner. Idempotent: an existing row keeps
/// its name and fields; only the explicitly passed values are
/// inserted on first creation. This is the whole of
/// spec §10.2's "Human 校验/必要创建" — the actor row IS the
/// authority Human registry the Task-2/3 human-existence
/// guards read, so no OAuth identity row is created or
/// required any more.
///
/// Human verification itself is the register token
/// (issued only to Gateway-authenticated Humans); the row
/// is the in-transaction 必要创建 of spec §10.2 (ruling
/// T4-1) — never a fabricated external identity claim.
#[allow(clippy::too_many_arguments)]
pub fn ensure_human_actor_statement(
    flavor: DbSqlFlavor,
    env: &str,
    owner_user_id: &str,
    display_name: &str,
    session_token: &str,
) -> DbStatement {
    let human_id = format!("human_{owner_user_id}");
    DbStatement::with_params(
        &format!(
            "{} bcs_bots \
             (bot_uuid, actor_kind, name, bot_info, session_token, \
              created_by, visibility, status, env, registered_at, updated_at) \
             VALUES (?, 'human', ?, ?, ?, ?, 'protected', 'online', ?, \
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            insert_or_ignore(flavor)
        ),
        vec![
            DbValue::from(human_id.as_str()),
            DbValue::from(display_name),
            DbValue::from(r#"{"summary":null}"#),
            DbValue::from(session_token),
            DbValue::from(owner_user_id),
            DbValue::from(env),
        ],
    )
}

/// Insert the approved owner edge
/// `grant_ref_id = 0`, `rules = NULL` — Task-1 schema; the same
/// spine the transfer-accept flow writes). Idempotent: a repeated
/// identity insert is ignored; a DIFFERENT approved owner stays
/// untouched and the unique approved-owner key rejects a
/// conflicting one.
pub fn owner_edge_statement(
    flavor: DbSqlFlavor,
    env: &str,
    owner_actor_id: &str,
    bot_id: &str,
) -> DbStatement {
    match flavor {
        DbSqlFlavor::Mysql => DbStatement::with_params(
            "INSERT IGNORE INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
              status, originator_policy_type) \
             VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from')",
            vec![
                DbValue::from(env),
                DbValue::from(owner_actor_id),
                DbValue::from(bot_id),
            ],
        ),
        DbSqlFlavor::Sqlite => DbStatement::with_params(
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
              status, originator_policy_type) \
             VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from') \
             ON CONFLICT(from_id, to_id, env, grant_kind, grant_ref_id) DO NOTHING",
            vec![
                DbValue::from(env),
                DbValue::from(owner_actor_id),
                DbValue::from(bot_id),
            ],
        ),
    }
}

/// Guard: exactly ONE approved owner edge exists for the Bot
/// inside the transaction (After the owner insert this must count 1).
pub fn owner_edge_guard_statement(
    flavor: DbSqlFlavor,
    env: &str,
    bot_id: &str,
) -> DbStatement {
    DbStatement::with_params(
        &format!(
            "SELECT COUNT(*) AS c FROM edge_grants \
             WHERE env = ? AND {} AND {}",
            binary_eq(flavor, "to_id"),
            approved_owner_predicate()
        ),
        vec![DbValue::from(env), DbValue::from(bot_id)],
    )
}

/// Seed the default runtime profile (wildcard-allow, revision 1).
/// Idempotent on the active-default unique rule (D12 rule 2): a
/// pre-existing default keeps its revision/digest/rules.
pub fn default_profile_statement(
    flavor: DbSqlFlavor,
    bot_id: &str,
    env: &str,
) -> DbStatement {
    DbStatement::with_params(
        &format!(
            "{} permission_profiles \
             (bot_id, env, name, description, rules_template, \
              revision, digest, is_default, status, created_by, updated_by) \
             VALUES (?, ?, ?, NULL, ?, 1, ?, 1, 'active', 'system', NULL)",
            insert_or_ignore(flavor)
        ),
        vec![
            DbValue::from(bot_id),
            DbValue::from(env),
            DbValue::from(TRUSTED_DEFAULT_PROFILE_NAME),
            DbValue::from(TRUSTED_DEFAULT_RULES),
            DbValue::from(default_rules_digest()),
        ],
    )
}

/// Guard: the active default profile row exists for the Bot.
pub fn default_profile_guard_statement(
    flavor: DbSqlFlavor,
    bot_id: &str,
    env: &str,
) -> DbStatement {
    DbStatement::with_params(
        &format!(
            "SELECT id FROM permission_profiles \
             WHERE bot_id = ? AND env = ? \
               AND is_default = 1 AND status = 'active' LIMIT 1{}",
            match flavor {
                // MySQL returns the row under the transaction's
                // ownership; SQLite is single-writer. No lock suffix
                // needed — the profile insert above is in-tx.
                DbSqlFlavor::Mysql => " FOR UPDATE",
                DbSqlFlavor::Sqlite => "",
            }
        ),
        vec![DbValue::from(bot_id), DbValue::from(env)],
    )
}

/// One `initialize` audit row (spec §4: actor = the real initiator;
/// tokens/credentials never enter audit rows).
#[allow(clippy::too_many_arguments)]
pub fn initialize_audit_statement(
    env: &str,
    bot_id: &str,
    mutation_id: &str,
    actor_kind: &str,
    actor_id: &str,
    owner_actor_id: &str,
    version: u64,
) -> DbStatement {
    let after_state = initialize_audit_state(owner_actor_id, version);
    DbStatement::with_params(
        "INSERT INTO bot_authority_changes \
         (env, bot_id, mutation_id, sequence, action, actor_kind, \
          actor_id, subject_id, before_state, after_state) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
        vec![
            DbValue::from(env),
            DbValue::from(bot_id),
            DbValue::from(mutation_id),
            DbValue::from(INITIALIZE_SEQUENCE),
            DbValue::from(ACTION_INITIALIZE),
            DbValue::from(actor_kind),
            DbValue::from(actor_id),
            DbValue::from(owner_actor_id),
            DbValue::from(after_state.as_str()),
        ],
    )
}

/// Guard: the `initialize` audit row committed with this mutation.
pub fn initialize_audit_guard_statement(
    env: &str,
    bot_id: &str,
    mutation_id: &str,
) -> DbStatement {
    DbStatement::with_params(
        "SELECT sequence FROM bot_authority_changes \
         WHERE env = ? AND bot_id = ? AND mutation_id = ? AND sequence = ?",
        vec![
            DbValue::from(env),
            DbValue::from(bot_id),
            DbValue::from(mutation_id),
            DbValue::from(INITIALIZE_SEQUENCE),
        ],
    )
}

// ---------------------------------------------------------------------------
// Retirement (bot-authority spec §7.1, §3.2.7, §4.4): pure step
// builders for the atomic retire composites. The calling
// stores own the transaction — Bot tombstone, necessary
// binding disable, owner/manager edge revocation, pending
// transfer closure (fixed system marker and reason), and
// the `retire` audit commit together or not at all.
// ---------------------------------------------------------------------------

/// Audit action for the one atomic retirement commit
/// (spec §4.4: Bot 退役及其权限清理). The audit
/// identity is the REAL initiator (Human/Provider/
/// System); tokens never enter.
pub const ACTION_RETIRE: &str = "retire";

/// after_state JSON of the one `retire` audit row,
/// shared verbatim by the SQL and memory drivers.
pub fn retire_audit_state() -> &'static str {
    "{\"status\":\"retired\"}"
}

/// Fixed system decision reason used when a retirement
/// closes pending transfers (spec §4.4: 自动决议固定
/// system 原因; the retired Bot itself is the reason).
pub const REASON_BOT_RETIRED: &str = "bot_retired";

/// Tombstone the live Bot row (`is_deleted 0 → 1`).
/// `ExecuteChecked(1)` in the composite: a zero-row
/// UPDATE fails the transaction, so an already-tombstoned
/// Bot takes the caller's idempotent (changed = false)
/// path instead.
pub fn tombstone_bot_statement(
    env: &str,
    bot_id: &str,
) -> DbStatement {
    DbStatement::with_params(
        "UPDATE bcs_bots SET is_deleted = 1, \
         updated_at = CURRENT_TIMESTAMP \
         WHERE bot_uuid = ? AND env = ? AND is_deleted = 0",
        vec![DbValue::from(bot_id), DbValue::from(env)],
    )
}

/// Revoke every approved owner/manager authority edge of
/// the Bot (spec §3.2.7: Bot 删除时同事务撤销 owner/
/// manager). Idempotent: MySQL reports zero changed rows
/// for already-revoked edges, so the composite runs this
/// as a plain Execute and relies on the final guard set
/// instead of affected rows.
pub fn revoke_authority_edges_statement(
    env: &str,
    bot_id: &str,
) -> DbStatement {
    DbStatement::with_params(
        "UPDATE edge_grants \
         SET status = 'revoked', gmt_modified = CURRENT_TIMESTAMP \
         WHERE env = ? AND to_id = ? \
         AND grant_kind IN ('owner', 'manager') AND grant_ref_id = 0 \
         AND rules IS NULL AND status = 'approved'",
        vec![DbValue::from(env), DbValue::from(bot_id)],
    )
}

/// Close every VALID pending transfer of the Bot with the
/// fixed system marker and the retirement reason (spec
/// §4.4). Only pendings that are still valid
/// (`expires_at > now`) change; an already-expired row
/// keeps its projection. Idempotent for the same reason
/// as the edge revocation.
pub fn close_pending_transfers_statement(
    env: &str,
    bot_id: &str,
) -> DbStatement {
    DbStatement::with_params(
        "UPDATE bot_ownership_transfers \
         SET status = 'invalidated', decided_at = CURRENT_TIMESTAMP, \
             decided_by_kind = 'system', \
             decided_by_id = ?, reason = ?, \
             updated_at = CURRENT_TIMESTAMP, \
             gmt_modified = CURRENT_TIMESTAMP \
         WHERE env = ? AND bot_id = ? \
           AND status = 'pending' AND expires_at > CURRENT_TIMESTAMP",
        vec![
            DbValue::from(CLOSING_SYSTEM_ACTOR),
            DbValue::from(REASON_BOT_RETIRED),
            DbValue::from(env),
            DbValue::from(bot_id),
        ],
    )
}

/// before/after state JSON of one retirement pending
/// closure audit — shared verbatim by the SQL
/// statement below and the memory projection.
pub fn retirement_closure_states(
    from_owner_actor_id: &str,
    expected_version: u64,
) -> (String, String) {
    (
        format!(
            "{{\"status\":\"pending\",\"from_owner\":\"\
             {from_owner_actor_id}\",\"version\":\
             {expected_version}}}"
        ),
        format!(
            "{{\"status\":\"invalidated\",\"from_owner\":\"\
             {from_owner_actor_id}\",\"version\":\
             {expected_version}}}"
        ),
    )
}

/// One `transfer_invalidate` audit row for a pending the
/// retirement closed: the fixed System decision marker,
/// the pending pre-state and the invalidated after-state
/// (mirrors the Task-3 closure audit shapes).
#[allow(clippy::too_many_arguments)]
pub fn retirement_transfer_invalidate_audit_statement(
    env: &str,
    bot_id: &str,
    mutation_id: &str,
    sequence: u64,
    target_owner_actor_id: &str,
    from_owner_actor_id: &str,
    expected_version: u64,
) -> DbStatement {
    let (before_state, after_state) =
        retirement_closure_states(
            from_owner_actor_id,
            expected_version,
        );
    DbStatement::with_params(
        "INSERT INTO bot_authority_changes \
         (env, bot_id, mutation_id, sequence, action, actor_kind, \
          actor_id, subject_id, before_state, after_state) \
         VALUES (?, ?, ?, ?, 'transfer_invalidate', 'system', ?, ?, ?, ?)",
        vec![
            DbValue::from(env),
            DbValue::from(bot_id),
            DbValue::from(mutation_id),
            DbValue::from(sequence),
            DbValue::from(CLOSING_SYSTEM_ACTOR),
            DbValue::from(target_owner_actor_id),
            DbValue::from(before_state.as_str()),
            DbValue::from(after_state.as_str()),
        ],
    )
}

/// The one atomic `retire` audit row: the real mutation
/// actor (Human admin or verified Provider) is the
/// initiator; the retired Bot is the subject. No
/// credential ever enters the row.
pub fn retire_audit_statement(
    env: &str,
    bot_id: &str,
    mutation_id: &str,
    actor_kind: &str,
    actor_id: &str,
) -> DbStatement {
    DbStatement::with_params(
        "INSERT INTO bot_authority_changes \
         (env, bot_id, mutation_id, sequence, action, actor_kind, \
          actor_id, subject_id, before_state, after_state) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
        vec![
            DbValue::from(env),
            DbValue::from(bot_id),
            DbValue::from(mutation_id),
            DbValue::from(INITIALIZE_SEQUENCE),
            DbValue::from(ACTION_RETIRE),
            DbValue::from(actor_kind),
            DbValue::from(actor_id),
            DbValue::from(bot_id),
            DbValue::from(retire_audit_state()),
        ],
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The digest must stay stable so memory/SQL profile parity
    /// claims stay honest (A17).
    #[test]
    fn default_rules_digest_is_sha256_of_literal() {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        hasher.update(TRUSTED_DEFAULT_RULES.as_bytes());
        let expected = format!("{:x}", hasher.finalize());
        assert_eq!(default_rules_digest(), expected);
    }

    #[test]
    fn audit_state_string_is_stable() {
        assert_eq!(
            initialize_audit_state("human_alice", 1),
            "{\"owner\":\"human_alice\",\"version\":1}"
        );
    }

    #[test]
    fn retirement_audit_values_are_stable() {
        // Shared verbatim with the memory driver: the
        // retire audit and its pending-closure reason
        // must not drift between lifecycle verbs.
        assert_eq!(ACTION_INITIALIZE, "initialize");
        assert_eq!(ACTION_RETIRE, "retire");
        assert_eq!(REASON_BOT_RETIRED, "bot_retired");
        assert_eq!(retire_audit_state(), "{\"status\":\"retired\"}");
    }
}
