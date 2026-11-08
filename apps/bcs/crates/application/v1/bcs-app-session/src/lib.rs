//! Versioned Session application facade for the BCN V1 API.
//!
//! Implements both [`SessionService`] and [`SessionMessageService`]. The
//! facade owns authenticated-Caller resource authorization and V1 projections
//! while delegating the legacy session lifecycle to
//! [`SessionManagementService`]. No HTTP type crosses this boundary.
//!
//! V1 operations select their effective Actor according to the route contract.
//! Detail reads, actor-relative lists, message history, and mutations retain
//! separate resource-authorization rules.

mod connection;
mod delivery_admission;
mod file;
mod group_context_access;
mod authorization;
mod participant;
mod message;

pub use connection::GroupSessionConnectionServiceImpl;
pub use delivery_admission::{DeliveryAdmissionServiceImpl, ERROR_INTERNAL_READ};
pub use file::SessionFileApplicationServiceImpl;
pub use group_context_access::GroupContextAddAccessGuard;

use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::session::{SessionManagementService, SessionUseCaseError};
use bcs_service_api::application::system_message::SystemMessageService;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, DeleteResult, HumanPrincipal, IdentityPolicy, Page,
    Principal, resolve_authorized_principal,
    message::{ListSessionMessages, SessionMessageService},
    require_authenticated_user, require_human, select_principal,
    session::{
        AddSessionParticipant, CollectSession, CompleteSession, CreateSession,
        CreateSessionOutcome, DeleteSession, DeleteSessionParticipant, GetSession, ListSessions,
        SessionCollectionResult, SessionCompletionResult, SessionDetail, SessionParticipant,
        SessionService, SessionStatus as V1SessionStatus, SessionSummary, UncollectSession,
        UpdateSession, UpdateSessionParticipant,
    },
};
use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_service_api::port::{NoopParticipantViewBindingPort, ParticipantViewBindingPort};
use bcs_service_api::types::{HumanMessageView, MessageViewScope};
use bcs_service_api::{
    ActorKind, ActorStatus, BotRegistryCoreService, CallerContext, CollaborationRuntimeError,
    CollaborationRuntimeService, CreateSessionLaunch, FriendCoreService, Group as DomainGroup,
    GroupCoreService, GroupMessage, GroupMessageHistoryService, GroupStrategy, GroupUseCaseError,
    HumanActor, MessageHistoryOptions, Participant, ParticipantMode, ParticipantRole,
    RegisteredBot, RequestedSessionRole, ServiceError, Session,
    SessionCaller,
    SessionHistoryCommand, SessionKind, SessionLaunchError, SessionLaunchRequest,
    SessionLaunchService, SessionStatus as DomainSessionStatus, StateMachineRunView,
    SystemMessageEvent, backfill_participant_names,
};

/// OpenAPI v1 Session facade.
///
/// Holds the legacy [`SessionManagementService`] for lifecycle delegation,
/// `SessionRepoPort` for V1 list/count queries, and transport-neutral history
/// services for Session message reads.
pub struct SessionServiceImpl {
    launch: Arc<dyn SessionLaunchService>,
    sessions: Arc<dyn SessionManagementService>,
    groups: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    friends: Arc<dyn FriendCoreService>,
    session_repo: Arc<dyn SessionRepoPort>,
    history: Arc<dyn GroupMessageHistoryService>,
    collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    system_message: Arc<dyn SystemMessageService>,
    participant_view_bindings: Arc<dyn ParticipantViewBindingPort>,
    /// The narrow authority hook (bot-authority plan Task
    /// 6.3): every Human→Bot control judgment consults it
    /// live; a missing wiring fails closed.
    bot_authority: Option<Arc<dyn BotAuthorityPort>>,    /// The strict Session authorization-fact Core
    /// (bot-authority plan §3, spec §8.3): the
    /// resource-role legs of Session detail reads
    /// consume its snapshot — never this
    /// instance's display fetch. A missing wiring
    /// fails closed.
    session_snapshots: Option<Arc<dyn SessionAuthorizationCoreService>>,
}

