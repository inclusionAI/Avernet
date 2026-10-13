//! Final-writer delivery authorization (bot-authority spec §8.2/§8.3).
//!
//! The WS final writer consults this application service once per
//! protected frame. Every call re-reads the CURRENT strict
//! authorization snapshot of the binding's resource (master data
//! source, never a display cache) plus, for Bot views, the current
//! management edge — no connection-time scope, no enqueue-time
//! authorization and no positive-TTL cache is reused.
//!
//! Application-level result only: the WS adapter never observes a
//! SQL/Core error. Underlying read failures for a Bot view can only
//! raise an internal error; see [`ERROR_INTERNAL_READ`].

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, DeliveryAuthorizationService, DeliveryDecision,
    DeliveryVisibility, ProtectedDeliveryBinding,
};
use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::port::bot_authority::BotAuthorityHook;
use bcs_service_api::types::authorization_snapshot::{
    GroupAuthorizationSnapshot, ParticipantAuthorizationSnapshot,
    SessionAuthorizationSnapshot,
};
use bcs_service_api::types::{HumanMessageView, MessageViewScope};
use bcs_service_api::{ActorKind, GroupCoreService, GroupStrategy};

/// Edge case: the strict application facts could not be read
/// (unwired hook or storage failure). The value report is a
/// desensitized application error only — no SQL/Core detail crosses
/// the boundary.
pub const ERROR_INTERNAL_READ: &str = "delivery_authorization_read_failed";

/// Final-writer delivery authorization over committed authorization
/// facts.
///
/// `groups` provides the strict Group snapshot read (and the
/// associated Group strategy for Session frames);
/// `sessions` the strict Session snapshot read; `bot_authority` the
/// live Human→Bot management edge consulted ONLY for Bot views
/// (a missing wiring fails closed).
pub struct DeliveryAuthorizationServiceImpl {
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionAuthorizationCoreService>,
    bot_authority: Option<Arc<dyn BotAuthorityHook>>,
}

impl DeliveryAuthorizationServiceImpl {
    pub fn new(
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionAuthorizationCoreService>,
        bot_authority: Option<Arc<dyn BotAuthorityHook>>,
    ) -> Self {
        Self {
            groups,
            sessions,
            bot_authority,
        }
    }

    fn internal_read() -> ApplicationError {
        ApplicationError::internal(ERROR_INTERNAL_READ)
    }

    fn invalid_frame_read(
        error: bcs_service_api::ServiceError,
    ) -> ApplicationError {
        let _ = error;
        Self::internal_read()
    }

    /// The running decision for one frame: build the HumanMessageView
    /// from THIS read's participant scope (never a cached one), then
    /// apply the trusted producer domain/audience rules.
    fn decide(
        view_scope: MessageViewScope,
        allow_legacy_unclassified_chat: bool,
        actor_id: &str,
        message: &DeliveryVisibility,
    ) -> DeliveryDecision {
        let view = HumanMessageView {
            actor_id: actor_id.to_string(),
            scope: view_scope,
            allow_legacy_unclassified_chat,
        };
        if view.allows_artifact(message.domain, message.audience.as_ref()) {
            DeliveryDecision::Deliver
        } else {
            DeliveryDecision::SkipMessage
        }
    }

    /// Resource qualification: the view actor must still be a
    /// participant of the frame's resource with the expected actor
    /// kind. A missing participant is a lost resource qualification
    /// (invalidation), not a visibility miss (skip).
    fn current_view_participant<'a>(
        actor_id: &str,
        actor_kind: ActorKind,
        participants: &'a [ParticipantAuthorizationSnapshot],
    ) -> Option<&'a ParticipantAuthorizationSnapshot> {
        participants
            .iter()
            .find(|participant| {
                participant.actor_id == actor_id
                    && participant.actor_kind == actor_kind
            })
    }
}

