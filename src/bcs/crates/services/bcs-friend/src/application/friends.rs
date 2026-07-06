//! Friend use-case implementation.

use std::sync::Arc;

use async_trait::async_trait;

use bcs_service_api::{
    ActorKind, BotRegistryCoreService, CreateFriendRequestCommand, DynamicStatusResponse,
    FriendCoreService, FriendListEntry, FriendRequest, FriendRequestCoreService,
    FriendRequestDecisionCommand, FriendUseCaseError, FriendService, ListFriendRequestsCommand,
    ListFriendsCommand, RelationCoreService, ServiceError,
};

/// Friend application service backed by friend, friend-request, registry, and
/// relation services.
pub struct Friend {
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    friend_request: Arc<dyn FriendRequestCoreService>,
    relation: Arc<dyn RelationCoreService>,
}

impl Friend {
    pub fn new(
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
        friend_request: Arc<dyn FriendRequestCoreService>,
        relation: Arc<dyn RelationCoreService>,
    ) -> Self {
        Self {
            registry,
            friend,
            friend_request,
            relation,
        }
    }

    async fn authorize_request_receiver(
        &self,
        command: &FriendRequestDecisionCommand,
        action: &str,
    ) -> Result<(), FriendUseCaseError> {
        let request_to_bot = if let Some(to_bot) = command.request_to_bot.as_deref() {
            to_bot.to_string()
        } else {
            self.request_receiver_actor(&command.request_id).await?
        };

        if request_to_bot != command.caller_actor_id {
            return Err(FriendUseCaseError::Forbidden(format!(
                "Cannot {} request intended for '{}': caller is '{}'",
                action, request_to_bot, command.caller_actor_id
            )));
        }

        Ok(())
    }

    async fn request_receiver_actor(&self, request_id: &str) -> Result<String, FriendUseCaseError> {
        Ok(self
            .friend_request
            .get_request(request_id)
            .await
            .map_err(FriendUseCaseError::service)?
            .to_bot)
    }

    async fn check_self_or_owner_access(
        &self,
        caller_actor_id: &str,
        target_actor_id: &str,
    ) -> Result<(), FriendUseCaseError> {
        if caller_actor_id == target_actor_id {
            return Ok(());
        }

        match self.registry.get(target_actor_id).await {
            Some(bot)
                if !matches!(bot.capabilities.visibility.as_str(), "public" | "protected") =>
            {
                Err(FriendUseCaseError::service(ServiceError::BotNotFound(
                    target_actor_id.to_string(),
                )))
            }
            Some(_) => Err(FriendUseCaseError::Forbidden(
                "Not authorized to access this bot's friends".to_string(),
            )),
            None => Err(FriendUseCaseError::service(ServiceError::BotNotFound(
                target_actor_id.to_string(),
            ))),
        }
    }

    async fn ensure_target_actor_not_private(
        &self,
        target_id: &str,
    ) -> Result<(), FriendUseCaseError> {
        match self.registry.get(target_id).await {
            Some(bot) if matches!(bot.capabilities.visibility.as_str(), "public" | "protected") => {
                Ok(())
            }
            Some(_) => {
                tracing::debug!(
                    target_id = %target_id,
                    "friend request target exists but is not externally visible"
                );
                Err(FriendUseCaseError::service(ServiceError::BotNotFound(
                    target_id.to_string(),
                )))
            }
            None => {
                tracing::debug!(
                    target_id = %target_id,
                    "friend request target actor does not exist"
                );
                Err(FriendUseCaseError::service(ServiceError::BotNotFound(
                    target_id.to_string(),
                )))
            }
        }
    }
}

#[async_trait]
impl FriendService for Friend {
    async fn create_friend_request(
        &self,
        command: CreateFriendRequestCommand,
    ) -> Result<FriendRequest, FriendUseCaseError> {
        self.ensure_target_actor_not_private(&command.to_bot)
            .await?;
        self.friend_request
            .create_request(&command.caller_actor_id, &command.to_bot)
            .await
            .map_err(FriendUseCaseError::service)
    }

    async fn list_friend_requests(
        &self,
        command: ListFriendRequestsCommand,
    ) -> Result<Vec<FriendRequest>, FriendUseCaseError> {
        Ok(self
            .friend_request
            .list_requests(
                &command.caller_actor_id,
                command.direction,
                command.status_filter,
            )
            .await)
    }

    async fn accept_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        self.authorize_request_receiver(&command, "accept").await?;
        self.friend_request
            .accept_request(&command.request_id)
            .await
            .map_err(FriendUseCaseError::service)
    }

    async fn reject_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        self.authorize_request_receiver(&command, "reject").await?;
        self.friend_request
            .reject_request(&command.request_id)
            .await
            .map_err(FriendUseCaseError::service)
    }

    async fn friend_request_receiver(
        &self,
        request_id: &str,
    ) -> Result<String, FriendUseCaseError> {
        self.request_receiver_actor(request_id).await
    }

    async fn list_friends(
        &self,
        command: ListFriendsCommand,
    ) -> Result<Vec<FriendListEntry>, FriendUseCaseError> {
        self.check_self_or_owner_access(&command.caller_actor_id, &command.target_actor_id)
            .await?;

        let friend_uuids = if command.target_actor_id.starts_with("human_") {
            let env = bcs_config::resolve_env_str();
            self.relation
                .list_friends_via_relation(&command.target_actor_id, &env)
                .await
                .unwrap_or_default()
        } else {
            self.friend.list_friends(&command.target_actor_id).await
        };

        let mut friends = Vec::with_capacity(friend_uuids.len());
        for uuid in friend_uuids {
            let (name, summary, is_online) = if let Some(bot) = self.registry.get(&uuid).await {
                if bot.actor_kind == ActorKind::Human {
                    continue;
                }
                let is_online = self.registry.is_effectively_online(&uuid).await;
                (bot.capabilities.name, bot.capabilities.summary, is_online)
            } else {
                (None, None, false)
            };
            let dynamic_status = DynamicStatusResponse {
                status: if is_online { "active" } else { "offline" }.to_string(),
            };
            friends.push(FriendListEntry {
                bot_uuid: uuid,
                name,
                summary,
                is_online,
                dynamic_status,
            });
        }

        Ok(friends)
    }
}