impl SessionServiceImpl {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        launch: Arc<dyn SessionLaunchService>,
        sessions: Arc<dyn SessionManagementService>,
        groups: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friends: Arc<dyn FriendCoreService>,
        session_repo: Arc<dyn SessionRepoPort>,
        history: Arc<dyn GroupMessageHistoryService>,
        collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
        system_message: Arc<dyn SystemMessageService>,
    ) -> Self {
        Self {
            launch,
            sessions,
            groups,
            registry,
            friends,
            session_repo,
            history,
            collaboration_runtime,
            system_message,
            participant_view_bindings: Arc::new(NoopParticipantViewBindingPort),
            bot_authority: None,
            session_snapshots: None,
        }
    }

    /// Wire the strict Session authorization-snapshot
    /// Core (spec §8.3).
    pub fn with_session_authorization_core(
        mut self,
        session_snapshots: Arc<dyn SessionAuthorizationCoreService>,
    ) -> Self {
        self.session_snapshots = Some(session_snapshots);
        self
    }

    /// Wire the authority hook (bot-authority plan Task 6.3).
    pub fn with_bot_authority_hook(
        mut self,
        bot_authority: Arc<dyn BotAuthorityPort>,
    ) -> Self {
        self.bot_authority = Some(bot_authority);
        self
    }

    /// The wired authority hook; failing to wire one is a
    /// composition error and must fail closed (never deny-as-Ok).
    fn authority(&self) -> Result<&dyn BotAuthorityPort, ApplicationError> {
        self.bot_authority
            .as_deref()
            .ok_or_else(|| {
                ApplicationError::internal(
                    "bot authority hook is not wired",
                )
            })
    }

    /// The CURRENT Human→Bot control judgment (rows 4/6/7,
    /// bot-authority plan Task 6.3): a live `can_manage`
    /// edge for the verified User id. The historical
    /// `created_by` fact and the legacy creator relation
    /// edge no longer authorize anything here.
    async fn human_controls_bot(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> Result<bool, ApplicationError> {
        self.authority()?
            .can_manage(user_id, bot_id)
            .await
            .map_err(map_authority_error)
    }

    /// Hybrid identity resolution (rows 4/7): replaces the
    /// claim-local `HumanOrOwnedBot` selection with
    /// `resolve_authorized_principal` for the authenticated
    /// Bot under the live management edge; pure Bot / pure
    /// Human credentials keep their existing selection.
    async fn resolve_principal(
        &self,
        caller: &AuthenticatedCaller,
    ) -> Result<Principal, ApplicationError> {
        match &caller.bot {
            Some(bot) => {
                resolve_authorized_principal(
                    caller,
                    IdentityPolicy::HumanOrAuthorizedBot {
                        bot_id: bot.bot_uuid.clone(),
                    },
                    self.authority()?,
                )
                .await
            }
            None => select_principal(caller, IdentityPolicy::HumanOrOwnedBot),
        }
    }

    /// Project the authenticated caller of a V1 create onto
    /// the launch contract's `SessionCaller` (row 4). The
    /// Bot caller choice for hybrid credentials happens
    /// HERE, through the LIVE authority edge; the adapter
    /// keeps protocol adaptation only.
    async fn launch_caller_from_principal(
        &self,
        caller: &AuthenticatedCaller,
    ) -> Result<SessionCaller, ApplicationError> {
        match self.resolve_principal(caller).await? {
            Principal::Bot(bot) => Ok(SessionCaller::Bot {
                bot_uuid: bot.bot_uuid.clone(),
            }),
            Principal::Human(human) => Ok(SessionCaller::Human {
                actor_id: format!("human_{}", human.subject.id),
                owner_id: human.subject.id.clone(),
                display_name: human
                    .subject
                    .display_name
                    .clone()
                    .or_else(|| human.subject.full_name.clone()),
            }),
        }
    }

    pub fn with_participant_view_bindings(
        mut self,
        participant_view_bindings: Arc<dyn ParticipantViewBindingPort>,
    ) -> Self {
        self.participant_view_bindings = participant_view_bindings;
        self
    }

}

