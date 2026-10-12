//! V1 Invitation + Friendship facade: write use cases (plan Task 12 split).
//!
//! Traits implemented here: [`InvitationService`], [`FriendshipService`],
//! [`FriendConnectionService`]. Authorization decisions live in
//! [`crate::authorization`]; every connect-lane write carries the REQUIRED
//! §12.5 [`BotOperationContext`] derived from the verified caller.

use async_trait::async_trait;
use bcs_domain::{
    invite_token_decode_and_verify, invite_token_encode, InviteTargetType, InviteTokenPayload,
};
use bcs_service_api::application::v1::{
    require_authenticated_user, require_human, AcceptFriendConnectionRequest, AcceptFriendRequest,
    AcceptInvitation, ApplicationError, CancelFriendConnectionRequest, CreateBotFriendRequest,
    CreateFriendConnectionRequest, CreateGroupInvitation, CreateSessionInvitation,
    DeleteBotFriendship, DeleteFriendConnection, DeleteResult, FriendConnectionCreateResult,
    FriendConnectionCreateStatus, FriendConnectionRequestView, FriendConnectionService,
    FriendRequest, FriendshipService, Invitation, InvitationAcceptResult, InvitationService,
    InvitationState, InvitationTargetType, ListBotFriendRequests, ListBotFriendships,
    ListFriendConnectionRequests, ListFriendConnections, Page, Principal, RejectFriendConnectionRequest,
    RejectFriendRequest,
};
use bcs_service_api::{
    ActorKind, GroupKind, GroupStatus, InviteUseCaseError, JoinByInviteCommand,
};

use crate::{
    map_invite_token_error, map_invite_use_case_error, map_service_error, map_session_error,
    map_v1_target_to_domain, now_secs, project_friend_connection_request,
    project_friend_request, InvitationFriendshipServiceImpl,
};

impl InvitationFriendshipServiceImpl {
    // ── invitation helpers ─────────────────────────────────────────────

    pub(crate) fn mint_invitation(
        &self,
        target_type: InvitationTargetType,
        target_id: &str,
        ttl_seconds: Option<u64>,
    ) -> Invitation {
        let now = now_secs();
        let exp = now.saturating_add(ttl_seconds.unwrap_or(self.config.default_ttl_seconds));
        let payload = InviteTokenPayload {
            v: 1,
            id: target_id.to_string(),
            exp,
            target_type: Some(map_v1_target_to_domain(target_type)),
        };
        let token = invite_token_encode(&payload, &self.token_secret);
        Invitation {
            token,
            target_type,
            target_id: target_id.to_string(),
            state: InvitationState::Pending,
            expires_at: Some(exp),
            created_at: now,
        }
    }
}

#[async_trait]
impl InvitationService for InvitationFriendshipServiceImpl {
    async fn create_group_invitation(
        &self,
        command: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let group = self
            .load_manageable_group(&principal, &command.group_id)
            .await?;
        // VaGQI: DM (DirectMessage) groups are pairwise (participant_count=2);
        // minting an invitation + accept would add a third participant. Mirror
        // the legacy invite service, which rejects DM groups with Forbidden.
        if group.group_kind == GroupKind::Dm {
            return Err(ApplicationError::forbidden(
                "Invitations are not available for direct-message groups",
            ));
        }
        // Vcj6P: legacy `create_group_invite_token` rejects minting on a
        // non-active group ("group is not active"). Mirror it so V1 does not
        // hand out tokens for Completed/Closed/Error targets.
        if group.status != GroupStatus::Active {
            return Err(ApplicationError::conflict(
                "conflict",
                "group is not active",
            ));
        }
        Ok(self.mint_invitation(
            InvitationTargetType::Group,
            &command.group_id,
            command.expires_in_seconds,
        ))
    }

