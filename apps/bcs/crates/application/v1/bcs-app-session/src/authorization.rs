//! V1 Session application facade: ownership of the authenticated-Caller
//! authorization surface (plan Task 11, spec §8/§12.1).
//!
//! Every mixed-identity question resolves through the asynchronous
//! [`BotAuthorityHook`] — the live role facts decide owner/manager
//! eligibility for an EXACT bot, never the Gateway's signed `owner_id`
//! claim and never the legacy `created_by` value (spec §12.1(2)/§12.2).
//! The verified Human caller STAYS in the use case and the audit rows
//! record BOTH identities (operator + effective actor, spec §12.1(6)),
//! through the REQUIRED [`BotOperationContext`] carried by every write.

use std::collections::HashSet;
use std::sync::Arc;

use bcs_service_api::application::v1::{
    resolve_authorized_principal, ApplicationError, AuthenticatedCaller, BotAuthorityHook,
    Principal,
};
use bcs_service_api::application::v1::authorization::require_authenticated_user;
use bcs_service_api::types::{
    BotOperationActor, BotOperationContext, Group as DomainGroup, RegisteredBot, Session,
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotRegistryCoreService, FriendCoreService, GroupCoreService,
    Participant, ParticipantMode, SessionManagementService, SessionUseCaseError,
};

use crate::{map_service_error, map_session_error};

/// V1 Session facade configuration.
#[derive(Debug, Clone)]
pub struct SessionServiceConfig {}

/// OpenAPI v1 Session facade: registry + authority + legacy management
/// delegation for session lifecycle mutations.
pub struct SessionServiceImpl {
    pub(crate) launch: Arc<dyn bcs_service_api::SessionLaunchService>,
    pub(crate) sessions: Arc<dyn SessionManagementService>,
    pub(crate) groups: Arc<dyn GroupCoreService>,
    pub(crate) registry: Arc<dyn BotRegistryCoreService>,
    pub(crate) friends: Arc<dyn FriendCoreService>,
    pub(crate) authority: Arc<dyn BotAuthorityHook>,
    pub(crate) session_repo: Arc<dyn bcs_service_api::port::repo::SessionRepoPort>,
    pub(crate) history: Arc<dyn bcs_service_api::GroupMessageHistoryService>,
    pub(crate) collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService>,
    pub(crate) system_message: Arc<dyn bcs_service_api::SystemMessageService>,
    pub(crate) participant_view_bindings:
        Arc<dyn bcs_service_api::port::ParticipantViewBindingPort>,
    pub(crate) config: SessionServiceConfig,
}