#[async_trait]
impl SessionService for SessionServiceImpl {
    async fn create(
        &self,
        command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError> {
        // Row 4 (resolve before the launch): the effective
        // SessionCaller comes from the LIVE hybrid rule —
        // `resolve_authorized_principal` for the
        // authenticated Bot. A hybrid User+Bot credential
        // becomes the Bot caller ONLY through the CURRENT
        // management edge; a revoked historical owner is a
        // 403 here, and a current manager is admitted. Pure
        // Bot / pure Human credentials keep their existing
        // selection (the hook is not consulted).
        let caller = self
            .launch_caller_from_principal(&command.caller)
            .await?;
        let outcome = self
            .launch
            .create(CreateSessionLaunch {
                request: SessionLaunchRequest {
                    caller,
                    group_id: command.group_id,
                    requested_creator: command.acting_bot_id,
                    title: command.title,
                    kind: command.kind,
                    input: command.input,
                    meta: command.meta,
                    public_creator_role: command.creator_role.map(RequestedSessionRole::from),
                    human_message_view_scope: command.message_view_scope,
                    context_delivery: command.context_delivery,
                },
            })
            .await
            .map_err(map_launch_error)?;
        let detail = self
            .project_detail(&outcome.session, outcome.state_machine_run)
            .await?;
        Ok(CreateSessionOutcome {
            session: detail,
            created: outcome.created,
        })
    }

    async fn list(&self, command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        let view_actor_id = self
            .resolve_view_actor(&command.caller, command.view_bot_id.as_deref())
            .await?;
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        self.load_group(&command.group_id).await?;
        let participant_id = Some(view_actor_id.as_str());
        let status = command.status.map(map_status_to_domain);
        let mut sessions = self
            .sessions
            .list_by_group(
                &command.group_id,
                status,
                command.offset,
                command.limit,
                None,
                participant_id,
            )
            .await
            .map_err(map_session_error)?;
        // Repo ORDER BY already guarantees created_at DESC, session_id ASC
        // (VSN7M); keep this sort as a no-op safety net for impls that do not
        // honour the ordered contract.
        sessions.sort_by(|a, b| {
            b.created_at
                .cmp(&a.created_at)
                .then_with(|| a.id.cmp(&b.id))
        });
        let total = self
            .session_repo
            .count_by_group(&command.group_id, status, None, participant_id)
            .await
            .map_err(map_service_error)?;
        // Surface per-session collected state for the view actor, but only
        // when the request explicitly names that actor (mirrors the legacy
        // `list_sessions_for_group` gating: collected is only meaningful
        // relative to a named participant). When `view_bot_id` is omitted the
        // field stays `None` and is skipped on serialization.
        let collected_set: HashSet<String> = if command.view_bot_id.is_some() {
            let ids: Vec<&str> = sessions.iter().map(|s| s.id.as_str()).collect();
            self.sessions
                .collected_at_map(&ids, &view_actor_id)
                .await
                .map_err(map_session_error)?
                .into_iter()
                .map(|(sid, _collected_at)| sid)
                .collect()
        } else {
            HashSet::new()
        };
        let items = sessions
            .iter()
            .map(|s| {
                let mut summary = project_summary(s);
                if command.view_bot_id.is_some() {
                    summary.collected = Some(collected_set.contains(&s.id));
                }
                summary
            })
            .collect::<Vec<_>>();
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn get(&self, query: GetSession) -> Result<SessionDetail, ApplicationError> {
        let session = self
            .load_session_for_detail(&query.caller, &query.session_id)
            .await?;
        self.project_detail(&session, None).await
    }

    async fn update(&self, command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        let principal = self.resolve_principal(&command.caller).await?;
        // Only `title` is mutable in phase one; a request carrying no field is
        // rejected (mirrors the sibling Group V1 facade).
        if command.title.is_none() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "at least one mutable field is required",
            ));
        }
        self.load_session_for_manage(&principal, &command.session_id)
            .await?;
        let session = self
            .sessions
            .update_title(&command.session_id, command.title)
            .await
            .map_err(map_session_error)?;
        self.project_detail(&session, None).await
    }

    async fn delete(&self, command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        let principal = self.resolve_principal(&command.caller).await?;
        // Idempotent: a missing session yields `deleted: false` rather than a
        // 404 so repeat deletes converge. Non-managers still get 403.
        let session = match self
            .sessions
            .get(&command.session_id)
            .await
            .map_err(map_session_error)?
        {
            Some(session) => session,
            None => return Ok(DeleteResult { deleted: false }),
        };
        let group = self.load_group(&session.group_id).await?;
        let can_delete = if let Some(requested) = command.acting_bot_id.as_deref() {
            let acting_actor_id = match &principal {
                Principal::Human(_) => {
                    self.resolve_view_actor(&command.caller, Some(requested))
                        .await?
                }
                Principal::Bot(bot) if requested == bot.bot_uuid => requested.to_string(),
                Principal::Bot(_) => {
                    return Err(ApplicationError::forbidden(
                        "The explicit acting Bot must identify the authenticated Bot",
                    ));
                }
            };
            // Spec §8.3: management anchors as
            // snapshot facts, re-read at the
            // decision — the display copy never
            // authorizes.
            let management_actor_ids = self
                .groups
                .read_authorization_snapshot(&group.id)
                .await
                .map_err(map_service_error)?
                .map(|snapshot| {
                    snapshot
                        .management_actor_ids()
                        .into_iter()
                        .map(str::to_string)
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            management_actor_ids
                .iter()
                .any(|actor_id| actor_id == &acting_actor_id)
                || session
                    .created_by
                    .as_deref()
                    .is_some_and(|creator| creator == acting_actor_id)
        } else {
            self.can_manage_session(&principal, &session, &group)
                .await?
        };
        if !can_delete {
            return Err(ApplicationError::forbidden(
                "Principal may not delete this Session",
            ));
        }
        let deleted = self
            .sessions
            .delete(&command.session_id)
            .await
            .map_err(map_session_error)?;
        Ok(DeleteResult { deleted })
    }

    async fn complete(
        &self,
        command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let (session, _) = self
            .load_session_for_manage(&principal, &command.session_id)
            .await?;
        // VaGQN: ServiceInvocation sessions have their own callback/output
        // lifecycle and must not be completed via this V1 endpoint (legacy
        // handler rejects "service sessions cannot be completed via this
        // endpoint"). Gate the CAS with a `session_kind` check.
        if session.session_kind == SessionKind::ServiceInvocation {
            return Err(ApplicationError::conflict(
                "conflict",
                "Service sessions cannot be completed via this endpoint",
            ));
        }
        // If already Completed, return the stable completed state idempotently
        // without invoking the CAS. Otherwise attempt completion; a `None`
        // result means a concurrent caller completed it between our read and
        // the CAS, so reload to surface the final `completed_at`.
        let completed = if matches!(session.status, DomainSessionStatus::Completed) {
            session
        } else {
            match self
                .sessions
                .complete_if_running(&command.session_id, None, None)
                .await
                .map_err(map_session_error)?
            {
                Some(session) => session,
                None => match self
                    .sessions
                    .get(&command.session_id)
                    .await
                    .map_err(map_session_error)?
                {
                    Some(session) => session,
                    None => {
                        return Err(ApplicationError::not_found(
                            "session_not_found",
                            format!("Session '{}' was not found", command.session_id),
                        ));
                    }
                },
            }
        };
        let completed_at = completed.completed_at.unwrap_or(completed.updated_at);
        Ok(SessionCompletionResult {
            session_id: completed.id,
            status: V1SessionStatus::Completed,
            completed_at,
        })
    }

    async fn collect(
        &self,
        command: CollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        self.load_session_for_collection(
            &command.caller,
            &command.session_id,
            &command.participant,
        )
        .await?;
        self.sessions
            .collect(&command.session_id, &command.participant)
            .await
            .map_err(map_session_error)?;
        Ok(SessionCollectionResult {
            session_id: command.session_id,
            participant: command.participant,
            collected: true,
        })
    }

    async fn uncollect(
        &self,
        command: UncollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        self.load_session_for_collection(
            &command.caller,
            &command.session_id,
            &command.participant,
        )
        .await?;
        self.sessions
            .uncollect(&command.session_id, &command.participant)
            .await
            .map_err(map_session_error)?;
        Ok(SessionCollectionResult {
            session_id: command.session_id,
            participant: command.participant,
            collected: false,
        })
    }

    async fn add_participant(
        &self,
        command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let (session, group) = self
            .load_session_for_manage(&principal, &command.session_id)
            .await?;
        // VSN7B: the added Bot must be collaboration-eligible from the caller
        // OR from the parent Group's driver/originator (visible + friend/creator
        // relation), not merely registered.
        self.ensure_collaboration_eligible(&principal, &command.bot_uuid, "bot_uuid", &group)
            .await?;
        // VfhG3: explicit 409 if the target Bot is already a session participant.
        // The legacy memory repo silently skipped duplicates (idempotent); the V1
        // contract surfaces a `participant_already_exists` Conflict so callers
        // can distinguish a real add from a no-op.
        if session
            .participants
            .iter()
            .any(|p| p.bot_uuid == command.bot_uuid)
        {
            return Err(ApplicationError::conflict(
                "participant_already_exists",
                format!(
                    "Bot '{}' is already a participant of Session '{}'",
                    command.bot_uuid, command.session_id
                ),
            ));
        }
        let target = self
            .registry
            .try_get(&command.bot_uuid)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "actor_not_found",
                    format!("Actor '{}' was not found", command.bot_uuid),
                )
            })?;
        let mode = ParticipantMode::default_for(target.actor_kind);
        // VfhG3: derive role from parent group.participants if the bot is already
        // there; otherwise strategy default (ManagerWorker→Worker, else
        // Consultant). Mirrors legacy bcs-http add_session_participant which picks
        // role from body.role or defaults by strategy (ManagerWorker→Worker,
        // Chat→Consultant) rather than hardcoding Consultant.
        let group_participant = group
            .participants
            .iter()
            .find(|p| p.bot_uuid == command.bot_uuid);
        let role = group_participant.map(|p| p.role).unwrap_or_else(|| {
            if target.actor_kind == ActorKind::Human {
                ParticipantRole::Observer
            } else {
                match group.group_strategy {
                    GroupStrategy::ManagerWorker => ParticipantRole::Worker,
                    _ => ParticipantRole::Consultant,
                }
            }
        });
        let tags = group_participant
            .map(|participant| participant.tags.clone())
            .unwrap_or_default();
        let message_view_scope = command
            .message_view_scope
            .or_else(|| group_participant.map(|participant| participant.message_view_scope))
            .unwrap_or(MessageViewScope::Full);
        if !message_view_scope.is_valid_for(target.actor_kind) {
            return Err(ApplicationError::invalid(
                "invalid_message_view_scope",
                "Bot participants must use full message_view_scope",
            ));
        }
        let participant = Participant {
            bot_uuid: command.bot_uuid.clone(),
            bot_name: None,
            kind: None,
            role,
            actor_kind: target.actor_kind,
            mode: Some(mode),
            tags,
            message_view_scope,
        };
        let mut updated = self
            .sessions
            .add_participant(&command.session_id, participant)
            .await
            .map_err(map_session_error)?;
        // Emit a `BotJoined` system message so the newly added participant (and
        // the rest of the session) receives the join notification — mirroring
        // the legacy `bcs_http::routes::sessions::add_session_participant` path,
        // which the OpenAPI route previously lacked. Best-effort: a dispatch
        // failure is logged and never surfaces to the caller.
        if let Some(actor) = updated
            .participants
            .iter()
            .find(|p| p.bot_uuid == command.bot_uuid)
            .cloned()
        {
            let event = SystemMessageEvent::BotJoined {
                group_id: updated.group_id.clone(),
                actor,
                session_id: command.session_id.clone(),
                session_input: updated.input.clone(),
            };
            if let Err(error) = self
                .system_message
                .notify(
                    &updated.group_id,
                    event,
                    &command.session_id,
                    &updated.participants,
                )
                .await
            {
                tracing::warn!(
                    session_id = %command.session_id,
                    bot_uuid = %command.bot_uuid,
                    error = %error,
                    "notify bot joined failed"
                );
            }
        }
        self.backfill_and_project_participant(&mut updated.participants, &command.bot_uuid)
            .await
    }

    async fn update_participant(
        &self,
        command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        self.update_participant_impl(command).await
    }

    async fn delete_participant(
        &self,
        command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        let principal = require_human(&command.caller)?;
        // Design §8.7 parity with the Group facade: the target Actor may leave
        // the Session (self-service delete) without session-manage
        // authorization — removing the caller's own Human Actor, or a Bot the
        // caller owns, is a voluntary leave. The legacy removal path still
        // rejects removing the group driver or the ManagerWorker Manager, so
        // the structural invariant is preserved for self-leaves too.
        let is_self = self
            .principal_can_act_as(&principal, &command.bot_uuid)
            .await?;
        let session = if is_self {
            self.load_session(&command.session_id).await?
        } else {
            self.load_session_for_manage(&principal, &command.session_id)
                .await?
                .0
        };
        // Capture the participant being removed (its real role/mode/actor_kind)
        // before the mutation so the `BotLeft` event carries accurate identity
        // — the legacy `remove_session_participant` hardcodes `Observer`/`None`.
        let removed = session
            .participants
            .iter()
            .find(|p| p.bot_uuid == command.bot_uuid)
            .cloned();
        // Idempotent: if the target is not a current participant, return
        // `deleted: false` without invoking the legacy removal (which would
        // surface a `SessionInvalidParams` "not in session" error otherwise).
        if removed.is_none() {
            return Ok(DeleteResult { deleted: false });
        }
        let updated = self
            .sessions
            .remove_participant(&command.session_id, &command.bot_uuid)
            .await
            .map_err(map_session_error)?;
        // Emit a `BotLeft` system message so the remaining participants receive
        // the leave notification — mirroring the legacy
        // `remove_session_participant` path, which the OpenAPI route lacked.
        // Best-effort: a dispatch failure is logged and never surfaces.
        if let Some(actor) = removed {
            let event = SystemMessageEvent::BotLeft {
                group_id: updated.group_id.clone(),
                actor,
            };
            if let Err(error) = self
                .system_message
                .notify(
                    &updated.group_id,
                    event,
                    &command.session_id,
                    &updated.participants,
                )
                .await
            {
                tracing::warn!(
                    session_id = %command.session_id,
                    bot_uuid = %command.bot_uuid,
                    error = %error,
                    "notify bot left failed"
                );
            }
        }
        Ok(DeleteResult { deleted: true })
    }
}

