use bcs_protocol as wire;
use bcs_service_api::application as app;

pub fn to_app_caller(caller: wire::CallerContext) -> app::CallerContext {
    match caller {
        wire::CallerContext::Public => app::CallerContext::Public,
        wire::CallerContext::Human(human) => app::CallerContext::Human(app::HumanActor {
            actor_id: human.actor_id,
            staff_no: human.staff_no,
        }),
        wire::CallerContext::Bot(bot) => app::CallerContext::Bot(app::BotActor {
            bot_uuid: bot.bot_uuid,
        }),
        wire::CallerContext::Integration(client) => {
            app::CallerContext::Integration(app::IntegrationClient {
                client_id: client.client_id,
                scopes: client.scopes,
            })
        }
        wire::CallerContext::Admin(admin) => app::CallerContext::Admin(app::AdminActor {
            actor_id: admin.actor_id,
            scopes: admin.scopes,
        }),
    }
}

pub fn to_app_proposal_context(
    context: Option<wire::ProposalContext>,
) -> Option<app::ProposalContext> {
    context.map(|context| app::ProposalContext {
        user_query: context.user_query,
        detected_gap: context.detected_gap,
        relevant_history: context.relevant_history,
    })
}
