//! Legacy `FriendService` compatibility facade over the edge-permission
//! `ConnectService`.
//!
//! The old `/friends/*` HTTP routes speak the legacy friend vocabulary
//! (`FriendRequest`, `pending/accepted/rejected`, `409 PendingRequestExists`)
//! while the friend-connect lifecycle is owned by the edge-permission
//! `ConnectService`. This facade keeps that wire contract intact and routes
//! every mutation through `ConnectService`, so legacy callers emit the same
//! owner notifications, status transitions and reverse Bot↔Bot rows as the
//! new routes.
//!
//! Semantics mirror the retired `bcs_friend::application::Friend`:
//! - target visibility gate → `BotNotFound` (legacy `ensure_target_actor_not_private`),
//! - duplicate pending → `PendingRequestExists` (legacy 409),
//! - receiver-only accept/reject with the legacy wording,
//! - `approved` projects back as `accepted`.
//!
//! The inbound `RequestAuthHeaders` carried by the commands are forwarded to
//! `ConnectService` so downstream owner notifications and friend-auth-sync
//! calls authenticate as the acting human. `ConnectService::reject` takes no
//! auth context (it has no downstream auth-consuming call), so reject ignores
//! it.
//!
//! Friendship membership and requests come exclusively from edge permission.
//! Legacy request IDs are not migrated or read as a fallback. Friend names and
//! presence are enriched from the registry while preserving the wire contract.
//!
//! COMPATIBILITY SHIM: delete this module together with the legacy
//! `/friends/*` routes once they are retired.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::edge_permission::{RequestKind, RequestStatus};

use bcs_service_api::application::connect::{ConnectService, ConnectStatus, RequestDirection};
use bcs_service_api::{
    BotRegistryCoreService, CreateFriendRequestCommand, FriendListEntry, FriendRequest,
    FriendRequestDecisionCommand, FriendRequestDirection, FriendRequestStatus, FriendService,
    FriendUseCaseError, ListFriendRequestsCommand, ListFriendsCommand, ServiceError,
};

/// `FriendService` adapter backed by the edge-permission `ConnectService`.
///
/// All friendship/request reads and mutations use `connect`; `registry` serves
/// the target-visibility gate and friend name/presence enrichment.
pub struct EdgeConnectFriendService {
    connect: Arc<dyn ConnectService>,
    registry: Arc<dyn BotRegistryCoreService>,
}

impl EdgeConnectFriendService {
    pub fn new(
        connect: Arc<dyn ConnectService>,
        registry: Arc<dyn BotRegistryCoreService>,
    ) -> Self {
        Self {
            connect,
            registry,
        }
    }

    /// Legacy target gate: the target must exist and be externally visible
    /// (`public` | `protected`). Mirrors `ensure_target_actor_not_private`.
    async fn ensure_target_actor_not_private(
        &self,
        target_id: &str,
    ) -> Result<(), FriendUseCaseError> {
        match self.registry.get(target_id).await {
            Some(bot) if matches!(bot.capabilities.visibility.as_str(), "public" | "protected") => {
                Ok(())
            }
            Some(_) => {
                tracing::debug!(
                    target_id = %target_id,
                    "friend request target exists but is not externally visible"
                );
                Err(FriendUseCaseError::service(ServiceError::BotNotFound(
                    target_id.to_string(),
                )))
            }
            None => {
                tracing::debug!(
                    target_id = %target_id,
                    "friend request target actor does not exist"
                );
                Err(FriendUseCaseError::service(ServiceError::BotNotFound(
                    target_id.to_string(),
                )))
            }
        }
    }

