use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, BotRegistryCoreService, GroupCoreService, GroupKind, GroupStatus,
    Participant, ParticipantMode, ParticipantRole,
    SessionManagementService,
    SystemMessageEvent, SystemMessageService,
    CreateInviteTokenCommand, InviteService, InviteTokenResult,
    InviteUseCaseError, JoinByInviteCommand, JoinByInviteResult,
    InviteTokenPayload, InviteTokenError,
    invite_token_encode, invite_token_decode_no_expiry,
};

pub struct InviteServiceImpl {
    pub registry: Arc<dyn BotRegistryCoreService>,
    pub group: Arc<dyn GroupCoreService>,
    pub session: Arc<dyn SessionManagementService>,
    pub system_message: Arc<dyn SystemMessageService>,
    pub token_secret: Vec<u8>,
    pub default_ttl_seconds: u64,
    pub base_url: Option<String>,
    pub group_link_url: Option<String>,
    pub session_link_url: Option<String>,
}

impl InviteServiceImpl {
    fn resolve_base_url(&self) -> String {
        self.base_url
            .clone()
            .unwrap_or_else(|| "http://localhost:21000".to_string())
    }

    async fn authorize_group_invite(
        &self,
        cmd: &CreateInviteTokenCommand,
        group: &bcs_service_api::Group,
    ) -> Result<(), InviteUseCaseError> {
        let caller_bot_id = cmd.caller_actor_id.as_deref();
        let caller_staff_no = cmd.caller_staff_no.as_deref();

        // Driver bot can always generate invites.
        if caller_bot_id == Some(group.driver_bot.as_str()) {
            return Ok(());
        }
        // Originator can always generate invites.
        let originator = group.originator();
        if caller_bot_id == Some(originator) {
            return Ok(());
        }
        // Human originator check.
        if let Some(staff_no) = caller_staff_no {
            let human_id = format!("human_{}", staff_no);
            if human_id == originator {
                return Ok(());
            }
        }
        // Owner of driver/originator bot can generate invites.
        if let Some(staff_no) = caller_staff_no {
            let owned = self.registry.list_bots_by_creator(staff_no).await;
            let owned_ids: Vec<&str> = owned.iter().map(|b| b.bot_uuid.as_str()).collect();
            if owned_ids.contains(&group.driver_bot.as_str()) {
                return Ok(());
            }
            if owned_ids.contains(&originator) {
                return Ok(());
            }
        }

        Err(InviteUseCaseError::Forbidden(
            "only group driver, originator, or their owner can generate invite links".to_string(),
        ))
    }

    fn make_token(&self, target_id: &str, ttl_seconds: Option<u64>) -> (String, u64) {
        let ttl = ttl_seconds.unwrap_or(self.default_ttl_seconds);
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let exp = now + ttl;
        let payload = InviteTokenPayload {
            v: 1,
            id: target_id.to_string(),
            exp,
        };
        let token = invite_token_encode(&payload, &self.token_secret);
        (token, exp)
    }

    fn decode_token(&self, token: &str) -> Result<InviteTokenPayload, InviteUseCaseError> {
        invite_token_decode_no_expiry(token, &self.token_secret).map_err(|e| match e {
            InviteTokenError::InvalidSignature | InviteTokenError::InvalidEncoding => {
                InviteUseCaseError::InvalidToken("invalid invite token".to_string())
            }
            InviteTokenError::UnsupportedVersion => {
                InviteUseCaseError::InvalidToken("unsupported invite token version".to_string())
            }
            InviteTokenError::MalformedPayload(msg) => {
                InviteUseCaseError::InvalidToken(msg)
            }
            _ => InviteUseCaseError::InvalidToken("invalid invite token".to_string()),
        })
    }

    async fn ensure_human(
        &self,
        staff_no: &str,
        nick_name: Option<&str>,
    ) -> Result<String, InviteUseCaseError> {
        let display = nick_name
            .filter(|s| !s.is_empty())
            .unwrap_or(staff_no);
        self.registry
            .ensure_human_actor(staff_no, display)
            .await
            .map_err(InviteUseCaseError::Service)?;
        Ok(format!("human_{}", staff_no))
    }

    async fn ensure_actor_is_human(&self, actor_id: &str) -> Result<(), InviteUseCaseError> {
        if let Some(bot) = self.registry.get(actor_id).await {
            if bot.actor_kind != ActorKind::Human {
                return Err(InviteUseCaseError::Forbidden(
                    "only human actors can join via invite link".to_string(),
                ));
            }
        }
        Ok(())
    }
}

#[async_trait]
impl InviteService for InviteServiceImpl {
    async fn create_group_invite_token(
        &self,
        cmd: CreateInviteTokenCommand,
    ) -> Result<InviteTokenResult, InviteUseCaseError> {
        let group = self.group.get(&cmd.target_id).await
            .ok_or_else(|| InviteUseCaseError::NotFound(format!("group not found: {}", cmd.target_id)))?;

        if group.group_kind == GroupKind::Dm {
            return Err(InviteUseCaseError::Forbidden(
                "DM groups do not support invite links".to_string(),
            ));
        }
        if group.status != GroupStatus::Active {
            return Err(InviteUseCaseError::Conflict(
                "group is not active".to_string(),
            ));
        }

        self.authorize_group_invite(&cmd, &group).await?;
        let (token, exp) = self.make_token(&cmd.target_id, cmd.ttl_seconds);
        let join_url = match &self.group_link_url {
            Some(url) => format!("{}/{}", url.trim_end_matches('/'), token),
            None => {
                let base = self.resolve_base_url();
                format!("{}/groups/join/{}", base, token)
            }
        };
        Ok(InviteTokenResult {
            join_url,
            invite_token: token,
            expires_at: exp,
        })
    }