impl SessionServiceImpl {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        launch: Arc<dyn bcs_service_api::SessionLaunchService>,
        sessions: Arc<dyn SessionManagementService>,
        groups: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friends: Arc<dyn FriendCoreService>,
        authority: Arc<dyn BotAuthorityHook>,
        session_repo: Arc<dyn bcs_service_api::port::repo::SessionRepoPort>,
        history: Arc<dyn bcs_service_api::GroupMessageHistoryService>,
        collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService>,
        system_message: Arc<dyn bcs_service_api::SystemMessageService>,
        config: SessionServiceConfig,
    ) -> Self {
        Self {
            launch,
            sessions,
            groups,
            registry,
            friends,
            authority,
            session_repo,
            history,
            collaboration_runtime,
            system_message,
            participant_view_bindings: Arc::new(
                bcs_service_api::port::NoopParticipantViewBindingPort,
            ),
            config,
        }
    }

    pub fn with_participant_view_bindings(
        mut self,
        participant_view_bindings: Arc<dyn bcs_service_api::port::ParticipantViewBindingPort>,
    ) -> Self {
        self.participant_view_bindings = participant_view_bindings;
        self
    }

    // ── authority helpers (spec §8/§12.1) ───────────────────────────────

    /// Live-role authority question: whether the Human `user_id` owns or
    /// manages the exact Bot `bot_id`. Never consults `created_by` or the
    /// signed `owner_id` claim (spec §12.2); the trusted source is only the
    /// BotAuthorityHook. Authorization failures propagate fail-closed.
    pub(crate) async fn authority_allows(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> Result<bool, ApplicationError> {
        self.authority
            .can_manage(user_id, bot_id)
            .await
            .map_err(map_authority_hook_error)
    }

    /// Manager of the parent group (driver / originator / manager participant).
    /// Mirrors `bcs-app-group`'s targeted Human-to-actor authority semantics.
    pub(crate) fn group_management_actor_ids(group: &DomainGroup) -> Vec<String> {
        let mut actor_ids = vec![group.driver_bot.clone(), group.originator().to_string()];
        if group.group_strategy == bcs_service_api::GroupStrategy::ManagerWorker {
            actor_ids.extend(
                group
                    .participants
                    .iter()
                    .filter(|participant| participant.role == bcs_service_api::ParticipantRole::Manager)
                    .map(|participant| participant.bot_uuid.clone()),
            );
        }
        actor_ids
    }

    /// Whether the Human may act as any of `actor_ids`: their own Human
    /// actor, or a Bot they own or manage per the LIVE role facts.
    pub(crate) async fn human_can_act_as_any(
        &self,
        human: &bcs_service_api::application::v1::HumanPrincipal,
        actor_ids: Vec<String>,
    ) -> Result<bool, ApplicationError> {
        let human_actor_id = format!("human_{}", human.subject.id);
        let mut seen = HashSet::new();
        for actor_id in actor_ids {
            if !seen.insert(actor_id.clone()) {
                continue;
            }
            if actor_id == human_actor_id {
                return Ok(true);
            }
            if actor_id.starts_with("human_") {
                continue;
            }
            let Some(bot) = self
                .registry
                .try_get(&actor_id)
                .await
                .map_err(map_service_error)?
            else {
                continue;
            };
            if bot.actor_kind != ActorKind::Bot {
                continue;
            }
            if self.authority_allows(&human.subject.id, &actor_id).await? {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// Whether the Principal acts as `actor_id` itself: the same Actor, or
    /// a Human acting for a Bot they own or manage.
    pub(crate) async fn principal_can_act_as(
        &self,
        principal: &Principal,
        actor_id: &str,
    ) -> Result<bool, ApplicationError> {
        if principal.actor_id() == actor_id {
            return Ok(true);
        }
        match principal {
            Principal::Human(human) => {
                self.human_can_act_as_any(human, vec![actor_id.to_string()])
                    .await
            }
            Principal::Bot(_) => Ok(false),
        }
    }

    pub(crate) async fn can_manage_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let actor_id = principal.actor_id();
        let candidates = Self::group_management_actor_ids(group);
        if candidates.iter().any(|candidate| candidate == &actor_id) {
            return Ok(true);
        }
        match principal {
            Principal::Human(human) => self.human_can_act_as_any(human, candidates).await,
            Principal::Bot(_) => Ok(false),
        }
    }

    /// Manage a specific session: group manager OR the session's creator
    /// (`session.created_by`). Human callers may act through a Bot they own
    /// or manage for any of those target actors.
    pub(crate) async fn can_manage_session(
        &self,
        principal: &Principal,
        session: &Session,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        if self.can_manage_group(principal, group).await? {
            return Ok(true);
        }
        let Some(created_by) = session.created_by.as_deref() else {
            return Ok(false);
        };
        if created_by == principal.actor_id() {
            return Ok(true);
        }
        match principal {
            Principal::Human(human) => {
                self.human_can_act_as_any(human, vec![created_by.to_string()])
                    .await
            }
            Principal::Bot(_) => Ok(false),
        }
    }

    pub(crate) async fn authorize_human_self_mode_update(
        &self,
        principal: &Principal,
        session: &Session,
        actor_id: &str,
    ) -> Result<(), ApplicationError> {
        if principal.actor_id() != actor_id {
            return Err(ApplicationError::forbidden(
                "A Human participant may only update its own mode",
            ));
        }
        if !self.can_read_session_detail(principal, session).await? {
            return Err(ApplicationError::forbidden(
                "The authenticated Human cannot access this Session",
            ));
        }
        Ok(())
    }

    pub(crate) fn validate_participant_mode(
        mode: ParticipantMode,
        actor_kind: ActorKind,
    ) -> Result<(), ApplicationError> {
        if mode.is_valid_for(actor_kind) {
            return Ok(());
        }
        Err(ApplicationError::invalid(
            "invalid_participant_mode",
            format!("Participant mode '{mode:?}' is invalid for actor kind '{actor_kind:?}'"),
        ))
    }

    pub(crate) async fn load_group(&self, group_id: &str) -> Result<DomainGroup, ApplicationError> {
        self.groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })
    }

    /// Load a session and its parent group, authorizing manage access.
    pub(crate) async fn load_session_for_manage(
        &self,
        principal: &Principal,
        session_id: &str,
    ) -> Result<(Session, DomainGroup), ApplicationError> {
        let session = self.load_session(session_id).await?;
        let group = self.load_group(&session.group_id).await?;
        if !self.can_manage_session(principal, &session, &group).await? {
            return Err(ApplicationError::forbidden(
                "Principal may not manage this Session",
            ));
        }
        Ok((session, group))
    }

    pub(crate) async fn load_session(&self, session_id: &str) -> Result<Session, ApplicationError> {
        let session = self
            .sessions
            .get(session_id)
            .await
            .map_err(map_session_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "session_not_found",
                    format!("Session '{session_id}' was not found"),
                )
            })?;
        Ok(session)
    }

    /// Load a session for a COLLECTION mutation on `participant`
    /// (spec §8.2: 收藏归属选定 Bot,同 owner). The participant must be the
    /// authenticated Human's own human-actor entry, or a Bot the Human owns
    /// or manages per the live authority facts — and it must actually
    /// participate in the session. The verified Human stays the operator in
    /// the audit context while the participant is the effective actor.
    pub(crate) async fn load_session_for_collection(
        &self,
        caller: &AuthenticatedCaller,
        session_id: &str,
        participant: &str,
    ) -> Result<Session, ApplicationError> {
        let user = require_authenticated_user(caller)?;
        let own_human_actor = format!("human_{}", user.id);

        let target = self
            .registry
            .try_get(participant)
            .await
            .map_err(map_service_error)?;
        match target.as_ref().map(|bot| (bot.actor_kind, bot.created_by.as_deref())) {
            // The Human's own actor entry (registered as `human_{staff_no}`
            // with `actor_kind = Human`) is a legitimate self view.
            Some((ActorKind::Human, _)) if participant == own_human_actor => {}
            // A Bot participant requires the caller to own or manage it.
            Some((ActorKind::Bot, _)) => {
                if !self.authority_allows(&user.id, participant).await? {
                    return Err(ApplicationError::forbidden(
                        "The target participant is not owned or managed by the authenticated Human",
                    ));
                }
            }
            _ => {
                return Err(ApplicationError::forbidden(
                    "The target participant is not owned or managed by the authenticated Human",
                ))
            }
        }

        let session = self.load_session(session_id).await?;
        let present = session
            .participants
            .iter()
            .any(|entry| entry.bot_uuid == participant);
        if !present {
            return Err(ApplicationError::not_found(
                "session_not_found",
                format!("Session '{session_id}' was not found"),
            ));
        }
        Ok(session)
    }

    pub(crate) async fn load_bot(&self, bot_uuid: &str) -> Result<RegisteredBot, ApplicationError> {
        self.registry
            .try_get(bot_uuid)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "bot_not_found",
                    format!("Bot '{bot_uuid}' was not found"),
                )
            })
    }

    /// Resolve the View Actor for list/message reads (spec §8.1). Omitted ⇒
    /// the Human's own view. `human_{self}` allowed. A Bot requires the
    /// authenticated Human to OWN or MANAGE that exact Bot per the live
    /// authority facts (manager parity) — the signed `owner_id` claim no
    /// longer substitutes for the role query.
    pub(crate) async fn resolve_view_actor(
        &self,
        caller: &AuthenticatedCaller,
        requested: Option<&str>,
    ) -> Result<String, ApplicationError> {
        let user = require_authenticated_user(caller)?;
        let human_actor_id = format!("human_{}", user.id);
        let Some(requested) = requested else {
            return Ok(human_actor_id);
        };
        if requested == human_actor_id {
            return Ok(human_actor_id);
        }
        if requested.starts_with("human_") {
            return Err(ApplicationError::forbidden(
                "The explicit Human View Actor must identify the authenticated User",
            ));
        }
        let bot = self
            .load_bot(requested)
            .await
            .map_err(|error| match error {
                ApplicationError::NotFound { .. } => {
                    ApplicationError::forbidden("The explicit View Actor is not authorized")
                }
                other => other,
            })?;
        if bot.actor_kind == ActorKind::Bot
            && self.authority_allows(&user.id, requested).await?
        {
            Ok(requested.to_string())
        } else {
            Err(ApplicationError::forbidden(
                "The explicit View Actor is not authorized",
            ))
        }
    }

    /// Read-access for detail queries: the principal's own participation, or
    /// (for a Human) a participating Bot the Human owns or manages
    /// (spec §8.2: 详情读取同 owner; an owned/managed bot does not grant
    /// visibility into sessions it does not participate in).
    pub(crate) async fn can_read_session_detail(
        &self,
        principal: &Principal,
        session: &Session,
    ) -> Result<bool, ApplicationError> {
        if session
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == principal.actor_id())
        {
            return Ok(true);
        }
        let Principal::Human(human) = principal else {
            return Ok(false);
        };
        let mut candidates = Vec::new();
        for participant in &session.participants {
            if participant.actor_kind == ActorKind::Bot {
                candidates.push(participant.bot_uuid.clone());
            }
        }
        self.human_can_act_as_any(human, candidates).await
    }

    pub(crate) async fn load_session_for_detail(
        &self,
        principal: &Principal,
        session_id: &str,
    ) -> Result<Session, ApplicationError> {
        let session = self.load_session(session_id).await?;
        self.load_group(&session.group_id).await?;
        if !self.can_read_session_detail(principal, &session).await? {
            return Err(ApplicationError::forbidden(
                "The selected Principal is not a Session Participant",
            ));
        }
        Ok(session)
    }

    /// VSN7B: a caller may add a Bot to a session only when that Bot is
    /// collaboration-eligible from the caller OR from at least one of the
    /// parent Group's management anchors (driver, originator). A Hidden bot
    /// is rejected outright regardless of which anchor could sponsor it.
    ///
    /// An anchor sponsors via: self-identity; the target being `public`;
    /// (for a Human anchor) the anchor owning or managing the target per
    /// the live authority facts (spec §8.3 sponsorship — manager grants the
    /// same sponsorship as owner for this collaboration-scope check); or
    /// direct friendship.
    pub(crate) async fn ensure_collaboration_eligible(
        &self,
        principal: &Principal,
        bot_uuid: &str,
        field_name: &str,
        group: &DomainGroup,
    ) -> Result<(), ApplicationError> {
        let bot = self.load_bot(bot_uuid).await?;
        if bot.actor_kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                format!("{field_name} must identify a Bot Actor"),
            ));
        }
        if bot.status == ActorStatus::Hidden {
            return Err(ApplicationError::forbidden(format!(
                "Bot '{bot_uuid}' is hidden and cannot collaborate"
            )));
        }
        if bot.capabilities.visibility == "public" {
            return Ok(());
        }

        // Anchor set: the calling Principal plus the parent Group's driver
        // and originator, deduplicated.
        let mut anchors = Vec::new();
        anchors.push(principal.actor_id());
        anchors.push(group.driver_bot.clone());
        if let Some(originator) = group.originator.as_deref() {
            anchors.push(originator.to_string());
        }
        let mut seen = HashSet::new();
        for anchor in anchors {
            if !seen.insert(anchor.clone()) {
                continue;
            }
            if self.anchor_reaches_bot(&anchor, &bot).await? {
                return Ok(());
            }
        }

        Err(ApplicationError::forbidden(format!(
            "Bot '{bot_uuid}' is not collaboration-eligible for this Principal or the Group driver/originator"
        )))
    }

    /// Whether `bot` is collaboration-reachable from the anchor actor. Only
    /// a Human anchor may sponsor via live authority over the target;
    /// friendship and self-identity are anchor-kind neutral. The legacy
    /// `created_by`/creator-relation sponsorship is deliberately gone
    /// (spec §12.2).
    pub(crate) async fn anchor_reaches_bot(
        &self,
        anchor_id: &str,
        bot: &RegisteredBot,
    ) -> Result<bool, ApplicationError> {
        if anchor_id == bot.bot_uuid {
            return Ok(true);
        }
        if let Some(staff_no) = anchor_id.strip_prefix("human_")
            && self.authority_allows(staff_no, &bot.bot_uuid).await?
        {
            return Ok(true);
        }
        if self
            .friends
            .try_are_friends(anchor_id, &bot.bot_uuid)
            .await
            .map_err(map_service_error)?
        {
            return Ok(true);
        }
        Ok(false)
    }

    // ── audit identity (spec §12.1(6)/§12.5) ───────────────────────────

    /// REQUIRED audit identity carrying the REAL verified operator plus the
    /// effective actor the use case selected — never just the returned
    /// Principal (spec §12.5: 仅记录被代理 Bot 会丢失管理员身份).
    pub(crate) fn operation_context(
        caller: &AuthenticatedCaller,
        effective_actor_id: &str,
    ) -> Result<BotOperationContext, ApplicationError> {
        let user = require_authenticated_user(caller)?;
        Ok(BotOperationContext {
            operation_id: uuid::Uuid::new_v4().to_string(),
            actor: BotOperationActor::Human {
                user_id: user.id.clone(),
                effective_actor_id: effective_actor_id.to_string(),
            },
        })
    }

    /// Audit identity for a Human-only lane where the effective actor IS the
    /// Human (no managed-bot delegation is involved).
    pub(crate) fn self_operation_context(
        caller: &AuthenticatedCaller,
    ) -> Result<BotOperationContext, ApplicationError> {
        let user = require_authenticated_user(caller)?;
        Ok(BotOperationContext {
            operation_id: uuid::Uuid::new_v4().to_string(),
            actor: BotOperationActor::Human {
                user_id: user.id.clone(),
                effective_actor_id: format!("human_{}", user.id),
            },
        })
    }


    /// Resolve the launch caller for V1 session creation: mixed Human+Bot
    /// identity is authorized through the LIVE role facts (the User must own
    /// or manage the exact authenticated Bot); the signed `owner_id` claim
    /// is no longer consulted. This is the spec §12.1(4) application-side
    /// migration of the DTO's former synchronous Principal selection.
    pub(crate) async fn resolve_launch_caller(
        &self,
        caller: &AuthenticatedCaller,
    ) -> Result<bcs_service_api::SessionCaller, ApplicationError> {
        match (&caller.user, &caller.bot) {
            (Some(_), Some(bot)) => {
                let user = require_authenticated_user(caller)?;
                if !self.authority_allows(&user.id, &bot.bot_uuid).await? {
                    return Err(ApplicationError::forbidden(
                        "The authenticated User may not act as the authenticated Bot",
                    ));
                }
                // The authorized mixed identity acts as the Bot itself.
                Ok(bcs_service_api::SessionCaller::Bot {
                    bot_uuid: bot.bot_uuid.clone(),
                })
            }
            (None, Some(bot)) => Ok(bcs_service_api::SessionCaller::Bot {
                bot_uuid: bot.bot_uuid.clone(),
            }),
            (Some(user), None) => Ok(bcs_service_api::SessionCaller::Human {
                actor_id: format!("human_{}", user.id),
                owner_id: user.id.clone(),
                display_name: user
                    .display_name
                    .clone()
                    .or_else(|| user.full_name.clone()),
            }),
            (None, None) => Err(ApplicationError::forbidden(
                "This operation requires a Human or Bot caller",
            )),
        }
    }

    /// Load a session for a DETAIL read after the asynchronous authorized
    /// principal selection (spec §12.1): a Human acting as an
    /// owned/managed participating Bot may read the detail (§8.2 详情读取
    /// 同 owner); the resolved principal still has to satisfy the
    /// participation conditions.
    pub(crate) async fn load_session_for_authorized_detail(
        &self,
        caller: &AuthenticatedCaller,
        session_id: &str,
    ) -> Result<(Principal, Session), ApplicationError> {
        let principal = resolve_authorized_principal(caller, self.authority.as_ref()).await?;
        let session = self.load_session_for_detail(&principal, session_id).await?;
        Ok((principal, session))
    }
    // ── projections ────────────────────────────────────────────────────

    /// Backfill display names for all participants, then project the one
    /// identified by `bot_uuid`.
    pub(crate) async fn backfill_and_project_participant(
        &self,
        participants: &mut [Participant],
        bot_uuid: &str,
    ) -> Result<bcs_service_api::application::v1::session::SessionParticipant, ApplicationError> {
        bcs_service_api::backfill_participant_names(self.registry.as_ref(), participants).await;
        participants
            .iter()
            .find(|p| p.bot_uuid == bot_uuid)
            .map(crate::project_participant)
            .ok_or_else(|| {
                ApplicationError::internal("participant not present in returned Session")
            })
    }
}

