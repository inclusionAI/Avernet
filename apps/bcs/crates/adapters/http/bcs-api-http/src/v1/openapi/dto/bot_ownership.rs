//! Wire DTOs of the Task 14 ownership-transfer lane (spec §11.1).
//!
//! Parsing boundaries owned HERE (transport contract):
//! - the create body is `additionalProperties: false` with EXACTLY
//!   `to_user_id`/`expected_owner_version`/`client_request_id`;
//!   `expected_owner_version` must be >= 1 and `client_request_id` must
//!   be a UUID — malformed values are 400 before any application call;
//! - the inbox/outbox query carries `direction` (received|sent, default
//!   received), the optional effective-`status` enum, and positional
//!   `offset`/`limit` (limit 1..=100, default 20; `deny_unknown_fields`
//!   rejects undeclared query parameters);
//! - accept/reject/cancel carry NO business body: absent or `{}` is the
//!   contract, any other content is 400 (the HTML-free actions never
//!   grow a body parameter);
//! - responses never name a `current_owner` — receipts are history;
//!   the live owner goes through the ownership query.

use bcs_service_api::application::v1::{
    BotOwnership, BotOwnershipTransferReceipt, TerminalReason, TransferListDirection,
    TransferStatus,
};
use serde::{Deserialize, Serialize};

fn default_limit() -> u64 {
    20
}

fn default_direction() -> DirectionDto {
    DirectionDto::Received
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum DirectionDto {
    Received,
    Sent,
}

impl From<DirectionDto> for TransferListDirection {
    fn from(direction: DirectionDto) -> Self {
        match direction {
            DirectionDto::Received => Self::Received,
            DirectionDto::Sent => Self::Sent,
        }
    }
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum StatusDto {
    Pending,
    Accepted,
    Rejected,
    Cancelled,
    Expired,
    Invalidated,
}

impl From<StatusDto> for TransferStatus {
    fn from(status: StatusDto) -> Self {
        match status {
            StatusDto::Pending => Self::Pending,
            StatusDto::Accepted => Self::Accepted,
            StatusDto::Rejected => Self::Rejected,
            StatusDto::Cancelled => Self::Cancelled,
            StatusDto::Expired => Self::Expired,
            StatusDto::Invalidated => Self::Invalidated,
        }
    }
}

/// `GET /bots/{bot_id}/ownership` response data (spec §11.1).
#[derive(Debug, Serialize)]
pub struct BotOwnershipDto {
    pub bot_id: String,
    pub owner_user_id: String,
    pub ownership_version: u64,
}

impl From<BotOwnership> for BotOwnershipDto {
    fn from(ownership: BotOwnership) -> Self {
        Self {
            bot_id: ownership.bot_id,
            owner_user_id: ownership.owner_user_id,
            ownership_version: ownership.ownership_version,
        }
    }
}

/// The wire transfer receipt: the §11.1 confirmation-scope projection
/// with no `current_owner`, no env and no `client_request_id`.
#[derive(Debug, Serialize)]
pub struct BotOwnershipTransferReceiptDto {
    pub transfer_id: String,
    pub bot_id: String,
    pub from_user_id: String,
    pub to_user_id: String,
    /// `pending` | `accepted` | `rejected` | `cancelled` | `expired` |
    /// `invalidated` (the effective-status projection, spec §9.1).
    pub status: String,
    pub expected_owner_version: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result_owner_version: Option<u64>,
    pub expires_at: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub decided_by: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub decided_at: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub terminal_reason: Option<String>,
    pub bot_name_snapshot: String,
    pub gmt_create: u64,
    pub gmt_modified: u64,
}

impl From<BotOwnershipTransferReceipt> for BotOwnershipTransferReceiptDto {
    fn from(receipt: BotOwnershipTransferReceipt) -> Self {
        Self {
            transfer_id: receipt.transfer_id,
            bot_id: receipt.bot_id,
            from_user_id: receipt.from_user_id,
            to_user_id: receipt.to_user_id,
            status: transfer_status_text(&receipt.status),
            expected_owner_version: receipt.expected_owner_version,
            result_owner_version: receipt.result_owner_version,
            expires_at: receipt.expires_at,
            decided_by: receipt.decided_by,
            decided_at: receipt.decided_at,
            terminal_reason: receipt.terminal_reason.map(|reason| terminal_reason_text(&reason)),
            bot_name_snapshot: receipt.bot_name_snapshot,
            gmt_create: receipt.gmt_create,
            gmt_modified: receipt.gmt_modified,
        }
    }
}

fn transfer_status_text(status: &TransferStatus) -> String {
    match status {
        TransferStatus::Pending => "pending",
        TransferStatus::Accepted => "accepted",
        TransferStatus::Rejected => "rejected",
        TransferStatus::Cancelled => "cancelled",
        TransferStatus::Expired => "expired",
        TransferStatus::Invalidated => "invalidated",
    }
    .to_string()
}

fn terminal_reason_text(reason: &TerminalReason) -> String {
    match reason {
        TerminalReason::BotDeleted => "bot_deleted",
        TerminalReason::ActorUnavailable => "actor_unavailable",
        TerminalReason::OwnerChanged => "owner_changed",
    }
    .to_string()
}

/// The create request body (spec §11.1): exactly the three mandatory
/// fields; shape validation happens here so a malformed value never
/// reaches an application command.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateBotOwnershipTransferRequest {
    pub to_user_id: String,
    pub expected_owner_version: u64,
    pub client_request_id: String,
}

impl CreateBotOwnershipTransferRequest {
    /// Transport-level shape validation (spec §11.1): non-blank
    /// recipient, a positive version snapshot, and a UUID idempotency
    /// key. `Err` is the 400 message.
    pub fn validate(&self) -> Result<(), String> {
        if self.to_user_id.trim().is_empty() {
            return Err("'to_user_id' must be a non-blank identity".to_string());
        }
        if self.expected_owner_version == 0 {
            return Err("'expected_owner_version' must be >= 1".to_string());
        }
        if uuid::Uuid::parse_str(self.client_request_id.trim()).is_err() {
            return Err("'client_request_id' must be a UUID".to_string());
        }
        Ok(())
    }
}

/// The inbox/outbox listing query (spec §11.1).
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListBotOwnershipTransfersQuery {
    #[serde(default = "default_direction")]
    pub direction: DirectionDto,
    #[serde(default)]
    pub status: Option<StatusDto>,
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
}

impl ListBotOwnershipTransfersQuery {
    /// Transport-level window validation: limit 1..=100 (default 20 at
    /// the transport; the application re-proves the bound so no other
    /// caller can smuggle an out-of-range window).
    pub fn validate(&self) -> Result<(), String> {
        if self.limit == 0 || self.limit > 100 {
            return Err("limit must be within 1..=100".to_string());
        }
        Ok(())
    }
}

/// `GET /ownership-transfers` response data.
#[derive(Debug, Serialize)]
pub struct BotOwnershipTransferPageDto {
    pub items: Vec<BotOwnershipTransferReceiptDto>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}