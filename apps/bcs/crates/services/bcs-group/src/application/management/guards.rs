//! Caller authorization and Group guards for the legacy Group service.

use super::*;

impl GroupManagement {
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

        if human_has_group_access(self.registry.as_ref(), group, actor_id, staff_no).await {
            return Ok(WorkbenchAuthorizedHuman {
                actor_id: actor_id.to_string(),
                staff_no: staff_no.to_string(),
            });
        }

        Err(WorkbenchUseCaseError::ForbiddenGroupAccess)
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

        let Some(bot) = self.registry.get(from_actor_id).await else {
            return Err(WorkbenchUseCaseError::ForbiddenSender);
        };
        if bot.actor_kind != ActorKind::Bot {
            return Err(WorkbenchUseCaseError::ForbiddenSender);
        }
        if bot_belongs_to_staff(from_actor_id, bot.created_by.as_deref(), &auth.staff_no) {
            return Ok(());
        }

        Err(WorkbenchUseCaseError::ForbiddenSender)
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
