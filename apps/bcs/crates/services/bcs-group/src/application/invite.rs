use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::core::{GroupMutationCommand, GroupMutationKind};
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::types::{EventActor, EventActorType, MessageViewScope};
use bcs_service_api::{
    ActorKind, BotRegistryCoreService, CreateInviteTokenCommand, GroupCoreService, GroupKind,
    GroupStatus, InviteService, InviteTargetType, InviteTokenError, InviteTokenPayload,
    InviteTokenResult, InviteUseCaseError, JoinByInviteCommand, JoinByInviteResult, Participant,
    ParticipantMode, ParticipantRole, ServiceError, SessionManagementService, SystemMessageEvent,
    SystemMessageService,
    invite_token_decode_no_expiry, invite_token_encode,
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
    /// The live Human→Bot control hook (bot-authority
    /// plan Task 6.5): the invite-issuance control legs
    /// resolve the CURRENT `can_manage` edge; the
    /// historical `list_bots_by_creator` claim is gone.
    /// A missing wiring fails closed.
    pub bot_authority: Option<Arc<dyn BotAuthorityPort>>,
}

impl InviteServiceImpl {
    pub fn with_bot_authority_hook(
        mut self,
        hook: Arc<dyn BotAuthorityPort>,
    ) -> Self {
        self.bot_authority = Some(hook);
        self
    }

    /// The CURRENT Human→Bot control judgment for the
    /// invite-issuance legs. The historical
    /// `created_by` claim no longer authorizes NEW
    /// invite issuance; a failing read is an internal
    /// error — never a deny-as-false.
    async fn human_controls_bot(
        &self,
        staff_no: &str,
        bot_id: &str,
    ) -> Result<bool, InviteUseCaseError> {
        let hook = self
            .bot_authority
            .as_deref()
            .ok_or_else(|| {
                InviteUseCaseError::Service(ServiceError::InternalError(
                    "bot authority hook is not wired".to_string(),
                ))
            })?;
        match hook.can_manage(staff_no, bot_id).await {
            Ok(result) => Ok(result),
            Err(ServiceError::Forbidden(message)) => Err(
                InviteUseCaseError::Forbidden(message),
            ),
            Err(other) => Err(
                InviteUseCaseError::Service(ServiceError::InternalError(
                    format!("bot authority lookup failed: {other}"),
                )),
            ),
        }
    }

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
        // CURRENT controller of the driver/originator
        // Bot can generate invites (owner or manager,
        // live edge; spec §3.2 — a creator whose edge
        // was revoked no longer mints NEW invitations;
        // previously-issued URLs keep working through
        // the join paths).
        if let Some(staff_no) = caller_staff_no {
            if self
                .human_controls_bot(staff_no, group.driver_bot.as_str())
                .await?
            {
                return Ok(());
            }
            if self.human_controls_bot(staff_no, originator).await? {
                return Ok(());
            }
        }

