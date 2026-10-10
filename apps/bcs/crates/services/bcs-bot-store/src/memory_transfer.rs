//! In-memory twin of the ownership transfer lanes (plan Task 8, spec
//! §10/§11.1/§9.1) — `bcs-edge-permission-store`'s SQL engines are the
//! canonical engines; this module mirrors them over the shared
//! `MemoryBotRepo` state boundary (the single `bots` + `authority`
//! critical section IS the per-Bot serialization).
//!
//! Semantics are the trait contract, verbatim:
//! - create: durable idempotency replay (`created = false`), current-owner
//!   validation, live-recipient/self-transfer/version guards, slot hygiene
//!   (time-lapsed pendings → `expired`; owner/version-mismatched pendings →
//!   `invalidated(owner_changed)`) and the +7-day DB-text deadline;
//! - decide: terminal-row retries re-derive the committed outcome from
//!   the persisted `terminal_reason` (owner_changed never degrades),
//!   deadline/mismatch materialization on the DB-text clock twin, and the
//!   all-or-nothing accept (owner edge swap + `ownership_transfer/<id>`
//!   manager source for the previous owner + recipient non-team source
//!   revoke with `team/*` preserved + version bump + accepted receipt);
//! - reads: party-visibility concealment, effective-expiry PROJECTION
//!   (never a write) and one-snapshot count/page.
//!
//! Time mirrors the SQL columns' TEXT shape ('YYYY-MM-DD HH:MM:SS', UTC),
//! parsed/formatted through the small civil-date helpers below so the
//! receipt projection stays byte-compatible with the SQL rows.

use std::time::{SystemTime, UNIX_EPOCH};

use bcs_service_api::port::repo::bot_authority::{human_actor_id, user_id_from_actor};
use bcs_service_api::types::error::{AuthorityError, TransferConflict};
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, CreateOwnershipTransfer, CreateTransferResult,
    ListOwnershipTransfers, OwnershipTransfer, OwnershipTransferPage, TransferListDirection,
};
use bcs_service_api::types::{AuditActor, TerminalReason, TransferAction, TransferStatus};
use bcs_service_api::{ServiceError, ServiceResult};

use super::memory_authority::{corrupt, MemoryOwnershipTransferRow, MemoryRoleEdgeRow};
use super::{MemoryBotRepo, resolve_env};

/// Milliseconds of one day; the create lane fixes `expires_at = now + 7`.
pub(crate) const DAY_MS: u64 = 86_400_000;

/// The transfer create window (spec §9.1: create time + 7 days).
const EXPIRY_WINDOW_DAYS: u64 = 7;

/// Fixed system decider recorded for deadline-lapsed materialization
/// (`expired` rows; `decided_at = expires_at` mirrors the logical time).
const EXPIRED_SYSTEM_MARKER: &str = "ownership-deadline";

/// Fixed system decider recorded for owner/version-mismatch invalidation
/// (`invalidated(owner_changed)`; spec §10.2/§11.2 use the same marker in
/// the SQL lane).
const VALIDATION_SYSTEM_MARKER: &str = "ownership-validation";

/// The listing page ceiling (transport contracts 1..100; the store clamps).
const TRANSFER_PAGE_LIMIT_CEILING: u64 = 100;

/// Bounded re-classification attempts of the memory decide twin: each
/// restart re-proves the CURRENT state on the shared critical section, so
/// a concurrent winner settles into its committed branch immediately.
const MAX_MEMORY_DECIDE_ATTEMPTS: usize = 3;

// ---------------------------------------------------------------------------
// DB-text timestamp twins ('YYYY-MM-DD HH:MM:SS', UTC)
// ---------------------------------------------------------------------------

pub(crate) fn now_epoch_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

fn epoch_ms_to_db_text(epoch_ms: u64) -> String {
    let secs = (epoch_ms / 1_000) as i64;
    let days = secs.div_euclid(86_400);
    let secs_of_day = secs.rem_euclid(86_400);
    // Howard Hinnant's civil_from_days (proleptic Gregorian, UTC).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 - doe / 36_524 + doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!(
        "{y:04}-{m:02}-{d:02} {:02}:{:02}:{:02}",
        secs_of_day / 3_600,
        (secs_of_day % 3_600) / 60,
        secs_of_day % 60
    )
}

pub(crate) fn now_db_text() -> String {
    epoch_ms_to_db_text(now_epoch_ms())
}

/// The create lane's deadline: DB-text now + `ms_ahead`.
pub(crate) fn deadline_db_text(ms_ahead: u64) -> String {
    epoch_ms_to_db_text(now_epoch_ms() + ms_ahead)
}

