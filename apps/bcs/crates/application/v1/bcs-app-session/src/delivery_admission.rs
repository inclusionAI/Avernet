//! Operation-boundary delivery admission (bot-authority spec
//! §8.2/§8.3 — 已准入输出不追溯).
//!
//! Delivery authorization is decided ONCE per business operation per
//! recipient connection, at operation-accept time — never per frame.
//! One resolution call reads the CURRENT strict authorization
//! snapshot of the operation's canonical resource (master data
//! source, never a display cache) a single time and shares it across
//! every candidate connection, plus the current management edge for
//! Bot views. The returned views are frozen by the adapter into
//! per-connection permits (business operations) or applied to one
//! independent resource event and discarded.
//!
//! Application-level result only: the caller never observes a
//! SQL/Core error. Underlying read failures are desensitized to
//! [`ERROR_INTERNAL_READ`] and fail the WHOLE call closed (zero
//! permits) without touching any earlier permit.

use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    AdmittedRecipient, ApplicationError, DeliveryAdmissionService, DeliveryScope,
    ProtectedDeliveryBinding,
};
use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::types::authorization_snapshot::ParticipantAuthorizationSnapshot;
use bcs_service_api::{ActorKind, GroupCoreService, GroupStrategy};

/// Edge case: the strict application facts could not be read
/// (unwired hook or storage failure). The value report is a
/// desensitized application error only — no SQL/Core detail crosses
/// the boundary.
pub const ERROR_INTERNAL_READ: &str = "delivery_admission_read_failed";

/// Operation-boundary delivery admission over committed
/// authorization facts.
///
/// `groups` provides the strict Group snapshot read (and the
/// associated Group strategy for Session scopes); `sessions` the
/// strict Session snapshot read; `bot_authority` the live Human→Bot
/// management edge consulted ONLY for Bot views (a missing wiring
/// fails closed).
pub struct DeliveryAdmissionServiceImpl {
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionAuthorizationCoreService>,
    bot_authority: Option<Arc<dyn BotAuthorityPort>>,
}

impl DeliveryAdmissionServiceImpl {
    pub fn new(
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionAuthorizationCoreService>,
        bot_authority: Option<Arc<dyn BotAuthorityPort>>,
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

    fn invalid_read(error: bcs_service_api::ServiceError) -> ApplicationError {
        let _ = error;
        Self::internal_read()
    }
}

#[async_trait]
impl DeliveryAdmissionService for DeliveryAdmissionServiceImpl {
    async fn resolve_recipient_views(
        &self,
        scope: &DeliveryScope,
        candidates: &[ProtectedDeliveryBinding],
    ) -> Result<Vec<AdmittedRecipient>, ApplicationError> {
        if candidates.is_empty() {
            return Ok(Vec::new());
        }

        // ONE batch of authoritative reads per admission boundary,
        // shared by every candidate: the frame's own canonical
        // resource snapshot plus, for Session scopes, the associated
        // Group (its strategy governs the legacy Chat projection).
        // A deleted resource admits nobody; a read failure fails the
        // whole call closed.
        let participant_source: Vec<ParticipantAuthorizationSnapshot>;
        let group = match scope.session_id.as_deref() {
            Some(session_id) => {
                let session = self
                    .sessions
                    .read_authorization_snapshot(session_id)
                    .await
                    .map_err(Self::invalid_read)?;
                let Some(session) = session else {
                    return Ok(Vec::new());
                };
                if session.group_id != scope.group_id {
                    return Ok(Vec::new());
                }
                participant_source = session.participants;
                let group = self
                    .groups
                    .read_authorization_snapshot(&session.group_id)
                    .await
                    .map_err(Self::invalid_read)?;
                let Some(group) = group else {
                    return Ok(Vec::new());
                };
                group
            }
            None => {
                let group = self
                    .groups
                    .read_authorization_snapshot(&scope.group_id)
                    .await
                    .map_err(Self::invalid_read)?;
                let Some(group) = group else {
                    return Ok(Vec::new());
                };
                participant_source = group.participants.clone();
                group
            }
        };

        // Legacy unclassified frames follow the associated Group's
        // own strategy rule (Chat groups keep the legacy Chat
        // projection; scoped strategies fail closed).
        let allow_legacy_unclassified_chat =
            group.group_strategy == GroupStrategy::Chat;

        // Bot-view candidates need the CURRENT management edge each:
        // collect the distinct Bot views so the loop below stays a
        // pure lookup and an unhealthy authority hook fails the whole
        // call BEFORE any permit is emitted (never a silent partial
        // downgrade of Bot viewers). The view kind comes from the
        // actor id SHAPE (a `human_*` actor is a Human participant
        // even when it is not the connection's own user), never from
        // the connection-self comparison.
        let bot_views: HashSet<(&str, &str)> = candidates
            .iter()
            .filter(|candidate| candidate.view_kind() == ActorKind::Bot)
            .map(|candidate| (candidate.user_id.as_str(), candidate.view_actor_id.as_str()))
            .collect();
        let mut denied_bot_views: HashSet<(&str, &str)> = HashSet::new();
        for (user_id, bot_id) in bot_views.iter().copied() {
            let authorized = match self.bot_authority.as_ref() {
                Some(hook) => hook.can_manage(user_id, bot_id).await,
                None => Err(bcs_service_api::ServiceError::InternalError(
                    "bot authority hook is not wired".to_string(),
                )),
            };
            match authorized {
                Ok(true) => {}
                Ok(false) => {
                    denied_bot_views.insert((user_id, bot_id));
                }
                Err(_) => return Err(Self::internal_read()),
            }
        }

        let mut admitted = Vec::with_capacity(candidates.len());
        for candidate in candidates {
            // The candidate must belong to THIS operation's canonical
            // resource exactly: a session-scoped operation never
            // repairs a missing Session participant from a Group
            // seed (F4 strict target), and a cross-resource
            // candidate (mis-keyed subscription) is skipped.
            if candidate.group_id != scope.group_id
                || candidate.session_id != scope.session_id
            {
                continue;
            }
            let actor_kind = candidate.view_kind();
            if actor_kind == ActorKind::Bot
                && denied_bot_views.contains(&(
                    candidate.user_id.as_str(),
                    candidate.view_actor_id.as_str(),
                ))
            {
                continue;
            }
            let Some(participant) = participant_source.iter().find(|participant| {
                participant.actor_id == candidate.view_actor_id
                    && participant.actor_kind == actor_kind
            }) else {
                // Not a participant of the canonical resource: lost
                // resource qualification at admission ⇒ no permit.
                continue;
            };
            // A Human participant that moved to Absent lost what the
            // connection's earlier admission granted (default_for:
            // Human mode None is effectively Absent).
            if participant.actor_kind == ActorKind::Human
                && participant.effective_mode == bcs_service_api::ParticipantMode::Absent
            {
                continue;
            }
            admitted.push(AdmittedRecipient {
                connection_instance_id: candidate.connection_instance_id,
                view_actor_id: candidate.view_actor_id.clone(),
                scope: participant.message_view_scope,
                allow_legacy_unclassified_chat,
            });
        }
        Ok(admitted)
    }
}
