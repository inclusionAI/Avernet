//! Wire DTOs of the Task 13 manager/team-manager lanes (spec §6/§6.1).
//!
//! Parsing boundaries owned HERE (transport contract, spec §6):
//! - the manager list query only carries `offset`/`limit`;
//! - the grant carries NO business body: any JSON body content — most
//!   importantly a `team` field — is rejected 400 (the direct API never
//!   grows a team parameter; unknown body fields never reach an
//!   application command);
//! - the team sync body is `additionalProperties: false` with exactly
//!   `operation`/`manager_user_ids`/`idempotency_key` (+`new_team_id`
//!   for move, forbidden for sync). A MISSING or null
//!   `manager_user_ids` never decodes into an empty snapshot — 400 —
//!   and an empty ARRAY stays a legal full revoke (200). Duplicate ids
//!   pass through verbatim: the store normalizes them into a set
//!   inside its transaction;
//! - the DELETE member repair keeps its identity in `?user_id=` plus
//!   the `Idempotency-Key` header (the POST repair body is
//!   `user_id`/`idempotency_key` only);
//! - no service identity, scope, or credential ever parses from a body.

use bcs_service_api::application::v1::{
    BotManagerEntry, BotManagerGrantResult, BotManagerPage, BotManagerRevokeResult,
    TeamManagerOperationKind, TeamManagerSyncCommand, TeamSyncReceiptValue,
};

use serde::{Deserialize, Serialize};
use serde_json::Value;

fn default_limit() -> u64 {
    20
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListBotManagersQuery {
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
}

/// Flattened manager list page (the envelope's `data`).
#[derive(Debug, Serialize)]
pub struct BotManagerPageDto {
    pub bot_id: String,
    pub owner_user_id: String,
    pub items: Vec<BotManagerEntry>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}

impl From<BotManagerPage> for BotManagerPageDto {
    fn from(page: BotManagerPage) -> Self {
        Self {
            bot_id: page.bot_id,
            owner_user_id: page.owner_user_id,
            items: page.items,
            total: page.total,
            offset: page.offset,
            limit: page.limit,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct BotManagerGrantedDto {
    pub bot_id: String,
    pub user_id: String,
    pub role: String,
    pub changed: bool,
}

impl From<BotManagerGrantResult> for BotManagerGrantedDto {
    fn from(result: BotManagerGrantResult) -> Self {
        Self {
            bot_id: result.bot_id,
            user_id: result.user_id,
            role: result.role,
            changed: result.changed,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct BotManagerRevokedDto {
    pub bot_id: String,
    pub user_id: String,
    pub revoked: bool,
    pub remaining_team_sources: Vec<String>,
}

impl From<BotManagerRevokeResult> for BotManagerRevokedDto {
    fn from(result: BotManagerRevokeResult) -> Self {
        Self {
            bot_id: result.bot_id,
            user_id: result.user_id,
            revoked: result.revoked,
            remaining_team_sources: result.remaining_team_sources,
        }
    }
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
enum TeamManagerOperationDto {
    Sync,
    Move,
}

/// The team snapshot body (spec §6.1). `manager_user_ids` is deliberately
/// a raw [`Value`]: presence, null, type, and item-shape are validated
/// HERE so a missing/null snapshot can never decode into an empty one.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TeamManagerSyncRequest {
    operation: TeamManagerOperationDto,
    manager_user_ids: Option<Value>,
    idempotency_key: String,
    #[serde(default)]
    new_team_id: Option<String>,
}

impl TeamManagerSyncRequest {
    /// Transport-level shape validation (spec §6.1): exactly the three
    /// required fields, `move` needs a non-blank `new_team_id` different
    /// from the URL team, `sync` rejects it.
    pub fn into_command(
        self,
        service: bcs_service_api::application::v1::VerifiedTeamService,
        bot_id: String,
        team_id: String,
    ) -> Result<TeamManagerSyncCommand, String> {
        let manager_user_ids = parse_snapshot(self.manager_user_ids)?;
        let operation = match (self.operation, self.new_team_id) {
            (TeamManagerOperationDto::Sync, None) => TeamManagerOperationKind::Sync,
            (TeamManagerOperationDto::Move, Some(new_team_id)) => {
                if new_team_id.trim().is_empty() {
                    return Err(
                        "operation 'move' requires a non-blank 'new_team_id'".to_string(),
                    );
                }
                TeamManagerOperationKind::Move { new_team_id }
            }
            (TeamManagerOperationDto::Move, None) => {
                return Err("operation 'move' requires 'new_team_id'".to_string());
            }
            (TeamManagerOperationDto::Sync, Some(_)) => {
                return Err(
                    "operation 'sync' must not carry 'new_team_id' (use operation 'move')"
                        .to_string(),
                );
            }
        };
        let idempotency_key = self.idempotency_key.trim().to_string();
        if idempotency_key.is_empty() {
            return Err("'idempotency_key' must be a non-blank identity".to_string());
        }
        Ok(TeamManagerSyncCommand {
            service,
            bot_id,
            team_id,
            operation,
            manager_user_ids,
            idempotency_key,
        })
    }
}

/// A missing snapshot, a null snapshot, a non-array snapshot, or a
/// non-string/blank member is 400 — never an implicit empty snapshot.
fn parse_snapshot(raw: Option<Value>) -> Result<Vec<String>, String> {
    let Some(Value::Array(items)) = raw else {
        return Err("'manager_user_ids' is required and must be an array".to_string());
    };
    let mut manager_user_ids = Vec::with_capacity(items.len());
    for item in items {
        let Value::String(user_id) = item else {
            return Err("'manager_user_ids' entries must be strings".to_string());
        };
        if user_id.trim().is_empty() {
            return Err("'manager_user_ids' entries must not be blank".to_string());
        }
        manager_user_ids.push(user_id);
    }
    // Duplicates stay verbatim (the store normalizes into a set); an empty
    // array is a legal validated full revoke.
    Ok(manager_user_ids)
}

/// The single-member repair POST body (spec §6.1 internal repair lane):
/// `user_id` + `idempotency_key` only.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TeamMemberRepairRequest {
    pub user_id: String,
    pub idempotency_key: String,
}

#[derive(Debug, Serialize)]
pub struct TeamSyncReceiptDto {
    pub operation_id: String,
    pub bot_id: String,
    pub team_id: String,
    /// `sync` | `move` (the committed operation KIND).
    pub operation: String,
    /// Present only for a committed move (the moved-to team).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub new_team_id: Option<String>,
    pub granted_count: u64,
    pub revoked_count: u64,
}

impl From<TeamSyncReceiptValue> for TeamSyncReceiptDto {
    fn from(receipt: TeamSyncReceiptValue) -> Self {
        let (operation, new_team_id) = match receipt.operation {
            TeamManagerOperationKind::Sync => ("sync".to_string(), None),
            TeamManagerOperationKind::Move { new_team_id } => {
                ("move".to_string(), Some(new_team_id))
            }
        };
        Self {
            operation_id: receipt.operation_id,
            bot_id: receipt.bot_id,
            team_id: receipt.team_id,
            operation,
            new_team_id,
            granted_count: receipt.granted_count,
            revoked_count: receipt.revoked_count,
        }
    }
}