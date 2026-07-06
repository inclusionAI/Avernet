use std::sync::Arc;

use async_trait::async_trait;
use bcs_friend_store::MemoryFriendRequestRepo;
use bcs_service_api::{
    ActorKind, BotRegistryCoreService, FriendCoreService, FriendRequest, FriendRequestCoreService,
    FriendRequestDirection, FriendRequestRepoPort, FriendRequestStatus, ServiceError, ServiceResult,
};
use tracing::{info, warn};

/// Core friend-request service implementation.
///
/// `FriendRequestCore` owns request workflow rules and delegates persistence to
/// a repository.
#[derive(Clone)]
pub struct FriendRequestCore {
    repo: Arc<dyn FriendRequestRepoPort>,
    friend_service: Arc<dyn FriendCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
}

impl FriendRequestCore {
    pub fn new(
        friend_service: Arc<dyn FriendCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
    ) -> Self {
        Self::with_repo(
            Arc::new(MemoryFriendRequestRepo::new()),
            friend_service,
            registry,
        )
    }

    pub fn with_repo(
        repo: Arc<dyn FriendRequestRepoPort>,
        friend_service: Arc<dyn FriendCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
    ) -> Self {
        Self {
            repo,
            friend_service,
            registry,
        }
    }

    pub fn memory(
        friend_service: Arc<dyn FriendCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
    ) -> Self {
        Self::new(friend_service, registry)
    }

    async fn reject_human_human(&self, from_bot: &str, to_bot: &str) -> ServiceResult<()> {
        let from_kind = self.registry.get(from_bot).await.map(|b| b.actor_kind);
        let to_kind = self.registry.get(to_bot).await.map(|b| b.actor_kind);
        if matches!(
            (from_kind, to_kind),
            (Some(ActorKind::Human), Some(ActorKind::Human))
        ) {
            return Err(ServiceError::InvalidOperation {
                message: "不支持用户之间添加好友".to_string(),
                request_id: None,
            });
        }
        Ok(())
    }
}

#[async_trait]
impl FriendRequestCoreService for FriendRequestCore {
    async fn create_request(&self, from_bot: &str, to_bot: &str) -> ServiceResult<FriendRequest> {
        if from_bot == to_bot {
            return Err(ServiceError::CannotAddSelf);
        }

        let target = self
            .registry
            .get(to_bot)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(to_bot.to_string()))?;

        if target.capabilities.visibility == "private" {
            return Err(ServiceError::Unauthorized("对方未开放好友申请".to_string()));
        }

        self.reject_human_human(from_bot, to_bot).await?;

        if self.friend_service.are_friends(from_bot, to_bot).await {
            let now = now_millis();
            return Ok(FriendRequest {
                id: String::new(),
                from_bot: from_bot.to_string(),
                to_bot: to_bot.to_string(),
                status: FriendRequestStatus::Accepted,
                created_at: now,
                updated_at: now,
            });
        }

        if target.capabilities.visibility == "public" {
            self.friend_service.add_friendship(from_bot, to_bot).await?;
            let now = now_millis();
            let request = FriendRequest {
                id: uuid::Uuid::new_v4().to_string(),
                from_bot: from_bot.to_string(),
                to_bot: to_bot.to_string(),
                status: FriendRequestStatus::Accepted,
                created_at: now,
                updated_at: now,
            };
            let request = self.repo.insert_accepted_request_if_absent(request).await?;
            // Accept any reverse pending request (B→A pending) to avoid contradictory state.
            if let Err(err) = self.repo.accept_reverse_pending_requests(from_bot, to_bot).await {
                warn!(from = %from_bot, to = %to_bot, error = %err, "Failed to accept reverse pending request during public auto-accept");
            }
            info!(from = %from_bot, to = %to_bot, "F.8: visibility=public auto-accepted friend request");
            return Ok(request);
        }

        let now = now_millis();
        let request = FriendRequest {
            id: uuid::Uuid::new_v4().to_string(),
            from_bot: from_bot.to_string(),
            to_bot: to_bot.to_string(),
            status: FriendRequestStatus::Pending,
            created_at: now,
            updated_at: now,
        };

        if let Some(existing) = self
            .repo
            .insert_pending_request_if_absent(request.clone())
            .await?
        {
            return Err(ServiceError::PendingRequestExists {
                request_id: existing.id,
                from_bot: Some(from_bot.to_string()),
                to_bot: Some(to_bot.to_string()),
            });
        }

        info!(request_id = %request.id, from = %from_bot, to = %to_bot, "Friend request created");
        Ok(request)
    }

    async fn accept_request(&self, request_id: &str) -> ServiceResult<()> {
        let request = self.repo.get_request(request_id).await?;

        match request.status {
            FriendRequestStatus::Accepted => return Ok(()),
            FriendRequestStatus::Rejected => return Err(ServiceError::CannotAcceptRejected),
            FriendRequestStatus::Pending => {}
        }

        self.reject_human_human(&request.from_bot, &request.to_bot)
            .await?;

        self.repo
            .update_request_status(request_id, FriendRequestStatus::Accepted)
            .await?;

        match self
            .repo
            .accept_reverse_pending_requests(&request.from_bot, &request.to_bot)
            .await
        {
            Ok(affected) if affected > 0 => {
                info!(from = %request.to_bot, to = %request.from_bot, accepted = affected, "Auto-accepted reverse pending request");
            }
            Ok(_) => {}
            Err(err) => {
                warn!(from = %request.to_bot, to = %request.from_bot, error = %err, "Failed to auto-accept reverse pending request; B can manually accept later for consistency");
            }
        }

        self.friend_service
            .add_friendship(&request.from_bot, &request.to_bot)
            .await?;

        info!(request_id = %request_id, from = %request.from_bot, to = %request.to_bot, "Friend request accepted");
        Ok(())
    }

    async fn reject_request(&self, request_id: &str) -> ServiceResult<()> {
        let request = self.repo.get_request(request_id).await?;

        match request.status {
            FriendRequestStatus::Rejected => return Ok(()),
            FriendRequestStatus::Accepted => return Err(ServiceError::CannotRejectAccepted),
            FriendRequestStatus::Pending => {}
        }

        self.repo
            .update_request_status(request_id, FriendRequestStatus::Rejected)
            .await?;

        info!(request_id = %request_id, from = %request.from_bot, to = %request.to_bot, "Friend request rejected");
        Ok(())
    }

    async fn get_request(&self, request_id: &str) -> ServiceResult<FriendRequest> {
        self.repo.get_request(request_id).await
    }

    async fn list_requests(
        &self,
        bot_id: &str,
        direction: FriendRequestDirection,
        status_filter: Option<FriendRequestStatus>,
    ) -> Vec<FriendRequest> {
        self.repo
            .list_requests(bot_id, direction, status_filter)
            .await
    }

    async fn cancel_pending_requests(&self, bot_id: &str) -> ServiceResult<usize> {
        self.repo.delete_pending_requests_for_bot(bot_id).await
    }
}

fn now_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