fn db_text_to_epoch_ms(text: &str) -> Option<u64> {
    let mut fields = [0i64; 6];
    let mut index = 0usize;
    for part in text.split(|c: char| !c.is_ascii_digit()) {
        if part.is_empty() {
            continue;
        }
        if index >= fields.len() {
            return None;
        }
        fields[index] = part.parse().ok()?;
        index += 1;
    }
    if index < fields.len() {
        return None;
    }
    let [year, month, day, hour, minute, second] = fields;
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    // days_from_civil, inverted counterpart of the formatter above.
    let y = if month <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = y.rem_euclid(400);
    let mp = if month > 2 { month - 3 } else { month + 9 };
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    let secs = days * 86_400 + hour * 3_600 + minute * 60 + second;
    (secs >= 0).then(|| (secs as u64) * 1_000)
}

// ---------------------------------------------------------------------------
// Shared row helpers
// ---------------------------------------------------------------------------

/// Strictly decode one persisted transfer row into its receipt,
/// PROJECTING effective expiry (§9.1): a physically-`pending` row whose
/// deadline already lapsed reads as `expired` with the system decider and
/// `decided_at = expires_at`. Nothing is written — the projection is
/// computed, and a later materialization keeps the same logical time.
fn receipt_of(row: &MemoryOwnershipTransferRow) -> ServiceResult<OwnershipTransfer> {
    let expires_at = db_text_to_epoch_ms(&row.expires_at)
        .ok_or_else(|| corrupt(&row.bot_id, &row.env, "undecodable expires_at text"))?;
    let stored_status = decode_status(&row.status).ok_or_else(|| {
        corrupt(&row.bot_id, &row.env, format!("unknown transfer status '{}'", row.status))
    })?;
    let terminal_reason = row
        .terminal_reason
        .as_deref()
        .map(|text| {
            decode_terminal_reason(text).ok_or_else(|| {
                corrupt(&row.bot_id, &row.env, format!("unknown terminal reason '{text}'"))
            })
        })
        .transpose()?;

    let mut status = stored_status;
    let mut decision_actor = decode_decision_actor(&row.decision_actor_kind, &row.decided_by)
        .ok_or_else(|| {
            corrupt(
                &row.bot_id,
                &row.env,
                "undecodable decision actor columns on a transfer row",
            )
        })?;
    // The schema's decision CHECK as a read: a terminal row carries BOTH
    // decider columns and a decided_at; a pending row carries none of
    // them. Anything else is corruption, never an implicit projection.
    let mut decided_at = match (&row.decided_at, &decision_actor) {
        (Some(text), Some(_)) => Some(db_text_to_epoch_ms(text).ok_or_else(|| {
            corrupt(&row.bot_id, &row.env, "undecodable decided_at text")
        })?),
        (None, None) => None,
        _ => {
            return Err(corrupt(
                &row.bot_id,
                &row.env,
                "inconsistent decision columns on a transfer row",
            ))
        }
    };
    if stored_status != TransferStatus::Pending && decision_actor.is_none() {
        return Err(corrupt(
            &row.bot_id,
            &row.env,
            "terminal transfer row without a decision actor",
        ));
    }
    if stored_status == TransferStatus::Pending && now_epoch_ms() >= expires_at {
        // Effective-expiry projection: the same logical decision the later
        // materialization would persist.
        status = TransferStatus::Expired;
        decision_actor = Some(AuditActor::System {
            name: EXPIRED_SYSTEM_MARKER.to_string(),
        });
        decided_at = Some(expires_at);
    }

    Ok(OwnershipTransfer {
        transfer_id: row.transfer_id.clone(),
        env: row.env.clone(),
        bot_id: row.bot_id.clone(),
        from_user_id: row.from_user_id.clone(),
        to_user_id: row.to_user_id.clone(),
        expected_owner_version: row.expected_owner_version,
        client_request_id: row.client_request_id.clone(),
        status,
        expires_at,
        decision_actor,
        decided_at,
        result_owner_version: row.result_owner_version,
        terminal_reason,
        bot_name_snapshot: row.bot_name_snapshot.clone(),
        gmt_create: db_text_to_epoch_ms(&row.gmt_create)
            .ok_or_else(|| corrupt(&row.bot_id, &row.env, "undecodable gmt_create text"))?,
        gmt_modified: db_text_to_epoch_ms(&row.gmt_modified)
            .ok_or_else(|| corrupt(&row.bot_id, &row.env, "undecodable gmt_modified text"))?,
    })
}

fn decode_status(text: &str) -> Option<TransferStatus> {
    match text {
        "pending" => Some(TransferStatus::Pending),
        "accepted" => Some(TransferStatus::Accepted),
        "rejected" => Some(TransferStatus::Rejected),
        "cancelled" => Some(TransferStatus::Cancelled),
        "expired" => Some(TransferStatus::Expired),
        "invalidated" => Some(TransferStatus::Invalidated),
        _ => None,
    }
}

