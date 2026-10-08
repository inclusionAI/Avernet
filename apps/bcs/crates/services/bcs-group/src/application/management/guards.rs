//! Caller authorization and Group guards for the legacy Group service.

use super::*;

impl GroupManagement {
    /// Live Human→Bot can_manage through the wired authority hook
    /// (spec §8/§12.4). Fails CLOSED when no hook is wired — `created_by`,
    /// creator edges and Bot-ID suffixes never substitute authority.
    pub(crate) async fn human_can_manage_bot(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> Result<bool, GroupUseCaseError> {
        let hook = self.authority.as_ref().ok_or_else(|| {
            GroupUseCaseError::Forbidden(
                "Human→Bot authority is not configured for this operation".to_string(),
            )
        })?;
        hook.can_manage(user_id, bot_id)
            .await
            .map_err(GroupUseCaseError::Service)
    }

    /// Eligibility form: an UNINITIALIZED Bot is a plain non-match for
    /// enumeration-style questions (spec §12.4: callers who do not already
    /// manage the Bot must learn nothing); corrupt and other typed
    /// failures still propagate.
    pub(crate) async fn human_maybe_manages_bot(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> Result<bool, GroupUseCaseError> {
        match self.human_can_manage_bot(user_id, bot_id).await {
            Ok(allowed) => Ok(allowed),
            Err(GroupUseCaseError::Service(ServiceError::Authority(
                bcs_service_api::types::error::AuthorityError::OwnershipNotInitialized { .. },
            ))) => Ok(false),
            Err(other) => Err(other),
        }
    }

    /// Validate the trusted Human sponsorship credential of a command
    /// (spec §8.3): the credential itself must name the Human the boundary
    /// authenticated (`expected_user_id`), and every sponsored decision later
    /// re-verifies CURRENT qualification via [`Self::human_can_manage_bot`] —
    /// the credential is never trusted as a bare string.
    pub(crate) fn verify_sponsorship_identity(
        &self,
        sponsorship: &HumanSponsorship,
        expected_user_id: &str,
    ) -> Result<(), GroupUseCaseError> {
        if sponsorship.user_id != expected_user_id {
            return Err(GroupUseCaseError::Forbidden(format!(
                "Human sponsorship credential must identify the authenticated human '{}'",
                expected_user_id
            )));
        }
        Ok(())
    }

    /// The Human sponsorship predicate over one target Bot
    /// (spec §8.3):
    /// ```text
    /// human sponsor = not hidden AND (public OR authority.can_manage(human, bot))
    /// ```
    pub(crate) async fn sponsorship_allows_bot(
        &self,
        sponsor_user_id: &str,
        bot: &RegisteredBot,
    ) -> Result<bool, GroupUseCaseError> {
        if bot.status == ActorStatus::Hidden {
            return Ok(false);
        }
        if bot.capabilities.visibility == "public" {
            return Ok(true);
        }
        self.human_can_manage_bot(sponsor_user_id, &bot.bot_uuid).await
    }

    pub(crate) async fn authorize_originator(
        &self,
        caller_actor_id: Option<&str>,
        originator: &str,
    ) -> Result<(), GroupUseCaseError> {
        let caller = caller_actor_id
            .filter(|caller| !caller.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;
        if caller == originator {
            return Ok(());
        }

        // Human callers can designate any bot or human as originator.
        if caller.starts_with("human_") {
            return Ok(());
        }

        Err(GroupUseCaseError::Forbidden(format!(
            "Not authorized to create group as '{}'",
            originator
        )))
    }

    pub(crate) async fn ensure_limits(
        &self,
        driver_bot_id: &str,
        participant_ids: &[String],
    ) -> Result<(), GroupUseCaseError> {
        if participant_ids.len() > self.config.max_group_members {
            return Err(GroupUseCaseError::InvalidProposal(format!(
                "Group would have {} members, exceeding the limit of {}",
                participant_ids.len(),
                self.config.max_group_members
            )));
        }

        let driver_active_count = self
            .groups_for_quota(driver_bot_id)
            .await?
            .into_iter()
            .filter(|group| {
                group.driver_bot == driver_bot_id && group.status == GroupStatus::Active
            })
            .count();
        if driver_active_count >= self.config.max_groups_as_driver {
            return Err(GroupUseCaseError::InvalidProposal(format!(
                "Bot '{}' already drives {} active group(s) (max {})",
                driver_bot_id, driver_active_count, self.config.max_groups_as_driver
            )));
        }

        for bot_id in participant_ids {
            let active_count = self
                .groups_for_quota(bot_id)
                .await?
                .into_iter()
                .filter(|group| group.status == GroupStatus::Active)
                .count();
            if active_count >= self.config.max_groups_as_member {
                return Err(GroupUseCaseError::InvalidProposal(format!(
                    "Bot '{}' is already in {} active group(s) (max {})",
                    bot_id, active_count, self.config.max_groups_as_member
                )));
            }
        }

        Ok(())
    }

    pub(crate) async fn groups_for_quota(
        &self,
        actor_id: &str,
    ) -> Result<Vec<DomainGroup>, GroupUseCaseError> {
        if self.v1_openapi_create_policy {
            return Ok(self.group.try_find_by_participant(actor_id).await?);
        }
        Ok(self.group.find_by_participant(actor_id).await)
    }

    pub(crate) async fn ensure_reachable(
        &self,
        driver_bot_id: &str,
        target_bot_id: &str,
    ) -> Result<(), GroupUseCaseError> {
        let target = self
            .registry
            .get(target_bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(target_bot_id.to_string()))?;

        if target.status == ActorStatus::Hidden {
            return Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is hidden (offline) and cannot be invited into a group",
                target_bot_id
            )));
        }

        let is_friend = self.friend.are_friends(driver_bot_id, target_bot_id).await;
        match target.capabilities.visibility.as_str() {
            "public" => Ok(()),
            _ if is_friend => Ok(()),
            "protected" => Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is not friends with '{}'",
                driver_bot_id, target_bot_id
            ))),
            _ => Err(ServiceError::BotNotFound(target_bot_id.to_string()).into()),
        }
    }

    pub(crate) async fn ensure_v1_reachable(
        &self,
        driver_bot_id: &str,
        target: &RegisteredBot,
    ) -> Result<(), GroupUseCaseError> {
        if target.status == ActorStatus::Hidden {
            return Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is hidden (offline) and cannot be invited into a group",
                target.bot_uuid
            )));
        }

        match target.capabilities.visibility.as_str() {
            "public" => Ok(()),
            "protected"
                if self
                    .friend
                    .try_are_friends(driver_bot_id, &target.bot_uuid)
                    .await? =>
            {
                Ok(())
            }
            "protected" => Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is not friends with '{}'",
                driver_bot_id, target.bot_uuid
            ))),
            _ => Err(ServiceError::BotNotFound(target.bot_uuid.clone()).into()),
        }
    }

    pub(crate) async fn try_write_subscription_edge(&self, requester_bot_id: &str, target: &RegisteredBot) {
        if target.capabilities.visibility != "public" {
            return;
        }

        let env = &self.config.relation_env;
        match self
            .relation
            .get_edge(requester_bot_id, &target.bot_uuid, env)
            .await
        {
            Ok(Some(_)) => {}
            Ok(None) => {
                if let Err(error) = self
                    .relation
                    .add_relation_edge(requester_bot_id, &target.bot_uuid, env)
                    .await
                {
                    warn!(
                        requester = %requester_bot_id,
                        target = %target.bot_uuid,
                        env = %env,
                        error = %error,
                        "subscription edge write failed; group membership remains the source of truth"
                    );
                }
            }
            Err(error) => {
                warn!(
                    requester = %requester_bot_id,
                    target = %target.bot_uuid,
                    env = %env,
                    error = %error,
                    "relation.get_edge failed before subscription edge write; skipping"
                );
            }
        }
    }

    pub(crate) async fn ensure_add_member_reachable(
        &self,
        driver_bot_id: &str,
        target_bot_id: &str,
    ) -> Result<(), GroupUseCaseError> {
        let target = self
            .registry
            .get(target_bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(target_bot_id.to_string()))?;

        if target.status == ActorStatus::Hidden {
            return Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is hidden (offline) and cannot be invited into a group",
                target_bot_id
            )));
        }

        let is_friend = self.friend.are_friends(driver_bot_id, target_bot_id).await;
        match target.capabilities.visibility.as_str() {
            "public" => Ok(()),
            _ if is_friend => Ok(()),
            "protected" => Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is not friends with '{}'",
                driver_bot_id, target_bot_id
            ))),
            _ => Err(ServiceError::BotNotFound(target_bot_id.to_string()).into()),
        }
    }

    pub(crate) async fn ensure_manager_worker_accepts_participants(
        &self,
        _strategy: GroupStrategy,
        _participants: &[Participant],
    ) -> Result<(), GroupUseCaseError> {
        Ok(())
    }

    /// Check that all bot participants in the list have public visibility.
    /// Returns error listing non-public bots if any exist.
    pub(crate) async fn ensure_all_bots_public(
        &self,
        participants: &[Participant],
    ) -> Result<(), GroupUseCaseError> {
        let mut non_public = Vec::new();
        for p in participants {
            if p.actor_kind != ActorKind::Bot {
                continue;
            }
            if let Some(bot) = self.registry.get(&p.bot_uuid).await {
                if bot.capabilities.visibility != "public" {
                    non_public.push((p.bot_uuid.clone(), bot.capabilities.name.clone()));
                }
            }
        }
        if non_public.is_empty() {
            Ok(())
        } else {
            Err(GroupUseCaseError::Service(
                ServiceError::ExistNonPublicBots { bots: non_public },
            ))
        }
    }

    pub(crate) async fn relation_has_creator_edge(
        &self,
        human_actor_id: &str,
        bot_id: &str,
    ) -> Result<bool, GroupUseCaseError> {
        match self
            .relation
            .get_edge(human_actor_id, bot_id, &self.config.relation_env)
            .await
        {
            Ok(Some(edge)) => Ok(edge.is_creator),
            Ok(None) => Ok(false),
            Err(error) => Err(ServiceError::InternalError(format!(
                "Failed to verify owner relation: {}",
                error
            ))
            .into()),
        }
    }

    pub(crate) async fn has_bidirectional_relation_or_friendship(
        &self,
        actor_id: &str,
        bot_id: &str,
    ) -> Result<bool, GroupUseCaseError> {
        let relation_friends = self
            .relation
            .list_friends_via_relation(actor_id, &self.config.relation_env)
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "Failed to verify relation friendship: {}",
                    error
                ))
            })?;
        if relation_friends.iter().any(|friend| friend == bot_id) {
            return Ok(true);
        }

        if self.v1_openapi_create_policy {
            return Ok(self.friend.try_are_friends(actor_id, bot_id).await?);
        }
        Ok(self.friend.are_friends(actor_id, bot_id).await)
    }

    pub(crate) async fn ensure_human_can_dm_bot(
        &self,
        human_actor_id: &str,
        target: &RegisteredBot,
    ) -> Result<(), GroupUseCaseError> {
        let staff_no = human_actor_id.strip_prefix("human_").ok_or_else(|| {
            GroupUseCaseError::InvalidProposal("caller actor_id must use human_ prefix".to_string())
        })?;

        if target.created_by.as_deref() == Some(staff_no)
            || self
                .relation_has_creator_edge(human_actor_id, &target.bot_uuid)
                .await?
        {
            return Ok(());
        }

        // Authority cutover (spec §8.2/§12.4): a Human who currently owns or
        // manages the target Bot may DM it without any legacy creator fact.
        // Public Bots never reach this point — the public arm below keeps the
        // DM lane independent of any ownership initialization.
        if target.capabilities.visibility != "public"
            && self.authority.is_some()
            && self
                .human_can_manage_bot(staff_no, &target.bot_uuid)
                .await?
        {
            return Ok(());
        }

        let has_relation = self
            .has_bidirectional_relation_or_friendship(human_actor_id, &target.bot_uuid)
            .await?;
        match target.capabilities.visibility.as_str() {
            "public" => Ok(()),
            "protected" if has_relation => Ok(()),
            "protected" => Err(GroupUseCaseError::Forbidden(format!(
                "Human '{}' is not related to protected bot '{}'",
                human_actor_id, target.bot_uuid
            ))),
            "private" if has_relation => Ok(()),
            _ => Err(ServiceError::BotNotFound(target.bot_uuid.clone()).into()),
        }
    }

    pub(crate) fn is_human_bot_dm(group: &DomainGroup) -> bool {
        group.group_kind == GroupKind::Dm
            && group
                .participants
                .iter()
                .any(|participant| participant.is_human())
            && group
                .participants
                .iter()
                .any(|participant| participant.is_bot())
    }

    pub(crate) async fn authorize_human_owner(
        &self,
        human_actor_id: Option<&str>,
        bot_id: &str,
    ) -> Result<(), GroupUseCaseError> {
        let Some(human_actor_id) = human_actor_id else {
            return Ok(()); // bot 自主操作，由后续 ensure_group_coordinator 接管
        };
        let staff_no = human_actor_id
            .strip_prefix("human_")
            .unwrap_or(human_actor_id);
        // The act-as check is meaningful only when the caller actor is a
        // real Bot; a Human originator/driver acting as themselves is not
        // an act-as at all (legacy parity: human actors have no
        // authority edge of their own).
        let caller_is_bot = self
            .registry
            .try_get(bot_id)
            .await
            .map(|actor| actor.is_some_and(|actor| actor.actor_kind == ActorKind::Bot))
            .unwrap_or(false);
        if !caller_is_bot {
            return Ok(());
        }
        // Authority cutover (spec §12.4): when the live hook is wired, a
        // Human acting AS a Bot is judged by the CURRENT owner/manager edges
        // only — never by `created_by` (spec §12.2 forbids the fallback).
        if self.authority.is_some() {
            return if self.human_can_manage_bot(staff_no, bot_id).await? {
                Ok(())
            } else {
                Err(GroupUseCaseError::Forbidden(format!(
                    "Not authorized as bot '{}'",
                    bot_id
                )))
            };
        }
        let bot = self.registry.get(bot_id).await.ok_or_else(|| {
            GroupUseCaseError::Forbidden(format!("Not authorized as bot '{}'", bot_id))
        })?;
        if bot
            .created_by
            .as_deref()
            .map(|owner| owner == staff_no)
            .unwrap_or(true)
        {
            return Ok(());
        }

        Err(GroupUseCaseError::Forbidden(format!(
            "Not authorized as bot '{}'",
            bot_id
        )))
    }

    pub(crate) fn ensure_group_coordinator(
        &self,
        group: &DomainGroup,
        caller_actor_id: &str,
        action: &str,
    ) -> Result<(), GroupUseCaseError> {
        if group.originator() == caller_actor_id || group.driver_bot == caller_actor_id {
            return Ok(());
        }
        if group.group_strategy == GroupStrategy::ManagerWorker
            && group.participants.iter().any(|participant| {
                participant.bot_uuid == caller_actor_id
                    && participant.role == ParticipantRole::Manager
            })
        {
            return Ok(());
        }

        Err(GroupUseCaseError::Forbidden(format!(
            "Only the group coordinator (originator: {} or driver: {}) can {}, not '{}'",
            group.originator(),
            group.driver_bot,
            action,
            caller_actor_id
        )))
    }

    pub(crate) async fn authorize_add_member(
        &self,
        cmd: &GroupAddMemberCommand,
    ) -> Result<(String, DomainGroup), GroupUseCaseError> {
        let caller = cmd
            .caller_actor_id
            .as_deref()
            .filter(|c| !c.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        if group.group_kind == GroupKind::Dm {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM groups cannot add members".to_string(),
            ));
        }
        self.authorize_human_owner(cmd.human_actor_id.as_deref(), caller)
            .await?;
        self.ensure_group_coordinator(&group, caller, "add members")?;
        // Order pins the brief: the group-management authorization above must
        // pass BEFORE any sponsorship claim is looked at. Once reached, the
        // sponsorship credential must be anchored to the verified Human
        // context of this command (never a bare request string).
        if let Some(sponsor) = cmd.human_sponsorship.as_ref() {
            let trusted_human = cmd
                .human_actor_id
                .as_deref()
                .and_then(|actor| actor.strip_prefix("human_"))
                .or_else(|| caller.strip_prefix("human_"));
            match trusted_human {
                Some(staff_no) => self.verify_sponsorship_identity(sponsor, staff_no)?,
                None => {
                    return Err(GroupUseCaseError::Forbidden(
                        "Human sponsorship requires a verified Human caller context".to_string(),
                    ));
                }
            }
        }
        Ok((caller.to_string(), group))
    }

    pub(crate) async fn ensure_actor_self_or_creator(
        &self,
        caller_actor_id: &str,
        actor_id: &str,
    ) -> Result<(), GroupUseCaseError> {
        if caller_actor_id == actor_id {
            return Ok(());
        }

        match self
            .relation
            .get_edge(caller_actor_id, actor_id, &self.config.relation_env)
            .await
        {
            Ok(Some(edge)) if edge.is_creator => Ok(()),
            Ok(_) => Err(GroupUseCaseError::Forbidden(format!(
                "Caller '{}' is not the actor itself nor a creator of '{}'",
                caller_actor_id, actor_id
            ))),
            Err(error) => Err(ServiceError::InternalError(format!(
                "Failed to verify creator relation: {}",
                error
            ))
            .into()),
        }
    }

    pub(crate) async fn authorize_workbench_group_access(
        &self,
        group: &DomainGroup,
        bound_actor_id: Option<&str>,
    ) -> Result<WorkbenchAuthorizedHuman, WorkbenchUseCaseError> {
        let actor_id = bound_actor_id.ok_or(WorkbenchUseCaseError::Unauthorized)?;
        let staff_no = staff_no_from_bound_actor(Some(actor_id))?;

        if self
            .human_may_view_group_participants(group, actor_id, staff_no)
            .await?
        {
            return Ok(WorkbenchAuthorizedHuman {
                actor_id: actor_id.to_string(),
                staff_no: staff_no.to_string(),
            });
        }

        Err(WorkbenchUseCaseError::ForbiddenGroupAccess)
    }

    /// Workbench group access (spec §8.1/§12.4): with the live hook wired, a
    /// bound Human sees groups they participate in OR whose Bot participants
    /// they CURRENTLY own/manage — no `created_by`/Bot-ID-suffix fallback.
    /// Without a hook (legacy lanes) the original created_by heuristic stays.
    async fn human_may_view_group_participants(
        &self,
        group: &DomainGroup,
        actor_id: &str,
        staff_no: &str,
    ) -> Result<bool, WorkbenchUseCaseError> {
        if self.authority.is_none() {
            return Ok(human_has_group_access(
                self.registry.as_ref(),
                group,
                actor_id,
                staff_no,
            )
            .await);
        }
        if group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == actor_id)
        {
            return Ok(true);
        }
        for participant in group.participants.iter().filter(|p| p.is_bot()) {
            match self.human_maybe_manages_bot(staff_no, &participant.bot_uuid).await {
                Ok(true) => return Ok(true),
                Ok(false) => {}
                Err(error) => {
                    return Err(WorkbenchUseCaseError::Service(ServiceError::InternalError(
                        error.to_string(),
                    )))
                }
            }
        }
        Ok(false)
    }

    /// May the bound Human send as `from_actor_id` (spec §8.2: managing the
    /// Bot grants the owner's message parity)? Same hook-first rule as
    /// [`Self::human_may_view_group_participants`].
    async fn human_may_act_as_sender(
        &self,
        from_actor_id: &str,
        staff_no: &str,
    ) -> Result<bool, WorkbenchUseCaseError> {
        let Some(bot) = self.registry.get(from_actor_id).await else {
            return Ok(false);
        };
        if bot.actor_kind != ActorKind::Bot {
            return Ok(false);
        }
        if self.authority.is_none() {
            return Ok(bot_belongs_to_staff(
                from_actor_id,
                bot.created_by.as_deref(),
                staff_no,
            ));
        }
        match self.human_can_manage_bot(staff_no, from_actor_id).await {
            Ok(allowed) => Ok(allowed),
            Err(error) => Err(WorkbenchUseCaseError::Service(ServiceError::InternalError(
                error.to_string(),
            ))),
        }
    }

    pub(crate) async fn authorize_workbench_sender(
        &self,
        group: &DomainGroup,
        from_actor_id: &str,
        auth: &WorkbenchAuthorizedHuman,
    ) -> Result<(), WorkbenchUseCaseError> {
        if from_actor_id == auth.actor_id {
            return Ok(());
        }

        if Self::is_human_bot_dm(group) {
            return Err(WorkbenchUseCaseError::ForbiddenSender);
        }

        if self
            .human_may_act_as_sender(from_actor_id, &auth.staff_no)
            .await?
        {
            return Ok(());
        }

        Err(WorkbenchUseCaseError::ForbiddenSender)
    }

    /// Session-operate authority (spec §8.2/§12.4): with the live hook wired,
    /// the bound Human may operate a session bot they CURRENTLY own/manage;
    /// legacy lanes keep the created_by heuristic.
    pub(crate) async fn human_may_operate_bot(
        &self,
        staff_no: &str,
        bot_id: &str,
        created_by: Option<&str>,
    ) -> ServiceResult<bool> {
        if self.authority.is_some() {
            return match self.human_can_manage_bot(staff_no, bot_id).await {
                Ok(allowed) => Ok(allowed),
                Err(GroupUseCaseError::Service(ServiceError::Authority(
                    bcs_service_api::types::error::AuthorityError::OwnershipNotInitialized { .. },
                ))) => Ok(false),
                Err(other) => Err(ServiceError::InternalError(other.to_string())),
            };
        }
        Ok(bot_belongs_to_staff(bot_id, created_by, staff_no))
    }

    pub(crate) async fn session_participant(
        &self,
        session_id: Option<&str>,
        group_id: &str,
        actor_id: &str,
    ) -> Result<Option<bcs_service_api::Participant>, WorkbenchUseCaseError> {
        let Some(session_id) = session_id else {
            return Ok(None);
        };
        let session = self
            .session_management
            .get(session_id)
            .await
            .map_err(|error| {
                WorkbenchUseCaseError::Service(ServiceError::InternalError(error.to_string()))
            })?;
        Ok(session
            .filter(|session| session.group_id == group_id)
            .and_then(|session| find_session_participant(&session, actor_id)))
    }
}
