use super::*;

impl SessionServiceImpl {
    // ── authorization helpers ──────────────────────────────────────────

    /// Manager of the parent group (driver / originator / manager participant).
    /// Mirrors `bcs-app-group`'s targeted Human-to-actor authority semantics.
    pub(super) fn group_management_actor_ids(group: &DomainGroup) -> Vec<String> {
        let mut actor_ids = vec![group.driver_bot.clone(), group.originator().to_string()];
        if group.group_strategy == GroupStrategy::ManagerWorker {
            actor_ids.extend(
                group
                    .participants
                    .iter()
                    .filter(|participant| participant.role == ParticipantRole::Manager)
                    .map(|participant| participant.bot_uuid.clone()),
            );
        }
        actor_ids
    }

    async fn human_can_act_as_any(
        &self,
        human: &HumanPrincipal,
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
            if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
                return Ok(true);
            }
            let creator_edge = self
                .relation
                .get_edge(&human_actor_id, &actor_id, &self.config.relation_env)
                .await
                .map_err(map_service_error)?;
            if creator_edge.is_some_and(|edge| edge.is_creator) {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// Whether the Principal acts as `actor_id` itself: the same Actor, or
    /// a Human acting for a Bot it owns / has a creator relation edge to.
    /// Mirrors the sibling `bcs-app-group` helper of the same name.
    pub(super) async fn principal_can_act_as(
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

    pub(super) async fn can_manage_group(
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
    /// (`session.created_by`). Human callers may also act as any of those
    /// target actors through direct Human identity, Bot ownership, or creator
    /// relation edges.
    pub(super) async fn can_manage_session(
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

    pub(super) async fn authorize_human_self_mode_update(
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

    pub(super) fn validate_participant_mode(
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

    pub(super) async fn load_group(&self, group_id: &str) -> Result<DomainGroup, ApplicationError> {
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
    pub(super) async fn load_session_for_manage(
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
        if !self.can_manage_session(principal, &session, &group).await? {
            return Err(ApplicationError::forbidden(
                "Principal may not manage this Session",
            ));
        }
        Ok((session, group))
    }

    pub(super) async fn load_session(&self, session_id: &str) -> Result<Session, ApplicationError> {
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

    pub(super) async fn load_session_for_collection(
        &self,
        caller: &AuthenticatedCaller,
        session_id: &str,
        participant: &str,
    ) -> Result<Session, ApplicationError> {
        let user = require_authenticated_user(caller)?;
        // The participant must be owned by the authenticated Human: one of the
        // Human's own bots, OR the Human's own actor entry. `ensure_human_actor`
        // registers the latter as `human_{staff_no}` with `created_by` =
        // `staff_no` and `actor_kind = Human`. The legacy
        // `resolve_collector_bot` admits both via `list_bots_by_creator`, which
        // does not filter by actor kind — so a Human can collect a Session as
        // itself, not only through an owned bot. Match that contract by keying
        // ownership on `created_by` alone.
        let owned = self
            .registry
            .try_get(participant)
            .await
            .map_err(map_service_error)?
            .is_some_and(|bot| bot.created_by.as_deref() == Some(user.id.as_str()));
        if !owned {
            return Err(ApplicationError::forbidden(
                "The target participant is not owned by the authenticated Human",
            ));
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

    pub(super) async fn load_bot(&self, bot_uuid: &str) -> Result<RegisteredBot, ApplicationError> {
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

    pub(super) async fn resolve_view_actor(
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
        if bot.actor_kind == ActorKind::Bot && bot.created_by.as_deref() == Some(user.id.as_str()) {
            Ok(requested.to_string())
        } else {
            Err(ApplicationError::forbidden(
                "The explicit View Actor is not authorized",
            ))
        }
    }

    async fn can_read_session_detail(
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
        let owned_bot_ids = self
            .registry
            .try_list_bots_by_creator(&human.subject.id)
            .await
            .map_err(map_service_error)?
            .into_iter()
            .filter(|bot| bot.actor_kind == ActorKind::Bot)
            .map(|bot| bot.bot_uuid)
            .collect::<HashSet<_>>();
        Ok(session.participants.iter().any(|participant| {
            participant.actor_kind == ActorKind::Bot
                && owned_bot_ids.contains(&participant.bot_uuid)
        }))
    }

    pub(super) async fn load_session_for_detail(
        &self,
        caller: &AuthenticatedCaller,
        session_id: &str,
    ) -> Result<Session, ApplicationError> {
        let principal = select_principal(caller, IdentityPolicy::HumanOrOwnedBot)?;
        let session = self.load_session(session_id).await?;
        self.load_group(&session.group_id).await?;
        if !self.can_read_session_detail(&principal, &session).await? {
            return Err(ApplicationError::forbidden(
                "The selected Principal is not a Session Participant",
            ));
        }
        Ok(session)
    }

    /// VSN7B: A caller may add a Bot to a session only when that Bot is
    /// collaboration-eligible from the caller OR from at least one of the
    /// parent Group's management anchors (driver, originator). A Hidden bot is
    /// rejected outright regardless of which anchor could sponsor it.
    ///
    /// Eligibility from an anchor actor (a Human `human_{id}` or a Bot uuid) is:
    /// - the target must be a Bot Actor that is not Hidden; AND
    /// - the anchor IS the target bot; OR the target is `public`; OR (for a
    ///   Human anchor) the anchor owns the target via `created_by` or a creator
    ///   relation edge; OR the anchor and target are friends.
    ///
    /// This widens the sibling `bcs-app-group::ensure_collaboration_eligible`
    /// (caller-only) check for the session-add case: a group's existing
    /// driver/originator may legitimately sponsor a participant that the caller
    /// itself cannot reach (e.g. a protected Bot that is the driver's friend
    /// but not the caller's), so a manager cannot be blocked from pulling a Bot
    /// the group already collaborates with.
    pub(super) async fn ensure_collaboration_eligible(
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

        // Anchor set: the calling Principal plus the parent Group's driver and
        // originator. Dedup so a Human originator that also equals the caller,
        // or a driver that equals the originator, is not consulted twice.
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

    /// Whether `bot` is collaboration-reachable from the anchor actor
    /// `anchor_id`. An anchor is a `human_{id}` actor id or a Bot uuid; only a
    /// Human anchor may sponsor via `created_by` or a creator relation edge
    /// (ownership semantics), while any anchor may sponsor via self-identity or
    /// direct friendship. The public/hidden gates are evaluated by the caller.
    async fn anchor_reaches_bot(
        &self,
        anchor_id: &str,
        bot: &RegisteredBot,
    ) -> Result<bool, ApplicationError> {
        if anchor_id == bot.bot_uuid {
            return Ok(true);
        }
        if let Some(staff_no) = anchor_id.strip_prefix("human_") {
            if bot.created_by.as_deref() == Some(staff_no) {
                return Ok(true);
            }
            let creator_edge = self
                .relation
                .get_edge(anchor_id, &bot.bot_uuid, &self.config.relation_env)
                .await
                .map_err(map_service_error)?;
            if creator_edge.is_some_and(|edge| edge.is_creator) {
                return Ok(true);
            }
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

    // ── projections ────────────────────────────────────────────────────

    pub(super) async fn project_detail(
        &self,
        session: &Session,
        state_machine_run: Option<StateMachineRunView>,
    ) -> Result<SessionDetail, ApplicationError> {
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
            kind: session.session_kind,
            title: session.session_title.clone(),
            input: session.input.clone(),
            meta: session.meta.clone(),
            participants,
            caller_principal: session.caller_principal.clone(),
            created_by: session.created_by.clone(),
            created_at: session.created_at,
            updated_at: session.updated_at,
            state_machine_run_id: state_machine_run
                .as_ref()
                .map(|view| view.run.run_id.clone()),
            state_machine_run,
        })
    }

    /// Backfill display names for all participants, then project the one
    /// identified by `bot_uuid`. Backfilling the whole slice first avoids
    /// borrowing the slice immutably (for the lookup) while it is still
    /// mutably borrowed by `backfill_participant_names`.
    pub(super) async fn backfill_and_project_participant(
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
