//! Wire DTOs for the unified authority surface
//! (spec §5/§6). Projections only: internal mutation ids,
//! SQL internals, and the internal ownership version never
//! appear here.

use serde::{Deserialize, Serialize};

use bcs_service_api::application::v1::bot_authority::{
    BotOwnershipView, ManagerPage, ManagerSetChange, OwnershipChange,
    TransferStatus, TransferView, human_user_id_of_actor,
};

/// `PUT /bots/{bot_id}/managers` body (spec §5.2):
/// one full manager snapshot. The field is REQUIRED: a
/// missing or null `manager_user_ids` fails the whole
/// request — it must never be read as the empty
/// clear-everything snapshot.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SyncManagersBody {
    pub manager_user_ids: Vec<String>,
}

/// Owner summary (spec §3: minimal projection, no
/// internal version).
#[derive(Debug, Serialize)]
pub struct OwnerSummaryDto {
    pub bot_id: String,
    pub owner_user_id: String,
    pub owner_actor_id: String,
}

impl From<BotOwnershipView> for OwnerSummaryDto {
    fn from(view: BotOwnershipView) -> Self {
        Self {
            bot_id: view.bot_id,
            owner_user_id: view.owner_user_id,
            owner_actor_id: view.owner_actor_id,
        }
    }
}

/// `GET /bots/{bot_id}/managers` response: the owner
/// summary plus the paged manager set (items are User
/// IDs).
#[derive(Debug, Serialize)]
pub struct ManagerPageDto {
    pub owner: OwnerSummaryDto,
    pub items: Vec<String>,
    pub total: u64,
}

impl From<ManagerPage> for ManagerPageDto {
    fn from(page: ManagerPage) -> Self {
        Self {
            owner: page.owner.into(),
            items: page.items,
            total: page.total,
        }
    }
}

/// Result of a manager edit/sync (spec §5.2: final count
/// plus added/removed).
#[derive(Debug, Serialize)]
pub struct ManagerSetChangeDto {
    pub added: u64,
    pub removed: u64,
    pub total: u64,
}

impl From<ManagerSetChange> for ManagerSetChangeDto {
    fn from(change: ManagerSetChange) -> Self {
        Self {
            added: change.added,
            removed: change.removed,
            total: change.total,
        }
    }
}

/// `GET /bots/{bot_id}/ownership` response.
#[derive(Debug, Serialize)]
pub struct BotOwnershipDto {
    pub bot_id: String,
    pub owner_user_id: String,
    pub owner_actor_id: String,
}

impl From<BotOwnershipView> for BotOwnershipDto {
    fn from(view: BotOwnershipView) -> Self {
        Self {
            bot_id: view.bot_id,
            owner_user_id: view.owner_user_id,
            owner_actor_id: view.owner_actor_id,
        }
    }
}

/// `PUT /bots/{bot_id}/ownership` body (设计 §3.1): one
/// target owner. The field is REQUIRED: a missing or
/// null `owner_user_id` fails the whole request — it
/// must never read as a "clear owner" snapshot.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SyncOwnershipBody {
    pub owner_user_id: String,
}

/// Result of an ownership sync (设计 §3.2).
#[derive(Debug, Serialize)]
pub struct OwnershipChangeDto {
    pub bot_id: String,
    pub owner_user_id: Option<String>,
    pub previous_owner_user_id: Option<String>,
    pub changed: bool,
}

impl From<OwnershipChange> for OwnershipChangeDto {
    fn from(change: OwnershipChange) -> Self {
        Self {
            bot_id: change.bot_id,
            owner_user_id: change.owner_user_id,
            previous_owner_user_id: change.previous_owner_user_id,
            changed: change.changed,
        }
    }
}

/// One transfer row in its minimal projection: only the
/// current-round two parties and this round's fields (spec
/// §4.3/§6.4; no Bot configuration, no resource content).
#[derive(Debug, Serialize)]
pub struct TransferViewDto {
    pub bot_id: String,
    pub target_owner_actor_id: String,
    pub target_owner_user_id: Option<String>,
    pub from_owner_actor_id: String,
    pub from_owner_user_id: Option<String>,
    /// Effective status by DB time (pending past expiry
    /// projects to `expired` without writing, spec §6.4).
    pub status: String,
    pub requested_at: u64,
    pub expires_at: u64,
    pub updated_at: u64,
    pub decided_at: Option<u64>,
    /// `human` | `service` | `provider` | `system`.
    pub decided_by_kind: Option<String>,
    pub decided_by_id: Option<String>,
    pub reason: Option<String>,
    pub result_ownership_version: Option<u64>,
    pub bot_name_snapshot: Option<String>,
}

