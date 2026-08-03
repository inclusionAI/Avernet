//! Versioned Session application facade for the BCN V1 API.
//!
//! Implements both [`SessionService`] and [`SessionMessageService`]. The
//! facade owns authenticated-Caller resource authorization and V1 projections
//! while delegating the legacy session lifecycle to
//! [`SessionManagementService`]. No HTTP type crosses this boundary.
//!
//! All current V1 operations are Human-facing. Detail reads, actor-relative
//! lists, message history, and mutations intentionally use separate
//! authorization rules defined by the V1 contract.

mod connection;

pub use connection::GroupSessionConnectionServiceImpl;

use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{PersistedMessage, SenderType};
use bcs_message::MessageService;
use bcs_service_api::application::v1::{
    message::{
        ListSessionMessages, MessageSenderKind, SessionMessage, SessionMessageKind,
        SessionMessagePage, SessionMessageService,
    },
    session::{
        AddSessionParticipant, BotParticipantMode, CompleteSession, CreateSession,
        CreateSessionOutcome, DeleteSession, DeleteSessionParticipant, GetSession, ListSessions,
        SessionCompletionResult, SessionDetail, SessionInput, SessionParticipant,
        SessionParticipantInput, SessionService, SessionStatus as V1SessionStatus,
        SessionSummary, UpdateSession, UpdateSessionParticipant,
    },
    ApplicationError, AuthenticatedCaller, DeleteResult, Page, Principal,
    require_authenticated_user, require_human,
};
use bcs_service_api::application::session::{
    CreateOrReactivateCommand, SessionManagementService, SessionUseCaseError,
};
use bcs_service_api::port::repo::{MessageRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    backfill_participant_names, ActorKind, ActorStatus, BotRegistryCoreService,
    FriendCoreService, Group as DomainGroup, GroupCoreService, GroupStrategy, GroupUseCaseError,
    Participant, ParticipantMode, ParticipantRole, RegisteredBot, RelationCoreService,
    ServiceError, Session, SessionKind, SessionStatus as DomainSessionStatus,
};

#[derive(Debug, Clone)]
pub struct SessionServiceConfig {
    /// Relation environment tag retained for parity with the sibling Group V1
    /// facade; used by the collaboration-eligibility creator-edge check
    /// (`ensure_collaboration_eligible`).
    pub relation_env: String,
}

/// OpenAPI v1 Session facade.
///
/// Holds the legacy [`SessionManagementService`] for lifecycle delegation plus
/// its own `Arc<dyn SessionRepoPort>` / `Arc<dyn MessageRepoPort>` for the V1
/// `count_by_group` (total) and `list_session_messages_by_seq` (chronological
/// history) paths that are not exposed on the legacy application trait.
pub struct SessionServiceImpl {
    sessions: Arc<dyn SessionManagementService>,
    groups: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    friends: Arc<dyn FriendCoreService>,
    relation: Arc<dyn RelationCoreService>,
    session_repo: Arc<dyn SessionRepoPort>,
    message_repo: Arc<dyn MessageRepoPort>,
    config: SessionServiceConfig,
}