fn decode_terminal_reason(text: &str) -> Option<TerminalReason> {
    match text {
        "bot_deleted" => Some(TerminalReason::BotDeleted),
        "actor_unavailable" => Some(TerminalReason::ActorUnavailable),
        "owner_changed" => Some(TerminalReason::OwnerChanged),
        _ => None,
    }
}

/// The schema's decision-column CHECK as a decoder: `None` requires BOTH
/// columns empty (pending), `Some` requires a kind and a non-empty decider.
fn decode_decision_actor(kind: &Option<String>, decided_by: &Option<String>) -> Option<Option<AuditActor>> {
    match (kind.as_deref(), decided_by.as_deref()) {
        (Some("human"), Some(user_id)) if !user_id.is_empty() => Some(Some(AuditActor::Human {
            user_id: user_id.to_string(),
        })),
        (Some("system"), Some(name)) if !name.is_empty() => Some(Some(AuditActor::System {
            name: name.to_string(),
        })),
        (None, None) => Some(None),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Seed lever (Task 8 test harness driver)
// ---------------------------------------------------------------------------

/// Seed one full-shape transfer row (test-only harness lever; production
/// rows come from `memory_create_transfer`).
pub(crate) async fn seed_pending_transfer_row(
    repo: &MemoryBotRepo,
    row: MemoryOwnershipTransferRow,
) -> ServiceResult<()> {
    if repo.take_authority_write_failure().await {
        return Err(ServiceError::InternalError(
            "test-injected authority write failure".into(),
        ));
    }
    let mut authority = repo.authority.write().await;
    authority.transfer_rows.push(row);
    Ok(())
}

// ---------------------------------------------------------------------------
// create_transfer
// ---------------------------------------------------------------------------

pub(crate) async fn memory_create_transfer(
    repo: &MemoryBotRepo,
    command: CreateOwnershipTransfer,
) -> ServiceResult<CreateTransferResult> {
    let env = resolve_env();
    fail_closed_create(&command)?;
    if repo.take_authority_write_failure().await {
        return Err(ServiceError::InternalError(
            "test-injected authority write failure".into(),
        ));
    }
    let to_id = human_actor_id(&command.to_user_id);

    // Liveness pre-checks before the long critical section (deletion lane
    // orders deleted → bots → authority; reading the set first can never
    // deadlock with us).
    if repo.deleted_bot_ids.read().await.contains(&command.bot_id)
        || !repo.bots.read().await.contains_key(&command.bot_id)
    {
        return Err(ServiceError::BotNotFound(command.bot_id.clone()));
    }

    let bots = repo.bots.write().await;
    let mut authority = repo.authority.write().await;

    // In-section liveness re-read (the bots guard is already in hand, so
    // this stays inside the section's acquisition order): a retirement that
    // committed while we waited for the lock removed the row — answer
    // BotNotFound here, never the zero-owner CorruptAuthority the emptied
    // role rows would otherwise produce.
    if !bots.contains_key(&command.bot_id) {
        return Err(ServiceError::BotNotFound(command.bot_id.clone()));
    }

    // -- Idempotency replay never re-executes and never re-requires the
    //    caller's current ownership (spec §10.1 step 1). ------------------
    if let Some(existing) = authority.transfer_rows.iter().find(|row| {
        row.env == env
            && row.bot_id == command.bot_id
            && row.from_user_id == command.actor_user_id
            && row.client_request_id == command.client_request_id
    }) {
        let payload_matches = existing.to_user_id == command.to_user_id
            && existing.expected_owner_version == command.expected_owner_version;
        if !payload_matches {
            return Err(ServiceError::Authority(AuthorityError::TransferConflict(
                TransferConflict::IdempotencyBody {
                    bot_id: command.bot_id.clone(),
                    client_request_id: command.client_request_id.clone(),
                },
            )));
        }
        return Ok(CreateTransferResult {
            receipt: receipt_of(existing)?,
            created: false,
        });
    }

    // -- Validation of a FRESH pending: live/initialized Bot, unique owner
    //    slot, actor IS the current owner, live recipient, no
    //    self-transfer, matching version snapshot. ------------------------
    let ownership_version = authority
        .ownership_versions
        .get(&command.bot_id)
        .copied()
        .unwrap_or(bcs_service_api::types::UNINITIALIZED_OWNERSHIP_VERSION);
    if ownership_version == bcs_service_api::types::UNINITIALIZED_OWNERSHIP_VERSION {
        return Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized {
            bot_id: command.bot_id.clone(),
            env,
        }));
    }
    let owner_edges: Vec<&MemoryRoleEdgeRow> = authority
        .role_rows
        .iter()
        .filter(|row| {
            row.env == env
                && row.to_id == command.bot_id
                && row.status == "approved"
                && row.grant_kind == "owner"
        })
        .collect();
    if owner_edges.len() != 1 {
        return Err(corrupt(
            &command.bot_id,
            &env,
            format!(
                "initialized bot has {} approved owner edges (transfer create denied)",
                owner_edges.len()
            ),
        ));
    }
    let owner_row = owner_edges[0];
    let owner_user_id =
        user_id_from_actor(&owner_row.from_id)
            .ok_or_else(|| {
                corrupt(
                    &command.bot_id,
                    &env,
                    format!("owner edge from non-human actor id '{}'", owner_row.from_id),
                )
            })?
            .to_string();
    if owner_user_id != command.actor_user_id {
        return Err(ServiceError::Authority(AuthorityError::Forbidden(format!(
            "user '{}' is not the owner of bot '{}'; only the current owner may \
             initiate an ownership transfer",
            command.actor_user_id, command.bot_id
        ))));
    }
    if command.to_user_id == command.actor_user_id {
        return Err(ServiceError::Authority(AuthorityError::InvalidSubject(
            "the owner cannot transfer a bot to themselves".to_string(),
        )));
    }
    if command.expected_owner_version != ownership_version {
        // Ownership/version changed since the client read it: reject with
        // the TYPED conflict WITHOUT persisting, WITHOUT cleanup — the
        // client re-reads and retries with the fresh version (spec
        // §10.1 step 3).
        return Err(ServiceError::Authority(AuthorityError::TransferConflict(
            TransferConflict::VersionSnapshotStale {
                bot_id: command.bot_id.clone(),
                expected_owner_version: command.expected_owner_version,
                current_owner_version: ownership_version,
            },
        )));
    }
    let recipient_live = match bots.get(&to_id) {
        Some(row) => {
            row.actor_kind == bcs_service_api::ActorKind::Human
                && row.env.as_deref() == Some(env.as_str())
        }
        None => false,
    };
    if !recipient_live {
        return Err(ServiceError::Authority(AuthorityError::InvalidSubject(format!(
            "recipient '{}' is not a live human actor in env '{env}'",
            command.to_user_id
        ))));
    }

    // -- Slot hygiene in the same section: materialize lapsed and
    //    mismatched pendings (system deciders, §10.1 step 4). ------------
    let now = now_epoch_ms();
    for row in authority.transfer_rows.iter_mut() {
        if row.env != env || row.bot_id != command.bot_id || row.status != "pending" {
            continue;
        }
        let expires_ms = db_text_to_epoch_ms(&row.expires_at).ok_or_else(|| {
            corrupt(&row.bot_id, &row.env, "undecodable expires_at text on a pending row")
        })?;
        if now >= expires_ms {
            row.status = "expired".to_string();
            row.decision_actor_kind = Some("system".to_string());
            row.decided_by = Some(EXPIRED_SYSTEM_MARKER.to_string());
            row.decided_at = Some(row.expires_at.clone());
            row.gmt_modified = now_db_text();
            continue;
        }
        let mismatched =
            row.from_user_id != owner_user_id || row.expected_owner_version != ownership_version;
        if mismatched {
            row.status = "invalidated".to_string();
            row.terminal_reason = Some("owner_changed".to_string());
            row.decision_actor_kind = Some("system".to_string());
            row.decided_by = Some(VALIDATION_SYSTEM_MARKER.to_string());
            row.decided_at = Some(now_db_text());
            row.gmt_modified = now_db_text();
        }
    }
    if authority
        .transfer_rows
        .iter()
        .any(|row| row.env == env && row.bot_id == command.bot_id && row.status == "pending")
    {
        return Err(ServiceError::Authority(AuthorityError::TransferConflict(
            TransferConflict::PendingSlot {
                bot_id: command.bot_id.clone(),
            },
        )));
    }

    // -- Insert the fresh pending row (final in-section slot check). -----
    let now_text = now_db_text();
    let row = MemoryOwnershipTransferRow {
        transfer_id: uuid::Uuid::new_v4().to_string(),
        env: env.clone(),
        bot_id: command.bot_id.clone(),
        from_user_id: command.actor_user_id.clone(),
        to_user_id: command.to_user_id.clone(),
        expected_owner_version: command.expected_owner_version,
        client_request_id: command.client_request_id.clone(),
        status: "pending".to_string(),
        expires_at: deadline_db_text(DAY_MS * EXPIRY_WINDOW_DAYS),
        decision_actor_kind: None,
        decided_by: None,
        decided_at: None,
        result_owner_version: None,
        terminal_reason: None,
        bot_name_snapshot: bots
            .get(&command.bot_id)
            .map(|bot| bot.bot_id.clone())
            .unwrap_or_else(|| command.bot_id.clone()),
        gmt_create: now_text.clone(),
        gmt_modified: now_text,
    };
    authority.transfer_rows.push(row.clone());
    Ok(CreateTransferResult {
        receipt: receipt_of(&row)?,
        created: true,
    })
}