impl TransferViewDto {
    pub fn project(view: TransferView) -> Self {
        let TransferView {
            bot_id,
            target_owner_actor_id,
            from_owner_actor_id,
            status,
            requested_at,
            expires_at,
            updated_at,
            decided_at,
            decided_by_kind,
            decided_by_id,
            reason,
            result_ownership_version,
            bot_name_snapshot,
        } = view;
        let (target_owner_actor_id, target_owner_user_id) =
            actor_user_pair(&target_owner_actor_id);
        let (from_owner_actor_id, from_owner_user_id) =
            actor_user_pair(&from_owner_actor_id);
        Self {
            bot_id,
            target_owner_actor_id,
            target_owner_user_id,
            from_owner_actor_id,
            from_owner_user_id,
            status: status.as_str().to_string(),
            requested_at,
            expires_at,
            updated_at,
            decided_at,
            decided_by_kind: decided_by_kind
                .map(|kind| kind.as_str().to_string()),
            decided_by_id,
            reason,
            result_ownership_version,
            bot_name_snapshot,
        }
    }
}

/// User-ID projection of a Human actor id; non-Human
/// actor ids keep `None` (never silently reshaped).

/// Minimal inbox/outbox page (spec §6.1).
#[derive(Debug, Serialize)]
pub struct TransferPageDto {
    pub items: Vec<TransferViewDto>,
    pub total: u64,
}

/// Query for `GET /ownership-transfers`.
///
/// The documented `status` parameter is an OpenAPI
/// array (bot-authority.yaml `type: array`, so the
/// form-style REPEATED `status=pending&status=accepted`),
/// and native `Query` (serde_urlencoded) cannot decode
/// any sequence — a legal filter died at the extractor
/// with "expected a sequence". The raw query string is
/// therefore parsed here instead of by derivation and
/// the parser accepts the repeated form, a single
/// value, AND a comma-separated value list
/// (`status=pending,cancelled`).
///
/// Scalar semantics are preserved: `direction`,
/// `offset`, and `limit` stay single-valued (a
/// repeated occurrence is a bad request), and unknown
/// parameters are bad requests (the
/// `deny_unknown_fields` behavior).
#[derive(Debug, Default)]
pub struct TransferListParams {
    /// `received` (default) or `sent`.
    pub direction: Option<String>,
    /// `pending` | `accepted` | `rejected` |
    /// `cancelled` | `expired` | `invalidated`.
    pub status: Vec<String>,
    pub offset: u64,
    pub limit: Option<u64>,
}

impl TransferListParams {
    pub fn parse_query(raw: Option<&str>) -> Result<Self, String> {
        use url::form_urlencoded;

        let mut direction: Option<String> = None;
        let mut status: Vec<String> = Vec::new();
        let mut offset: Option<u64> = None;
        let mut limit: Option<u64> = None;
        for (key, value) in form_urlencoded::parse(
            raw.unwrap_or_default().as_bytes(),
        ) {
            match key.as_ref() {
                "direction" => {
                    if direction.is_some() {
                        return Err(
                            "query parameter 'direction' must \
                             not be repeated"
                                .to_string(),
                        );
                    }
                    direction = Some(value.into_owned());
                }
                "status" => {
                    // The documented repeated form plus the
                    // single-value and comma-separated
                    // spellings all route here.
                    for part in value.split(',') {
                        status.push(part.trim().to_string());
                    }
                }
                "offset" => {
                    if offset.is_some() {
                        return Err(
                            "query parameter 'offset' must \
                             not be repeated"
                                .to_string(),
                        );
                    }
                    offset = Some(value.parse::<u64>().map_err(|_| {
                        format!(
                            "query parameter 'offset' must be an \
                             unsigned integer, got '{value}'"
                        )
                    })?);
                }
                "limit" => {
                    if limit.is_some() {
                        return Err(
                            "query parameter 'limit' must \
                             not be repeated"
                                .to_string(),
                        );
                    }
                    limit =
                        Some(value.parse::<u64>().map_err(|_| {
                            format!(
                                "query parameter 'limit' must be an \
                                 unsigned integer, got '{value}'"
                            )
                        })?);
                }
                other => {
                    return Err(format!(
                        "unknown query parameter '{other}'"
                    ));
                }
            }
        }
        Ok(Self {
            direction,
            status,
            offset: offset.unwrap_or_default(),
            limit,
        })
    }
}

/// Transfer status set by the query (unknown spellings are
/// adapter-level bad requests).
impl TransferListParams {
    pub fn statuses(&self) -> Result<Vec<TransferStatus>, String> {
        self.status
            .iter()
            .map(|text| parse_transfer_status(text))
            .collect()
    }
}

fn parse_transfer_status(text: &str) -> Result<TransferStatus, String> {
    match text {
        "pending" => Ok(TransferStatus::Pending),
        "accepted" => Ok(TransferStatus::Accepted),
        "rejected" => Ok(TransferStatus::Rejected),
        "cancelled" => Ok(TransferStatus::Cancelled),
        "expired" => Ok(TransferStatus::Expired),
        "invalidated" => Ok(TransferStatus::Invalidated),
        other => Err(format!(
            "unknown transfer status '{other}'; allowed spellings \
             are pending, accepted, rejected, cancelled, expired, \
             invalidated"
        )),
    }
}