impl SessionServiceImpl {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        sessions: Arc<dyn SessionManagementService>,
        groups: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friends: Arc<dyn FriendCoreService>,
        relation: Arc<dyn RelationCoreService>,
        session_repo: Arc<dyn SessionRepoPort>,
        message_repo: Arc<dyn MessageRepoPort>,
        config: SessionServiceConfig,
    ) -> Self {
        Self {
            sessions,
            groups,
            registry,
            friends,
            relation,
            session_repo,
            message_repo,
            config,
        }
    }

    // ── authorization helpers ──────────────────────────────────────────

    /// Manager of the parent group (driver / originator / ManagerWorker
    /// manager). Mirrors `bcs-app-group`'s `can_manage_group`.
    fn can_manage_group(principal: &Principal, group: &DomainGroup) -> bool {
        let actor_id = principal.actor_id();
        actor_id == group.driver_bot
            || actor_id == group.originator()
            || (group.group_strategy == GroupStrategy::ManagerWorker
                && group
                    .participants
                    .iter()
                    .any(|p| p.bot_uuid == actor_id && p.role == ParticipantRole::Manager))
    }

    /// Manage a specific session: group manager OR the session's creator
    /// (`session.created_by`). The creator qualifier covers the V1 design's
    /// "creator" authorization for update / delete / complete.
    fn can_manage_session(principal: &Principal, session: &Session, group: &DomainGroup) -> bool {
        Self::can_manage_group(principal, group)
            || session
                .created_by
                .as_deref()
                .is_some_and(|creator| creator == principal.actor_id())
    }

    async fn load_group(&self, group_id: &str) -> Result<DomainGroup, ApplicationError> {
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

    async fn load_manageable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self.load_group(group_id).await?;
        if !Self::can_manage_group(principal, &group) {
            return Err(ApplicationError::forbidden(
                "Only the Group originator, driver, or manager may manage Sessions",
            ));
        }
        Ok(group)
    }

    /// Load a session and its parent group, authorizing manage access.
    async fn load_session_for_manage(
        &self,
        principal: &Principal,
        session_id: &str,
    ) -> Result<(Session, DomainGroup), ApplicationError> {
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
        let group = self.load_group(&session.group_id).await?;
        if !Self::can_manage_session(principal, &session, &group) {
            return Err(ApplicationError::forbidden(
                "Principal may not manage this Session",
            ));
        }
        Ok((session, group))
    }

    async fn load_session(&self, session_id: &str) -> Result<Session, ApplicationError> {
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

    async fn load_bot(
        &self,
        bot_uuid: &str,
    ) -> Result<RegisteredBot, ApplicationError> {
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

    async fn resolve_view_actor(
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
        let bot = self.load_bot(requested).await.map_err(|error| match error {
            ApplicationError::NotFound { .. } => {
                ApplicationError::forbidden("The explicit View Actor is not authorized")
            }
            other => other,
        })?;
        if bot.actor_kind == ActorKind::Bot
            && bot.created_by.as_deref() == Some(user.id.as_str())
        {
            Ok(requested.to_string())
        } else {
            Err(ApplicationError::forbidden(
                "The explicit View Actor is not authorized",
            ))
        }
    }

    async fn can_read_session_detail(
        &self,
        caller: &AuthenticatedCaller,
        session: &Session,
    ) -> Result<bool, ApplicationError> {
        let user = require_authenticated_user(caller)?;
        let human_actor_id = format!("human_{}", user.id);
        if session
            .participants
            .iter()
            .any(|participant| {
                participant.actor_kind == ActorKind::Human
                    && participant.bot_uuid == human_actor_id
            })
        {
            return Ok(true);
        }
        let owned_bot_ids = self
            .registry
            .try_list_bots_by_creator(&user.id)
            .await
            .map_err(map_service_error)?
            .into_iter()
            .filter(|bot| bot.actor_kind == ActorKind::Bot)
            .map(|bot| bot.bot_uuid)
            .collect::<HashSet<_>>();
        Ok(session
            .participants
            .iter()
            .any(|participant| {
                participant.actor_kind == ActorKind::Bot
                    && owned_bot_ids.contains(&participant.bot_uuid)
            }))
    }

    async fn load_session_for_detail(
        &self,
        caller: &AuthenticatedCaller,
        session_id: &str,
    ) -> Result<Session, ApplicationError> {
        let session = self.load_session(session_id).await?;
        self.load_group(&session.group_id).await?;
        if !self.can_read_session_detail(caller, &session).await? {
            return Err(ApplicationError::forbidden(
                "Neither the Human Actor nor an owned Bot is a Session Participant",
            ));
        }
        Ok(session)
    }

    /// VSN7B: Mirror `bcs-app-group`'s `ensure_collaboration_eligible`. A
    /// caller may add a Bot to a session only when that Bot is
    /// collaboration-eligible for the caller:
    /// - the target must be a Bot Actor that is not Hidden; AND
    /// - the caller IS the target bot; OR the target is `public`; OR (for a
    ///   Human caller) the caller owns the target via `created_by` or a
    ///   creator relation edge; OR the caller and target are friends.
    ///
    /// Called for the session driver, every participant in `create`, and in
    /// `add_participant` so a manager cannot pull a hidden / protected Bot
    /// into a session without the required relation.
    async fn ensure_collaboration_eligible(
        &self,
        principal: &Principal,
        bot_uuid: &str,
        field_name: &str,
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
        let principal_actor_id = principal.actor_id();
        if principal_actor_id == bot_uuid || bot.capabilities.visibility == "public" {
            return Ok(());
        }

        if let Principal::Human(human) = principal {
            if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
                return Ok(());
            }
            let creator_edge = self
                .relation
                .get_edge(&principal_actor_id, bot_uuid, &self.config.relation_env)
                .await
                .map_err(map_service_error)?;
            if creator_edge.is_some_and(|edge| edge.is_creator) {
                return Ok(());
            }
        }

        if self
            .friends
            .try_are_friends(&principal_actor_id, bot_uuid)
            .await
            .map_err(map_service_error)?
        {
            return Ok(());
        }

        Err(ApplicationError::forbidden(format!(
            "Bot '{bot_uuid}' is not collaboration-eligible for this Principal"
        )))
    }

    // ── projections ────────────────────────────────────────────────────

    async fn project_detail(&self, session: &Session) -> Result<SessionDetail, ApplicationError> {
        let mut participants = session.participants.clone();
        backfill_participant_names(self.registry.as_ref(), &mut participants).await;
        let participants = participants
            .iter()
            .map(project_participant)
            .collect::<Vec<_>>();
        Ok(SessionDetail {
            session_id: session.id.clone(),
            version: session.group_version.unwrap_or(1),
            group_id: session.group_id.clone(),
            status: project_status(session.status),
            title: session.session_title.clone(),
            input: project_input(&session.input),
            participants,
            created_at: session.created_at,
            updated_at: session.updated_at,
        })
    }

    /// Backfill display names for all participants, then project the one
    /// identified by `bot_uuid`. Backfilling the whole slice first avoids
    /// borrowing the slice immutably (for the lookup) while it is still
    /// mutably borrowed by `backfill_participant_names`.
    async fn backfill_and_project_participant(
        &self,
        participants: &mut [Participant],
        bot_uuid: &str,
    ) -> Result<SessionParticipant, ApplicationError> {
        backfill_participant_names(self.registry.as_ref(), participants).await;
        participants
            .iter()
            .find(|p| p.bot_uuid == bot_uuid)
            .map(project_participant)
            .ok_or_else(|| {
                ApplicationError::internal("participant not present in returned Session")
            })
    }
}

