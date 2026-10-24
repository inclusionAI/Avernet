//! Bot-authority value types (owner / manager, ownership transfers).
//!
//! Freezes the shared vocabulary of the 2026-10-10 Bot authority
//! design (owner/manager edges, ownership transfers, strict reads)
//! across `application`, `core`, and `port` contracts. Types here are
//! transport-independent: public responses are projected into
//! adapter DTOs that must not serialize internal mutation ids or SQL
//! errors.
//!
//! Roles live on `edge_grants` as Human → Bot management edges
//! (`grant_kind = owner | manager`, unified `grant_ref_id = 0`). They
//! are metadata edges only and never take part in runtime permission
//! decisions (spec §3.2.5).

/// Relation granted by an approved management edge.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BotAccessRelation {
    /// Approved owner edge. Unique per `(env, bot)` and takes
    /// precedence over manager.
    Owner,
    /// Approved manager edge. One shared manager set per bot.
    Manager,
}

/// The real initiating identity of a permission mutation.
///
/// A mutation is only constructed for permission write commands
/// (manager edit/sync, transfer, lifecycle initialization); it never
/// spreads into ordinary Group/Session/File/Message writes. Service
/// and Provider ids identify the actual initiator and never constitute
/// an alternative authorization source for manager operations.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuthorityActor {
    Human {
        user_id: String,
    },
    /// Gateway App acting under an explicit trusted scope.
    Service {
        app_id: i64,
    },
    /// Provider retirement/initiation; recorded as the real initiator.
    Provider {
        provider_id: String,
    },
    /// Automatic decisions (e.g. expiry/invalidation closures).
    System {
        name: String,
    },
}

impl AuthorityActor {
    /// Open error/query surface for the actor kind
    /// (`human` / `service` / `provider` / `system`).
    pub fn kind(&self) -> AuthorityActorKind {
        match self {
            AuthorityActor::Human { .. } => AuthorityActorKind::Human,
            AuthorityActor::Service { .. } => AuthorityActorKind::Service,
            AuthorityActor::Provider { .. } => AuthorityActorKind::Provider,
            AuthorityActor::System { .. } => AuthorityActorKind::System,
        }
    }

    /// The actor id as recorded in audit/transfer rows. Provider
    /// retirement records `Provider { provider_id }` verbatim (spec
    /// §3.1); it never impersonates a Human or system id.
    pub fn actor_id(&self) -> String {
        match self {
            AuthorityActor::Human { user_id } => human_actor_id(user_id),
            AuthorityActor::Service { app_id } => format!("app_{}", app_id),
            AuthorityActor::Provider { provider_id } => format!("provider_{}", provider_id),
            AuthorityActor::System { name } => format!("system_{}", name),
        }
    }
}

/// Identity kind for audit/transfer rows and transfer views.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthorityActorKind {
    Human,
    Service,
    Provider,
    System,
}

impl AuthorityActorKind {
    /// Stable snake_case spelling shared by views and audit rows.
    pub fn as_str(&self) -> &'static str {
        match self {
            AuthorityActorKind::Human => "human",
            AuthorityActorKind::Service => "service",
            AuthorityActorKind::Provider => "provider",
            AuthorityActorKind::System => "system",
        }
    }
}

/// Storage width of the server-generated mutation
/// id: `bot_authority_changes.mutation_id` is
/// VARCHAR(64) (binary collation) in the authority
/// migrations. Validator and every generator MUST
/// stay within it — an over-long id would fail the
/// audit INSERT on real MySQL (ERROR 1406) after
/// the payload writes, unrecoverable by retry.
pub const MUTATION_ID_MAX_LEN: usize = 64;

/// Server-generated idempotence/scatter key for one permission
/// mutation. `mutation_id` is only for the permission write commands;
/// it must never leak into public responses.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityMutation {
    pub mutation_id: String,
    pub actor: AuthorityActor,
}

/// Trusted initialization payload for a Bot's ownership
/// (spec §10: owner + version in the same transaction).
pub struct OwnershipInitialization {
    pub owner_user_id: String,
    pub mutation: AuthorityMutation,
}

/// One role query: which relation does `user_id` hold on `bot_id`
/// within the caller's bound environment?
///
/// `user_id` is the verified User ID; [`BotAuthorityCoreService`] (in
/// `core::bot_authority`) maps it to the persisted Human actor id via
/// [`human_actor_id`]. Repo implementations take that persisted actor
/// id in the `user_id` parameter and compare it byte-exactly.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotRoleQuery {
    pub user_id: String,
    pub bot_id: String,
}

/// Localization key for one transfer record (spec §6.1): Bot ID +
/// target owner Human actor id. No `transfer_id` is ever exposed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransferKey {
    pub bot_id: String,
    pub target_owner_actor_id: String,
}

/// Lifecycle status of one transfer row (spec §4.3).
///
/// `Expired` is a read-time projection when a `Pending` row passed its
/// `expires_at`; writes close it lazily (no background expiry task).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransferStatus {
    Pending,
    Accepted,
    Rejected,
    Cancelled,
    Expired,
    Invalidated,
}