    async fn create_session_invitation(
        &self,
        command: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let session = self
            .sessions
            .get(&command.session_id)
            .await
            .map_err(map_session_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "session_not_found",
                    format!("Session '{}' was not found", command.session_id),
                )
            })?;
        let group = self
            .groups
            .try_get(&session.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{}' was not found", session.group_id),
                )
            })?;
        // Vcj6M: legacy `create_session_invite_token` rejects DM parent
        // groups. Mirror it so session invitations on pairwise DM targets are
        // not minted. The legacy session path skips `session.status` (it never
        // checked session status); V1 follows the same precedent.
        if group.group_kind == GroupKind::Dm {
            return Err(ApplicationError::forbidden(
                "Invitations are not available for direct-message groups",
            ));
        }
        // Vcj6P: legacy `create_group_invite_token` rejects non-active
        // groups. The session path's parent shares the same lifecycle as the
        // group, so mirror the inactive guard on the parent here too.
        if group.status != GroupStatus::Active {
            return Err(ApplicationError::conflict(
                "conflict",
                "group is not active",
            ));
        }
        // Minting is gated on session membership only: any participant of the
        // session (any role) may create an invitation for it. A Human caller
        // also qualifies through a Bot they CURRENTLY own or manage (live
        // role facts) that participates in the session; historical
        // `created_by` listings are not an answer (spec §12.4). Group-level
        // roles (driver, originator, manager) are intentionally NOT required,
        // mirroring the relaxed legacy `create_session_invite_token`.
        let is_member = |actor_id: &str| {
            session
                .participants
                .iter()
                .any(|p| p.bot_uuid == actor_id)
        };
        if is_member(&principal.actor_id()) {
            return Ok(self.mint_invitation(
                InvitationTargetType::Session,
                &command.session_id,
                command.expires_in_seconds,
            ));
        }
        if let Principal::Human(human) = &principal {
            if self
                .human_can_act_as_any(
                    human,
                    session
                        .participants
                        .iter()
                        .map(|p| p.bot_uuid.clone())
                        .collect(),
                )
                .await?
            {
                return Ok(self.mint_invitation(
                    InvitationTargetType::Session,
                    &command.session_id,
                    command.expires_in_seconds,
                ));
            }
        }
        Err(ApplicationError::forbidden(
            "Only Session participants may create invitations for this Session",
        ))
    }

    async fn accept_invitation(
        &self,
        command: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        let payload = invite_token_decode_and_verify(&command.token, &self.token_secret)
            .map_err(map_invite_token_error)?;
        let target_type = payload.target_type.ok_or_else(|| {
            ApplicationError::invalid(
                "invalid_request",
                "legacy invitation token without target_type is not supported by V1",
            )
        })?;
        let user = require_authenticated_user(&command.caller)?;
        let nick_name = user
            .display_name
            .clone()
            .filter(|value| !value.is_empty())
            .or_else(|| (!user.username.is_empty()).then(|| user.username.clone()));
        let join_command = JoinByInviteCommand {
            token: command.token.clone(),
            staff_no: user.id.clone(),
            nick_name,
            message_view_scope: command.message_view_scope,
        };
        let result = match target_type {
            InviteTargetType::Group => self.invite.join_group_by_invite(join_command).await,
            InviteTargetType::Session => {
                self.invite.join_session_by_invite(join_command).await
            }
        };
        let result = result.map_err(map_invite_use_case_error)?;
        let mapped_target_type = match target_type {
            InviteTargetType::Group => InvitationTargetType::Group,
            InviteTargetType::Session => InvitationTargetType::Session,
        };
        Ok(InvitationAcceptResult {
            target_type: mapped_target_type,
            target_id: result.target_id,
            joined: result.joined,
            // `already_member == !joined` from the legacy result; flip the
            // boolean to populate the V1 `already_joined` idempotency flag.
            already_joined: Some(!result.joined),
        })
    }
}

#[async_trait]
impl FriendshipService for InvitationFriendshipServiceImpl {
    async fn list_bot_friendships(
        &self,
        command: ListBotFriendships,
    ) -> Result<Page<bcs_service_api::application::v1::friendship::Friendship>, ApplicationError> {
        self.list_bot_friendships_impl(command).await
    }

    async fn delete_bot_friendship(
        &self,
        command: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        // The contract allows either endpoint of the friendship to initiate
        // deletion ("Principal cannot manage either friendship endpoint").
        // The live-role gate applies to BOTH endpoints: a former creator of
        // either side cannot delete anymore, and the first endpoint that the
        // caller currently owns or manages suffices.
        match self
            .ensure_bot_resource(&command.caller, &command.bot_uuid)
            .await
        {
            Ok(()) => {}
            Err(_) => {
                self.ensure_bot_resource(&command.caller, &command.friend_bot_uuid)
                    .await?;
            }
        }
        let deleted = self
            .friends
            .remove_friendship(&command.bot_uuid, &command.friend_bot_uuid)
            .await
            .map_err(map_service_error)?;
        Ok(DeleteResult { deleted })
    }