// ── projection helpers ────────────────────────────────────────────────

fn merge_participant_state_machine_snapshot(
    persisted: Vec<GroupMessage>,
    snapshot: Option<Vec<GroupMessage>>,
    limit: u64,
) -> Vec<GroupMessage> {
    bcs_domain::state_machine_history::merge_state_machine_history(
        persisted, snapshot.unwrap_or_default(), limit,
    )
}

fn project_participant(participant: &Participant) -> SessionParticipant {
    // Vey7i: pass `actor_kind` and the 4-value domain `ParticipantMode`
    // through verbatim so a Human participant inserted by the legacy
    // invitation-accept path (`actor_kind: Human, mode: Present`) is surfaced
    // as-is, not boot-truncated to `Auto`. Client-input Bot participants still
    // round-trip `Auto`/`Muted` because `build_participant` / `add_participant`
    // map the V1 input mode into the domain mode before persistence.
    SessionParticipant {
        actor_id: participant.bot_uuid.clone(),
        actor_kind: participant.actor_kind,
        name: participant.bot_name.clone(),
        role: participant.role,
        tags: participant.tags.clone(),
        mode: participant.effective_mode(),
        message_view_scope: participant.message_view_scope,
        joined_at: None,
    }
}

fn project_status(status: DomainSessionStatus) -> V1SessionStatus {
    match status {
        DomainSessionStatus::Running => V1SessionStatus::Running,
        DomainSessionStatus::Completed => V1SessionStatus::Completed,
    }
}

