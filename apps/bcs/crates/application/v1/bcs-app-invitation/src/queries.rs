//! V1 Invitation + Friendship facade: read use cases (plan Task 12 split).
//!
//! Reads verify the caller's live authority first (a former creator with no
//! current role is denied, spec §12.2) and then project the legacy stores
//! onto the V1 shapes with stable ordering/pagination.

use bcs_service_api::application::v1::{
    ApplicationError, FriendConnectionPage, FriendConnectionRequestDirection,
    FriendConnectionRequestPage, FriendConnectionRequestStatus, FriendRequest,
    FriendRequestDirection, Friendship, ListBotFriendRequests, ListBotFriendships,
    ListFriendConnectionRequests, ListFriendConnections, Page,
};
use bcs_service_api::{
    ActorKind, ServiceError,
    FriendRequestDirection as DomainFriendRequestDirection,
};

use crate::{
    map_service_error, project_friend_connection, project_friend_connection_request,
    saturating_usize, InvitationFriendshipServiceImpl,
};

impl InvitationFriendshipServiceImpl {
    /// `list_bot_friendships`: the authenticated User must currently own or
    /// manage the Bot (live role facts, spec §12.4).
    pub(crate) async fn list_bot_friendships_impl(
        &self,
        command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        self.ensure_bot_resource(&command.caller, &command.bot_uuid)
            .await?;
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        let (friendships, total) = self
            .friends
            .list_friendships_paginated(&command.bot_uuid, command.offset, command.limit)
            .await
            .map_err(map_service_error)?;
        let items = friendships.iter().map(crate::project_friendship).collect();
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    /// `list_bot_friend_requests`: same live-role gate, then a stable
    /// `created_at DESC, request_id ASC` ordering before offset/limit so V1
    /// pagination is deterministic.
    pub(crate) async fn list_bot_friend_requests_impl(
        &self,
        command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        self.ensure_bot_resource(&command.caller, &command.bot_uuid)
            .await?;
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        let direction = match command.direction {
            FriendRequestDirection::Sent => DomainFriendRequestDirection::Sent,
            FriendRequestDirection::Received => DomainFriendRequestDirection::Received,
        };
        let mut requests = self
            .friend_requests
            .try_list_requests(&command.bot_uuid, direction, command.status)
            .await
            .map_err(map_service_error)?;
        // The repo returns all matches without ordering or pagination. Sort
        // `created_at` DESC with a `request_id` ASC tie-breaker, then apply
        // offset/limit so V1 pagination is stable. `try_list_requests`
        // propagates persistence failures (HTTP 500) instead of masking them
        // as an empty 200 page.
        requests.sort_by(|a, b| {
            b.created_at
                .cmp(&a.created_at)
                .then_with(|| a.id.cmp(&b.id))
        });
        let total = requests.len() as u64;
        let items = requests
            .iter()
            .skip(saturating_usize(command.offset))
            .take(saturating_usize(command.limit))
            .map(crate::project_friend_request)
            .collect();
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    /// `list_friend_connection_requests` (edge-permission inbox/outbox).
    pub(crate) async fn list_friend_connection_requests_impl(
        &self,
        command: ListFriendConnectionRequests,
    ) -> Result<FriendConnectionRequestPage, ApplicationError> {
        if command.page_size == 0 || command.page_size > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "page_size must be between 1 and 100",
            ));
        }
        let connect = self.connect_service()?;
        let actor = self
            .resolve_acting_actor(&command.caller, command.actor.as_ref())
            .await?;
        let direction = match command.direction {
            FriendConnectionRequestDirection::Received => {
                bcs_service_api::application::RequestDirection::Received
            }
            FriendConnectionRequestDirection::Sent => {
                bcs_service_api::application::RequestDirection::Sent
            }
            FriendConnectionRequestDirection::All => {
                bcs_service_api::application::RequestDirection::All
            }
        };
        let status = command.status.map(|status| match status {
            FriendConnectionRequestStatus::Pending => {
                bcs_domain::edge_permission::RequestStatus::Pending
            }
            FriendConnectionRequestStatus::Approved => {
                bcs_domain::edge_permission::RequestStatus::Approved
            }
            FriendConnectionRequestStatus::Rejected => {
                bcs_domain::edge_permission::RequestStatus::Rejected
            }
            FriendConnectionRequestStatus::Cancelled => {
                bcs_domain::edge_permission::RequestStatus::Cancelled
            }
        });
        let page = connect
            .list_requests(&actor, direction, status, command.page, command.page_size)
            .await
            .map_err(map_service_error)?;
        Ok(FriendConnectionRequestPage {
            items: page.items.iter().map(project_friend_connection_request).collect(),
            total: page.total,
            page: page.page,
            page_size: page.page_size,
        })
    }

    /// `list_friend_connections` (edge-permission friend list projection).
    pub(crate) async fn list_friend_connections_impl(
        &self,
        command: ListFriendConnections,
    ) -> Result<FriendConnectionPage, ApplicationError> {
        if command.page == 0 || command.page_size == 0 || command.page_size > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "page must be at least 1 and page_size must be between 1 and 100",
            ));
        }
        let connect = self.connect_service()?;
        let actor = self
            .resolve_acting_actor(&command.caller, Some(&command.actor))
            .await?;
        let query = bcs_service_api::application::connect::FriendListQuery {
            target_type: command.target_type.map(|kind| match kind {
                bcs_service_api::application::v1::friend_connection::FriendConnectionActorType::Human => ActorKind::Human,
                bcs_service_api::application::v1::friend_connection::FriendConnectionActorType::Bot => ActorKind::Bot,
            }),
            offset: u64::from(command.page - 1) * u64::from(command.page_size),
            limit: command.page_size,
        };
        let result = connect
            .list_friends_paginated(&actor, query)
            .await
            .map_err(map_service_error)?;
        let total = u32::try_from(result.total).map_err(|_| map_service_error(
            ServiceError::InternalError("friend count exceeds response range".into()),
        ))?;
        let items = result.items.iter().map(project_friend_connection).collect();
        Ok(FriendConnectionPage {
            items,
            page: command.page,
            page_size: command.page_size,
            total,
        })
    }
}