//! Bot authority: explicit owner/manager roles and their provenance.
//!
//! Pure domain types for the bot manage-permission reform
//! (`docs/superpowers/specs/2026-09-18-bot-manage-permission-design.md`
//! §5.1). Roles are carried as dedicated edge kinds (`GrantKind::Owner`
//! / `GrantKind::Manager`); every role edge carries non-nullable source
//! metadata:
//!
//! ```text
//! owner          -> management_source_kind = owner, id = owner
//! manager/direct -> management_source_kind = direct, id = manual
//! manager/team   -> management_source_kind = team, id = <team_id>
//! manager/ot     -> management_source_kind = ownership_transfer,
//!                   id = <transfer_id>
//! ```
//!
//! Non-role edges (permission_profile/rules, including the friend
//! default-profile edges) use the fixed `none/none` encoding — see
//! [`is_non_role_source`]. Missing/unknown role sources must never
//! default to `direct`; they are data errors.

use serde::{Deserialize, Serialize};

use crate::edge_permission::GrantKind;

/// `grant_ref_id` placeholder for Owner/Manager role edges. It does not
/// point at a `PermissionProfile` (spec §5.1).
pub const ROLE_GRANT_REF_ID: u64 = 0;

/// Source encoding of every non-role edge (`permission_profile`/`rules`,
/// including friend edges carried by the default profile). All stores,
/// migrations and writers must use exactly these values.
pub const NON_ROLE_SOURCE_KIND: &str = "none";
/// See [`NON_ROLE_SOURCE_KIND`].
pub const NON_ROLE_SOURCE_ID: &str = "none";

/// Fixed source encoding of the owner role edge (spec §5.1); it
/// participates in the same six-column dedup and revoke semantics as
/// manager edges but cannot be chosen by any writer.
pub const OWNER_SOURCE_KIND: &str = "owner";
/// See [`OWNER_SOURCE_KIND`].
pub const OWNER_SOURCE_ID: &str = "owner";

/// `management_source_kind` of a direct manager edge (manual
/// `PUT /bots/{bot_id}/managers/{user_id}` authorization).
pub const DIRECT_SOURCE_KIND: &str = "direct";
/// Fixed `management_source_id` of a direct manager edge.
pub const DIRECT_SOURCE_ID: &str = "manual";

/// `ownership_version` of a Bot whose ownership is not initialized yet.
pub const UNINITIALIZED_OWNERSHIP_VERSION: u64 = 0;
/// `ownership_version` assigned to a Bot at first-owner initialization.
pub const INITIALIZED_OWNERSHIP_VERSION: u64 = 1;

// ---------------------------------------------------------------------------
// VARCHAR width budgets of the authority ledger columns (migration
// 033_bot_authority.sql / sqlite twin 034). The migrations are unshipped and
// may still change widths; these constants are the single source the SQL
// literals are pinned to (bootstrap test
// `sql_files_share_the_domain_source_encoding_constants`) and the budget the
// writer conformance tests assert against.
// ---------------------------------------------------------------------------

/// `audit_id` width of `bcs_bot_action_audits`, `bot_manager_changes` and
/// `bot_ownership_initializations` (MySQL VARCHAR, single-column UNIQUE
/// key: 512 × 4 = 2048 bytes < InnoDB's 3072-byte limit).
///
/// Derivation — worst composite over every writer stays under the budget by
/// construction, because every input piece is itself bounded by its sibling
/// column width:
/// - action-audit lanes: `{lane}:{env}:{operation_id}:{step_key}` with the
///   longest lane prefix `chat-run-action` (16), env ≤ its column (64),
///   operation_id ≤ [`AUTHORITY_OPERATION_ID_VARCHAR_WIDTH`], step_key ≤
///   its column (96) => 16 + 1 + 64 + 1 + 256 + 1 + 96 = 435 ≤ 512;
/// - `bot_manager_changes`: `{operation_id}-{edge_id}` with edge_id ≤ 19
///   digits => 256 + 1 + 19 = 276 ≤ 512;
/// - `bot_ownership_initializations`: `{operation_id}-init-{bot_id}` with
///   bot_id ≤ the `bcs_bots.bot_uuid` shape (~40) => ≈ 302 ≤ 512.
pub const AUTHORITY_AUDIT_ID_VARCHAR_WIDTH: usize = 512;