impl TransferStatus {
    /// Stable snake_case spelling shared by projections.
    pub fn as_str(&self) -> &'static str {
        match self {
            TransferStatus::Pending => "pending",
            TransferStatus::Accepted => "accepted",
            TransferStatus::Rejected => "rejected",
            TransferStatus::Cancelled => "cancelled",
            TransferStatus::Expired => "expired",
            TransferStatus::Invalidated => "invalidated",
        }
    }
}

/// Inbox/outbox direction of a transfer list read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransferDirection {
    /// Rows whose `target_owner_actor_id` is the requesting Human.
    Received,
    /// Rows whose `from_owner_actor_id` is the requesting Human.
    Sent,
}

/// Minimal owner summary carried by every authority read of a bot.
///
/// Internal `ownership_version` is deliberately NOT part of this view;
/// clients never save or submit it (spec §4.2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotOwnershipView {
    pub bot_id: String,
    /// User id portion of the owner's Human actor id.
    pub owner_user_id: String,
    /// Full persisted Human actor id (`human_<user_id>`).
    pub owner_actor_id: String,
}

/// Governance facts of ONE Bot row for the bcs-admin
/// ownership-backfill dry-run (spec §10.4: 仅创建记录与历史
/// 关系不矛盾、指向有效 Human 的 version 0 Bot 可申请回填).
///
/// A pure read snapshot — the classification and the apply
/// decision stay with the governance consumer
/// (`initialize_ownership` remains the only write command).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotOwnershipGovernance {
    pub bot_id: String,
    pub actor_kind: crate::ActorKind,
    /// The row exists and is not tombstoned.
    pub live: bool,
    pub created_by: Option<String>,
    /// `created_by`'s Human ACTOR row is live in the instance
    /// env (`bcs_bots`, `actor_kind='human'`, `human_<user_id>`,
    /// not tombstoned — the same registry the authority guards
    /// read). `false` when `created_by` is absent.
    pub owner_actor_present: bool,
    pub ownership_version: u64,
    /// Approved owner-edge subjects (Human actor ids,
    /// byte-exact, stable order).
    pub owner_edges: Vec<String>,
}

/// Current manager-set projection: one owner summary plus the shared
/// manager set. Owner never appears in `items`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ManagerPage {
    pub owner: BotOwnershipView,
    pub items: Vec<String>,
    pub total: u64,
}

/// Pagination query for a bot's manager list (default offset 0 /
/// limit 50, hard max 100).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ManagerListQuery {
    pub bot_id: String,
    pub offset: u64,
    pub limit: u64,
}

impl Default for ManagerListQuery {
    fn default() -> Self {
        Self {
            bot_id: String::new(),
            offset: 0,
            limit: DEFAULT_PAGE_LIMIT,
        }
    }
}

/// Minimal status projection of one transfer row (spec §4.3 fields).
///
/// The row carries no bot configuration or resource content beyond the
/// name snapshot; pending grants the target no bot resource access.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransferView {
    pub bot_id: String,
    pub target_owner_actor_id: String,
    pub from_owner_actor_id: String,
    /// Read-time effective status (pending past expiry projects to
    /// `Expired` without writing).
    pub status: TransferStatus,
    pub requested_at: u64,
    pub expires_at: u64,
    pub updated_at: u64,
    /// Decision fields; empty while pending.
    pub decided_at: Option<u64>,
    pub decided_by_kind: Option<AuthorityActorKind>,
    pub decided_by_id: Option<String>,
    pub reason: Option<String>,
    /// Ownership version after an accepted transfer.
    pub result_ownership_version: Option<u64>,
    pub bot_name_snapshot: Option<String>,
}

/// Transfer list query (default offset 0 / limit 50, hard max 100).
///
/// `user_id` is filled by the Application from the authenticated
/// identity; lists project only the two actors of each row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransferListQuery {
    /// Human actor id of the requesting party (inbox/outbox owner).
    pub user_id: String,
    pub direction: TransferDirection,
    /// Empty = all statuses under the same effective-status lens.
    pub statuses: Vec<TransferStatus>,
    pub offset: u64,
    pub limit: u64,
}

impl Default for TransferListQuery {
    fn default() -> Self {
        Self {
            user_id: String::new(),
            direction: TransferDirection::Received,
            statuses: Vec::new(),
            offset: 0,
            limit: DEFAULT_PAGE_LIMIT,
        }
    }
}

impl TransferListQuery {
    /// Same effective-status lens for filter/count/page: pending past
    /// expiry projects to `Expired` and pending filters exclude it.
    pub fn matches(&self, view: &TransferView) -> bool {
        self.statuses.is_empty() || self.statuses.contains(&view.status)
    }
}

/// One page of the inbox/outbox transfer list; `total` uses the same
/// effective-status lens as `items`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransferPage {
    pub items: Vec<TransferView>,
    pub total: u64,
}

