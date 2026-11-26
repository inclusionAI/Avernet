//! Access decisions shared by monolithic Direct chat and distributed admission.
use super::*;
use bcs_service_api::port::bot_authority::BotAuthorityPort;

#[derive(Clone)]
pub struct DirectChatAccess {
    pub(super) registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    pub(super) organization: Option<Arc<dyn OrganizationCoreService>>,
    pub(super) bot_authority: Option<Arc<dyn BotAuthorityPort>>,
}

impl DirectChatAccess {
    pub fn new(
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
        organization: Option<Arc<dyn OrganizationCoreService>>,
        bot_authority: Option<Arc<dyn BotAuthorityPort>>,
    ) -> Self {
        Self {
            registry,
            friend,
            organization,
            bot_authority,
        }
    }

    pub async fn authorize(
        &self,
        source: &str,
        target: &str,
        staff: Option<&str>,
        organization: Option<&str>,
    ) -> ServiceResult<()> {
        bcs_observability::observe_result(
            "chat.authorize.owner",
            self.ensure_source_owner(source, staff),
        )
        .await?;
        let target_bot = if let Some(code) = organization {
            let organization =
                self.organization
                    .as_ref()
                    .ok_or_else(|| ServiceError::InvalidOperation {
                        message: "organization service is not configured".to_string(),
                        request_id: None,
                    })?;
            bcs_observability::observe_result(
                "chat.authorize.organization",
                organization.authorize_pair(code, source, target),
            )
            .await?;
            bcs_observability::observe_result(
                "chat.authorize.organization_target",
                self.ensure_organization_target_reachable(source, target),
            )
            .await?
        } else {
            bcs_observability::observe_result(
                "chat.authorize.target",
                self.ensure_target_reachable(source, target),
            )
            .await?
        };
        if target_bot.status == ActorStatus::Hidden {
            let name = target_bot.capabilities.name.as_deref().unwrap_or(target);
            return Err(ServiceError::BotHidden(name.to_string()));
        }
        Ok(())
    }

    /// Human→Bot control judgment for acting AS the
    /// source Bot (spec §8 / bot-authority plan Task 6.5).
    /// The legacy `created_by` comparison — including its
    /// `created_by: None → allow` auto-claim — is replaced
    /// by the live management edge: a Bot without an
    /// authorized manager controls nothing, and a failing
    /// authority read is an internal error, never a deny.
    async fn ensure_source_owner(
        &self,
        from_bot_id: &str,
        authenticated_staff_id: Option<&str>,
    ) -> ServiceResult<()> {
        let Some(staff_id) = authenticated_staff_id.filter(|id| !id.is_empty()) else {
            return Ok(());
        };
        let authority = self
            .bot_authority
            .as_deref()
            .ok_or_else(|| ServiceError::InternalError("bot authority hook is not wired".into()))?;
        match authority.can_manage(staff_id, from_bot_id).await {
            Ok(true) => Ok(()),
            Ok(false) => Err(ServiceError::Unauthorized(format!(
                "User {} does not control bot {}",
                staff_id, from_bot_id
            ))),
            Err(error) => Err(ServiceError::InternalError(format!(
                "bot authority lookup failed: {error}"
            ))),
        }
    }

    pub(super) async fn ensure_target_reachable(
        &self,
        from_bot_id: &str,
        target_bot_id: &str,
    ) -> ServiceResult<RegisteredBot> {
        let target = self
            .registry
            .get(target_bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(target_bot_id.to_string()))?;

        if self
            .registry
            .collaboration_owner(&[from_bot_id.into(), target_bot_id.into()])
            .await?
            .is_some()
        {
            if target.status == ActorStatus::Hidden {
                return Err(ServiceError::Forbidden("target Bot is hidden".into()));
            }
            return Ok(target);
        }

        if from_bot_id == target_bot_id {
            return Ok(target);
        }

        match target.capabilities.visibility.as_str() {
            "public" => Ok(target),
            "protected" if self.friend.are_friends(from_bot_id, target_bot_id).await => Ok(target),
            "protected" => Err(ServiceError::NotFriends(vec![target_bot_id.to_string()])),
            // "private" or unknown: friends can still reach, strangers get 404
            _ if self.friend.are_friends(from_bot_id, target_bot_id).await => Ok(target),
            _ => Err(ServiceError::BotNotFound(target_bot_id.to_string())),
        }
    }

    pub(super) async fn ensure_organization_target_reachable(
        &self,
        from_bot_id: &str,
        target_bot_id: &str,
    ) -> ServiceResult<RegisteredBot> {
        self.registry
            .collaboration_owner(&[from_bot_id.into(), target_bot_id.into()])
            .await?;
        let target = self
            .registry
            .get(target_bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(target_bot_id.to_string()))?;

        match target.capabilities.visibility.as_str() {
            "public" | "protected" => Ok(target),
            _ if self.friend.are_friends(from_bot_id, target_bot_id).await => Ok(target),
            _ => Err(ServiceError::BotNotFound(target_bot_id.to_string())),
        }
    }
}
