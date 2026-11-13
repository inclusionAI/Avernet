//! Legacy list projection; edge permission is the only membership authority.
use super::*;
use bcs_service_api::{ActorKind, ActorStatus, DynamicStatusResponse};
use bcs_service_api::port::repo::edge_grant::FriendListQuery;

impl EdgeConnectFriendService {
    pub(super) async fn list_edge_friends(
        &self,
        command: ListFriendsCommand,
    ) -> Result<Vec<FriendListEntry>, FriendUseCaseError> {
        // Preserve the old list's self-only policy and hidden-target error.
        // Ownership authorization, where supported, is resolved by the caller.
        if command.caller_actor_id != command.target_actor_id {
            self.ensure_target_actor_not_private(&command.target_actor_id).await?;
            return Err(FriendUseCaseError::Forbidden(
                "Not authorized to access this bot's friends".to_string(),
            ));
        }

        let mut query = FriendListQuery {
            target_type: Some(ActorKind::Bot), offset: 0, limit: 100,
        };
        let mut page = self.connect.list_friends_paginated(&command.target_actor_id, query)
            .await.map_err(FriendUseCaseError::service)?;
        // The legacy wire response is unpaged. Bound this invocation by its
        // initial total, so concurrent additions cannot keep the loop running.
        let total = page.total;
        let mut friends = Vec::new();
        loop {
            if page.items.is_empty() {
                break;
            }
            let ids = page.items.into_iter().map(|entry| entry.actor_id).collect::<Vec<_>>();
            // Strict, batched registry read (at most 100 peers). Unknown/deleted
            // peers are omitted; storage errors must not masquerade as deletion.
            let bots = self.registry.try_get_many(&ids).await.map_err(FriendUseCaseError::service)?;
            let online_candidates = bots.iter()
                .filter(|bot| bot.actor_kind == ActorKind::Bot && bot.status == ActorStatus::Online)
                .map(|bot| bot.bot_uuid.clone()).collect::<Vec<_>>();
            let active = if online_candidates.is_empty() {
                std::collections::HashSet::new()
            } else {
                self.registry.try_runtime_activity_for(&online_candidates).await
                    .map_err(FriendUseCaseError::service)?.active_bot_ids
            };
            for bot in bots {
                if bot.actor_kind == ActorKind::Human {
                    continue;
                }
                // Effective online = Online lifecycle AND a live WS/routable
                // Provider transport. Batch observation avoids per-peer DB reads.
                let is_online = bot.status == ActorStatus::Online && active.contains(&bot.bot_uuid);
                friends.push(FriendListEntry {
                    bot_uuid: bot.bot_uuid,
                    name: bot.capabilities.name,
                    summary: bot.capabilities.summary,
                    is_online,
                    dynamic_status: DynamicStatusResponse {
                        status: if is_online { "active" } else { "offline" }.to_string(),
                    },
                });
            }
            query.offset += u64::from(query.limit);
            if query.offset >= total {
                break;
            }
            page = self.connect.list_friends_paginated(&command.target_actor_id, query)
                .await.map_err(FriendUseCaseError::service)?;
        }
        Ok(friends)
    }
}
