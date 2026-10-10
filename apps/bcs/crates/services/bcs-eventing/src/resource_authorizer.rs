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
            let bot_is_candidate = candidates.contains(&bot.bot_uuid);
            match caller.user.as_ref() {
                // Live-authority cutover for MIXED Human+Bot callers (spec
                // §12.2/§12.4, review finding F7): the signed `owner_id`
                // claim NEVER decides here — neither to reject nor to
                // grant. The Bot may act for its own candidate slot only
                // while the LIVE authority hook confirms the verified Human
                // CURRENTLY manages it (`can_manage`, exactly like the
                // per-candidate loop below); `Ok(false)`
                // and hook failures deny that slot fail-closed (warn on
                // failure) and fall through so the Human candidate branch
                // and the live-authority loop below stay reachable — e.g. a
                // current owner behind a stale claim, or another Bot under
                // the caller's live control.
                Some(user) => {
                    if bot_is_candidate {
                        match self.authority.can_manage(&user.id, &bot.bot_uuid).await {
                            Ok(true) => {
                                return Ok(EventActor {
                                    actor_type: EventActorType::Bot,
                                    id: bot.bot_uuid.clone(),
                                    display_name: None,
                                });
                            }
                            Ok(false) => {}
                            Err(error) => {
                                tracing::warn!(
                                    user_id = %user.id,
                                    bot_id = %bot.bot_uuid,
                                    error = %error,
                                    "event subscription authorization: live control lookup failed; denying the caller Bot candidate"
                                );
                            }
                        }
                    }
                }
                // Bot-only callers keep the established behaviour: the
                // authenticated Bot may act for its own candidate slot.
                None => {
                    if bot_is_candidate {
                        return Ok(EventActor {
                            actor_type: EventActorType::Bot,
                            id: bot.bot_uuid.clone(),
                            display_name: None,
                        });
                    }
                }
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

/// Hook double whose live control lookups always FAIL (corrupt or
/// unavailable authority) — never a plain deny — exercising the
/// fail-closed `Err` branch of the mixed-caller path.
struct FailingAuthority;

#[async_trait]
impl BotAuthorityHook for FailingAuthority {
    async fn can_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        Err(bcs_service_api::ServiceError::InternalError(format!(
            "authority lookup failed for user '{user_id}' and bot '{bot_id}'"
        )))
    }

    async fn require_owner(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        Err(bcs_service_api::ServiceError::InternalError(format!(
            "authority lookup failed for user '{user_id}' and bot '{bot_id}'"
        )))
    }
}

#[tokio::test]
async fn mixed_caller_hook_failure_denies_the_bot_slot_fail_closed() {
    // The live control lookup itself FAILS (corrupt/unavailable authority):
    // the Bot candidate slot is denied fail-closed (warn + fall-through)
    // and, with no other reachable candidate, authorize denies.
    let groups = Arc::new(GroupCore::memory());
    groups
        .upsert(Group::new("group-f7-hook-error", "bot-x", Vec::new()))
        .await
        .expect("store group");
    let bots = Arc::new(BotCore::memory());
    register_driver_bot(&bots, "bot-x", "alice").await;

    let authorizer = CoreEventSubscriptionAuthorizer::new(groups, bots, Arc::new(FailingAuthority));
    let error = authorizer
        .authorize(
            &mixed_caller("alice", "bot-x", "alice"),
            &EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: "group-f7-hook-error".to_string(),
            },
            EventSubscriptionAuthorizationAction::Create,
        )
        .await
        .expect_err("a hook failure denies the Bot candidate slot fail-closed");
    assert!(
        matches!(
            error,
            ApplicationError::Forbidden(_) | ApplicationError::ForbiddenCode { .. }
        ),
        "expected a forbidden branch, got {error:?}"
    );
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

fn mixed_caller(user_id: &str, bot_uuid: &str, owner_id: &str) -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: Some("tenant-a".to_string()),
            user: Some(bcs_service_api::application::v1::AuthenticatedUserIdentity {
                id: user_id.to_string(),
                username: user_id.to_string(),
                display_name: None,
                full_name: None,
            }),
            bot: Some(bcs_service_api::application::v1::AuthenticatedBotIdentity {
                bot_uuid: bot_uuid.to_string(),
                owner_id: owner_id.to_string(),
                app_id: 7,
                agent_code: format!("agent-{bot_uuid}"),
            }),
            app: None,
            access_key: None,
        }
    }