/// Map a `BotAuthorityHook` resolution failure (spec §12.4): typed authority
/// branches keep their fixed codes; storage/decode failures stay internal
/// with no SQL leakage.
pub(crate) fn map_authority_hook_error(
    error: bcs_service_api::ServiceError,
) -> ApplicationError {
    match error {
        bcs_service_api::ServiceError::Authority(authority) => ApplicationError::authority(authority),
        other => ApplicationError::internal(other.to_string()),
    }
}

/// Dual-identity audit builder shared by the session and session-file
/// facades (spec §12.1(6)/§12.5): select the effective Principal through the
/// ASYNC authority hook, then carry the verified Human operator (when
/// present) plus that effective actor in the REQUIRED operation context —
/// never just the returned Principal.
pub(crate) async fn authorized_operation_context(
    caller: &AuthenticatedCaller,
    authority: &dyn BotAuthorityHook,
) -> Result<(Principal, BotOperationContext), ApplicationError> {
    let principal = resolve_authorized_principal(caller, authority).await?;
    let context = match (&caller.user, &principal) {
        (Some(user), Principal::Bot(bot)) => BotOperationContext {
            operation_id: uuid::Uuid::new_v4().to_string(),
            actor: BotOperationActor::Human {
                user_id: user.id.clone(),
                effective_actor_id: bot.bot_uuid.clone(),
            },
        },
        (Some(user), Principal::Human(_)) => BotOperationContext {
            operation_id: uuid::Uuid::new_v4().to_string(),
            actor: BotOperationActor::Human {
                user_id: user.id.clone(),
                effective_actor_id: principal.actor_id().to_string(),
            },
        },
        (None, Principal::Bot(bot)) => BotOperationContext {
            operation_id: uuid::Uuid::new_v4().to_string(),
            actor: BotOperationActor::Bot {
                bot_id: bot.bot_uuid.clone(),
            },
        },
        (None, Principal::Human(_)) => {
            return Err(ApplicationError::Unauthenticated);
        }
    };
    Ok((principal, context))
}