fn map_status_to_domain(status: V1SessionStatus) -> DomainSessionStatus {
    match status {
        V1SessionStatus::Running => DomainSessionStatus::Running,
        V1SessionStatus::Completed => DomainSessionStatus::Completed,
    }
}

fn project_summary(session: &Session) -> SessionSummary {
    SessionSummary {
        session_id: session.id.clone(),
        version: session.group_version.unwrap_or(1),
        group_id: session.group_id.clone(),
        status: project_status(session.status),
        title: session.session_title.clone(),
        participant_count: Some(session.participants.len()),
        caller_principal: session.caller_principal.clone(),
        created_by: session.created_by.clone(),
        created_at: session.created_at,
        updated_at: session.updated_at,
        collected: None,
    }
}

// ── error mappers ─────────────────────────────────────────────────────

fn map_session_error(error: SessionUseCaseError) -> ApplicationError {
    match error {
        SessionUseCaseError::NotFound(sid) => ApplicationError::not_found(
            "session_not_found",
            format!("Session '{sid}' was not found"),
        ),
        SessionUseCaseError::InvalidParams(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        SessionUseCaseError::Conflict(message) => ApplicationError::conflict("conflict", message),
        SessionUseCaseError::Internal(service_error) => map_service_error(service_error),
    }
}

fn map_launch_error(error: SessionLaunchError) -> ApplicationError {
    match error {
        SessionLaunchError::GroupNotFound(group_id) => ApplicationError::not_found(
            "group_not_found",
            format!("Group '{group_id}' was not found"),
        ),
        SessionLaunchError::SessionNotFound(session_id) => ApplicationError::not_found(
            "session_not_found",
            format!("Session '{session_id}' was not found"),
        ),
        SessionLaunchError::Forbidden(message) => ApplicationError::forbidden(message),
        SessionLaunchError::InvalidRole(message) => {
            ApplicationError::invalid("invalid_participant", message)
        }
        SessionLaunchError::InvalidRequest(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        SessionLaunchError::Conflict(message) => ApplicationError::conflict("conflict", message),
        SessionLaunchError::Runtime(error) => map_runtime_error(error),
        SessionLaunchError::Internal(error) => map_service_error(error),
    }
}

/// Map one authority read to application errors: a strict
/// deny (`Forbidden`) is 403; every other read failure
/// propagates as an internal error (spec §3.2: 存储错误
/// 返回错误 — never a deny-as-false).
fn map_authority_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::Forbidden(message) => ApplicationError::forbidden(message),
        other => ApplicationError::internal(format!(
            "bot authority lookup failed: {other}"
        )),
    }
}

