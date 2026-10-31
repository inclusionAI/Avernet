//! Trusted-registration ownership composition (bot-authority
//! plan Task 4, spec §10.2).
//!
//! Shared SQL piece-builders for this crate's Bot and Provider
//! registration composites; each store runs its OWN
//! transaction, splicing these statements with its Bot-row
//! and binding steps so Bot + Human actor + owner edge +
//! ownership version + default runtime profile + the
//! `initialize` audit commit (or roll back) atomically. The
//! pure SQL step builders live in
//! `bcs-edge-permission-store::authority::lifecycle` (the
//! one-way dependency). The memory projection mirrors the
//! exact semantics in its registration module — same guard
//! set, same audit strings, same reconciliation.
//!
//! Registry rules encoded here for both composites:
//! - `owner_user_id` comes only from the verified
//!   registration token; the mutation must carry the same
//!   Human as the real initiator (fail-closed, tokens never
//!   reach audit rows);
//! - the Human ACTOR row (`bcs_bots`) is created when
//!   absent (ruling T4-1) — the actor row IS the authority
//!   Human registry the Task-2/3 guards read, so a
//!   fresh-Human registration leaves the owner manageable
//!   without touching the OAuth identity table;
//! - the Human-actor/owner/version/profile writes reuse
//!   existing chemistry (version 1 from creation; a reused
//!   Bot ID never resets a committed owner);
//! - failures surface as errors that carry no SQL text and
//!   no credentials.

use std::time::Duration;

use bcs_db_api::{
    DbSqlFlavor, DbStatement, DbTransactionOptions, DbTransactionParam,
    DbTransactionStep, DbValue,
};
use bcs_edge_permission_store::lifecycle;
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, MUTATION_ID_MAX_LEN,
    OwnershipInitialization,
};
use bcs_service_api::{ServiceError, ServiceResult};

/// Task-0 方案 A deadline for the registration transaction —
/// the same bounded-work bound the manager/transfer
/// transactions use.
pub(crate) const TX_DEADLINE: Duration = Duration::from_secs(10);

fn initialization_invalid(detail: String) -> ServiceError {
    ServiceError::InvalidOperation {
        message: format!("trusted bot registration invalid: {detail}"),
        request_id: None,
    }
}

/// Validate the trusted initialization and derive the audit
/// identity. Owner and mutation initiator must be the SAME
/// verified Human; nothing may carry a runtime token.
pub(crate) fn verified_initialization(
    initialization: &OwnershipInitialization,
) -> ServiceResult<(String, String)> {
    let owner = verified_owner(initialization)?;
    match &initialization.mutation.actor {
        AuthorityActor::Human { user_id } => {
            if user_id != owner {
                return Err(initialization_invalid(format!(
                    "registration mutation actor '{user_id}' does not \
                     match the verified registration owner '{owner}'"
                )));
            }
        }
        other => {
            return Err(initialization_invalid(format!(
                "registration mutation must carry the verified Human \
                 initiator, got {:?}",
                other.kind()
            )));
        }
    }
    validate_mutation(&initialization.mutation)?;
    Ok((owner.to_string(), format!("human_{owner}")))
}

fn verified_owner(initialization: &OwnershipInitialization) -> ServiceResult<&str> {
    let owner = initialization.owner_user_id.trim();
    if owner.is_empty() {
        return Err(initialization_invalid(
            "owner_user_id must not be blank".to_string(),
        ));
    }
    if owner != initialization.owner_user_id {
        return Err(initialization_invalid(
            "owner_user_id must not carry surrounding whitespace".to_string(),
        ));
    }
    if owner.starts_with("human_") {
        return Err(initialization_invalid(
            "owner_user_id must be the raw User ID, not a persisted \
             Human actor id"
                .to_string(),
        ));
    }
    Ok(owner)
}

/// Mutation ids are server-generated idempotence keys, never
/// client data (spec §3.1). The length bound is the
/// audit table's column width
/// ([`MUTATION_ID_MAX_LEN`]): a wider id would fail the
/// audit INSERT on real MySQL (ERROR 1406) only after
/// the fact.
pub(crate) fn validate_mutation(mutation: &AuthorityMutation) -> ServiceResult<()> {
    let mutation_id = mutation.mutation_id.trim();
    if mutation_id.is_empty() || mutation_id != mutation.mutation_id {
        return Err(initialization_invalid(
            "mutation_id must be a non-blank trimmed server value"
                .to_string(),
        ));
    }
    if mutation_id.len() > MUTATION_ID_MAX_LEN {
        return Err(initialization_invalid(
            "mutation_id is too long".to_string(),
        ));
    }
    Ok(())
}

/// The Human actor id for a verified owner User ID.
pub(crate) fn owner_actor_id(owner_user_id: &str) -> String {
    format!("human_{owner_user_id}")
}

/// The after-state JSON of the one `initialize` audit row —
/// shared verbatim by the SQL and memory drivers.
pub(crate) fn initialize_audit_state(
    owner_actor_id: &str,
    version: u64,
) -> String {
    lifecycle::initialize_audit_state(owner_actor_id, version)
}

