pub mod a2a_chat;
pub mod bot_event;
pub mod group_flow;
pub mod group_fusion;
pub mod group_history;
pub mod message_delivery;
pub mod managed_delivery;
pub mod queued_payload;
pub mod queued_group;
pub mod queued_admission;
pub mod queued_system;
mod queued_task;
mod queued_task_terminal;
mod task_failure;
mod run_reply;
mod reply_timing;
mod storage_retry;
pub mod delivery_runtime;
pub mod delivery_policy;
pub mod delivery_notifications;
mod delivery_failure_notice;
mod delivery_control;
mod delivery_abort;
pub(crate) mod human_notify_hook;
mod pending_message;
pub(crate) mod protocol_context;
pub(crate) mod message_tracker;
pub mod run_context;
pub mod run_context_redis;
pub mod task_flow;
pub mod task_store;

#[cfg(test)]
pub mod test_fakes;

pub(crate) const MSG_LOG_TARGET: &str = "bcs_message";

pub(crate) fn bot_event_actor(bot_id: &str) -> bcs_service_api::types::EventActor {
    bcs_service_api::types::EventActor {
        actor_type: bcs_service_api::types::EventActorType::Bot,
        id: bot_id.to_string(),
        display_name: None,
    }
}

/// Verified-caller §12.5 operation context for the externally initiated
/// message-control lanes (plan Task 12 fix round): a Human caller keeps its
/// trusted staff number and human actor as effective actor, a Bot-only
/// caller its verified Bot id, and the non-principal integration/admin/public
/// identities map to their own honest operator (never a forged Human).
pub(crate) fn caller_operation_context(
    caller: &bcs_service_api::CallerContext,
    label: &str,
) -> bcs_service_api::types::BotOperationContext {
    use bcs_service_api::CallerContext;
    use bcs_service_api::types::{BotOperationActor, BotOperationContext};
    let actor = match caller {
        CallerContext::Human(human) => BotOperationActor::Human {
            user_id: human.staff_no.clone(),
            effective_actor_id: human.actor_id.clone(),
        },
        CallerContext::Bot(bot) => BotOperationActor::Bot {
            bot_id: bot.bot_uuid.clone(),
        },
        CallerContext::Integration(client) => BotOperationActor::System {
            system_id: format!("integration:{}", client.client_id),
            effective_actor_id: format!("integration:{}", client.client_id),
        },
        CallerContext::Admin(admin) => BotOperationActor::System {
            system_id: format!("admin:{}", admin.actor_id),
            effective_actor_id: format!("admin:{}", admin.actor_id),
        },
        CallerContext::Public => BotOperationActor::System {
            system_id: "public".to_string(),
            effective_actor_id: "public".to_string(),
        },
    };
    BotOperationContext {
        operation_id: format!("message-control-{label}:{}", uuid::Uuid::new_v4()),
        actor,
    }
}

pub(crate) fn caller_event_actor(
    caller: &bcs_service_api::CallerContext,
) -> bcs_service_api::types::EventActor {
    use bcs_service_api::CallerContext;
    use bcs_service_api::types::EventActorType;

    let (actor_type, id) = match caller {
        CallerContext::Human(human) => (EventActorType::Human, human.actor_id.clone()),
        CallerContext::Bot(bot) => (EventActorType::Bot, bot.bot_uuid.clone()),
        CallerContext::Integration(integration) => {
            (EventActorType::App, integration.client_id.clone())
        }
        CallerContext::Admin(admin) => (EventActorType::App, admin.actor_id.clone()),
        CallerContext::Public => (EventActorType::System, "public".to_string()),
    };
    bcs_service_api::types::EventActor {
        actor_type,
        id,
        display_name: None,
    }
}

pub(crate) async fn update_group_status(
    group: &dyn bcs_service_api::GroupCoreService,
    group_id: &str,
    status: bcs_service_api::GroupStatus,
    reason: impl Into<String>,
    actor: bcs_service_api::types::EventActor,
) -> bcs_service_api::ServiceResult<()> {
    let operation = bcs_service_api::types::operation_context_from_event_actor(
        uuid::Uuid::new_v4().to_string(),
        // The transport-neutral event actor the delivery boundary already
        // authenticated (spec §12.5).
        &actor,
    );
    group
        .mutate(bcs_service_api::core::GroupMutationCommand {
            group_id: group_id.to_string(),
            actor,
            correlation_id: None,
            trace_id: None,
            mutation: bcs_service_api::core::GroupMutationKind::UpdateStatus {
                status,
                reason: reason.into(),
            },
            operation: operation.clone(),
        })
        .await
        .map(|_| ())
}

pub use a2a_chat::A2aChat;
pub use group_flow::BcsMessageFlow;
pub use group_fusion::BcsGroupFusion;
pub use group_history::BcsGroupMessageHistory;
pub use bcs_service_api::ProviderStreamGrayList;
pub use run_context::MemoryBotRunContextStore;
pub use run_context_redis::RedisBotRunContextStore;
