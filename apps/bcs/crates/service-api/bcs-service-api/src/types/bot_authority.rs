//! Transport-neutral bot authority commands (plan Task 1).
//!
//! Role/source vocabulary itself is pure domain: `bcs_domain`
//! (`BotAccessRelation`, `ManagementSource`, `AuditActor`,
//! `ManagerMutation`, `ManagerMutationResult`, `OwnershipState`). This
//! module carries the cross-boundary command envelopes that the
//! application/Core/Repo layers share for role lifecycle entrances.
//! Presence/absence of a role is `Option<BotAccessRelation>`;
//! corrupted or uninitialized authority is an error
//! (`ServiceError::Authority`), never an implicit role.

use bcs_domain::{AuditActor, ManagementSource, UNINITIALIZED_OWNERSHIP_VERSION};
use serde::{Deserialize, Serialize};

/// First-ownership initialization for a not-yet-owned Bot
/// (spec §13.3, plan Task 5).
///
/// Only a trusted registration context may supply this; the initial
/// owner can never come from an arbitrary request body. Runtime-only
/// Bot connects with no Human do NOT carry an initialization and stay
/// at version 0 (uninitialized).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipInitialization {
    /// Trusted first owner User ID.
    pub owner_user_id: String,
    /// Typed role-lifecycle operator (Human on registration, System on
    /// governed repair/migration); service ids are never recorded as
    /// Human user ids.
    pub actor: AuditActor,
    /// Operation id grouping the initialization audit row.
    pub operation_id: String,
}

impl OwnershipInitialization {
    /// A not-yet-initialized Bot reports this ownership version.
    pub fn uninitialized_version() -> u64 {
        UNINITIALIZED_OWNERSHIP_VERSION
    }
}

/// One deduplicated manager entry of a [`BotManagerList`] page (spec §6):
/// the subject's bare User ID plus every management source it currently
/// holds on the Bot, decoded strictly and canonically ordered (by the stored
/// (kind, id) pair — `direct` < `ownership_transfer` < `team`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotManagerSummary {
    /// The trusted User ID of the manager (never the `human_<uid>` actor id).
    pub user_id: String,
    /// All live (approved) management sources of this user on the Bot.
    pub sources: Vec<ManagementSource>,
}

/// Result of `list_managers` (spec §6): the single owner in its own
/// read-only field — the owner never mixes into the manager page — plus one
/// user-deduplicated, user_id-ASC sorted page of explicit managers.
///
/// Pagination is positional: `offset` skips users, `limit == 0` yields an
/// empty page, and a page reaching beyond the last manager is empty rather
/// than an error. The application layer clamps `limit` into 1..100 per the
/// API contract; the store only requires the positional semantics.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotManagerList {
    /// The single effective owner User ID of the initialized Bot.
    pub owner_user_id: String,
    /// One page of explicit (non-owner) managers, sorted user_id ASC.
    pub managers: Vec<BotManagerSummary>,
}

// ---------------------------------------------------------------------------
// Historical ownership migration (plan Task 17, spec §16.1)
// ---------------------------------------------------------------------------

/// Engineering default of the governed migration: one candidate/initialization
/// batch carries at most [`MIGRATION_MAX_BATCH_SIZE`] Bots. Over-limit
/// requests are REJECTED fail-closed (never silently clamped); bounded
/// batches keep every transaction inside the store's short-lock discipline.
pub const MIGRATION_MAX_BATCH_SIZE: usize = 100;

/// Fixed system identifier of the governed migration lanes: the
/// `AuditActor::System` name recorded on `bot_ownership_initializations`
/// rows and the operation-id prefix grouping one batch's initialization
/// audits. It is a system marker, never a User ID.
pub const MIGRATION_SYSTEM_ACTOR: &str = "ownership-migration";