/// `operation_id` column width everywhere in migrations 032/033 (audit slot
/// unique key `uk_bot_action_audit_slot` = env(256B) + operation_id(1024B)
/// + step_key(384B) = 1664 bytes < 3072).
///
/// Writers embed bounded resource ids (`run_id`/`delivery_id` ≤ 128 chars,
/// uuids 36, fixed lane prefixes ≤ ~28) or system-lane ids
/// (`{system_id}:{uuid}`); the observed worst shape is
/// `bot-event-delivery:{delivery_id}:{uuid}` ≈ 190, so 256 keeps headroom
/// while parent-embedding writers (`{parent}:{resource_id}`) stay legal at
/// 256 + 1 + 128 < 384.
pub const AUTHORITY_OPERATION_ID_VARCHAR_WIDTH: usize = 256;

/// `service_id` column width of `bot_manager_sync_operations` — the bound
/// the credential verifier itself enforces on the `sub` claim
/// (`MAX_CLAIM_LEN` = 256), so every verified service id fits.
/// The unique key `uk_manager_sync_scope` stays within
/// 256 + 1024 + 1024 + 256 + 256 = 2816 bytes < 3072.
pub const AUTHORITY_SERVICE_ID_VARCHAR_WIDTH: usize = 256;

/// Schema-backed bound for CLIENT-SUPPLIED ids of the authority lanes
/// (team sync `team_id` / `new_team_id` / `idempotency_key`, transfer
/// `client_request_id`, migration `batch_id`): every storing column is
/// VARCHAR(64) and participates in wide composite unique keys that cannot
/// be widened further, so the application validates the bound up front
/// (fail-closed 400) instead of letting an overlong id die as a DB 1406.
pub const AUTHORITY_CLIENT_ID_MAX: usize = 64;

/// The explicit access relation a Human holds on a physical Bot.
///
/// Missing role is represented by `Option<BotAccessRelation>`; a
/// corrupted or uninitialized authority representation is an error, not
/// an implicit role.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotAccessRelation {
    /// The single effective owner of an initialized Bot (spec §4).
    Owner,
    /// An explicitly granted manager (any management source).
    Manager,
}

impl BotAccessRelation {
    /// The edge `GrantKind` carrying this relation.
    pub fn grant_kind(&self) -> GrantKind {
        match self {
            Self::Owner => GrantKind::Owner,
            Self::Manager => GrantKind::Manager,
        }
    }

    /// Fixed storage encoding of the source parts for this role edge.
    ///
    /// Owner never uses the manager [`ManagementSource`] enum: the
    /// passed-in source is ignored and the fixed `owner/owner` pair is
    /// returned. Manager edges encode the given management source.
    pub fn source_parts<'a>(
        &self,
        source: &'a ManagementSource,
    ) -> (&'a str, &'a str) {
        match self {
            Self::Owner => (OWNER_SOURCE_KIND, OWNER_SOURCE_ID),
            Self::Manager => source.storage_parts(),
        }
    }
}

/// A management source of a manager role edge (spec §5.1).
///
/// Owner edges do NOT use this enum. `Team` and `OwnershipTransfer`
/// hold non-empty, exact (case-sensitive) identity strings; empty IDs
/// are rejected at construction and at serde decode so a corrupt
/// storage value can never silently become a valid source.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "ManagementSourceRepr", into = "ManagementSourceRepr")]
pub enum ManagementSource {
    /// Manual single-user authorization via the owner/manager API.
    Direct,
    /// Manager by platform team synchronization (`team/<team_id>`).
    Team(String),
    /// Manager retained across an accepted ownership transfer
    /// (`ownership_transfer/<transfer_id>`).
    OwnershipTransfer(String),
}

// Serde intermediate keeping the externally-tagged JSON shape while
// rejecting empty inner IDs at the decode boundary.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum ManagementSourceRepr {
    Direct,
    Team(String),
    OwnershipTransfer(String),
}

