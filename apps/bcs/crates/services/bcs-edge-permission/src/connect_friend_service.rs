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
//! `list_friends` is the one exception: the legacy name list still comes from
//! the injected legacy delegate (bot→`bcs_friendships`, human→relation) until
//! the group gate stops reading the legacy tables.
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
/// All request mutations and reads are delegated to `connect`; `registry` only
/// answers the legacy target-visibility gate; `legacy_list_delegate` serves the
/// (still legacy-owned) friend name list.
pub struct EdgeConnectFriendService {
    connect: Arc<dyn ConnectService>,
    registry: Arc<dyn BotRegistryCoreService>,
    legacy_list_delegate: Arc<dyn FriendService>,
}

impl EdgeConnectFriendService {
    pub fn new(
        connect: Arc<dyn ConnectService>,
        registry: Arc<dyn BotRegistryCoreService>,
        legacy_list_delegate: Arc<dyn FriendService>,
    ) -> Self {
        Self {
            connect,
            registry,
            legacy_list_delegate,
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
    /// to the caller).
    async fn ensure_no_pending_request(
        &self,
        caller: &str,
        to_bot: &str,
    ) -> Result<(), FriendUseCaseError> {
        let page = self
            .connect
            .list_requests(
                caller,
                RequestDirection::Sent,
                Some(RequestStatus::Pending),
                1,
                u32::MAX,
            )
            .await
            .map_err(FriendUseCaseError::service)?;

        if let Some(existing) = page
            .items
            .into_iter()
            // Only connect requests are friend requests: a pending
            // permission/rules/revoke request is not a duplicate friend
            // request and must not turn into a legacy 409.
            .find(|request| {
                request.request_kind == RequestKind::Connect
                    && request.to_id == to_bot
                    && request.created_by == caller
            })
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

    /// Legacy receiver resolution: the command may carry the receiver
    /// explicitly (URL addressee); otherwise read it from the request.
    async fn request_receiver_actor(
        &self,
        command: &FriendRequestDecisionCommand,
    ) -> Result<String, FriendUseCaseError> {
        if let Some(to_bot) = command.request_to_bot.as_deref() {
            return Ok(to_bot.to_string());
        }
        Ok(self
            .connect
            .get_request(&command.request_id)
            .await
            .map_err(FriendUseCaseError::service)?
            .to_id)
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

        // `request_auth` stays `None`: the legacy bot↔bot path carries no
        // inbound principal for friend-auth-sync, and human→bot never used the
        // sync hook on this route.
        let result = self
            .connect
            .create_connect(&command.caller_actor_id, &command.to_bot, None, None)
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
        // legacy service did for an existing edge.
        let Some(request_id) = result.request_ids.first() else {
            return Ok(FriendRequest {
                id: String::new(),
                from_bot: command.caller_actor_id,
                to_bot: command.to_bot,
                status,
                created_at: 0,
                updated_at: 0,
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
            .approve(&command.request_id, &command.caller_actor_id, None)
            .await
            .map_err(FriendUseCaseError::service)?;
        Ok(())
    }

    async fn reject_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        self.authorize_request_receiver(&command, "reject").await?;
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
        // The legacy name list (bot→bcs_friendships, human→relation) stays with
        // the legacy service; its consistency with connect edges is maintained
        // by `DbConnectService`'s friendship writeback.
        self.legacy_list_delegate.list_friends(command).await
    }
}