/// Machine-read reason vocabulary shared by the dry-run candidate page and
/// the initialization report (spec §16.1.2–16.1.4). Fixed wire strings:
/// governance tooling parses `as_str`, never free prose; nothing here may
/// authorize access — every non-`Ready` outcome is excluded from
/// initialization and only a fresh verified `Ready` classification may call
/// the governed Task 5 initialization lane.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OwnershipMigrationReason {
    /// Live physical version-0 Bot with a legal, corroborated created_by
    /// owner: the only class the cutover may initialize.
    Ready,
    /// Version already > 0 with the owner equal to created_by: verified and
    /// skipped on rerun; never re-claimed.
    AlreadyInitialized,
    /// Version > 0 and the current owner diverges from created_by (the
    /// authority was transferred after an earlier initialization): skipped,
    /// owner and version MUST stay untouched.
    OwnershipTransferred,
    /// `created_by` is absent/blank (bare runtime ownership). The Bot keeps
    /// version 0 and its runtime identity; it is never auto-claimed by
    /// suffix, relation or any other heuristic.
    MissingCreator,
    /// Multiple conflicting legacy creator claims, or the single claim
    /// contradicts the current created_by: governance owns the decision,
    /// the migration does not pick a winner.
    ConflictingCreators,
    /// created_by references a User with no live Human actor row: the source
    /// is unverifiable, so the Bot is not migrated.
    MissingHuman,
    /// The owner/version pair is internally inconsistent (approved owner
    /// edges at version 0, or an initialized version with no owner edge):
    /// corrupted authority never auto-repairs into a candidate.
    AuthorityInconsistent,
    /// Missing or soft-deleted Bot row: excluded from migration.
    BotNotLive,
    /// A Human actor self row was confirmed: Human rows never migrate into
    /// transferable Bot owners (spec §16.1.2).
    HumanRowExcluded,
    /// The per-Bot initialization attempt failed at the storage boundary.
    /// The already-committed prefix of the batch is recoverable by replaying
    /// the same `batch_id`; the command must exit nonzero.
    StorageFailure,
}

impl OwnershipMigrationReason {
    /// Fixed machine word (wire/ledger vocabulary).
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Ready => "ready",
            Self::AlreadyInitialized => "already_initialized",
            Self::OwnershipTransferred => "ownership_transferred",
            Self::MissingCreator => "missing_creator",
            Self::ConflictingCreators => "conflicting_creators",
            Self::MissingHuman => "missing_human",
            Self::AuthorityInconsistent => "authority_inconsistent",
            Self::BotNotLive => "bot_not_live",
            Self::HumanRowExcluded => "human_row_excluded",
            Self::StorageFailure => "storage_failure",
        }
    }
}

/// One governed migration candidate from `inspect_batch` (the dry-run page):
/// the Bot, its env, the derived candidate owner User ID — `None` when the
/// missing source is legal (bare runtime rows stay listed for governance
/// accounting with an empty owner) — and the machine-read classification.
///
/// The candidate list is a transient governance projection produced by
/// re-reading current facts; it is never persisted as a writable parallel
/// owner list, and confirming a Bot re-derives the same classification at
/// execution time (spec §16.1.2–16.1.3).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipCandidate {
    /// The physical Bot's id.
    pub bot_id: String,
    /// The env the candidate was read in (store-resolved, never request input).
    pub env: String,
    /// Derived candidate owner User ID (legal `created_by`), or `None` when
    /// no legal source exists (bare runtime shape).
    pub candidate_user_id: Option<String>,
    /// Machine-read classification of the candidate.
    pub reason: OwnershipMigrationReason,
}

/// One `inspect_batch` page (plan Task 17): a bounded slice of the
/// candidate scan plus the keyset continuation cursor. Pagination is
/// `bot_uuid`-keyset over the live/physical/version-0 scan in strictly
/// ascending order; `next_bot_id` is the last row's id while more rows may
/// follow, `None` at the end of the scan.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipCandidatePage {
    /// Candidates of this page (ready AND conflicted: the dry-run is the
    /// complete governance list, conflicts are never silently hidden).
    pub candidates: Vec<OwnershipCandidate>,
    /// Keyset cursor: strictly-after position of the next page.
    pub next_bot_id: Option<String>,
}

