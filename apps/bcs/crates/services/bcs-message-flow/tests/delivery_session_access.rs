//! `message-deliveries/query` session access: callers may
//! read through the current session's participants, and a
//! Human caller reads the session through a participating
//! Bot it currently controls — the historical `created_by`
//! whitelist was not part of this gate, and neither is a
//! bare "owner in session" claim without live control.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_service_api::MessageFlowService;

use bcs_domain::ParticipantRole;
use bcs_message_flow::BcsMessageFlow;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::Participant;
use bcs_service_api::application::message_delivery::DeliveryStatusQuery;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::types::bot_authority::BotAccessRelation;
use bcs_service_api::{
    CallerContext, HumanActor, ServiceError, ServiceResult,
};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "support/session.rs"]
mod session_support;

/// Static rule table for `(staff_no, bot_id)` control
/// judgments — stays local to this suite's authority
/// surface instead of wiring a whole store.
struct RuleTableHook {
    rules: Vec<(String, String)>,
}

#[async_trait]
impl BotAuthorityPort for RuleTableHook {
    async fn role(&self, user_id: &str, bot_id: &str) -> ServiceResult<Option<BotAccessRelation>> {
        Ok(self
            .rules
            .iter()
            .any(|(user, bot)| user == user_id && bot == bot_id)
            .then_some(BotAccessRelation::Owner))
    }

    async fn roles_for(
        &self,
        queries: &[bcs_service_api::types::bot_authority::BotRoleQuery],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        let mut page = Vec::with_capacity(queries.len());
        for query in queries {
            page.push(self.role(&query.user_id, &query.bot_id).await?);
        }
        Ok(page)
    }

    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
        Ok(self
            .rules
            .iter()
            .any(|(user, bot)| user == user_id && bot == bot_id))
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        match self.role(user_id, bot_id).await? {
            Some(BotAccessRelation::Owner) => Ok(()),
            _ => Err(ServiceError::Forbidden(format!(
                "owner role required for bot {bot_id}"
            ))),
        }
    }

    async fn require_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        match self.role(user_id, bot_id).await? {
            Some(_) => Ok(()),
            None => Err(ServiceError::Forbidden(format!(
                "owner or manager role required for bot {bot_id}"
            ))),
        }
    }
}


fn part(bot: &str) -> Participant {
    Participant::bot(bot, ParticipantRole::Observer)
}

async fn build_flow(rules: Vec<(String, String)>) -> Arc<BcsMessageFlow> {
    let fixture = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session = session_support::test_session(
        "group-1:deliveries",
        "group-1",
        vec![part("bot-driver"), part("bot-observer")],
    );
    Arc::new(
        BcsMessageFlow::new(
            fixture.group,
            fixture.routing,
            fixture.registry,
            fixture.bot_delivery,
            fixture.frontend_delivery,
        )
        .with_message_repo(Arc::new(MemoryMessageRepo::new()))
        .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session)))
        .with_bot_authority_hook(Some(Arc::new(RuleTableHook { rules }))),
    )
}

fn human(staff: &str) -> CallerContext {
    CallerContext::Human(HumanActor {
        actor_id: format!("human_{staff}"),
        staff_no: staff.to_string(),
    })
}

fn bot_query(staff: &str) -> DeliveryStatusQuery {
    DeliveryStatusQuery {
        caller: human(staff),
        session_id: "group-1:deliveries".to_string(),
        message_ids: vec!["m-1".to_string()],
        client_msg_id: None,
    }
}

#[tokio::test]
async fn participant_bot_caller_reads_without_further_control() {
    let flow = build_flow(Vec::new()).await;
    let query = DeliveryStatusQuery {
        caller: CallerContext::Bot(bcs_service_api::BotActor {
            bot_uuid: "bot-observer".to_string(),
        }),
        session_id: "group-1:deliveries".to_string(),
        message_ids: vec!["m-1".to_string()],
        client_msg_id: None,
    };
    let rows = flow
        .query_message_deliveries(query)
        .await
        .expect("participant Bot caller reads");
    assert!(rows.is_empty());
}

#[tokio::test]
async fn human_reads_through_a_participating_bot_they_currently_control() {
    // alice holds a live owner edge over bot-observer (a
    // session participant); the historical `created_by`
    // predicate is not consulted by this gate.
    let flow = build_flow(vec![("alice".to_string(), "bot-observer".to_string())]).await;
    let rows = flow
        .query_message_deliveries(bot_query("alice"))
        .await
        .expect("owner-of-participant reads the session");
    assert!(rows.is_empty(), "empty store mirrors an empty page");
}

#[tokio::test]
async fn human_without_live_control_stays_denied() {
    let flow = build_flow(vec![("alice".to_string(), "bot-observer".to_string())]).await;
    let error = flow
        .query_message_deliveries(bot_query("bob"))
        .await
        .expect_err("bob holds no controllable participant");
    assert!(
        matches!(&error, ServiceError::Forbidden(message) if message == &"session membership required".to_string()),
        "only the current members or their controlling owners pass: {error:?}"
    );
}