#[async_trait]
impl SessionService for SessionServiceImpl {
    async fn create(
        &self,
        command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let group = self
            .load_manageable_group(&principal, &command.group_id)
            .await?;

        // The contract marks `driver_bot_uuid` as required and 404s on an
        // unknown referenced Bot. The session's routing driver is inherited
        // from the parent group (legacy semantics); `driver_bot_uuid` is
        // validated and injected into the participant roster as the Driver so
        // the session carries a lead responder.
        // VSN7B: the driver and every participant must be collaboration-eligible
        // for the caller (visible + friend/creator relation) before the session
        // is materialized, mirroring the sibling Group V1 facade.
        self.ensure_collaboration_eligible(
            &principal,
            &command.driver_bot_uuid,
            "driver_bot_uuid",
        )
        .await?;

        if command.participants.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "at least one session participant is required",
            ));
        }
        // VeHS7: reject duplicate participants before any persistence so the
        // memory store doesn't silently accept an inflated roster and the MySQL
        // store doesn't surface the `uk_session_participants_env_session_bot`
        // unique constraint as a generic session-ID collision on retry. The
        // contract mirrors this with `uniqueItems: true` on the array.
        let mut seen: HashSet<&str> = HashSet::new();
        for input in &command.participants {
            if !seen.insert(input.bot_uuid.as_str()) {
                return Err(ApplicationError::invalid(
                    "invalid_request",
                    format!("duplicate participant: {}", input.bot_uuid),
                ));
            }
        }
        for input in &command.participants {
            self.ensure_collaboration_eligible(
                &principal,
                &input.bot_uuid,
                "participants",
            )
            .await?;
        }

        // Wrap the V1 SessionInput into the legacy arbitrary-JSON `input`. When
        // no input is supplied, fall back to the parent group's `context` as
        // the session task (design note).
        let input = match command.input.as_ref() {
            Some(session_input) => Some(serde_json::json!({ "query": session_input.query })),
            None => group
                .context
                .as_ref()
                .map(|ctx| serde_json::json!({ "query": ctx })),
        };

        let mut participants: Vec<Participant> = command
            .participants
            .iter()
            .map(|input| {
                // VfhG3: derive role from parent group.participants if the bot is
                // already there; otherwise strategy default (ManagerWorker→Worker,
                // else Consultant). Mirrors legacy bcs-http create_session which
                // clones group.participants preserving their roles rather than
                // hardcoding Consultant and losing the Worker/Manager role.
                let group_role = group
                    .participants
                    .iter()
                    .find(|p| p.bot_uuid == input.bot_uuid)
                    .map(|p| p.role);
                let role = group_role.unwrap_or_else(|| match group.group_strategy {
                    GroupStrategy::ManagerWorker => ParticipantRole::Worker,
                    _ => ParticipantRole::Consultant,
                });
                build_participant(input, role)
            })
            .collect();

        // Ensure the driver is present in the roster with the Driver role.
        match participants
            .iter()
            .position(|p| p.bot_uuid == command.driver_bot_uuid)
        {
            Some(index) => participants[index].role = ParticipantRole::Driver,
            None => participants.push(Participant::bot(
                command.driver_bot_uuid.clone(),
                ParticipantRole::Driver,
            )),
        }

        let caller_actor_id = principal.actor_id();
        let params = NewSessionParams {
            session_kind: SessionKind::Chat,
            participants,
            group_version: Some(group.version),
            caller_id: Some(caller_actor_id.clone()),
            caller_principal: Some(caller_actor_id.clone()),
            input,
            created_by: Some(caller_actor_id),
            session_title: command.title.clone(),
            id: None,
            meta: None,
        };
        let outcome = self
            .sessions
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id: command.group_id.clone(),
                session_id: None,
                params,
            })
            .await
            .map_err(map_session_error)?;
        let detail = self.project_detail(&outcome.session).await?;
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
        let items = sessions.iter().map(project_summary).collect::<Vec<_>>();
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
        self.project_detail(&session).await
    }

    async fn update(&self, command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        let principal = require_human(&command.caller)?;
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
        self.project_detail(&session).await
    }

    async fn delete(&self, command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        let principal = require_human(&command.caller)?;
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
        if !Self::can_manage_session(&principal, &session, &group) {
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
                        ))
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

    async fn add_participant(
        &self,
        command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let (session, group) = self
            .load_session_for_manage(&principal, &command.session_id)
            .await?;
        // VSN7B: the added Bot must be collaboration-eligible for the caller
        // (visible + friend/creator relation), not merely registered.
        self.ensure_collaboration_eligible(&principal, &command.bot_uuid, "bot_uuid")
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
        let mode = command
            .mode
            .unwrap_or(BotParticipantMode::Auto);
        // VfhG3: derive role from parent group.participants if the bot is already
        // there; otherwise strategy default (ManagerWorker→Worker, else
        // Consultant). Mirrors legacy bcs-http add_session_participant which picks
        // role from body.role or defaults by strategy (ManagerWorker→Worker,
        // Chat→Consultant) rather than hardcoding Consultant.
        let group_role = group
            .participants
            .iter()
            .find(|p| p.bot_uuid == command.bot_uuid)
            .map(|p| p.role);
        let role = group_role.unwrap_or_else(|| match group.group_strategy {
            GroupStrategy::ManagerWorker => ParticipantRole::Worker,
            _ => ParticipantRole::Consultant,
        });
        let participant = Participant {
            bot_uuid: command.bot_uuid.clone(),
            bot_name: None,
            kind: None,
            role,
            actor_kind: ActorKind::Bot,
            mode: Some(map_v1_mode_to_domain(mode)),
        };
        let mut updated = self
            .sessions
            .add_participant(&command.session_id, participant)
            .await
            .map_err(map_session_error)?;
        self.backfill_and_project_participant(&mut updated.participants, &command.bot_uuid)
            .await
    }

    async fn update_participant(
        &self,
        command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        let principal = require_human(&command.caller)?;
        self.load_session_for_manage(&principal, &command.session_id)
            .await?;
        let domain_mode = map_v1_mode_to_domain(command.mode);
        let mut updated = self
            .sessions
            .update_participant_mode(&command.session_id, &command.bot_uuid, domain_mode)
            .await
            .map_err(map_session_error)?;
        match self
            .backfill_and_project_participant(&mut updated.participants, &command.bot_uuid)
            .await
        {
            Ok(participant) => Ok(participant),
            Err(ApplicationError::Internal(_)) => Err(ApplicationError::not_found(
                "participant_not_found",
                format!(
                    "Participant '{}' not found in Session '{}'",
                    command.bot_uuid, command.session_id
                ),
            )),
            Err(other) => Err(other),
        }
    }

    async fn delete_participant(
        &self,
        command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let (session, _) = self
            .load_session_for_manage(&principal, &command.session_id)
            .await?;
        // Idempotent: if the target is not a current participant, return
        // `deleted: false` without invoking the legacy removal (which would
        // surface a `SessionInvalidParams` "not in session" error otherwise).
        let present = session
            .participants
            .iter()
            .any(|p| p.bot_uuid == command.bot_uuid);
        if !present {
            return Ok(DeleteResult { deleted: false });
        }
        self.sessions
            .remove_participant(&command.session_id, &command.bot_uuid)
            .await
            .map_err(map_session_error)?;
        Ok(DeleteResult { deleted: true })
    }
}

