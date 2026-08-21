pub mod a2a_chat;
pub mod bot_event;
pub mod group_flow;
pub mod group_fusion;
pub mod group_history;
pub(crate) mod protocol_context;
pub(crate) mod message_tracker;
pub mod run_context;
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
