//! Event Subscription authorization over existing BCS resource services.

use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{ApplicationError, AuthenticatedCaller};
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::types::{
    EventActor, EventActorType, EventSubscriptionScope, EventSubscriptionScopeType,
};
use bcs_service_api::{BotRegistryCoreService, GroupCoreService};

use crate::{
    AuthorizedEventSubscriptionScope, EventSubscriptionAuthorizationAction,
    EventSubscriptionAuthorizer,
};

/// Authorizes scopes backed by an existing durable BCS authority chain.
///
/// MVP management is deliberately limited to Group scope. Child resource
/// Events are selected through the Group's descendant stream hierarchy.
///
/// The resource leg reads the CURRENT Group management edge through
/// the strict authorization snapshot (bot-authority plan §8.3); the
/// Human→Bot judge leg consults the live `BotAuthorityPort`. The
/// legacy `list_bots_by_creator` / `bot.owner_id` probes are gone.
pub struct CoreEventSubscriptionAuthorizer {
    groups: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    bot_authority: Arc<dyn BotAuthorityPort>,
}

impl CoreEventSubscriptionAuthorizer {
    pub fn new(
        groups: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        bot_authority: Arc<dyn BotAuthorityPort>,
    ) -> Self {
        Self {
            groups,
            registry,
            bot_authority,
        }
    }

    async fn group_management_actor_ids(
        &self,
        group_id: &str,
        caller: &AuthenticatedCaller,
    ) -> Result<Vec<String>, ApplicationError> {
        let snapshot = self
            .groups
            .read_authorization_snapshot(group_id)
            .await
            .map_err(|error| ApplicationError::internal(error.to_string()))?
            .filter(|snapshot| snapshot.live)
            .ok_or_else(|| {
                ApplicationError::event_subscription_not_found(
                    "Event Subscription scope was not found",
                )
            })?;
        let mut scope_actors = snapshot.participants.iter().map(|p| p.actor_id.clone()).collect::<Vec<_>>();
        if let Some(bot) = &caller.bot { scope_actors.push(bot.bot_uuid.clone()); }
        if let Some(user) = &caller.user { scope_actors.push(format!("human_{}", user.id)); }
        self.registry.collaboration_owner(&scope_actors).await.map_err(|error| match error {
            bcs_service_api::ServiceError::Forbidden(message) => ApplicationError::event_subscription_forbidden(message),
            other => ApplicationError::internal(other.to_string()),
        })?;
        Ok(snapshot
            .management_actor_ids()
            .into_iter()
            .map(str::to_string)
            .collect())
    }

    async fn authorized_actor(
        &self,
        caller: &AuthenticatedCaller,
        actor_ids: Vec<String>,
    ) -> Result<EventActor, ApplicationError> {
        let candidates = actor_ids.iter().map(String::as_str).collect::<HashSet<_>>();
        if let Some(bot) = caller.bot.as_ref() {
            if let Some(user) = caller.user.as_ref() {
                // Hybrid User+Bot credential: the current live
                // management edge decides (spec §8). The legacy
                // `bot.owner_id` claim comparison is gone.
                match self
                    .bot_authority
                    .can_manage(&user.id, &bot.bot_uuid)
                    .await
                {
                    Ok(true) => {}
                    Ok(false) => {
                        return Err(ApplicationError::event_subscription_forbidden(
                            "The authenticated Bot is not managed by the authenticated User",
                        ))
                    }
                    Err(error) => {
                        return Err(ApplicationError::internal(format!(
                            "bot authority lookup failed: {error}"
                        )))
                    }
                }
            }
            if candidates.contains(bot.bot_uuid.as_str()) {
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
        if candidates.contains(human_actor_id.as_str()) || candidates.contains(user.id.as_str()) {
            return Ok(EventActor {
                actor_type: EventActorType::Human,
                id: human_actor_id,
                display_name: user.display_name.clone().or_else(|| user.full_name.clone()),
            });
        }

        // The legacy `try_list_bots_by_creator` probe is
        // replaced by the live management edge over the
        // candidate Bots (spec §8.3); a failure is an
        // internal error, not a deny-as-verdict.
        for id in &actor_ids {
            if id.starts_with("human_") {
                continue;
            }
            match self.bot_authority.can_manage(&user.id, id).await {
                Ok(true) => {
                    let display_name = self.registry.get(id).await.and_then(|bot| bot.capabilities.name);
                    return Ok(EventActor {
                        actor_type: EventActorType::Bot,
                        id: id.clone(),
                        display_name,
                    });
                }
                Ok(false) => {}
                Err(error) => {
                    return Err(ApplicationError::internal(format!(
                        "bot authority lookup failed: {error}"
                    )))
                }
            }
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
            EventSubscriptionScopeType::Group => self.group_management_actor_ids(&scope.id, caller).await?,
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
        CoreEventSubscriptionAuthorizer::new(
            groups,
            Arc::new(NoopBotRegistryCoreService),
            Arc::new(bcs_test_support::NoopBotAuthorityPort),
        )
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