#[async_trait]
impl SessionMessageService for SessionServiceImpl {
    async fn list(
        &self,
        query: ListSessionMessages,
    ) -> Result<SessionMessagePage, ApplicationError> {
        if query.limit == 0 || query.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        let view_actor_id = self
            .resolve_view_actor(&query.caller, query.view_bot_id.as_deref())
            .await?;
        let session = self.load_session(&query.session_id).await?;
        if !session
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == view_actor_id)
        {
            return Err(ApplicationError::forbidden(
                "The selected View Actor is not a Session Participant",
            ));
        }
        // VSN7A/VUlai/VHxMU — reuse the legacy `bcs-message` visibility helper
        // (single source of truth) so the V1 session list applies the EXACT
        // same scoping the group history path does: the full 3-state
        // `MessageOwnerFilter` (incl. ManagerWorker public-only `IsNull`) and
        // the spec §5.2 new-participant `visible_from_seq` cutoff. The V1 facade
        // no longer reimplements these predicates.
        let group = self.load_group(&session.group_id).await?;
        let (owner_filter, visible_from_seq) =
            MessageService::compute_session_history_query(
                &group,
                &session,
                Some(&view_actor_id),
                NEW_PARTICIPANT_VISIBLE_LIMIT as u64,
            )
            .map_err(map_group_use_case_error)?;
        // Cursor-based direct read (legacy `created_at DESC, session_seq DESC`);
        // `has_more` + `next_cursor` replace the separate COUNT(*) estimate.
        // VYQHI: the cursor is the opaque composite `"created_at:session_seq"`
        // string; decode it here into the `(created_at, session_seq)` tuple the
        // repo expects, and re-encode the repo's tuple `next_cursor` for the
        // V1 page response.
        let before = decode_cursor(query.before).map_err(|e| {
            ApplicationError::invalid("invalid_request", format!("invalid before cursor: {e}"))
        })?;
        let page = self
            .message_repo
            .list_session_history(
                &query.session_id,
                owner_filter,
                visible_from_seq,
                before,
                query.limit as u32,
            )
            .await
            .map_err(map_service_error)?;
        let messages = page.messages.iter().map(project_message).collect::<Vec<_>>();
        Ok(SessionMessagePage {
            messages,
            next_cursor: encode_cursor(page.next_cursor),
            has_more: page.has_more,
        })
    }
}