impl From<ManagementSource> for ManagementSourceRepr {
    fn from(value: ManagementSource) -> Self {
        match value {
            ManagementSource::Direct => Self::Direct,
            ManagementSource::Team(id) => Self::Team(id),
            ManagementSource::OwnershipTransfer(id) => Self::OwnershipTransfer(id),
        }
    }
}

impl TryFrom<ManagementSourceRepr> for ManagementSource {
    type Error = AuthorityShapeError;

    fn try_from(repr: ManagementSourceRepr) -> Result<Self, Self::Error> {
        match repr {
            ManagementSourceRepr::Direct => Ok(Self::Direct),
            ManagementSourceRepr::Team(id) => Self::team(id),
            ManagementSourceRepr::OwnershipTransfer(id) => Self::ownership_transfer(id),
        }
    }
}

/// Shape violation of a role-edge source encoding.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum AuthorityShapeError {
    /// Inner source id (team id / transfer id) is empty or blank.
    #[error("empty management source id (kind '{kind}')")]
    EmptySourceId { kind: String },
    /// Unrecognized `management_source_kind`/id pair; decode must fail
    /// closed instead of defaulting to a valid source.
    #[error("unknown management source encoding: kind '{kind}', id '{id}'")]
    UnknownSource { kind: String, id: String },
}

impl ManagementSource {
    /// Typed constructor for a team source; rejects empty/blank IDs.
    pub fn team(id: impl Into<String>) -> Result<Self, AuthorityShapeError> {
        let id = id.into();
        if id.trim().is_empty() {
            return Err(AuthorityShapeError::EmptySourceId {
                kind: "team".to_string(),
            });
        }
        Ok(Self::Team(id))
    }

    /// Typed constructor for an ownership-transfer source; rejects
    /// empty/blank IDs.
    pub fn ownership_transfer(id: impl Into<String>) -> Result<Self, AuthorityShapeError> {
        let id = id.into();
        if id.trim().is_empty() {
            return Err(AuthorityShapeError::EmptySourceId {
                kind: "ownership_transfer".to_string(),
            });
        }
        Ok(Self::OwnershipTransfer(id))
    }

    pub fn storage_parts(&self) -> (&str, &str) {
        match self {
            Self::Direct => (DIRECT_SOURCE_KIND, DIRECT_SOURCE_ID),
            Self::Team(id) => ("team", id.as_str()),
            Self::OwnershipTransfer(id) => ("ownership_transfer", id.as_str()),
        }
    }

    /// Whether the direct manager API (`DELETE /bots/{bot_id}/managers/
    /// {user_id}`) may revoke a manager holding this source. `team/*`
    /// sources are governed exclusively by team synchronization and are
    /// out of the direct API's scope (spec §6).
    pub fn revocable_by_direct_api(&self) -> bool {
        matches!(self, Self::Direct | Self::OwnershipTransfer(_))
    }
}

/// Strict decode result of the `(kind, id)` source parts of a ROLE edge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecodedRoleSource {
    /// The fixed `owner/owner` encoding.
    Owner,
    /// A manager edge with the decoded management source.
    Manager(ManagementSource),
}

/// Decode the `(management_source_kind, management_source_id)` pair of a
/// role edge strictly. Non-role edges (`none/none`), unknown kinds, and
/// mismatched IDs (including `direct` with a non-`manual` id, or empty
/// team/transfer ids) are errors — never defaults into a valid source.
pub fn decode_role_source(
    kind: &str,
    id: &str,
) -> Result<DecodedRoleSource, AuthorityShapeError> {
    if kind == OWNER_SOURCE_KIND && id == OWNER_SOURCE_ID {
        return Ok(DecodedRoleSource::Owner);
    }
    match kind {
        DIRECT_SOURCE_KIND if id == DIRECT_SOURCE_ID => {
            Ok(DecodedRoleSource::Manager(ManagementSource::Direct))
        }
        "team" => ManagementSource::team(id).map(DecodedRoleSource::Manager),
        "ownership_transfer" => {
            ManagementSource::ownership_transfer(id).map(DecodedRoleSource::Manager)
        }
        other => Err(AuthorityShapeError::UnknownSource {
            kind: other.to_string(),
            id: id.to_string(),
        }),
    }
}