    async fn create_session_invite_token(
        &self,
        cmd: CreateInviteTokenCommand,
    ) -> Result<InviteTokenResult, InviteUseCaseError> {
        let session = self.session
            .get(&cmd.target_id)
            .await
            .map_err(|e| InviteUseCaseError::NotFound(format!("session lookup failed: {}", e)))?
            .ok_or_else(|| InviteUseCaseError::NotFound(format!("session not found: {}", cmd.target_id)))?;

        let group = self.group.get(&session.group_id).await
            .ok_or_else(|| InviteUseCaseError::NotFound(format!("group not found: {}", session.group_id)))?;

        if group.group_kind == GroupKind::Dm {
            return Err(InviteUseCaseError::Forbidden(
                "DM groups do not support invite links".to_string(),
            ));
        }

        self.authorize_group_invite(&cmd, &group).await?;
        let (token, exp) = self.make_token(&cmd.target_id, cmd.ttl_seconds);
        let join_url = match &self.session_link_url {
            Some(url) => format!("{}/{}", url.trim_end_matches('/'), token),
            None => {
                let base = self.resolve_base_url();
                format!("{}/sessions/join/{}", base, token)
            }
        };
        Ok(InviteTokenResult {
            join_url,
            invite_token: token,
            expires_at: exp,
        })
    }

    async fn join_group_by_invite(
        &self,
        cmd: JoinByInviteCommand,
    ) -> Result<JoinByInviteResult, InviteUseCaseError> {
        let payload = self.decode_token(&cmd.token)?;
        let group_id = &payload.id;
        let group = self.group.get(group_id).await
            .ok_or_else(|| InviteUseCaseError::NotFound(format!("group not found: {}", group_id)))?;

        let actor_id = self.ensure_human(&cmd.staff_no, cmd.nick_name.as_deref()).await?;

        self.ensure_actor_is_human(&actor_id).await?;

        if group.participants.iter().any(|p| p.bot_uuid == actor_id) {
            return Ok(JoinByInviteResult {
                joined: false,
                already_member: true,
                target_type: "group".to_string(),
                target_id: group_id.to_string(),
                actor_id,
            });
        }

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        if payload.exp < now {
            return Err(InviteUseCaseError::Expired);
        }

        let participant = Participant {
            bot_uuid: actor_id.clone(),
            bot_name: cmd.nick_name.clone(),
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
        };
        self.group.add_participant(group_id, participant).await?;

        Ok(JoinByInviteResult {
            joined: true,
            already_member: false,
            target_type: "group".to_string(),
            target_id: group_id.to_string(),
            actor_id,
        })
    }

    async fn join_session_by_invite(
        &self,
        cmd: JoinByInviteCommand,
    ) -> Result<JoinByInviteResult, InviteUseCaseError> {
        let payload = self.decode_token(&cmd.token)?;
        let session_id = &payload.id;
        let session = self.session
            .get(session_id)
            .await
            .map_err(|e| InviteUseCaseError::NotFound(format!("session lookup failed: {}", e)))?
            .ok_or_else(|| InviteUseCaseError::NotFound(format!("session not found: {}", session_id)))?;

        let actor_id = self.ensure_human(&cmd.staff_no, cmd.nick_name.as_deref()).await?;

        self.ensure_actor_is_human(&actor_id).await?;

        if session.participants.iter().any(|p| p.bot_uuid == actor_id) {
            return Ok(JoinByInviteResult {
                joined: false,
                already_member: true,
                target_type: "session".to_string(),
                target_id: session_id.to_string(),
                actor_id,
            });
        }

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        if payload.exp < now {
            return Err(InviteUseCaseError::Expired);
        }

        let participant = Participant {
            bot_uuid: actor_id.clone(),
            bot_name: cmd.nick_name.clone(),
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
        };
        let updated_session = self.session
            .add_participant(session_id, participant.clone())
            .await
            .map_err(|e| match e {
                bcs_service_api::SessionUseCaseError::NotFound(msg) => InviteUseCaseError::NotFound(msg),
                bcs_service_api::SessionUseCaseError::Conflict(msg) => InviteUseCaseError::Conflict(msg),
                other => InviteUseCaseError::NotFound(other.to_string()),
            })?;

        let _ = self.system_message.notify(
            &session.group_id,
            SystemMessageEvent::HumanJoined {
                group_id: session.group_id.clone(),
                actor: participant,
            },
            session_id,
            &updated_session.participants,
        ).await;

        Ok(JoinByInviteResult {
            joined: true,
            already_member: false,
            target_type: "session".to_string(),
            target_id: session_id.to_string(),
            actor_id,
        })
    }
}