#[async_trait]
impl DeliveryAuthorizationService for DeliveryAuthorizationServiceImpl {
    async fn authorize_delivery(
        &self,
        binding: &ProtectedDeliveryBinding,
        message: &DeliveryVisibility,
    ) -> Result<DeliveryDecision, ApplicationError> {
        match binding.session_id.as_deref() {
            Some(session_id) => {
                // Session/Run frame: current Session scope plus the
                // associated Group's strategy (spec §8.3). A missing
                // Session participant cannot be repaired by a Group
                // seed.
                let session = self
                    .sessions
                    .read_authorization_snapshot(session_id)
                    .await
                    .map_err(Self::invalid_frame_read)?;
                let Some(session) = session else {
                    // Session deleted: component qualification is gone.
                    return Ok(DeliveryDecision::InvalidateBinding);
                };
                if session.group_id != binding.group_id {
                    return Ok(DeliveryDecision::InvalidateBinding);
                }
                let group = self
                    .groups
                    .read_authorization_snapshot(&session.group_id)
                    .await
                    .map_err(Self::invalid_frame_read)?;
                let Some(group) = group else {
                    // The associated Group was deleted: qualification gone.
                    return Ok(DeliveryDecision::InvalidateBinding);
                };
                self.authorize_against(binding, message, &group, Some(&session))
                    .await
            }
            None => {
                // Group frame: current Group scope.
                let group = self
                    .groups
                    .read_authorization_snapshot(&binding.group_id)
                    .await
                    .map_err(Self::invalid_frame_read)?;
                let Some(group) = group else {
                    return Ok(DeliveryDecision::InvalidateBinding);
                };
                self.authorize_against(binding, message, &group, None).await
            }
        }
    }
}

impl DeliveryAuthorizationServiceImpl {
    /// The expected view actor kind from the actor id shape:
    /// `human_*` actors are Human participants, everything else is a
    /// Bot participant. (V1 rejects other-Human views at connect;
    /// legacy UserBound connections keep their bcs-group
    /// connect-time view authorization and are re-qualified here by
    /// the same participant facts.)
    fn view_kind(binding: &ProtectedDeliveryBinding) -> ActorKind {
        if binding.view_actor_id.starts_with("human_") {
            ActorKind::Human
        } else {
            ActorKind::Bot
        }
    }

    /// The Bot uuid when the selected view is a Bot actor.
    fn bot_view_id(binding: &ProtectedDeliveryBinding) -> Option<&str> {
        (Self::view_kind(binding) == ActorKind::Bot)
            .then_some(binding.view_actor_id.as_str())
    }

    async fn authorize_against(
        &self,
        binding: &ProtectedDeliveryBinding,
        message: &DeliveryVisibility,
        group: &GroupAuthorizationSnapshot,
        session: Option<&SessionAuthorizationSnapshot>,
    ) -> Result<DeliveryDecision, ApplicationError> {
        // Bot views need the CURRENT Human→Bot management edge:
        // 转交 keeps the old owner usable while the manager edge
        // holds; removing the edge invalidates the binding. The
        // Human self view keeps its independent §3.3 resource right
        // and consults no authority edge.
        if let Some(bot_id) = Self::bot_view_id(binding) {
            let authorized = match self.bot_authority.as_ref() {
                Some(hook) => hook.can_manage(&binding.user_id, bot_id).await,
                None => Err(bcs_service_api::ServiceError::InternalError(
                    "bot authority hook is not wired".to_string(),
                )),
            };
            match authorized {
                Ok(true) => {}
                Ok(false) => return Ok(DeliveryDecision::InvalidateBinding),
                Err(_) => return Err(Self::internal_read()),
            }
        }

        // Resource qualification on the frame's own resource: the
        // view actor must still be a participant. A missing
        // Session participant cannot be repaired by a Group seed
        // (spec §8.3); a Human participant that moved to Absent lost
        // what connect granted.
        let resource_participants = match session {
            Some(session) => &session.participants,
            None => &group.participants,
        };
        let Some(participant) = Self::current_view_participant(
            &binding.view_actor_id,
            Self::view_kind(binding),
            resource_participants,
        ) else {
            return Ok(DeliveryDecision::InvalidateBinding);
        };
        if participant.actor_kind == ActorKind::Human
            && participant.effective_mode == bcs_service_api::ParticipantMode::Absent
        {
            return Ok(DeliveryDecision::InvalidateBinding);
        }

        // Legacy unclassified frames follow the associated Group's
        // own strategy rule (Chat groups keep the legacy Chat
        // projection; scoped strategies fail closed).
        let allow_legacy_unclassified_chat =
            group.group_strategy == GroupStrategy::Chat;

        Ok(Self::decide(
            participant.message_view_scope,
            allow_legacy_unclassified_chat,
            &binding.view_actor_id,
            message,
        ))
    }
}
