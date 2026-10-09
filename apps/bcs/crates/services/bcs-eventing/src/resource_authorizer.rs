//! Event Subscription authorization over existing BCS resource services.

use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{ApplicationError, AuthenticatedCaller, BotAuthorityHook};
use bcs_service_api::types::{
    EventActor, EventActorType, EventSubscriptionScope, EventSubscriptionScopeType,
};
use bcs_service_api::{ActorKind, BotRegistryCoreService, GroupCoreService, GroupStrategy, ParticipantRole};

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
    /// Live Human→Bot authority (spec §12.2/§12.4, final-review cutover):
    /// the Human side of scope management resolves through the live
    /// owner/manager role facts — never the historical `created_by`
    /// creation listing.
    authority: Arc<dyn BotAuthorityHook>,
}

impl CoreEventSubscriptionAuthorizer {
    pub fn new(
        groups: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        authority: Arc<dyn BotAuthorityHook>,
    ) -> Self {
        Self {
            groups,
            registry,
            authority,
        }
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
        let candidates = actor_ids.iter().cloned().collect::<HashSet<_>>();
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

        // Final-review cutover (spec §12.2/§12.4): a Human caller may act for a
        // candidate they CURRENTLY own or manage — the live authority facts
        // per candidate Bot, never the `list_bots_by_creator` historical
        // creation listing. A hook failure DENIES that candidate
        // (fail-closed): it never grants and never falls back.
        for candidate in actor_ids {
            let Some(bot) = self
                .registry
                .try_get(&candidate)
                .await
                .map_err(|error| ApplicationError::internal(error.to_string()))?
            else {
                continue;
            };
            if bot.actor_kind != ActorKind::Bot {
                continue;
            }
            match self.authority.can_manage(&user.id, &bot.bot_uuid).await {
                Ok(true) => {
                    return Ok(EventActor {
                        actor_type: EventActorType::Bot,
                        id: bot.bot_uuid.clone(),
                        display_name: bot.capabilities.name,
                    })
                }
                Ok(false) => {}
                Err(error) => {
                    tracing::warn!(
                        user_id = %user.id,
                        bot_id = %bot.bot_uuid,
                        error = %error,
                        "event subscription authorization: live control lookup failed; denying"
                    );
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
        CoreEventSubscriptionAuthorizer::new(
            groups,
            Arc::new(NoopBotRegistryCoreService),
            // Noop hook denies — fail-closed, exactly as an unwired test
            // assembly must.
            Arc::new(bcs_service_api::application::v1::NoopBotAuthorityHook),
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

// ---------------------------------------------------------------------------
// Final-review cutover tests (spec §12.2/§12.4): the Human side of scope
// management resolves through the LIVE authority facts, never created_by.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod cutover_tests {
    #![allow(clippy::expect_used)]

    use std::collections::HashSet;
    use std::sync::Mutex;

    use super::*;
    use bcs_group::GroupCore;
    use bcs_service_api::types::EventSubscriptionScopeType;
    use bcs_bot::BotCore;
    use bcs_service_api::{BotCapabilities, Group};

    fn caller(user_id: &str) -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: Some("tenant-a".to_string()),
            user: Some(bcs_service_api::application::v1::AuthenticatedUserIdentity {
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

/// Map-backed LIVE authority double: answers ONLY from seeded control
/// facts — never from any `created_by` value — mirroring the V1 facade
/// parity fixtures.
struct SeededAuthority {
    grants: Mutex<HashSet<(String, String)>>,
}

impl SeededAuthority {
    fn control(&self, user_id: &str, bot_id: &str) {
        self.grants
            .lock()
            .unwrap()
            .insert((user_id.to_string(), bot_id.to_string()));
    }
}

#[async_trait]
impl BotAuthorityHook for SeededAuthority {
    async fn can_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        Ok(self
            .grants
            .lock()
            .unwrap()
            .contains(&(user_id.to_string(), bot_id.to_string())))
    }

    async fn require_owner(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        Err(bcs_service_api::ServiceError::Forbidden(format!(
            "user '{user_id}' is not the owner of bot '{bot_id}'"
        )))
    }
}

async fn register_driver_bot(bots: &BotCore, bot_id: &str, staff_no: &str) {
    use bcs_service_api::{BotCapabilities, BotRegistryCoreService};
    bots.register(
        bot_id.to_string(),
        BotCapabilities {
            name: Some(bot_id.to_string()),
            ..BotCapabilities::default()
        },
    )
    .await
    .expect("register bot");
    bots.save_created_by(bot_id, staff_no, true)
        .await
        .expect("record the historical creator");
}

#[tokio::test]
async fn former_creator_of_driver_cannot_manage_event_scope() {
    // After a completed ownership transfer the historical `created_by` of
    // the driver no longer authorizes Human scope management (over-grant
    // retired by the final-review cutover).
    let groups = Arc::new(GroupCore::memory());
    groups
        .upsert(Group::new("group-2", "driver-bot", Vec::new()))
        .await
        .expect("store group");
    let bots = Arc::new(BotCore::memory());
    register_driver_bot(&bots, "driver-bot", "staff-9").await;
    let authority = Arc::new(SeededAuthority {
        grants: Mutex::new(HashSet::new()),
    });
    // `staff-9` has NO live control over the driver.

    let authorizer = CoreEventSubscriptionAuthorizer::new(groups, bots, authority);
    let error = authorizer
        .authorize(
            &caller("staff-9"),
            &EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: "group-2".to_string(),
            },
            EventSubscriptionAuthorizationAction::Create,
        )
        .await
        .expect_err("the historical creator without live control is denied");
    assert!(
        matches!(
            error,
            ApplicationError::Forbidden(_) | ApplicationError::ForbiddenCode { .. }
        ),
        "expected a forbidden branch, got {error:?}"
    );
}

#[tokio::test]
async fn live_manager_of_driver_can_manage_event_scope() {
    // A legitimate MANAGER of the driver Bot — with no historical creation
    // fact on the group actors — authorizes scope management (AC08).
    let groups = Arc::new(GroupCore::memory());
    groups
        .upsert(Group::new("group-3", "driver-bot", Vec::new()))
        .await
        .expect("store group");
    let bots = Arc::new(BotCore::memory());
    register_driver_bot(&bots, "driver-bot", "somebody-else").await;
    let authority = Arc::new(SeededAuthority {
        grants: Mutex::new(HashSet::new()),
    });
    authority.control("staff-9", "driver-bot");

    let authorizer = CoreEventSubscriptionAuthorizer::new(groups, bots, authority);
    let grant = authorizer
        .authorize(
            &caller("staff-9"),
            &EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: "group-3".to_string(),
            },
            EventSubscriptionAuthorizationAction::Create,
        )
        .await
        .expect("a live manager of the driver may manage the scope");
    assert_eq!(grant.actor.actor_type, EventActorType::Bot);
    assert_eq!(grant.actor.id, "driver-bot");
}

}