/// One guard consumed by the final binding query: a guard
/// statement that returns ZERO rows fails the transaction
/// at final-binding resolution (Task-0 ruling T0-2), so a
/// wrong count never passes silently.
struct GuardClause {
    statement: DbTransactionStep,
}

/// A guard that passes iff the sub-select's `c` equals
/// `expected`. Row-table guards pass `with_lock = true` so
/// MySQL takes the row locks the composite must keep until
/// commit (the edge table), mirroring the manager paths.
fn count_guard(
    flavor: DbSqlFlavor,
    count_sql: &str,
    params: Vec<DbValue>,
    expected: u64,
    with_lock: bool,
) -> GuardClause {
    let lock = if with_lock && flavor == DbSqlFlavor::Mysql {
        " FOR UPDATE"
    } else {
        ""
    };
    let expected_param = DbTransactionStep::Query(DbStatement::with_transaction_params(
        format!("SELECT t.c AS ok FROM ({count_sql}) t WHERE t.c = ?{lock}")
            .as_str(),
        {
            let mut guard_params: Vec<DbTransactionParam> =
                params.into_iter().map(DbTransactionParam::value).collect();
            guard_params.push(DbTransactionParam::value(DbValue::from(expected)));
            guard_params
        },
    ));
    GuardClause { statement: expected_param }
}

/// Ownership pieces shared by the Bot and Provider composites.
pub(crate) struct OwnershipSteps {
    /// Steps joining the caller's Bot-row step(s); ordered
    /// Human-actor first AFTER the caller's Bot row (Bot row
    /// lock precedes any Human lock — the managers' lock
    /// order).
    pub steps: Vec<DbTransactionStep>,
    /// Final guard-consumption statement; the caller appends
    /// it LAST, after its own Bot/binding guards.
    pub final_guard: DbTransactionStep,
}

/// Compose the Human-identity guard, Human actor create,
/// owner edge, default profile and `initialize` audit rows
/// for ONE registration transaction.
#[allow(clippy::too_many_arguments)]
pub(crate) fn ownership_authorization_steps(
    flavor: DbSqlFlavor,
    env: &str,
    bot_id: &str,
    initialization: &OwnershipInitialization,
    human_session_token: &str,
    // Absolute guard-index base: the count of caller-side
    // transaction steps that precede these ownership steps.
    steps_base: usize,
) -> ServiceResult<OwnershipSteps> {
    let (owner, owner_actor) = verified_initialization(initialization)?;
    let actor_id = owner_actor.clone();
    build_ownership_steps(flavor, env, bot_id, initialization, human_session_token,
        steps_base, (owner, owner_actor, "human", actor_id))
}

/// Separate Provider-admin admission; the Human-token operation stays strict.
#[allow(clippy::too_many_arguments)]
pub(crate) fn provider_ownership_authorization_steps(
    flavor: DbSqlFlavor, env: &str, bot_id: &str, provider_id: &str,
    initialization: &OwnershipInitialization, human_session_token: &str, steps_base: usize,
) -> ServiceResult<OwnershipSteps> {
    let owner = verified_owner(initialization)?.to_string();
    if provider_id.trim().is_empty() || !matches!(&initialization.mutation.actor,
        AuthorityActor::Provider {provider_id:actor} if actor == provider_id) {
        return Err(initialization_invalid("Provider registration actor does not match the verified Provider".into()));
    }
    validate_mutation(&initialization.mutation)?;
    let owner_actor = owner_actor_id(&owner);
    build_ownership_steps(flavor, env, bot_id, initialization, human_session_token,
        steps_base, (owner, owner_actor, "provider", format!("provider_{provider_id}")))
}

