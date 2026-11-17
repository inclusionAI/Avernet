//! Invitation minting, read-only membership preflight and explicit acceptance.
use super::*;
use bcs_service_api::application::v1::{InvitationPreview, PreviewInvitation};
use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::types::authorization_snapshot::{
    GroupAuthorizationSnapshot, ParticipantAuthorizationSnapshot,
};

impl InvitationFriendshipServiceImpl {
    /// Strict, environment-scoped Session authorization reads; never display cache.
    pub fn with_session_authorization_core(
        mut self,
        session_snapshots: Arc<dyn SessionAuthorizationCoreService>,
    ) -> Self {
        self.session_snapshots = Some(session_snapshots);
        self
    }

    async fn load_invitation_group_snapshot(
        &self,
        group_id: &str,
    ) -> Result<GroupAuthorizationSnapshot, ApplicationError> {
        self.groups.read_authorization_snapshot(group_id).await
            .map_err(map_snapshot_error)?
            .filter(|group| group.live && group.record_status == "active")
            .ok_or_else(|| invitation_not_found(group_id))
    }

    /// Manager of a group: driver, originator, or ManagerWorker manager.
    /// A Human may also act for an owned driver/originator Bot, preserving the
    /// legacy invitation authorization contract.
    async fn can_manage_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let actor_id = principal.actor_id();
        if actor_id == group.driver_bot
            || actor_id == group.originator()
            || (group.group_strategy == GroupStrategy::ManagerWorker
                && group.participants.iter().any(|p| {
                    p.bot_uuid == actor_id && p.role == ParticipantRole::Manager
                }))
        {
            return Ok(true);
        }
        let Principal::Human(human) = principal else {
            return Ok(false);
        };
        // Row 11 group-management leg: a Human manages a
        // Group through a driver/originator Bot it
        // CURRENTLY controls (live `can_manage`), never
        // through a creator-list whitelist.
        if group.driver_bot != group.originator() {
            if self
                .human_controls_bot(&human.subject.id, &group.driver_bot)
                .await?
            {
                return Ok(true);
            }
        }
        let originator_bot = group.originator();
        Ok(!originator_bot.starts_with("human_")
            && self
                .human_controls_bot(&human.subject.id, originator_bot)
                .await?)
    }

    async fn load_manageable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if !self.can_manage_group(principal, &group).await? {
            return Err(ApplicationError::forbidden(
                "Only the Group originator, driver, or manager may manage this Group",
            ));
        }
        Ok(group)
    }

    // ── invitation helpers ─────────────────────────────────────────────

    fn mint_invitation(
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
    async fn preview_invitation(
        &self,
        query: PreviewInvitation,
    ) -> Result<InvitationPreview, ApplicationError> {
        let user = require_authenticated_user(&query.caller)?;
        let payload = invite_token_decode_and_verify(&query.token, &self.token_secret)
            .map_err(map_invite_token_error)?;
        let target_type = payload.target_type.ok_or_else(|| {
            ApplicationError::invalid(
                "invalid_request",
                "legacy invitation token without target_type is not supported by V1",
            )
        })?;
        let actor_id = format!("human_{}", user.id);
        let is_member = |participants: &[ParticipantAuthorizationSnapshot]| {
            participants.iter().any(|p| p.actor_kind == ActorKind::Human && p.actor_id == actor_id)
        };
        let (target_type, group_id, already_joined) = match target_type {
            InviteTargetType::Group => {
                let group = self.load_invitation_group_snapshot(&payload.id).await?;
                (InvitationTargetType::Group, group.id, is_member(&group.participants))
            }
            InviteTargetType::Session => {
                let session = self.session_snapshots.as_deref().ok_or_else(|| {
                    ApplicationError::internal("session authorization snapshot core is not wired")
                })?
                    .read_authorization_snapshot(&payload.id)
                    .await
                    .map_err(map_snapshot_error)?
                    .filter(|session| session.live)
                    .ok_or_else(|| invitation_not_found(&payload.id))?;
                // Reject a deleted/provisioning parent too, without reading any full row.
                self.load_invitation_group_snapshot(&session.group_id).await?;
                (InvitationTargetType::Session, session.group_id, is_member(&session.participants))
            }
        };
        Ok(InvitationPreview {
            target_type,
            target_id: payload.id,
            group_id,
            already_joined,
        })
    }

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
        // Vcj6P: legacy `create_group_invite_token` L151-153 rejects minting on
        // a non-active group ("group is not active"). Mirror it so V1 does not
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
        // Vcj6M: legacy `create_session_invite_token` L186-189 rejects DM parent
        // groups. Mirror it so session invitations on pairwise DM targets are
        // not minted. The legacy session path skips `session.status` (it never
        // checked session status); V1 follows the same precedent.
        if group.group_kind == GroupKind::Dm {
            return Err(ApplicationError::forbidden(
                "Invitations are not available for direct-message groups",
            ));
        }
        // Vcj6P: legacy `create_group_invite_token` L151-153 rejects non-active
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
        // also qualifies through any owned Bot that participates in the
        // session. Group-level roles (driver, originator, manager) are
        // intentionally NOT required, mirroring the relaxed legacy
        // `create_session_invite_token`.
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
        if let Principal::Human(human) = principal {
            // Row 11: membership through a Bot the Human
            // CURRENTLY controls (live `can_manage` per
            // Bot participant, never a creator-list
            // whitelist).
            for participant in session.participants.iter() {
                if self
                    .human_controls_bot(&human.subject.id, &participant.bot_uuid)
                    .await?
                {
                    return Ok(self.mint_invitation(
                        InvitationTargetType::Session,
                        &command.session_id,
                        command.expires_in_seconds,
                    ));
                }
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


fn map_v1_target_to_domain(target: InvitationTargetType) -> InviteTargetType {
    match target {
        InvitationTargetType::Group => InviteTargetType::Group,
        InvitationTargetType::Session => InviteTargetType::Session,
    }
}

pub(super) fn map_invite_token_error(error: InviteTokenError) -> ApplicationError {
    match error {
        InviteTokenError::Expired => ApplicationError::Gone {
            code: "invitation_expired".to_string(),
            message: "invitation link has expired".to_string(),
        },
        InviteTokenError::InvalidEncoding | InviteTokenError::InvalidSignature => {
            ApplicationError::invalid("invalid_request", "invalid invitation token")
        }
        InviteTokenError::UnsupportedVersion => {
            ApplicationError::invalid("invalid_request", "unsupported invitation token version")
        }
        InviteTokenError::MalformedPayload(message) => {
            ApplicationError::invalid("invalid_request", format!("malformed invitation token: {message}"))
        }
    }
}

/// Map legacy `InviteService::join_*_by_invite` errors onto the V1
/// `acceptInvitation` contract surface. The contract declares `forbidden`
/// (403), `invitation_not_found` (404), `conflict` (409), and
/// `invitation_expired` (410); the join path may surface any of these via
/// `InviteUseCaseError`. `InvalidToken` maps to `invalid_request` (400) so a
/// malformed token is still rejected at the contract boundary. `LoginRequired`
/// and `Service` collapse to `internal_error` (500) — they are not part of the
/// V1 accept contract surface.
fn map_invite_use_case_error(error: InviteUseCaseError) -> ApplicationError {
    match error {
        InviteUseCaseError::Forbidden(message) => ApplicationError::forbidden(message),
        InviteUseCaseError::NotFound(target) => ApplicationError::not_found(
            "invitation_not_found",
            format!("Invitation target '{target}' was not found"),
        ),
        InviteUseCaseError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        InviteUseCaseError::Expired => ApplicationError::Gone {
            code: "invitation_expired".to_string(),
            message: "invitation link has expired".to_string(),
        },
        InviteUseCaseError::InvalidToken(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        InviteUseCaseError::LoginRequired => {
            ApplicationError::internal("login required to accept invitation")
        }
        InviteUseCaseError::Service(error) => map_service_error(error),
    }
}


fn invitation_not_found(target: &str) -> ApplicationError {
    ApplicationError::not_found("invitation_not_found", format!("Invitation target '{target}' was not found"))
}

// Snapshot failures, including missing wiring, are server failures, never invalid
// user input or a false membership result.
fn map_snapshot_error(error: ServiceError) -> ApplicationError {
    ApplicationError::internal(format!("invitation membership snapshot failed: {error}"))
}

#[cfg(test)]
mod tests;