/// One Bot outcome line of [`OwnershipMigrationReport`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipMigrationBotOutcome {
    /// The confirmed Bot the outcome is about.
    pub bot_id: String,
    /// Machine-read outcome reason.
    pub reason: OwnershipMigrationReason,
    /// Optional machine-readable detail (e.g. `batch_replay` for recovered
    /// entries, or a sanitized storage-failure marker). Never free prose a
    /// tool must parse.
    pub detail: Option<String>,
}

/// Result of `initialize_batch` (plan Task 17): every confirmed Bot lands in
/// exactly one outcome lane. `initialized` counts this run's committed
/// initializations (including the ones recovered from an earlier committed
/// prefix of the same `batch_id`); `skipped`/`conflicted` attribute the
/// re-verified exclusions; `failed` carries per-Bot storage failures — a
/// non-empty `failed` MUST make the maintenance command exit nonzero.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipMigrationReport {
    /// The batch the operator confirmed; the replay/recovery key.
    pub batch_id: String,
    /// Bots this batch initialized (committed now, or recovered from this
    /// batch's earlier committed prefix).
    pub initialized: Vec<OwnershipMigrationBotOutcome>,
    /// Bots verified and excluded without any state change (rerun safety).
    pub skipped: Vec<OwnershipMigrationBotOutcome>,
    /// Bots whose re-verification exposed governance conflicts.
    pub conflicted: Vec<OwnershipMigrationBotOutcome>,
    /// Bots whose initialization attempt failed at the storage boundary.
    pub failed: Vec<OwnershipMigrationBotOutcome>,
}

/// Store-level migration facts of one Bot (read shape of the backfill scan).
///
/// Everything is a CURRENT re-read: version, owner edges and the creator
/// Human row are fetched at scan AND again at execution time, so a stale
/// candidate snapshot can never authorize an initialization (spec
/// §16.1.4). `owner_user_ids` carries the raw approved owner-edge
/// `from_id` values (the Human ACTOR ids) so a corrupt non-human-prefixed
/// edge stays visible to the classifier as `AuthorityInconsistent`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OwnershipMigrationBotState {
    /// Bot id.
    pub bot_id: String,
    /// The env the row was read in.
    pub env: String,
    /// `bcs_bots.created_by` — the migration's ONLY trusted creator source;
    /// `None` for bare runtime rows. Never modified by the migration.
    pub created_by: Option<String>,
    /// Current `ownership_version` (0 = uninitialized).
    pub ownership_version: u64,
    /// `false` for missing or soft-deleted rows.
    pub live: bool,
    /// `false` for Human actor self rows.
    pub physical: bool,
    /// Raw approved owner-edge `from_id` values of the Bot (empty at
    /// version 0 unless the authority is corrupt).
    pub owner_edge_claimants: Vec<String>,
    /// Whether a live Human actor row exists for `created_by`.
    pub creator_human_live: bool,
}

/// One committed initialization row of a migration batch (the recovery
/// ledger read): `bot_ownership_initializations` rows filtered by
/// `batch_id`, the substrate `initialize_batch` replays to rebuild the
/// original report counts of an interrupted run's committed prefix.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OwnershipBatchInitialization {
    /// The initialized Bot.
    pub bot_id: String,
    /// The owner the batch committed.
    pub owner_user_id: String,
    /// The operation id grouping the batch's initialization audits.
    pub operation_id: String,
}

/// One legacy creator claim of the migration cross-check (spec §16.1.2,
/// "对照 legacy creator"): the `bcs_actor_relations` rows with
/// `is_creator = 1` into the listed Bots. The claimant keeps the stored
/// ACTOR id shape (`human_<user_id>`); the migration normalizes through the
/// same D11 prefix mapping the role edges use.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LegacyCreatorClaim {
    /// Env scope of the claim.
    pub env: String,
    /// The claimed Bot id (`to_id`).
    pub bot_id: String,
    /// The claimant's stored actor id (`from_id`, `human_<user_id>`).
    pub claimant_actor_id: String,
}
