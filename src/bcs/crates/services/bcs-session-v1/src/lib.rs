//! Versioned Session application facade for the BCN V1 API.
//!
//! Implements both [`SessionService`] and [`SessionMessageService`]. The
//! facade owns Principal-based resource authorization and V1 projections
//! while delegating the legacy session lifecycle to
//! [`SessionManagementService`]. No HTTP type crosses this boundary.
//!
//! Authorization model (design §8.7):
//! - Manage operations (create / update / complete / participant mutations)
//!   require the caller to be a group manager (driver / originator /
//!   ManagerWorker manager) or the session creator.
//! - Read operations (get / list / list messages) additionally allow session
//!   participants and group members.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{PersistedMessage, SenderType};
use bcs_service_api::application::v1::{
    message::{ListSessionMessages, MessageSenderKind, SessionMessage, SessionMessageKind, SessionMessageService},
    session::{
        AddSessionParticipant, BotParticipantMode, CompleteSession, CreateSession,
        CreateSessionOutcome, DeleteSession, DeleteSessionParticipant, GetSession, ListSessions,
        SessionCompletionResult, SessionDetail, SessionInput, SessionParticipant,
        SessionParticipantInput, SessionService, SessionStatus as V1SessionStatus,
        SessionSummary, UpdateSession, UpdateSessionParticipant,
    },
    ApplicationError, DeleteResult, Page, Principal,
};
use bcs_service_api::application::session::{
    CreateOrReactivateCommand, SessionManagementService, SessionUseCaseError,
};
use bcs_service_api::port::repo::{MessageRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    backfill_participant_names, ActorKind, ActorStatus, BotRegistryCoreService,
    FriendCoreService, Group as DomainGroup, GroupCoreService, GroupStrategy, Participant,
    ParticipantMode, ParticipantRole, RegisteredBot, RelationCoreService, ServiceError, Session,
    SessionKind, SessionStatus as DomainSessionStatus,
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
    /// manager). Mirrors `bcs-group-v1`'s `can_manage_group`.
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

    /// Read a group's sessions: group participant or group manager.
    fn can_read_group(principal: &Principal, group: &DomainGroup) -> bool {
        let actor_id = principal.actor_id();
        Self::can_manage_group(principal, group)
            || group
                .participants
                .iter()
                .any(|p| p.bot_uuid == actor_id)
    }

    /// Read a specific session: session participant, group manager, or the
    /// session's creator.
    fn can_read_session(principal: &Principal, session: &Session, group: &DomainGroup) -> bool {
        let actor_id = principal.actor_id();
        session.participants.iter().any(|p| p.bot_uuid == actor_id)
            || Self::can_manage_session(principal, session, group)
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

    async fn load_readable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self.load_group(group_id).await?;
        if !Self::can_read_group(principal, &group) {
            return Err(ApplicationError::forbidden(
                "Principal has no readable relation to this Group",
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

    /// Load a session and its parent group, authorizing read access.
    async fn load_session_for_read(
        &self,
        principal: &Principal,
        session_id: &str,
    ) -> Result<Session, ApplicationError> {
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
        if !Self::can_read_session(principal, &session, &group) {
            return Err(ApplicationError::forbidden(
                "Principal has no readable relation to this Session",
            ));
        }
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

    /// VSN7B: Mirror `bcs-group-v1`'s `ensure_collaboration_eligible`. A
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

    /// Load a session and its parent group, authorizing read access only.
    /// Used by participant mutation endpoints that permit self-service (the
    /// target Bot updating its own participant mode / leaving) in addition to
    /// the manage path (VSN7L): the caller first proves a readable relation,
    /// then checks `is_self || can_manage_session` itself.
    async fn load_session_and_group_for_read(
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
        if !Self::can_read_session(principal, &session, &group) {
            return Err(ApplicationError::forbidden(
                "Principal has no readable relation to this Session",
            ));
        }
        Ok((session, group))
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
        let group = self
            .load_manageable_group(&command.principal, &command.group_id)
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
            &command.principal,
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
        for input in &command.participants {
            self.ensure_collaboration_eligible(
                &command.principal,
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

        let mut participants = command
            .participants
            .iter()
            .map(|input| build_participant(input, ParticipantRole::Consultant))
            .collect::<Vec<_>>();

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

        let caller_actor_id = command.principal.actor_id();
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
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        self.load_readable_group(&command.principal, &command.group_id)
            .await?;
        let status = command.status.map(map_status_to_domain);
        let mut sessions = self
            .sessions
            .list_by_group(
                &command.group_id,
                status,
                command.offset,
                command.limit,
                None,
                None,
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
            .count_by_group(&command.group_id, status, None, None)
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
        let session = self.load_session_for_read(&query.principal, &query.session_id).await?;
        self.project_detail(&session).await
    }

    async fn update(&self, command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        // Only `title` is mutable in phase one; a request carrying no field is
        // rejected (mirrors the sibling Group V1 facade).
        if command.title.is_none() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "at least one mutable field is required",
            ));
        }
        self.load_session_for_manage(&command.principal, &command.session_id)
            .await?;
        let session = self
            .sessions
            .update_title(&command.session_id, command.title)
            .await
            .map_err(map_session_error)?;
        self.project_detail(&session).await
    }

    async fn delete(&self, command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
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
        if !Self::can_manage_session(&command.principal, &session, &group) {
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
        let (session, _) = self
            .load_session_for_manage(&command.principal, &command.session_id)
            .await?;
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
        let (session, _) = self
            .load_session_for_manage(&command.principal, &command.session_id)
            .await?;
        // VSN7B: the added Bot must be collaboration-eligible for the caller
        // (visible + friend/creator relation), not merely registered.
        self.ensure_collaboration_eligible(&command.principal, &command.bot_uuid, "bot_uuid")
            .await?;
        if session
            .participants
            .iter()
            .any(|p| p.bot_uuid == command.bot_uuid)
        {
            return Err(ApplicationError::conflict(
                "conflict",
                format!(
                    "Bot '{}' is already a participant of Session '{}'",
                    command.bot_uuid, command.session_id
                ),
            ));
        }
        let mode = command
            .mode
            .unwrap_or(BotParticipantMode::Auto);
        let participant = Participant {
            bot_uuid: command.bot_uuid.clone(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
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
        // VSN7L: the target Actor may update its own participant mode
        // (self-service) in addition to the driver/originator/manager path,
        // mirroring the sibling Group V1 facade's participant self-service.
        let (session, group) = self
            .load_session_and_group_for_read(&command.principal, &command.session_id)
            .await?;
        let is_self = command.principal.actor_id() == command.bot_uuid;
        if !is_self && !Self::can_manage_session(&command.principal, &session, &group) {
            return Err(ApplicationError::forbidden(
                "Principal may not manage this Session's participants",
            ));
        }
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
        // VSN7L: the target Actor may leave the session (self-service delete)
        // in addition to the driver/originator/manager path, mirroring the
        // sibling Group V1 facade's participant self-leave.
        let (session, group) = self
            .load_session_and_group_for_read(&command.principal, &command.session_id)
            .await?;
        let is_self = command.principal.actor_id() == command.bot_uuid;
        if !is_self && !Self::can_manage_session(&command.principal, &session, &group) {
            return Err(ApplicationError::forbidden(
                "Principal may not manage this Session's participants",
            ));
        }
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
    ) -> Result<Page<SessionMessage>, ApplicationError> {
        if query.limit == 0 || query.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        self.load_session_for_read(&query.principal, &query.session_id)
            .await?;
        let (messages, total) = self
            .message_repo
            .list_session_messages_by_seq(&query.session_id, query.offset, query.limit)
            .await
            .map_err(map_service_error)?;
        let items = messages.iter().map(project_message).collect::<Vec<_>>();
        Ok(Page {
            items,
            total,
            offset: query.offset,
            limit: query.limit,
        })
    }
}

// ── projection helpers ────────────────────────────────────────────────

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
    SessionParticipant {
        actor_id: participant.bot_uuid.clone(),
        actor_kind: participant.actor_kind,
        name: participant.bot_name.clone(),
        role: participant.role,
        mode: project_bot_mode(participant.effective_mode()),
        joined_at: None,
    }
}

/// Project the domain collaboration mode into the V1 Bot-only mode. Session
/// participants are Bot-only in V1; the Human-only `Present`/`Absent` variants
/// should not appear, but default to `Auto` if they do rather than failing the
/// read path.
fn project_bot_mode(mode: ParticipantMode) -> BotParticipantMode {
    match mode {
        ParticipantMode::Auto => BotParticipantMode::Auto,
        ParticipantMode::Muted => BotParticipantMode::Muted,
        ParticipantMode::Present | ParticipantMode::Absent => BotParticipantMode::Auto,
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
    fn project_bot_mode_narrows_to_auto_or_muted() {
        assert_eq!(
            project_bot_mode(ParticipantMode::Auto),
            BotParticipantMode::Auto
        );
        assert_eq!(
            project_bot_mode(ParticipantMode::Muted),
            BotParticipantMode::Muted
        );
        // Human-only modes default to Auto for Bot-only sessions.
        assert_eq!(
            project_bot_mode(ParticipantMode::Present),
            BotParticipantMode::Auto
        );
    }
}