    /// Legacy duplicate-pending pre-check (409 `PendingRequestExists`).
    ///
    /// `create_connect` is idempotent for a duplicate pending (it returns the
    /// existing id as `Pending`), but the legacy endpoint must keep answering
    /// 409 with the existing request id, so the pending row is looked up first.
    /// `created_by == caller` keeps this to the caller's own forward row and
    /// ignores the Bot↔Bot reverse pending row (created by the caller but sent
    /// to the caller). The lookup is a bounded existence query (`LIMIT 1` at
    /// the store): as `permission_requests` grows, an unbounded outbox scan
    /// would make every friend request create read the caller's full history.
    async fn ensure_no_pending_request(
        &self,
        caller: &str,
        to_bot: &str,
    ) -> Result<(), FriendUseCaseError> {
        if let Some(existing) = self
            .connect
            .find_pending_self_connect(caller, to_bot)
            .await
            .map_err(FriendUseCaseError::service)?
        {
            return Err(FriendUseCaseError::service(
                ServiceError::PendingRequestExists {
                    request_id: existing.request_id,
                    from_bot: Some(caller.to_string()),
                    to_bot: Some(to_bot.to_string()),
                },
            ));
        }
        Ok(())
    }

    /// Resolve the actual receiver from the persisted Connect request.
    /// A receiver hint is transport context, never authorization evidence.
    async fn request_receiver_actor(
        &self,
        command: &FriendRequestDecisionCommand,
    ) -> Result<String, FriendUseCaseError> {
        // A hint supplied by an adapter is not the authority: resolve the
        // persisted Connect row on every decision path, including Bot tokens.
        self.friend_request_receiver(&command.request_id).await
    }

    async fn authorize_request_receiver(
        &self,
        command: &FriendRequestDecisionCommand,
        action: &str,
    ) -> Result<(), FriendUseCaseError> {
        let request_to_bot = self.request_receiver_actor(command).await?;
        if request_to_bot != command.caller_actor_id {
            return Err(FriendUseCaseError::Forbidden(format!(
                "Cannot {} request intended for '{}': caller is '{}'",
                action, request_to_bot, command.caller_actor_id
            )));
        }
        Ok(())
    }
}

/// Current wall-clock time in epoch milliseconds (the unit the friend-request
/// rows carry).
fn now_epoch_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or_default()
}

/// Map the legacy direction filter onto the connect listing direction.
fn connect_direction(direction: FriendRequestDirection) -> RequestDirection {
    match direction {
        FriendRequestDirection::Sent => RequestDirection::Sent,
        FriendRequestDirection::Received => RequestDirection::Received,
        FriendRequestDirection::All => RequestDirection::All,
    }
}

/// Map the legacy status filter onto the connect vocabulary.
fn connect_status(status: FriendRequestStatus) -> RequestStatus {
    match status {
        FriendRequestStatus::Pending => RequestStatus::Pending,
        // Legacy `accepted` == connect `approved`.
        FriendRequestStatus::Accepted => RequestStatus::Approved,
        FriendRequestStatus::Rejected => RequestStatus::Rejected,
    }
}

/// Project the connect status back onto the legacy vocabulary.
fn legacy_status(status: RequestStatus) -> FriendRequestStatus {
    match status {
        RequestStatus::Pending => FriendRequestStatus::Pending,
        RequestStatus::Approved => FriendRequestStatus::Accepted,
        // The legacy vocabulary only has pending/accepted/rejected; a cancelled
        // request is terminal and projects as rejected.
        RequestStatus::Rejected | RequestStatus::Cancelled => FriendRequestStatus::Rejected,
    }
}