/// Structural fail-closed of the create command (blank identity strings
/// never reach the SQL/matching layer).
fn fail_closed_create(command: &CreateOwnershipTransfer) -> ServiceResult<()> {
    if command.actor_user_id.trim().is_empty()
        || command.bot_id.trim().is_empty()
        || command.to_user_id.trim().is_empty()
        || command.client_request_id.trim().is_empty()
    {
        return Err(ServiceError::Authority(AuthorityError::InvalidSubject(
            "ownership transfer create requires non-blank actor/bot/recipient \
             and a non-blank idempotency key"
                .to_string(),
        )));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// decide_transfer
// ---------------------------------------------------------------------------

pub(crate) async fn memory_decide_transfer(
    repo: &MemoryBotRepo,
    actor_user_id: &str,
    transfer_id: &str,
    action: TransferAction,
) -> ServiceResult<CommittedTransferOutcome> {
    let env = resolve_env();
    if actor_user_id.trim().is_empty() || transfer_id.trim().is_empty() {
        return Err(ServiceError::Authority(
            AuthorityError::OwnershipTransferNotFound {
                transfer_id: transfer_id.to_string(),
            },
        ));
    }
    if repo.take_authority_write_failure().await {
        return Err(ServiceError::InternalError(
            "test-injected authority write failure".into(),
        ));
    }

    // One bounded loop per call: every iteration re-proves visibility and
    // the CURRENT row state on the shared critical section (a row decided
    // while we waited merely restarts the classification; genuine states
    // settle long before the budget is spent).
    for _ in 0..MAX_MEMORY_DECIDE_ATTEMPTS {

    // -- Minimal-record visibility FIRST (§10.2: authenticate and read the
    //    minimal record; a third party learns nothing). ------------------
    let authority = repo.authority.read().await;
    let Some(row) = authority
        .transfer_rows
        .iter()
        .find(|row| row.env == env && row.transfer_id == transfer_id)
    else {
        return Err(ServiceError::Authority(
            AuthorityError::OwnershipTransferNotFound {
                transfer_id: transfer_id.to_string(),
            },
        ));
    };
    let is_recipient = row.to_user_id == actor_user_id;
    let is_initiator = row.from_user_id == actor_user_id;
    drop(authority);
    if !is_recipient && !is_initiator {
        // Concealment: a non-party caller gets the same branch as a missing
        // row, so transfer ids cannot be enumerated (spec §11.2).
        return Err(ServiceError::Authority(
            AuthorityError::OwnershipTransferNotFound {
                transfer_id: transfer_id.to_string(),
            },
        ));
    }
    let actor_role_allows = match action {
        TransferAction::Accept | TransferAction::Reject => is_recipient,
        TransferAction::Cancel => is_initiator,
    };
    if !actor_role_allows {
        return Err(ServiceError::Authority(AuthorityError::Forbidden(format!(
            "user '{actor_user_id}' holds the wrong party role for this transfer action"
        ))));
    }

    // -- Terminal-row retries: observe the persisted outcome, never rewrite
    //    history (spec §10.3; the terminal_reason re-derivation keeps
    //    owner_changed retries distinct from generic invalidations). -----
    let authority = repo.authority.read().await;
    let row = authority
        .transfer_rows
        .iter()
        .find(|row| row.env == env && row.transfer_id == transfer_id)
        .expect("the row was just located in the same state boundary");
    let row_bot_id = row.bot_id.clone();
    if row.status != "pending" {
        let outcome = match row.status.as_str() {
            "accepted" if action == TransferAction::Accept => {
                CommittedTransferOutcome::Receipt(receipt_of(row)?)
            }
            "rejected" if action == TransferAction::Reject => {
                CommittedTransferOutcome::Receipt(receipt_of(row)?)
            }
            "cancelled" if action == TransferAction::Cancel => {
                CommittedTransferOutcome::Receipt(receipt_of(row)?)
            }
            "expired" => CommittedTransferOutcome::Expired,
            "invalidated" => match row.terminal_reason.as_deref() {
                Some("owner_changed") => CommittedTransferOutcome::OwnerChanged,
                _ => CommittedTransferOutcome::Invalidated,
            },
            _ => {
                // An accepted/rejected/cancelled row cannot be re-decided
                // with an incompatible action: the TYPED NotPending branch
                // (409 `ownership_transfer_not_pending` at the application
                // layer).
                return Err(ServiceError::Authority(AuthorityError::TransferConflict(
                    TransferConflict::NotPending {
                        transfer_id: transfer_id.to_string(),
                        status: row.status.clone(),
                    },
                )));
            }
        };
        return Ok(outcome);
    }
    drop(authority);

    // -- Pending flow: liveness pre-checks BEFORE the long critical
    //    section, in the same deleted → bots order every memory lane uses
    //    (the deletion lane's lock order can never deadlock with us). A
    //    retired Bot has no live authority surface left; the receipt
    //    stays readable as history, but no decide may run against it. ----
    if repo.deleted_bot_ids.read().await.contains(&row_bot_id)
        || !repo.bots.read().await.contains_key(&row_bot_id)
    {
        return Err(ServiceError::BotNotFound(row_bot_id));
    }
    // -- The ONE critical section takes the Bot boundary, re-proves every
    //    invariant against CURRENT state and performs the deadline /
    //    mismatch materialization or the action atomically. --------------
    let bots = repo.bots.write().await;
    let mut authority = repo.authority.write().await;

    // In-section liveness re-read (the same one-liner the create lane
    // carries): a retirement that raced the pre-check answers BotNotFound,
    // never the zero-owner CorruptAuthority of the emptied rows.
    if !bots.contains_key(&row_bot_id) {
        return Err(ServiceError::BotNotFound(row_bot_id.clone()));
    }

    let row_index = authority
        .transfer_rows
        .iter()
        .position(|r| r.env == env && r.transfer_id == transfer_id)
        .expect("the row was just located in the same state boundary");
    // Re-read under the write lock (the whole critical section re-proves;
    // this is where the terminal dispatch re-checks the CURRENT state).
    if authority.transfer_rows[row_index].status != "pending" {
        drop(authority);
        drop(bots);
        // Restart the classification on the newly committed state (a
        // loop, never a recursive future).
        continue;
    }
    let bot_id = authority.transfer_rows[row_index].bot_id.clone();
    let now = now_epoch_ms();
    let expires_ms = db_text_to_epoch_ms(&authority.transfer_rows[row_index].expires_at)
        .ok_or_else(|| corrupt(&bot_id, &env, "undecodable expires_at text on a pending row"))?;

    let ownership_version = authority
        .ownership_versions
        .get(&bot_id)
        .copied()
        .unwrap_or(bcs_service_api::types::UNINITIALIZED_OWNERSHIP_VERSION);
    if ownership_version == bcs_service_api::types::UNINITIALIZED_OWNERSHIP_VERSION {
        return Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized {
            bot_id,
            env,
        }));
    }
    let owner_edges: Vec<&MemoryRoleEdgeRow> = authority
        .role_rows
        .iter()
        .filter(|row| {
            row.env == env
                && row.to_id == bot_id
                && row.status == "approved"
                && row.grant_kind == "owner"
        })
        .collect();
    if owner_edges.len() != 1 {
        // 未初始化或损坏的 authority 不伪造失效结论 (§OT12): the transfer
        // stays pending; the caller sees the consistency branch.
        return Err(corrupt(
            &bot_id,
            &env,
            format!(
                "bot has {} approved owner edges while deciding a pending transfer",
                owner_edges.len()
            ),
        ));
    }
    let owner_row = owner_edges[0].clone();

    // Deadline first (the decision point is the DB-clock twin, not the
    // request arrival): a lapsed pending materializes `expired` and
    // returns the committed domain result.
    if now >= expires_ms {
        let row = &mut authority.transfer_rows[row_index];
        row.status = "expired".to_string();
        row.decision_actor_kind = Some("system".to_string());
        row.decided_by = Some(EXPIRED_SYSTEM_MARKER.to_string());
        row.decided_at = Some(row.expires_at.clone());
        row.gmt_modified = now_db_text();
        return Ok(CommittedTransferOutcome::Expired);
    }

    // owner/version mismatch: committed-invalidation first (§10.2) — no
    // edges, no version bump, the pending slot releases, and the domain
    // result OwnerChanged is a RESULT, never an exception.
    let current_owner_user_id =
        user_id_from_actor(&owner_row.from_id)
            .ok_or_else(|| {
                corrupt(
                    &bot_id,
                    &env,
                    format!("owner edge from non-human actor id '{}'", owner_row.from_id),
                )
            })?
            .to_string();
    let snapshot = authority.transfer_rows[row_index].clone();
    if snapshot.from_user_id != current_owner_user_id
        || snapshot.expected_owner_version != ownership_version
    {
        let row = &mut authority.transfer_rows[row_index];
        row.status = "invalidated".to_string();
        row.terminal_reason = Some("owner_changed".to_string());
        row.decision_actor_kind = Some("system".to_string());
        row.decided_by = Some(VALIDATION_SYSTEM_MARKER.to_string());
        row.decided_at = Some(now_db_text());
        row.gmt_modified = now_db_text();
        return Ok(CommittedTransferOutcome::OwnerChanged);
    }

    // The action itself, all or nothing inside this section.
    let outcome = match action {
        TransferAction::Accept => {
            // The recipient must still be a live Human at decision time.
            let recipient_live = match bots.get(&human_actor_id(&snapshot.to_user_id)) {
                Some(bot) => {
                    bot.actor_kind == bcs_service_api::ActorKind::Human
                        && bot.env.as_deref() == Some(env.as_str())
                }
                None => false,
            };
            if !recipient_live {
                return Err(ServiceError::Authority(AuthorityError::InvalidSubject(format!(
                    "recipient '{}' is not a live human actor in env '{env}'",
                    snapshot.to_user_id
                ))));
            }
            let from_actor = human_actor_id(&snapshot.from_user_id);
            let to_actor = human_actor_id(&snapshot.to_user_id);
            // 1. revoke the previous owner edge.
            for edge in authority.role_rows.iter_mut() {
                if edge.env == env
                    && edge.to_id == bot_id
                    && edge.grant_kind == "owner"
                    && edge.status == "approved"
                {
                    edge.status = "revoked".to_string();
                }
            }
            // 2. restore/insert the recipient's owner edge (same-row
            //    restore for a previously revoked row — never a second slot).
            let existing_owner_slot = authority
                .role_rows
                .iter_mut()
                .find(|edge| {
                    edge.env == env
                        && edge.to_id == bot_id
                        && edge.from_id == to_actor
                        && edge.grant_kind == "owner"
                });
            match existing_owner_slot {
                Some(existing) => existing.status = "approved".to_string(),
                None => {
                    let edge_id = authority.allocate_edge_id();
                    authority.role_rows.push(MemoryRoleEdgeRow {
                        id: edge_id,
                        env: env.clone(),
                        from_id: to_actor.clone(),
                        to_id: bot_id.clone(),
                        grant_kind: "owner".to_string(),
                        grant_ref_id: bcs_service_api::types::ROLE_GRANT_REF_ID as i64,
                        rules: None,
                        status: "approved".to_string(),
                        management_source_kind:
                            bcs_service_api::types::OWNER_SOURCE_KIND.to_string(),
                        management_source_id: bcs_service_api::types::OWNER_SOURCE_ID.to_string(),
                    });
                }
            }
            // 3. the previous owner's ownership_transfer manager source.
            let manager_source = (
                "ownership_transfer".to_string(),
                transfer_id.to_string(),
            );
            let already = authority.role_rows.iter().find(|edge| {
                edge.env == env
                    && edge.to_id == bot_id
                    && edge.from_id == from_actor
                    && edge.grant_kind == "manager"
                    && edge.management_source_kind == manager_source.0
                    && edge.management_source_id == manager_source.1
            });
            if already.is_none() {
                let edge_id = authority.allocate_edge_id();
                authority.role_rows.push(MemoryRoleEdgeRow {
                    id: edge_id,
                    env: env.clone(),
                    from_id: from_actor,
                    to_id: bot_id.clone(),
                    grant_kind: "manager".to_string(),
                    grant_ref_id: bcs_service_api::types::ROLE_GRANT_REF_ID as i64,
                    rules: None,
                    status: "approved".to_string(),
                    management_source_kind: manager_source.0,
                    management_source_id: manager_source.1,
                });
            }
            // 4. revoke the recipient's non-team manager sources (`team/*`
            //    stays with team sync, OT02).
            for edge in authority.role_rows.iter_mut() {
                if edge.env == env
                    && edge.to_id == bot_id
                    && edge.from_id == to_actor
                    && edge.grant_kind == "manager"
                    && edge.status == "approved"
                    && (edge.management_source_kind == "direct"
                        || edge.management_source_kind == "ownership_transfer")
                {
                    edge.status = "revoked".to_string();
                }
            }
            // 5. CAS-bump the version and save the accepted receipt.
            let new_version = ownership_version + 1;
            authority
                .ownership_versions
                .insert(bot_id.clone(), new_version);
            let row = &mut authority.transfer_rows[row_index];
            row.status = "accepted".to_string();
            row.decision_actor_kind = Some("human".to_string());
            row.decided_by = Some(actor_user_id.to_string());
            row.decided_at = Some(now_db_text());
            row.result_owner_version = Some(new_version);
            row.gmt_modified = now_db_text();
            Ok(CommittedTransferOutcome::Receipt(receipt_of(row)?))
        }
        TransferAction::Reject | TransferAction::Cancel => {
            // Cancel re-proves the initiator is still the current owner;
            // the flows above only reach here with the snapshot matching,
            // which included that proof (§9: cancel by the pending
            // initiator while still owner).
            let new_status = match action {
                TransferAction::Reject => "rejected",
                TransferAction::Cancel => "cancelled",
                TransferAction::Accept => unreachable!("accept handled above"),
            };
            let row = &mut authority.transfer_rows[row_index];
            row.status = new_status.to_string();
            row.decision_actor_kind = Some("human".to_string());
            row.decided_by = Some(actor_user_id.to_string());
            row.decided_at = Some(now_db_text());
            row.gmt_modified = now_db_text();
            Ok(CommittedTransferOutcome::Receipt(receipt_of(row)?))
        }
        };
        return outcome;
    } // bounded re-classification loop (drift restarts `continue` above)

    // The budget is spent only when every attempt lost its window to a
    // concurrent decide: the retry surfaces the committed winner.
    Err(ServiceError::Authority(AuthorityError::TransferConflict(
        TransferConflict::Contended {
            resource: transfer_id.to_string(),
            detail: "the validated window was lost repeatedly; the committed \
                     winner answers the retry"
                    .to_string(),
        },
    )))
}