fn map_service_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::SessionNotFound(sid) => ApplicationError::not_found(
            "session_not_found",
            format!("Session '{sid}' was not found"),
        ),
        ServiceError::GroupNotFound(id) => {
            ApplicationError::not_found("group_not_found", format!("Group '{id}' was not found"))
        }
        ServiceError::BotNotFound(id) | ServiceError::BotNotRegistered(id) => {
            ApplicationError::not_found("bot_not_found", format!("Bot '{id}' was not found"))
        }
        ServiceError::ParticipantNotFound(id) => ApplicationError::not_found(
            "participant_not_found",
            format!("Participant '{id}' was not found"),
        ),
        ServiceError::Unauthorized(_) => ApplicationError::Unauthenticated,
        ServiceError::Forbidden(message) => ApplicationError::forbidden(message),
        ServiceError::Conflict(message) => ApplicationError::conflict("conflict", message),
        ServiceError::SessionInvalidParams(message)
        | ServiceError::InvalidOperation { message, .. } => {
            ApplicationError::invalid("invalid_request", message)
        }
        other => ApplicationError::internal(other.to_string()),
    }
}

fn map_runtime_error(error: CollaborationRuntimeError) -> ApplicationError {
    match error {
        CollaborationRuntimeError::Unauthenticated => ApplicationError::Unauthenticated,
        CollaborationRuntimeError::Forbidden(message) => ApplicationError::forbidden(message),
        CollaborationRuntimeError::InvalidDefinition(message)
        | CollaborationRuntimeError::InvalidParticipantBinding(message)
        | CollaborationRuntimeError::InvalidRequest(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        CollaborationRuntimeError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        other => ApplicationError::internal(other.to_string()),
    }
}

/// Map the legacy `GroupUseCaseError` returned by the shared
/// Session-history application service into the stable V1 error surface.
fn map_group_use_case_error(error: GroupUseCaseError) -> ApplicationError {
    match error {
        GroupUseCaseError::Unauthorized(_) => ApplicationError::Unauthenticated,
        GroupUseCaseError::Forbidden(message) => ApplicationError::forbidden(message),
        GroupUseCaseError::InvalidHistoryLimit(limit) => ApplicationError::invalid(
            "invalid_request",
            format!("limit must be greater than zero, got {limit}"),
        ),
        GroupUseCaseError::Service(service_error) => map_service_error(service_error),
        other => ApplicationError::internal(other.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_use_case_errors_map_to_stable_v1_codes() {
        assert_eq!(
            map_session_error(SessionUseCaseError::NotFound("s1".into())).code(),
            "session_not_found"
        );
        assert_eq!(
            map_session_error(SessionUseCaseError::InvalidParams("bad".into())).code(),
            "invalid_request"
        );
        assert_eq!(
            map_session_error(SessionUseCaseError::Conflict("running".into())).code(),
            "conflict"
        );
        assert_eq!(
            map_session_error(SessionUseCaseError::Internal(
                ServiceError::SessionNotFound("s2".into())
            ))
            .code(),
            "session_not_found"
        );
    }

    #[test]
    fn service_errors_map_to_stable_v1_codes() {
        assert_eq!(
            map_service_error(ServiceError::GroupNotFound("g1".into())).code(),
            "group_not_found"
        );
        assert_eq!(
            map_service_error(ServiceError::BotNotFound("b1".into())).code(),
            "bot_not_found"
        );
        assert_eq!(
            map_service_error(ServiceError::ParticipantNotFound("b2".into())).code(),
            "participant_not_found"
        );
        assert_eq!(
            map_service_error(ServiceError::Conflict("dup".into())).code(),
            "conflict"
        );
        assert_eq!(
            map_service_error(ServiceError::SessionInvalidParams("x".into())).code(),
            "invalid_request"
        );
    }

    #[test]
    fn session_history_errors_preserve_authentication_and_authorization_categories() {
        assert!(matches!(
            map_group_use_case_error(GroupUseCaseError::Unauthorized("missing".into())),
            ApplicationError::Unauthenticated
        ));
        assert!(matches!(
            map_group_use_case_error(GroupUseCaseError::Forbidden("denied".into())),
            ApplicationError::Forbidden(_)
        ));
        assert_eq!(
            map_group_use_case_error(GroupUseCaseError::InvalidHistoryLimit(0)).code(),
            "invalid_request"
        );
        assert_eq!(
            map_group_use_case_error(GroupUseCaseError::InvalidGroupId("bad".into())).code(),
            "internal_error"
        );
    }

    #[test]
    fn runtime_errors_map_to_stable_v1_codes() {
        assert_eq!(
            map_runtime_error(CollaborationRuntimeError::Unauthenticated).code(),
            "unauthenticated"
        );
        assert_eq!(
            map_runtime_error(CollaborationRuntimeError::Forbidden("denied".into())).code(),
            "forbidden"
        );
        for error in [
            CollaborationRuntimeError::InvalidDefinition("invalid definition".into()),
            CollaborationRuntimeError::InvalidParticipantBinding("invalid binding".into()),
            CollaborationRuntimeError::InvalidRequest("invalid request".into()),
        ] {
            assert_eq!(map_runtime_error(error).code(), "invalid_request");
        }
        assert_eq!(
            map_runtime_error(CollaborationRuntimeError::Conflict("running".into())).code(),
            "conflict"
        );
        assert_eq!(
            map_runtime_error(CollaborationRuntimeError::JudgeUnavailable(
                "offline".into()
            ))
            .code(),
            "internal_error"
        );
    }

    #[test]
    fn project_participant_preserves_human_present_mode() {
        // Vey7i: a legacy invitation-accept inserts a Human participant with
        // `actor_kind: Human, mode: Present`. The V1 projection must NOT map
        // that into the Bot-only `Auto`/`Muted` vocabulary; it must pass
        // `actor_kind` and `effective_mode()` through verbatim so `GET session`
        // surfaces the real membership.
        let human = Participant {
            bot_uuid: "human_staff-1".into(),
            bot_name: Some("Alice".into()),
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: ActorKind::Human,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
            mode: Some(ParticipantMode::Present),
        };
        let projected = project_participant(&human);
        assert_eq!(projected.actor_id, "human_staff-1");
        assert_eq!(projected.actor_kind, ActorKind::Human);
        assert_eq!(projected.role, ParticipantRole::Consultant);
        assert_eq!(projected.mode, ParticipantMode::Present);
        assert_eq!(projected.name.as_deref(), Some("Alice"));

        // `mode: None` falls back to the actor-kind default; for a Human that
        // is `Absent`, and the projection must surface that verbatim too.
        let absent = Participant {
            bot_uuid: "human_staff-2".into(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
            mode: None,
        };
        let projected_absent = project_participant(&absent);
        assert_eq!(projected_absent.actor_kind, ActorKind::Human);
        assert_eq!(projected_absent.mode, ParticipantMode::Absent);

        // No regression for Bot participants: Auto/Muted pass through as-is.
        let bot = Participant {
            bot_uuid: "bot-1".into(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
            mode: Some(ParticipantMode::Muted),
        };
        let projected_bot = project_participant(&bot);
        assert_eq!(projected_bot.actor_kind, ActorKind::Bot);
        assert_eq!(projected_bot.mode, ParticipantMode::Muted);
    }
}