/// Result of one manager-set edit/sync.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ManagerSetChange {
    pub added: u64,
    pub removed: u64,
    pub total: u64,
}

/// Single-item manager command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ManagerEdit {
    pub bot_id: String,
    /// Target User ID (Application maps verified users; persisted
    /// implementation stores the Human actor id).
    pub user_id: String,
    pub action: ManagerEditAction,
    pub mutation: AuthorityMutation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ManagerEditAction {
    Grant,
    Revoke,
}

/// Full manager-set replace (trusted sync). Owner is preserved and
/// normalized out of the input set; an empty set explicitly clears all
/// managers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ManagerSync {
    pub bot_id: String,
    pub user_ids: Vec<String>,
    pub mutation: AuthorityMutation,
}

/// Transfer action on the transfer key (spec §6).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransferAction {
    RequestOrRefresh,
    Accept,
    Reject,
    Cancel,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransferCommand {
    pub key: TransferKey,
    pub action: TransferAction,
    pub mutation: AuthorityMutation,
}

/// Whether a submitted transfer command changed state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransferChange {
    Created,
    Updated,
    Unchanged,
}

/// Committed, queryable business conflict (not a rollback error).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthorityConflictCode {
    /// Another pending target exists for this bot.
    TransferPending,
    /// The pending row's owner/version snapshot no longer matches.
    OwnerChanged,
    /// The pending row passed its expiry.
    TransferExpired,
    /// Any other state/action mismatch.
    TransferStateConflict,
}

impl AuthorityConflictCode {
    /// Stable snake_case spelling mapped to 409 by the adapter.
    pub fn as_str(&self) -> &'static str {
        match self {
            AuthorityConflictCode::TransferPending => "transfer_pending",
            AuthorityConflictCode::OwnerChanged => "owner_changed",
            AuthorityConflictCode::TransferExpired => "transfer_expired",
            AuthorityConflictCode::TransferStateConflict => "transfer_state_conflict",
        }
    }
}

/// Outcome of a transfer write command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TransferOutcome {
    Applied {
        transfer: TransferView,
        change: TransferChange,
    },
    Conflict {
        transfer: TransferView,
        code: AuthorityConflictCode,
    },
}

/// Maximum ids bound into one bounded collection query.
///
/// `roles_for` results must never be assembled by calling the single
/// `role` read per input; implementations read owners and target edges
/// in chunks of at most this many ids per statement.
pub const AUTHORITY_QUERY_CHUNK: usize = 128;

/// Default page limit for manager/transfer list queries.
pub const DEFAULT_PAGE_LIMIT: u64 = 50;

/// Hard page limit for manager/transfer list queries (plan §3).
pub const MAX_PAGE_LIMIT: u64 = 100;

/// Validate shared pagination bounds: `limit` must be in
/// 1..=[`MAX_PAGE_LIMIT`] (0 means an unset, not "everything").
pub fn check_page_bounds(offset: u64, limit: u64) -> crate::ServiceResult<()> {
    if limit == 0 || limit > MAX_PAGE_LIMIT {
        return Err(crate::ServiceError::InvalidOperation {
            message: format!(
                "invalid authority page bounds: limit must be in 1..={MAX_PAGE_LIMIT}"
            ),
            request_id: None,
        });
    }
    if offset > i64::MAX as u64 {
        return Err(crate::ServiceError::InvalidOperation {
            message: "invalid authority page bounds: offset too large".to_string(),
            request_id: None,
        });
    }
    Ok(())
}

/// Persisted Human actor id for a verified User ID (spec §5.2:
/// "Backend 使用稳定 User ID，由 BCS 映射为 `human_{user_id}`").
///
/// Authority edges store the composed actor id; comparisons stay
/// byte-exact (case-sensitive) end to end.
pub fn human_actor_id(user_id: &str) -> String {
    format!("human_{}", user_id)
}

/// Inverse of [`human_actor_id`]: user id portion of a persisted Human
/// actor id. `None` for ids without the prefix so non-Human rows can
/// never silently become owner summaries.
pub fn human_user_id_of_actor(actor_id: &str) -> Option<String> {
    actor_id.strip_prefix("human_").map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_bounds_reject_zero_and_oversize() {
        assert!(check_page_bounds(0, DEFAULT_PAGE_LIMIT).is_ok());
        assert!(check_page_bounds(0, MAX_PAGE_LIMIT).is_ok());
        assert!(check_page_bounds(0, 0).is_err());
        assert!(check_page_bounds(0, MAX_PAGE_LIMIT + 1).is_err());
    }

    #[test]
    fn human_actor_mapping_is_exact_and_reversible() {
        assert_eq!(human_actor_id("alice"), "human_alice");
        assert_eq!(human_user_id_of_actor("human_alice").as_deref(), Some("alice"));
        assert_eq!(human_user_id_of_actor("bot_x"), None);
        // Byte-exact: distinct principals stay distinct (spec §3.2.6).
        assert_ne!(human_actor_id("Alice"), human_actor_id("alice"));
    }
}