// ── projection helpers ────────────────────────────────────────────────

/// Visibility window applied to message history for a viewer that joined
/// late (spec §5.2: a participant sees at most the N messages preceding their
/// join point). Passed into the shared legacy `bcs-message`
/// `MessageService::compute_session_history_query` helper so the V1 session
/// list reuses the same scoping the group history path does; the V1 facade no
/// longer reimplements the predicate math (VUlai). Mirrors the bootstrap
/// default `new_participant_visible_limit`
/// (`config.rs::default_new_participant_visible_limit`); kept as a const here
/// because the V1 session facade does not (yet) own its own history config.
const NEW_PARTICIPANT_VISIBLE_LIMIT: i64 = 100;

fn build_participant(input: &SessionParticipantInput, role: ParticipantRole) -> Participant {
    let mode = map_v1_mode_to_domain(input.mode.unwrap_or(BotParticipantMode::Auto));
    Participant {
        bot_uuid: input.bot_uuid.clone(),
        bot_name: None,
        kind: None,
        role,
        actor_kind: ActorKind::Bot,
        mode: Some(mode),
    }
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
        mode: participant.effective_mode(),
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

/// Extract the V1 `SessionInput` from the legacy arbitrary-JSON session
/// `input`. Only the `{"query": "..."}` shape produced by `create` is
/// recognized; any other shape yields `None`.
fn project_input(input: &Option<serde_json::Value>) -> Option<SessionInput> {
    let value = input.as_ref()?;
    let query = value
        .get("query")
        .and_then(serde_json::Value::as_str)
        .map(str::to_string);
    query.map(|query| SessionInput { query: Some(query) })
}

fn project_summary(session: &Session) -> SessionSummary {
    SessionSummary {
        session_id: session.id.clone(),
        version: session.group_version.unwrap_or(1),
        group_id: session.group_id.clone(),
        status: project_status(session.status),
        title: session.session_title.clone(),
        participant_count: Some(session.participants.len()),
        created_at: session.created_at,
        updated_at: session.updated_at,
    }
}

fn project_message(message: &PersistedMessage) -> SessionMessage {
    SessionMessage {
        id: message.message_id.clone(),
        session_seq: message.session_seq,
        sender_id: message.sender_id.clone(),
        sender_type: project_sender_kind(message.sender_type),
        kind: project_message_kind(&message.message_type, message.sender_type),
        content: project_content(&message.content),
        created_at: message.created_at,
    }
}

fn project_sender_kind(sender: SenderType) -> MessageSenderKind {
    match sender {
        SenderType::Bot => MessageSenderKind::Bot,
        SenderType::Human => MessageSenderKind::Human,
        SenderType::System => MessageSenderKind::System,
    }
}

/// A message is `System` when sent by a System sender or persisted with a
/// `system` message type; everything else is `Text`.
fn project_message_kind(message_type: &str, sender: SenderType) -> SessionMessageKind {
    if sender == SenderType::System || message_type == "system" {
        SessionMessageKind::System
    } else {
        SessionMessageKind::Text
    }
}

fn project_content(content: &serde_json::Value) -> String {
    match content {
        serde_json::Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

// ── cursor codec (VYQHI composite cursor) ───────────────────────────

/// Encode the repo's composite `(created_at, session_seq)` cursor into the
/// opaque V1 wire string `"created_at:session_seq"` (e.g. `"1234567890:42"`).
/// `None` stays `None` (no next page).
fn encode_cursor(cursor: Option<(u64, i64)>) -> Option<String> {
    cursor.map(|(created_at, session_seq)| format!("{created_at}:{session_seq}"))
}

/// Decode the opaque V1 wire cursor string `"created_at:session_seq"` back
/// into the `(created_at, session_seq)` tuple the repo expects. `None`
/// passes through. Returns an error message string on a malformed token so
/// the caller can surface an `invalid_request` 400.
fn decode_cursor(before: Option<String>) -> Result<Option<(u64, i64)>, String> {
    match before {
        None => Ok(None),
        Some(token) => {
            let (ts, seq) = token
                .split_once(':')
                .ok_or_else(|| format!("missing ':' separator in {token:?}"))?;
            let created_at: u64 = ts
                .parse()
                .map_err(|_| format!("non-numeric created_at in {token:?}"))?;
            let session_seq: i64 = seq
                .parse()
                .map_err(|_| format!("non-numeric session_seq in {token:?}"))?;
            Ok(Some((created_at, session_seq)))
        }
    }
}

fn map_v1_mode_to_domain(mode: BotParticipantMode) -> ParticipantMode {
    match mode {
        BotParticipantMode::Auto => ParticipantMode::Auto,
        BotParticipantMode::Muted => ParticipantMode::Muted,
    }
}

// ── error mappers ─────────────────────────────────────────────────────

fn map_session_error(error: SessionUseCaseError) -> ApplicationError {
    match error {
        SessionUseCaseError::NotFound(sid) => {
            ApplicationError::not_found("session_not_found", format!("Session '{sid}' was not found"))
        }
        SessionUseCaseError::InvalidParams(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        SessionUseCaseError::CallbackPending(message) => {
            ApplicationError::conflict("conflict", message)
        }
        SessionUseCaseError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        SessionUseCaseError::Internal(service_error) => map_service_error(service_error),
    }
}

fn map_service_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::SessionNotFound(sid) => {
            ApplicationError::not_found("session_not_found", format!("Session '{sid}' was not found"))
        }
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

/// Map the legacy `GroupUseCaseError` returned by the shared
/// `MessageService::compute_session_history_query` helper into the stable V1
/// `ApplicationError` surface. The only realistic branch from the helper is
/// `Service(InvalidOperation)` (a non-participant view_bot_id); everything else
/// falls back to a generic `invalid_request`.
fn map_group_use_case_error(error: GroupUseCaseError) -> ApplicationError {
    match error {
        GroupUseCaseError::Service(service_error) => map_service_error(service_error),
        other => ApplicationError::invalid("invalid_request", other.to_string()),
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
            map_session_error(SessionUseCaseError::CallbackPending("pending".into())).code(),
            "conflict"
        );
        assert_eq!(
            map_session_error(SessionUseCaseError::Conflict("running".into())).code(),
            "conflict"
        );
        assert_eq!(
            map_session_error(SessionUseCaseError::Internal(ServiceError::SessionNotFound(
                "s2".into()
            )))
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
    fn project_input_extracts_query_string() {
        assert_eq!(
            project_input(&Some(serde_json::json!({ "query": "hello" }))),
            Some(SessionInput {
                query: Some("hello".into())
            })
        );
        assert_eq!(project_input(&Some(serde_json::json!({ "query": 42 }))), None);
        assert_eq!(project_input(&None), None);
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
            mode: Some(ParticipantMode::Muted),
        };
        let projected_bot = project_participant(&bot);
        assert_eq!(projected_bot.actor_kind, ActorKind::Bot);
        assert_eq!(projected_bot.mode, ParticipantMode::Muted);
    }
}