#[async_trait]
impl FriendService for EdgeConnectFriendService {
    async fn create_friend_request(
        &self,
        command: CreateFriendRequestCommand,
    ) -> Result<FriendRequest, FriendUseCaseError> {
        self.ensure_target_actor_not_private(&command.to_bot)
            .await?;
        self.ensure_no_pending_request(&command.caller_actor_id, &command.to_bot)
            .await?;

        // The inbound principal (if any) is forwarded so the downstream
        // owner notification / friend-auth-sync call can authenticate as the
        // acting human; Bot↔Bot callers have no principal and send `None`.
        let result = self
            .connect
            .create_connect(
                &command.caller_actor_id,
                &command.to_bot,
                None,
                command.request_auth.clone(),
            )
            .await
            .map_err(FriendUseCaseError::service)?;

        let status = match result.status {
            ConnectStatus::Pending => FriendRequestStatus::Pending,
            // `Approved` with no request ids means "already friends"; anything
            // else approved is an auto-accepted request row.
            ConnectStatus::Approved | ConnectStatus::PublicNoEdge => {
                FriendRequestStatus::Accepted
            }
        };

        // No persisted row (already friends, or a defensive empty pending):
        // synthesize the legacy response with an empty id, exactly like the
        // legacy service did for an existing edge. The retired core stamped
        // `now` on that synthetic row, so the envelope must not advertise
        // epoch-zero timestamps.
        let Some(request_id) = result.request_ids.first() else {
            let now = now_epoch_ms();
            return Ok(FriendRequest {
                id: String::new(),
                from_bot: command.caller_actor_id,
                to_bot: command.to_bot,
                status,
                created_at: now,
                updated_at: now,
            });
        };

        let persisted = self
            .connect
            .get_request(request_id)
            .await
            .map_err(FriendUseCaseError::service)?;
        Ok(FriendRequest {
            id: persisted.request_id,
            from_bot: persisted.from_id,
            to_bot: persisted.to_id,
            status,
            created_at: persisted.created_at,
            updated_at: persisted.updated_at,
        })
    }

    async fn list_friend_requests(
        &self,
        command: ListFriendRequestsCommand,
    ) -> Result<Vec<FriendRequest>, FriendUseCaseError> {
        // Ruling: connect-listing failures propagate (the legacy route answers
        // 500 instead of a silently-empty 200). Success payloads are unchanged.
        let page = self
            .connect
            .list_requests(
                &command.caller_actor_id,
                connect_direction(command.direction),
                command.status_filter.map(connect_status),
                1,
                u32::MAX,
            )
            .await
            .map_err(FriendUseCaseError::service)?;

        Ok(page
            .items
            .into_iter()
            // A Bot↔Bot connect inserts a reverse pending row (to_bot→caller,
            // created_by=caller) that the legacy list never exposed; keep only
            // rows the caller itself authored in the listed direction.
            .filter(|request| request.created_by == request.from_id)
            // Only connect requests are friend requests; permission-profile,
            // rules and revoke requests share the store but are not part of
            // the legacy friend-request listing.
            .filter(|request| request.request_kind == RequestKind::Connect)
            .map(|request| FriendRequest {
                id: request.request_id,
                from_bot: request.from_id,
                to_bot: request.to_id,
                status: legacy_status(request.status),
                created_at: request.created_at,
                updated_at: request.updated_at,
            })
            .collect())
    }

    async fn accept_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        self.authorize_request_receiver(&command, "accept").await?;
        self.connect
            .approve(
                &command.request_id,
                &command.caller_actor_id,
                command.request_auth.clone(),
            )
            .await
            .map_err(FriendUseCaseError::service)?;
        Ok(())
    }

    async fn reject_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        self.authorize_request_receiver(&command, "reject").await?;
        // `ConnectService::reject` has no downstream auth-consuming call (no
        // notification, no friend-auth-sync), so the inbound principal has
        // nowhere to go on this leg and the reason slot stays `None`.
        self.connect
            .reject(&command.request_id, &command.caller_actor_id, None)
            .await
            .map_err(FriendUseCaseError::service)?;
        Ok(())
    }

    async fn friend_request_receiver(
        &self,
        request_id: &str,
    ) -> Result<String, FriendUseCaseError> {
        let request = self
            .connect
            .get_request(request_id)
            .await
            .map_err(FriendUseCaseError::service)?;
        // Defensive: only connect requests are friend requests; anything else
        // must not leak a permission-request receiver through the legacy route.
        if request.request_kind != RequestKind::Connect {
            return Err(FriendUseCaseError::service(
                ServiceError::FriendRequestNotFound(request_id.to_string()),
            ));
        }
        Ok(request.to_id)
    }

    async fn list_friends(
        &self,
        command: ListFriendsCommand,
    ) -> Result<Vec<FriendListEntry>, FriendUseCaseError> {
        self.list_edge_friends(command).await
    }
}

#[path = "connect_friend_list.rs"]
mod friend_list;