#[tokio::test]
async fn mixed_caller_with_revoked_live_control_cannot_reclaim_the_bot_slot() {
    // Reviewer F7 repro: after the Bot was transferred away from the caller,
    // the SIGNED `owner_id` claim still happens to match the user id, but
    // the LIVE authority revoked the caller's control. The stale claim must
    // never grant the Bot actor (full payload) on its own — the live
    // authority decides.
    let groups = Arc::new(GroupCore::memory());
    groups
        .upsert(Group::new("group-f7-revoked", "bot-x", Vec::new()))
        .await
        .expect("store group");
    let bots = Arc::new(BotCore::memory());
    register_driver_bot(&bots, "bot-x", "alice").await;
    let authority = Arc::new(SeededAuthority {
        grants: Mutex::new(HashSet::new()),
    });
    // `alice` holds NO live control over `bot-x` (manager role revoked).

    let authorizer = CoreEventSubscriptionAuthorizer::new(groups, bots, authority);
    let error = authorizer
        .authorize(
            &mixed_caller("alice", "bot-x", "alice"),
            &EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: "group-f7-revoked".to_string(),
            },
            EventSubscriptionAuthorizationAction::Create,
        )
        .await
        .expect_err("the stale matching claim without live control is denied");
    assert!(
        matches!(
            error,
            ApplicationError::Forbidden(_) | ApplicationError::ForbiddenCode { .. }
        ),
        "expected a forbidden branch, got {error:?}"
    );
}

#[tokio::test]
async fn mixed_current_owner_with_stale_claim_acts_for_the_bot_via_live_control() {
    // Mirror of the F8 parity requirement: a CURRENT owner whose SIGNED claim
    // is STALE (mismatched `owner_id`) must still authorize — the live
    // authority, never the claim, decides. No early stale-claim rejection.
    let groups = Arc::new(GroupCore::memory());
    groups
        .upsert(Group::new("group-f7-stale-claim", "bot-x", Vec::new()))
        .await
        .expect("store group");
    let bots = Arc::new(BotCore::memory());
    register_driver_bot(&bots, "bot-x", "somebody-else").await;
    let authority = Arc::new(SeededAuthority {
        grants: Mutex::new(HashSet::new()),
    });
    authority.control("alice", "bot-x");

    let authorizer = CoreEventSubscriptionAuthorizer::new(groups, bots, authority);
    let grant = authorizer
        .authorize(
            &mixed_caller("alice", "bot-x", "bob"),
            &EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: "group-f7-stale-claim".to_string(),
            },
            EventSubscriptionAuthorizationAction::Create,
        )
        .await
        .expect("a current live owner with a stale claim may manage the scope");
    assert_eq!(grant.actor.actor_type, EventActorType::Bot);
    assert_eq!(grant.actor.id, "bot-x");
    assert!(grant.full_payload_allowed);
}

#[tokio::test]
async fn mixed_caller_with_stale_claim_still_reaches_the_human_candidate_slot() {
    // No early stale-claim rejection (F7): when the caller's `human_` id is
    // a candidate — even while the caller's Bot slot is live-denied — the
    // Human actor branch must stay reachable.
    let groups = Arc::new(GroupCore::memory());
    let mut group = Group::new("group-f7-human-slot", "bot-x", Vec::new());
    group.originator = Some("human_alice".to_string());
    groups
        .upsert(group)
        .await
        .expect("store group");
    let bots = Arc::new(BotCore::memory());
    register_driver_bot(&bots, "bot-x", "somebody-else").await;
    let authority = Arc::new(SeededAuthority {
        grants: Mutex::new(HashSet::new()),
    });
    // No live control anywhere: the caller's Bot slot is denied fall-through.

    let authorizer = CoreEventSubscriptionAuthorizer::new(groups, bots, authority);
    let grant = authorizer
        .authorize(
            &mixed_caller("alice", "bot-x", "bob"),
            &EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: "group-f7-human-slot".to_string(),
            },
            EventSubscriptionAuthorizationAction::Create,
        )
        .await
        .expect("the Human candidate slot stays reachable behind a stale Bot claim");
    assert_eq!(grant.actor.actor_type, EventActorType::Human);
    assert_eq!(grant.actor.id, "human_alice");
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