// ---------------------------------------------------------------------------
// get_transfer / list_transfers (read-only; no materialization writes)
// ---------------------------------------------------------------------------

pub(crate) async fn memory_get_transfer(
    repo: &MemoryBotRepo,
    viewer_user_id: &str,
    transfer_id: &str,
) -> ServiceResult<OwnershipTransfer> {
    let env = resolve_env();
    let authority = repo.authority.read().await;
    let row = authority
        .transfer_rows
        .iter()
        .find(|row| row.env == env && row.transfer_id == transfer_id);
    let Some(row) = row else {
        // Concealment: a missing id and a non-party viewer collapse into
        // the same branch, so transfers cannot be enumerated (spec §11.2).
        return Err(ServiceError::Authority(
            AuthorityError::OwnershipTransferNotFound {
                transfer_id: transfer_id.to_string(),
            },
        ));
    };
    if row.from_user_id != viewer_user_id && row.to_user_id != viewer_user_id {
        return Err(ServiceError::Authority(
            AuthorityError::OwnershipTransferNotFound {
                transfer_id: transfer_id.to_string(),
            },
        ));
    }
    receipt_of(row)
}

pub(crate) async fn memory_list_transfers(
    repo: &MemoryBotRepo,
    query: ListOwnershipTransfers,
) -> ServiceResult<OwnershipTransferPage> {
    let env = resolve_env();
    if query.viewer_user_id.trim().is_empty() {
        return Err(ServiceError::Authority(
            AuthorityError::OwnershipTransferNotFound {
                transfer_id: String::new(),
            },
        ));
    }
    let limit = query
        .limit
        .min(TRANSFER_PAGE_LIMIT_CEILING);
    let authority = repo.authority.read().await;
    // ONE snapshot for filter/count/page; the effective-status rule is the
    // same as the SQL store's in-database filter (a lapsed pending no
    // longer matches `pending` and now matches `expired`).
    let now = now_epoch_ms();
    let effective = |row: &MemoryOwnershipTransferRow| -> ServiceResult<TransferStatus> {
        let expires_ms = db_text_to_epoch_ms(&row.expires_at)
            .ok_or_else(|| corrupt(&row.bot_id, &row.env, "undecodable expires_at text"))?;
        if row.status == "pending" && now >= expires_ms {
            Ok(TransferStatus::Expired)
        } else {
            decode_status(&row.status).ok_or_else(|| {
                corrupt(&row.bot_id, &row.env, format!("unknown transfer status '{}'", row.status))
            })
        }
    };
    let mut matches: Vec<&MemoryOwnershipTransferRow> = Vec::new();
    for row in authority.transfer_rows.iter() {
        if row.env != env {
            continue;
        }
        let side_matches = match query.direction {
            TransferListDirection::Received => row.to_user_id == query.viewer_user_id,
            TransferListDirection::Sent => row.from_user_id == query.viewer_user_id,
        };
        if !side_matches {
            continue;
        }
        if let Some(want) = query.status {
            if effective(row)? != want {
                continue;
            }
        }
        matches.push(row);
    }
    let total = matches.len() as u64;
    // The binding ordering contract of the port (spec §11.1, identical to
    // the SQL lane): `gmt_create DESC, transfer_id ASC` — the DB-text
    // timestamps share the 'YYYY-MM-DD HH:MM:SS' shape, so lexicographic
    // string compare IS chronological.
    matches.sort_by(|left, right| {
        right
            .gmt_create
            .cmp(&left.gmt_create)
            .then_with(|| left.transfer_id.cmp(&right.transfer_id))
    });
    let items = matches
        .into_iter()
        .skip(query.offset as usize)
        .take(limit as usize)
        .map(receipt_of)
        .collect::<ServiceResult<Vec<OwnershipTransfer>>>()?;
    Ok(OwnershipTransferPage { items, total })
}