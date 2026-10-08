//! Event Subscription authorization over existing BCS resource services.

use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{ApplicationError, AuthenticatedCaller};
use bcs_service_api::types::{
    EventActor, EventActorType, EventSubscriptionScope, EventSubscriptionScopeType,
};
use bcs_service_api::{BotRegistryCoreService, GroupCoreService, GroupStrategy, ParticipantRole};

use crate::{
    AuthorizedEventSubscriptionScope, EventSubscriptionAuthorizationAction,
    EventSubscriptionAuthorizer,
};

/// Authorizes scopes backed by an existing durable BCS authority chain.
///
/// MVP management is deliberately limited to Group scope. Child resource
/// Events are selected through the Group's descendant stream hierarchy.
pub struct CoreEventSubscriptionAuthorizer {
    groups: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
}

impl CoreEventSubscriptionAuthorizer {
    pub fn new(
        groups: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
    ) -> Self {
        Self { groups, registry }
    }

    async fn group_management_actor_ids(
        &self,
        group_id: &str,
    ) -> Result<Vec<String>, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(|error| ApplicationError::internal(error.to_string()))?
            .ok_or_else(|| {
                ApplicationError::event_subscription_not_found(
                    "Event Subscription scope was not found",
                )
            })?;
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
        actor_ids.sort();
        actor_ids.dedup();
        Ok(actor_ids)
    }

    async fn authorized_actor(
        &self,
        caller: &AuthenticatedCaller,
        actor_ids: Vec<String>,
    ) -> Result<EventActor, ApplicationError> {
        let candidates = actor_ids.into_iter().collect::<HashSet<_>>();
        if let Some(bot) = caller.bot.as_ref() {
            if caller
                .user
                .as_ref()
                .is_some_and(|user| user.id != bot.owner_id)
            {
                return Err(ApplicationError::event_subscription_forbidden(
                    "The authenticated Bot is not owned by the authenticated User",
                ));
            }
            if candidates.contains(&bot.bot_uuid) {
                return Ok(EventActor {
                    actor_type: EventActorType::Bot,
                    id: bot.bot_uuid.clone(),
                    display_name: None,
                });
            }
        }

        let Some(user) = caller.user.as_ref() else {
            return Err(ApplicationError::event_subscription_forbidden(
                "Event Subscription management requires a Human or authorized Bot",
            ));
        };
        let human_actor_id = format!("human_{}", user.id);
        if candidates.contains(&human_actor_id) || candidates.contains(&user.id) {
            return Ok(EventActor {
                actor_type: EventActorType::Human,
                id: human_actor_id,
                display_name: user.display_name.clone().or_else(|| user.full_name.clone()),
            });
        }

        let owned_bots = self
            .registry
            .try_list_bots_by_creator(&user.id)
            .await
            .map_err(|error| ApplicationError::internal(error.to_string()))?;
        if let Some(bot) = owned_bots
            .into_iter()
            .find(|bot| candidates.contains(&bot.bot_uuid))
        {
            return Ok(EventActor {
                actor_type: EventActorType::Bot,
                id: bot.bot_uuid,
                display_name: bot.capabilities.name,
            });
        }

        Err(ApplicationError::event_subscription_forbidden(
            "Caller cannot manage the Event Subscription scope",
        ))
    }
}

#[async_trait]
impl EventSubscriptionAuthorizer for CoreEventSubscriptionAuthorizer {
    async fn authorize(
        &self,
        caller: &AuthenticatedCaller,
        scope: &EventSubscriptionScope,
        _action: EventSubscriptionAuthorizationAction,
    ) -> Result<AuthorizedEventSubscriptionScope, ApplicationError> {
        let actor_ids = match scope.scope_type {
            EventSubscriptionScopeType::Group => self.group_management_actor_ids(&scope.id).await?,
        };
        let actor = self.authorized_actor(caller, actor_ids).await?;
        Ok(AuthorizedEventSubscriptionScope {
            actor,
            full_payload_allowed: true,
        })
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used)]

    use super::*;
    use bcs_group::GroupCore;
    use bcs_service_api::application::v1::AuthenticatedUserIdentity;
    use bcs_service_api::{Group, GroupCoreService};
    use bcs_test_support::NoopBotRegistryCoreService;

    fn caller(user_id: &str) -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: Some("tenant-a".to_string()),
            user: Some(AuthenticatedUserIdentity {
                id: user_id.to_string(),
                username: user_id.to_string(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    fn authorizer(groups: Arc<dyn GroupCoreService>) -> CoreEventSubscriptionAuthorizer {
        CoreEventSubscriptionAuthorizer::new(groups, Arc::new(NoopBotRegistryCoreService))
    }

    #[tokio::test]
    async fn group_driver_human_is_authorized_with_server_derived_actor() {
        let groups = Arc::new(GroupCore::memory());
        groups
            .upsert(Group::new("group-1", "human_user-1", Vec::new()))
            .await
            .expect("store group");

        let grant = authorizer(groups)
            .authorize(
                &caller("user-1"),
                &EventSubscriptionScope {
                    scope_type: EventSubscriptionScopeType::Group,
                    id: "group-1".to_string(),
                },
                EventSubscriptionAuthorizationAction::Create,
            )
            .await
            .expect("group driver authorization");

        assert_eq!(grant.actor.actor_type, EventActorType::Human);
        assert_eq!(grant.actor.id, "human_user-1");
        assert!(grant.full_payload_allowed);
    }
}