#[allow(clippy::too_many_arguments)]
fn build_ownership_steps(
    flavor: DbSqlFlavor, env: &str, bot_id: &str, initialization: &OwnershipInitialization,
    human_session_token: &str, steps_base: usize,
    (owner, owner_actor, actor_kind, actor_id): (String, String, &str, String),
) -> ServiceResult<OwnershipSteps> {
    let mut steps: Vec<DbTransactionStep> = Vec::new();
    // (absolute-ish step index, result column) pairs the
    // final guard statement must bind.
    let mut guard_indexes: Vec<(usize, &'static str)> = Vec::new();

    let guard_caller = |steps: &mut Vec<DbTransactionStep>,
                        guard_indexes: &mut Vec<(usize, &'static str)>,
                        clause: GuardClause| {
        steps.push(clause.statement);
        guard_indexes.push((steps.len() - 1, "ok"));
    };

    // (0) The Human ACTOR row (bcs_bots) — create when
    // absent (spec §10.2's necessary Human creation,
    // ruling T4-1). The actor row is the authority
    // Human registry the Task-2/3 manager/transfer
    // guards read, so it is registration's ONE
    // necessary Human write; no OAuth identity row
    // is created or required.
    steps.push(DbTransactionStep::Execute(
        lifecycle::ensure_human_actor_statement(
            flavor,
            env,
            &owner,
            &owner,
            human_session_token,
        ),
    ));

    // (0a) Owner-Human liveness guard: the INSERT IGNORE
    // above is inert when the actor row already exists,
    // which must not prove the row is a LIVE Human. A
    // tombstoned owner (or a row of any other actor kind)
    // fails the whole transaction instead of releasing a
    // Bot whose owner edge points at a dead Human.
    guard_caller(
        &mut steps,
        &mut guard_indexes,
        count_guard(
            flavor,
            &format!(
                "SELECT COUNT(*) AS c FROM bcs_bots \
                 WHERE env = ? AND {} AND actor_kind = 'human' \
                 AND COALESCE(is_deleted, 0) = 0",
                match flavor {
                    DbSqlFlavor::Mysql => "CAST(bot_uuid AS BINARY) = ?",
                    DbSqlFlavor::Sqlite => "bot_uuid = ?",
                }
            ),
            vec![DbValue::from(env), DbValue::from(owner_actor.as_str())],
            1,
            true,
        ),
    );

    // (1) Approved owner edge (unified grant_ref_id = 0),
    // insert-or-ignore, then guard exactly one approved
    // owner.
    steps.push(DbTransactionStep::Execute(
        lifecycle::owner_edge_statement(flavor, env, &owner_actor, bot_id),
    ));
    guard_caller(
        &mut steps,
        &mut guard_indexes,
        count_guard(
            flavor,
            &format!(
                "SELECT COUNT(*) AS c FROM edge_grants \
                 WHERE env = ? AND {} AND grant_kind = 'owner' \
                 AND grant_ref_id = 0 AND rules IS NULL \
                 AND status = 'approved'",
                match flavor {
                    DbSqlFlavor::Mysql => "CAST(to_id AS BINARY) = ?",
                    DbSqlFlavor::Sqlite => "to_id = ?",
                }
            ),
            vec![DbValue::from(env), DbValue::from(bot_id)],
            1,
            true,
        ),
    );

    // (2) Default runtime profile (wildcard, revision 1),
    // then guard the active default exists.
    steps.push(DbTransactionStep::Execute(
        lifecycle::default_profile_statement(flavor, bot_id, env),
    ));
    {
        // A zero-row profile lookup must fail the
        // transaction, so guard by count rather than a plain
        // SELECT: COUNT over the active default must be 1.
        let profile_sql = "SELECT COUNT(*) AS c FROM permission_profiles \
             WHERE bot_id = ? AND env = ? AND is_default = 1 \
             AND status = 'active'";
        guard_caller(
            &mut steps,
            &mut guard_indexes,
            count_guard(
                flavor,
                &profile_sql.to_string(),
                vec![DbValue::from(bot_id), DbValue::from(env)],
                1,
                false,
            ),
        );
    }

    // (3) The `initialize` audit row; a duplicate mutation id
    // (env, mutation, sequence) would violate the audit key
    // and fail the transaction deterministically.
    steps.push(DbTransactionStep::ExecuteChecked {
        statement: lifecycle::initialize_audit_statement(
            env,
            bot_id,
            &initialization.mutation.mutation_id,
            actor_kind,
            &actor_id,
            &owner_actor,
            1,
        ),
        expected_affected_rows: 1,
    });
    {
        // The audit guard projects `sequence` (any present
        // row proves the audit committed).
        steps.push(DbTransactionStep::Query(
            lifecycle::initialize_audit_guard_statement(
                env,
                bot_id,
                &initialization.mutation.mutation_id,
            ),
        ));
        guard_indexes
            .push((steps.len() - 1, "sequence"));
    }

    // Final guard-consumption statement binding every guard
    // row: a failed guard has no row to bind and the whole
    // transaction rolls back (Task-0 s1c).
    let mut final_sql = String::from("SELECT ");
    let mut final_params: Vec<DbTransactionParam> = Vec::new();
    // Absolute-indexed guard bindings.
    for (offset, (index, column)) in guard_indexes.iter().enumerate() {
        if offset > 0 {
            final_sql.push_str(", ");
        }
        final_sql.push_str(&format!("? AS t{offset}"));
        final_params.push(DbTransactionParam::query_result(
            index + steps_base,
            0,
            *column,
        ));
    }
    let final_guard = DbTransactionStep::Query(
        DbStatement::with_transaction_params(final_sql.as_str(), final_params),
    );

    Ok(OwnershipSteps {
        steps,
        final_guard,
    })
}

/// Reminder for composite callers: the transaction uses the
/// Task-0 bounded-work deadline and maps `Timeout` to a
/// NEVER-retried uncertain-outcome error (the registration may
/// or may not have committed; no credential may leave the
/// core on that path).
pub(crate) fn transaction_options() -> DbTransactionOptions {
    DbTransactionOptions::with_deadline(TX_DEADLINE)
}

/// Public face of the composite's input validation for
/// callers outside this module.
pub(crate) fn initialization_invalid_public(detail: &str) -> ServiceError {
    initialization_invalid(detail.to_string())
}