        Err(InviteUseCaseError::Forbidden(
            "only group driver, originator, or their owner can generate invite links".to_string(),
        ))
    }

    /// Session invite tokens are gated on session membership only: any
    /// participant of the session (any role, bot or human) may mint one. A
    /// Human caller also qualifies through any owned Bot that participates in
    /// the session. Group-level roles (driver, originator, manager) are
    /// intentionally NOT required here.
    async fn ensure_session_member(
        &self,
        cmd: &CreateInviteTokenCommand,
        session: &bcs_service_api::Session,
    ) -> Result<(), InviteUseCaseError> {
        let is_member =
            |actor_id: &str| session.participants.iter().any(|p| p.bot_uuid == actor_id);
        if cmd.caller_actor_id.as_deref().is_some_and(is_member) {
            return Ok(());
        }
        if let Some(staff_no) = cmd.caller_staff_no.as_deref() {
            if is_member(&format!("human_{}", staff_no)) {
                return Ok(());
            }
            // The Human qualifies through any participant
            // Bot it CURRENTLY controls (live `can_manage`
            // per participant, never a creator-list
            // whitelist).
            for participant in session.participants.iter().filter(|p| p.is_bot()) {
                if self
                    .human_controls_bot(staff_no, &participant.bot_uuid)
                    .await?
                {
                    return Ok(());
                }
            }
        }
        Err(InviteUseCaseError::Forbidden(
            "only session participants can generate session invite links".to_string(),
        ))
    }

    fn make_token(
        &self,
        target_id: &str,
        ttl_seconds: Option<u64>,
        target_type: InviteTargetType,
    ) -> (String, u64) {
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
            // Newly minted legacy tokens carry target_type so the join
            // endpoints can reject a token presented to the wrong path and V1
            // `acceptInvitation` can route without a lookup. Tokens minted
            // before this field existed decode to `None` and stay accepted by
            // the legacy join paths (the JSON key is `serde(default)`).
            target_type: Some(target_type),
        };
        let token = invite_token_encode(&payload, &self.token_secret);
        (token, exp)
    }

    /// Reject a token whose `target_type` contradicts the join endpoint it is
    /// presented to (e.g. a session token on `POST /groups/join/{token}`).
    /// Pre-field legacy tokens decode to `None` and pass: they predate the
    /// distinction and remain honored on both endpoints as before.
    fn ensure_target_type(
        payload: &InviteTokenPayload,
        expected: InviteTargetType,
    ) -> Result<(), InviteUseCaseError> {
        match &payload.target_type {
            Some(actual) if *actual != expected => Err(InviteUseCaseError::InvalidToken(format!(
                "invite token targets a {}, not a {}",
                match actual {
                    InviteTargetType::Group => "group",
                    InviteTargetType::Session => "session",
                },
                match expected {
                    InviteTargetType::Group => "group",
                    InviteTargetType::Session => "session",
                },
            ))),
            _ => Ok(()),
        }
    }

    fn decode_token(&self, token: &str) -> Result<InviteTokenPayload, InviteUseCaseError> {
        invite_token_decode_no_expiry(token, &self.token_secret).map_err(|e| match e {
            InviteTokenError::InvalidSignature | InviteTokenError::InvalidEncoding => {
                InviteUseCaseError::InvalidToken("invalid invite token".to_string())
            }
            InviteTokenError::UnsupportedVersion => {
                InviteUseCaseError::InvalidToken("unsupported invite token version".to_string())
            }
            InviteTokenError::MalformedPayload(msg) => InviteUseCaseError::InvalidToken(msg),
            _ => InviteUseCaseError::InvalidToken("invalid invite token".to_string()),
        })
    }

    async fn ensure_human(
        &self,
        staff_no: &str,
        nick_name: Option<&str>,
    ) -> Result<String, InviteUseCaseError> {
        let display = nick_name.filter(|s| !s.is_empty()).unwrap_or(staff_no);
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
        let group = self.group.get(&cmd.target_id).await.ok_or_else(|| {
            InviteUseCaseError::NotFound(format!("group not found: {}", cmd.target_id))
        })?;

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
        let (token, exp) =
            self.make_token(&cmd.target_id, cmd.ttl_seconds, InviteTargetType::Group);
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
        let session = self
            .session
            .get(&cmd.target_id)
            .await
            .map_err(|e| InviteUseCaseError::NotFound(format!("session lookup failed: {}", e)))?
            .ok_or_else(|| {
                InviteUseCaseError::NotFound(format!("session not found: {}", cmd.target_id))
            })?;

        let group = self.group.get(&session.group_id).await.ok_or_else(|| {
            InviteUseCaseError::NotFound(format!("group not found: {}", session.group_id))
        })?;

        if group.group_kind == GroupKind::Dm {
            return Err(InviteUseCaseError::Forbidden(
                "DM groups do not support invite links".to_string(),
            ));
        }

        self.ensure_session_member(&cmd, &session).await?;
        let (token, exp) =
            self.make_token(&cmd.target_id, cmd.ttl_seconds, InviteTargetType::Session);
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
        Self::ensure_target_type(&payload, InviteTargetType::Group)?;
        let group_id = &payload.id;
        let group = self.group.get(group_id).await.ok_or_else(|| {
            InviteUseCaseError::NotFound(format!("group not found: {}", group_id))
        })?;

        let actor_id = self
            .ensure_human(&cmd.staff_no, cmd.nick_name.as_deref())
            .await?;

        self.ensure_actor_is_human(&actor_id).await?;
        let mut scope_actors = group.participants.iter().map(|p| p.bot_uuid.clone()).collect::<Vec<_>>();
        scope_actors.push(actor_id.clone());
        self.registry.collaboration_owner(&scope_actors).await?;

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
            tags: Vec::new(),
            message_view_scope: cmd.message_view_scope.unwrap_or(MessageViewScope::Full),
        };
        self.group.mutate(GroupMutationCommand {
            group_id: group_id.clone(),
            actor: EventActor { actor_type: EventActorType::Human, id: actor_id.clone(), display_name: cmd.nick_name.clone() },
            correlation_id: None, trace_id: None,
            mutation: GroupMutationKind::AddParticipant { participant, actor_is_public: true },
        }).await?;

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
        Self::ensure_target_type(&payload, InviteTargetType::Session)?;
        let session_id = &payload.id;
        let session = self
            .session
            .get(session_id)
            .await
            .map_err(|e| InviteUseCaseError::NotFound(format!("session lookup failed: {}", e)))?
            .ok_or_else(|| {
                InviteUseCaseError::NotFound(format!("session not found: {}", session_id))
            })?;
        let group = self.group.get(&session.group_id).await.ok_or_else(|| {
            InviteUseCaseError::NotFound(format!("group not found: {}", session.group_id))
        })?;

        let actor_id = self
            .ensure_human(&cmd.staff_no, cmd.nick_name.as_deref())
            .await?;

        self.ensure_actor_is_human(&actor_id).await?;
        let mut scope_actors = group.participants.iter().map(|p| p.bot_uuid.clone()).collect::<Vec<_>>();
        scope_actors.push(actor_id.clone());
        if self.registry.collaboration_owner(&scope_actors).await?.is_some() {
            scope_actors.extend(session.participants.iter().map(|p| p.bot_uuid.clone()));
            self.registry.collaboration_owner(&scope_actors).await?;
        }

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

        let message_view_scope = cmd
            .message_view_scope
            .or_else(|| {
                group
                    .participants
                    .iter()
                    .find(|participant| participant.bot_uuid == actor_id)
                    .map(|participant| participant.message_view_scope)
            })
            .unwrap_or(MessageViewScope::Full);
        let participant = Participant {
            bot_uuid: actor_id.clone(),
            bot_name: cmd.nick_name.clone(),
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
            tags: Vec::new(),
            message_view_scope,
        };
        let updated_session = self
            .session
            .add_participant(session_id, participant.clone())
            .await
            .map_err(|e| match e {
                bcs_service_api::SessionUseCaseError::NotFound(msg) => {
                    InviteUseCaseError::NotFound(msg)
                }
                bcs_service_api::SessionUseCaseError::Conflict(msg) => {
                    InviteUseCaseError::Conflict(msg)
                }
                other => InviteUseCaseError::NotFound(other.to_string()),
            })?;

        let _ = self
            .system_message
            .notify(
                &session.group_id,
                SystemMessageEvent::HumanJoined {
                    group_id: session.group_id.clone(),
                    actor: participant,
                },
                session_id,
                &updated_session.participants,
            )
            .await;

        Ok(JoinByInviteResult {
            joined: true,
            already_member: false,
            target_type: "session".to_string(),
            target_id: session_id.to_string(),
            actor_id,
        })
    }
}