    async fn create_bot_friend_request(
        &self,
        command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        self.ensure_bot_resource(&command.caller, &command.bot_uuid)
            .await?;
        let request = self
            .friend_requests
            .create_request(&command.bot_uuid, &command.to_bot_uuid)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_request(&request))
    }

    async fn list_bot_friend_requests(
        &self,
        command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        self.list_bot_friend_requests_impl(command).await
    }

    async fn accept_friend_request(
        &self,
        command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        let request = self
            .friend_requests
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        // Only the receiver may accept; this also covers Human-owned bots via
        // the live-role `authorize_bot_resource`.
        self.ensure_bot_resource(&command.caller, &request.to_bot)
            .await?;
        self.friend_requests
            .accept_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        let updated = self
            .friend_requests
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_request(&updated))
    }

    async fn reject_friend_request(
        &self,
        command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        let request = self
            .friend_requests
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        self.ensure_bot_resource(&command.caller, &request.to_bot)
            .await?;
        self.friend_requests
            .reject_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        let updated = self
            .friend_requests
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_request(&updated))
    }
}

#[async_trait]
impl FriendConnectionService for InvitationFriendshipServiceImpl {
    async fn create_friend_connection_request(
        &self,
        command: CreateFriendConnectionRequest,
    ) -> Result<FriendConnectionCreateResult, ApplicationError> {
        let connect = self.connect_service()?;
        let from = self
            .resolve_acting_actor(&command.caller, command.from_actor.as_ref())
            .await?;
        if command.to_actor.actor_type
            != bcs_service_api::application::v1::friend_connection::FriendConnectionActorType::Bot
        {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "to_actor.type must be bot for friend connection requests",
            ));
        }
        let operation = Self::connect_operation_context(&command.caller, &from, "create");
        let result = connect
            .create_connect(
                &from,
                &command.to_actor.id,
                command.message,
                command.request_auth.clone(),
                operation,
            )
            .await
            .map_err(map_service_error)?;
        let status = match result.status {
            bcs_service_api::application::ConnectStatus::Pending => {
                FriendConnectionCreateStatus::Pending
            }
            bcs_service_api::application::ConnectStatus::Approved => {
                FriendConnectionCreateStatus::Approved
            }
            bcs_service_api::application::ConnectStatus::PublicNoEdge => {
                FriendConnectionCreateStatus::PublicNoEdge
            }
        };
        Ok(FriendConnectionCreateResult {
            request_ids: result.request_ids,
            edge_ids: result.edge_ids,
            status,
            auto_accepted: result.auto_accepted,
        })
    }

    async fn list_friend_connection_requests(
        &self,
        command: ListFriendConnectionRequests,
    ) -> Result<bcs_service_api::application::v1::friend_connection::FriendConnectionRequestPage, ApplicationError> {
        self.list_friend_connection_requests_impl(command).await
    }

    async fn accept_friend_connection_request(
        &self,
        command: AcceptFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError> {
        let connect = self.connect_service()?;
        let request = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        let decider = self.resolve_request_decider(&command.caller, &request).await?;
        let operation = Self::connect_operation_context(&command.caller, &decider, "approve");
        connect
            .approve(
                &command.request_id,
                &decider,
                command.request_auth.clone(),
                operation,
            )
            .await
            .map_err(map_service_error)?;
        let updated = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_connection_request(&updated))
    }

    async fn reject_friend_connection_request(
        &self,
        command: RejectFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError> {
        let connect = self.connect_service()?;
        let request = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        let decider = self.resolve_request_decider(&command.caller, &request).await?;
        let operation = Self::connect_operation_context(&command.caller, &decider, "reject");
        connect
            .reject(&command.request_id, &decider, command.reason, operation)
            .await
            .map_err(map_service_error)?;
        let updated = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_connection_request(&updated))
    }

    async fn cancel_friend_connection_request(
        &self,
        command: CancelFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError> {
        let connect = self.connect_service()?;
        let request = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        let operator = Self::caller_default_actor(&command.caller)?;
        self.ensure_can_cancel_request(&command.caller, &request).await?;
        let operation = Self::connect_operation_context(&command.caller, &operator, "cancel");
        connect
            .cancel(&command.request_id, &operator, operation)
            .await
            .map_err(map_service_error)?;
        let updated = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_connection_request(&updated))
    }

    async fn list_friend_connections(
        &self,
        command: ListFriendConnections,
    ) -> Result<bcs_service_api::application::v1::friend_connection::FriendConnectionPage, ApplicationError> {
        self.list_friend_connections_impl(command).await
    }

    async fn delete_friend_connection(
        &self,
        command: DeleteFriendConnection,
    ) -> Result<DeleteResult, ApplicationError> {
        let connect = self.connect_service()?;
        let caller = Self::caller_default_actor(&command.caller)?;
        let target = Self::actor_to_internal(&command.target_actor);
        let operation = Self::connect_operation_context(&command.caller, &caller, "unfriend");
        let revoked = connect
            .revoke_friend(&caller, &target, command.request_auth.clone(), operation)
            .await
            .map_err(map_service_error)?;
        Ok(DeleteResult {
            deleted: !revoked.is_empty(),
        })
    }
}