/// Whether the `(kind, id)` source parts are the fixed non-role
/// `none/none` encoding used by all `permission_profile`/`rules` edges.
pub fn is_non_role_source(kind: &str, id: &str) -> bool {
    kind == NON_ROLE_SOURCE_KIND && id == NON_ROLE_SOURCE_ID
}

/// Operator identity of a role-lifecycle change (spec §5.4
/// `bot_manager_changes.actor_kind/actor_id`).
///
/// Used ONLY for role lifecycles (manager mutations, ownership
/// initialization/transfer, team sync). Ordinary business operations on
/// behalf of bots use `BotOperationActor` (service-api
/// `types/bot_operation.rs`). Service/system identifiers are never
/// recorded as Human user IDs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AuditActor {
    /// An authenticated Human; the trusted User ID is recorded verbatim.
    Human { user_id: String },
    /// A verified platform service credential
    /// (e.g. team-manager sync service); records the verified service id.
    Service { service_id: String },
    /// Governed cleanup/migration system action; records the fixed
    /// system identifier.
    System { name: String },
}

impl AuditActor {
    /// `actor_kind` column value (`human`/`service`/`system`).
    pub fn kind_str(&self) -> &'static str {
        match self {
            Self::Human { .. } => "human",
            Self::Service { .. } => "service",
            Self::System { .. } => "system",
        }
    }

    /// `actor_id` column value: the authenticated Human user id, the
    /// verified platform service id, or the fixed system identifier.
    pub fn actor_id(&self) -> &str {
        match self {
            Self::Human { user_id } => user_id.as_str(),
            Self::Service { service_id } => service_id.as_str(),
            Self::System { name } => name.as_str(),
        }
    }
}

/// A single manager-list mutation requested through the governed
/// manager API or team-sync repair entry (spec §6).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ManagerMutation {
    /// Idempotently grant one Human the direct/manual manager source.
    GrantDirect { user_id: String },
    /// Idempotently revoke one Human's non-team (direct and
    /// ownership_transfer) manager sources; `team/*` sources stay.
    RevokeNonTeam { user_id: String },
}

/// Result of a [`ManagerMutation`].
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct ManagerMutationResult {
    /// Whether the mutation actually changed a stored edge. Repeated
    /// grants/revokes of the current state return `false` (spec §5.4).
    pub changed: bool,
    /// Team source ids still held by the subject after a non-team
    /// revoke; empty unless a revoke left team sources in place.
    pub remaining_team_sources: Vec<String>,
}

/// Current ownership state of one Bot (spec §5.2).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipState {
    /// The single effective owner User ID of an initialized Bot.
    /// Uninitialized Bots never yield an [`OwnershipState`]; they are
    /// reported as an error branch by the authority contract.
    pub owner_user_id: String,
    /// Monotonic optimistic-concurrency version: 0 = uninitialized,
    /// 1 = first initialization, +1 per accepted transfer. Manager
    /// changes never bump it.
    pub ownership_version: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn storage_parts_match_spec_encoding() {
        assert_eq!(
            ManagementSource::Direct.storage_parts(),
            (DIRECT_SOURCE_KIND, DIRECT_SOURCE_ID)
        );
        assert_eq!(
            ManagementSource::Team("t".into()).storage_parts(),
            ("team", "t")
        );
        assert_eq!(
            ManagementSource::OwnershipTransfer("x".into()).storage_parts(),
            ("ownership_transfer", "x")
        );
    }

    #[test]
    fn audit_actor_kind_and_id_never_swap() {
        let human = AuditActor::Human {
            user_id: "u".into(),
        };
        let service = AuditActor::Service {
            service_id: "svc".into(),
        };
        let system = AuditActor::System {
            name: "governance".into(),
        };
        assert_eq!(human.kind_str(), "human");
        assert_eq!(human.actor_id(), "u");
        assert_eq!(service.kind_str(), "service");
        assert_eq!(system.actor_id(), "governance");
    }
}