/// Parse the `direction` query spelling; unknown is a bad
/// request. `Ok(true)` is `received` (the default), `Ok(false)`
/// is `sent`.
pub fn parse_direction(text: Option<&str>) -> Result<bool, String> {
    match text {
        None | Some("received") => Ok(true),
        Some("sent") => Ok(false),
        Some(other) => Err(format!(
            "unknown direction '{other}'; allowed spellings are \
             received, sent"
        )),
    }
}

/// Split a Human actor id into the (actor, user-id)
/// projection pair.
pub fn actor_user_pair(actor_id: &str) -> (String, Option<String>) {
    (
        actor_id.to_string(),
        human_user_id_of_actor(actor_id),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_query_defaults() {
        for raw in [None, Some("")] {
            let params = TransferListParams::parse_query(raw)
                .expect("empty query parses");
            assert_eq!(params.direction, None);
            assert!(params.status.is_empty());
            assert_eq!(params.offset, 0);
            assert_eq!(params.limit, None);
        }
    }

    #[test]
    fn scalar_params_decode() {
        let params = TransferListParams::parse_query(Some(
            "direction=sent&offset=12&limit=7",
        ))
        .expect("scalars parse");
        assert_eq!(params.direction.as_deref(), Some("sent"));
        assert_eq!(params.offset, 12);
        assert_eq!(params.limit, Some(7));
    }

    #[test]
    fn single_status_value_is_accepted() {
        let params = TransferListParams::parse_query(Some(
            "status=pending",
        ))
        .expect("one status parses");
        assert_eq!(params.statuses().expect("statuses"), vec![
            TransferStatus::Pending
        ]);
    }

    #[test]
    fn repeated_status_values_are_accepted() {
        // The OpenAPI array form (`status=pending&status=
        // accepted`): the shape a native `Query` extractor
        // rejected with "expected a sequence".
        let params = TransferListParams::parse_query(Some(
            "status=pending&status=accepted",
        ))
        .expect("repeated statuses parse");
        assert_eq!(params.statuses().map(|statuses| statuses.len()),
            Ok(2));
        assert!(params.statuses()
            .expect("statuses")
            .contains(&TransferStatus::Pending));
        assert!(params.statuses()
            .expect("statuses")
            .contains(&TransferStatus::Accepted));
    }

    #[test]
    fn comma_separated_status_values_are_accepted() {
        let params = TransferListParams::parse_query(Some(
            "status=pending,cancelled",
        ))
        .expect("csv statuses parse");
        assert_eq!(
            params.statuses().expect("statuses").len(),
            2
        );
    }

    #[test]
    fn unknown_query_parameter_is_a_bad_request() {
        let error = TransferListParams::parse_query(Some(
            "direction=sent&bogus=1",
        ))
        .expect_err("unknown parameter stays a bad request");
        assert!(error.contains("bogus"), "{error}");
    }

    #[test]
    fn repeated_scalars_and_bad_numbers_are_bad_requests() {
        for raw in [
            "direction=received&direction=sent",
            "offset=1&offset=2",
            "limit=1&limit=2",
            "offset=nope",
            "limit=nope",
        ] {
            assert!(
                TransferListParams::parse_query(Some(raw))
                    .is_err(),
                "{raw} must not parse"
            );
        }
    }

    #[test]
    fn sync_ownership_body_requires_the_single_field() {
        let ok: SyncOwnershipBody = serde_json::from_str(
            r#"{"owner_user_id":"alice"}"#,
        )
        .expect("valid body");
        assert_eq!(ok.owner_user_id, "alice");
        assert!(
            serde_json::from_str::<SyncOwnershipBody>(r#"{}"#).is_err(),
            "missing field fails"
        );
        assert!(
            serde_json::from_str::<SyncOwnershipBody>(
                r#"{"owner_user_id":null}"#
            )
            .is_err(),
            "null fails"
        );
        assert!(
            serde_json::from_str::<SyncOwnershipBody>(
                r#"{"owner_user_id":"a","extra":1}"#
            )
            .is_err(),
            "unknown field fails"
        );
    }

    #[test]
    fn ownership_change_dto_serializes_nulls_for_the_noop() {
        let dto =
            OwnershipChangeDto::from(OwnershipChange::unchanged("bot_x"));
        let value = serde_json::to_value(&dto).expect("serialize");
        assert_eq!(value["owner_user_id"], serde_json::Value::Null);
        assert_eq!(
            value["previous_owner_user_id"],
            serde_json::Value::Null
        );
        assert_eq!(value["changed"], false);
    }
}
