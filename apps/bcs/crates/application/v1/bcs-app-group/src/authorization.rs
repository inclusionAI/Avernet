//! Caller authorization and loadable-Group guards for the V1 Group facade.

use super::*;

impl GroupServiceImpl {
    pub(crate) async fn load_bot(
        &self,
        bot_uuid: &str,
    ) -> Result<bcs_service_api::RegisteredBot, ApplicationError> {
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

    pub(crate) async fn resolve_view_actor(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
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

    pub(crate) async fn ensure_collaboration_eligible(
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

    pub(crate) async fn can_read_group_detail(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let principal_actor_id = principal.actor_id();
        if group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == principal_actor_id)
        {
            return Ok(true);
        }
        match principal {
            Principal::Bot(_) => Ok(Self::group_management_actor_ids(group)
                .iter()
                .any(|actor_id| actor_id == &principal_actor_id)),
            Principal::Human(human) => {
                let owned_bot_ids = self
                    .registry
                    .try_list_bots_by_creator(&human.subject.id)
                    .await
                    .map_err(map_service_error)?
                    .into_iter()
                    .filter(|bot| bot.actor_kind == ActorKind::Bot)
                    .map(|bot| bot.bot_uuid)
                    .collect::<HashSet<_>>();
                Ok(group.participants.iter().any(|participant| {
                    participant.actor_kind == ActorKind::Bot
                        && owned_bot_ids.contains(&participant.bot_uuid)
                }))
            }
        }
    }

    /// The effective Human principal may act as itself or as an owned Bot
    /// originator. The effective Bot principal may act only as itself. Anything
    /// else is forbidden. This is stricter than legacy `authorize_originator`
    /// (which lets any human designate any originator); it is a V1 gate and
    /// legacy `POST /groups` is unaffected.
    pub(crate) async fn authorize_originator(
        &self,
        principal: &Principal,
        originator: &str,
    ) -> Result<(), ApplicationError> {
        if principal.actor_id() == originator {
            return Ok(());
        }
        let bot = self.load_bot(originator).await.map_err(|error| match error {
            ApplicationError::NotFound { .. } => ApplicationError::forbidden(format!(
                "Authenticated Principal cannot act as originator '{originator}'"
            )),
            other => other,
        })?;
        if bot.actor_kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_originator",
                "originator must be a Bot Actor",
            ));
        }
        if let Principal::Human(human) = principal {
            if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
                return Ok(());
            }
        }
        Err(ApplicationError::forbidden(format!(
            "Authenticated Principal cannot act as originator '{originator}'"
        )))
    }

    /// Structural validity only: the driver must resolve to a registered Bot
    /// Actor. Does NOT check any caller↔driver relationship (that was dropped
    /// by design) — it only rejects a non-existent or non-Bot driver id.
    pub(crate) async fn ensure_driver_is_registered_bot(
        &self,
        driver_bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        let bot = self.load_bot(driver_bot_uuid).await?;
        if bot.actor_kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "driver_bot_uuid must identify a Bot Actor",
            ));
        }
        Ok(())
    }

    pub(crate) async fn can_read_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let principal_actor_id = principal.actor_id();
        if group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == principal_actor_id)
        {
            return Ok(true);
        }
        let management_actor_ids = Self::group_management_actor_ids(group);
        if management_actor_ids
            .iter()
            .any(|actor_id| actor_id == &principal_actor_id)
        {
            return Ok(true);
        }
        if let Principal::Human(human) = principal {
            let mut actor_ids = group
                .participants
                .iter()
                .map(|participant| participant.bot_uuid.clone())
                .collect::<Vec<_>>();
            actor_ids.extend(management_actor_ids);
            if self.human_can_act_as_any(human, actor_ids).await? {
                return Ok(true);
            }
        }
        let session_group_ids = self
            .sessions
            .list_group_ids_by_session_participant(&principal_actor_id)
            .await
            .map_err(|error| ApplicationError::internal(error.to_string()))?;
        Ok(session_group_ids.iter().any(|id| id == &group.id))
    }

    pub(crate) fn group_management_actor_ids(group: &DomainGroup) -> Vec<String> {
        let mut actor_ids = vec![group.driver_bot.clone(), group.originator().to_string()];
        if group.group_strategy == GroupStrategy::ManagerWorker {
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

    pub(crate) async fn human_actable_actor_id(
        &self,
        human: &HumanPrincipal,
        actor_ids: Vec<String>,
    ) -> Result<Option<String>, ApplicationError> {
        let human_actor_id = format!("human_{}", human.subject.id);
        let mut seen = HashSet::new();
        for actor_id in actor_ids {
            if !seen.insert(actor_id.clone()) {
                continue;
            }
            if actor_id == human_actor_id {
                return Ok(Some(actor_id));
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
                return Ok(Some(actor_id));
            }
            let creator_edge = self
                .relation
                .get_edge(&human_actor_id, &actor_id, &self.config.relation_env)
                .await
                .map_err(map_service_error)?;
            if creator_edge.is_some_and(|edge| edge.is_creator) {
                return Ok(Some(actor_id));
            }
        }
        Ok(None)
    }

    pub(crate) async fn human_can_act_as_any(
        &self,
        human: &HumanPrincipal,
        actor_ids: Vec<String>,
    ) -> Result<bool, ApplicationError> {
        Ok(self.human_actable_actor_id(human, actor_ids).await?.is_some())
    }

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
                self.human_can_act_as_any(human, vec![actor_id.to_string()]).await
            }
            Principal::Bot(_) => Ok(false),
        }
    }

    pub(crate) async fn resolve_group_manage_actor(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<Option<String>, ApplicationError> {
        let principal_actor_id = principal.actor_id();
        let candidates = Self::group_management_actor_ids(group);
        if candidates.iter().any(|actor_id| actor_id == &principal_actor_id) {
            return Ok(Some(principal_actor_id));
        }
        if let Principal::Human(human) = principal {
            return self.human_actable_actor_id(human, candidates).await;
        }
        Ok(None)
    }

    pub(crate) async fn can_manage_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        Ok(self
            .resolve_group_manage_actor(principal, group)
            .await?
            .is_some())
    }

    pub(crate) async fn load_readable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if group.record_status != "active" {
            return Err(ApplicationError::not_found(
                "group_not_found",
                format!("Group '{group_id}' was not found"),
            ));
        }
        if !self.can_read_group(principal, &group).await? {
            return Err(ApplicationError::forbidden(
                "Principal has no readable relation to this Group",
            ));
        }
        Ok(group)
    }

    pub(crate) async fn load_group_detail_for_caller(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let principal = select_principal(caller, IdentityPolicy::HumanOrOwnedBot)?;
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if group.record_status != "active" {
            return Err(ApplicationError::not_found(
                "group_not_found",
                format!("Group '{group_id}' was not found"),
            ));
        }
        if group.visibility != visibility_name(GroupVisibility::Public)
            && !self.can_read_group_detail(&principal, &group).await?
        {
            return Err(ApplicationError::forbidden(
                "The selected Principal has no readable relation to this Group",
            ));
        }
        Ok(group)
    }

    pub(crate) async fn load_manageable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if group.record_status != "active" {
            return Err(ApplicationError::not_found(
                "group_not_found",
                format!("Group '{group_id}' was not found"),
            ));
        }
        if !self.can_manage_group(principal, &group).await? {
            return Err(ApplicationError::forbidden(
                "Only the Group originator or driver may manage this Group",
            ));
        }
        Ok(group)
    }
}
