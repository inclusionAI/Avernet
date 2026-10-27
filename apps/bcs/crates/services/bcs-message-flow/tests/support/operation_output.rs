#![allow(dead_code)]
use bcs_service_api::{BotEventCommand, CallerContext, ChatEventState, HumanActor,
    OutputAdmissionPort, OutputOperationAdmission, ServiceResult, WebSendCommand};
pub fn web_send(group_id: &str, from: &str, session: Option<&str>, mentions: &[&str]) -> WebSendCommand {
    WebSendCommand {
        caller_execution: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".into(),
            staff_no: "1".into(),
        }),
        group_id: group_id.into(),
        session_id: session.map(str::to_string),
        from_actor_id: from.into(),
        from_name: None,
        message: mentions
            .iter()
            .map(|mention| format!("@{mention}"))
            .collect::<Vec<_>>()
            .join(" "),
        mentions: mentions.iter().map(|mention| mention.to_string()).collect(),
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: vec![],
    }
}

pub fn persistent_cmd(sender: &str, max_group_messages: u64) -> bcs_service_api::PersistentGroupSendCommand {
    use bcs_domain::{GroupMessageType, MessageRole};
    bcs_service_api::PersistentGroupSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".into(),
            staff_no: "1".into(),
        }),
        group_id: "group-1".into(),
        sender: sender.into(),
        content: "persistent hello".into(),
        message_type: GroupMessageType::Bot,
        role: MessageRole::Assistant,
        max_group_messages,
        store_messages: true,
    }
}

/// Records both boundary calls of the output-admission port: the
/// permit installation at accept time and the operation's complete
/// output-stream registration before downstream I/O.
#[derive(Default)]
pub struct RecordingOutputAdmission {
    pub admissions: std::sync::Mutex<Vec<OutputOperationAdmission>>,
    pub registered_runs: std::sync::Mutex<Vec<(String, Vec<String>)>>,
    pub released_runs: std::sync::Mutex<Vec<(String, Vec<String>)>>,
}

#[async_trait::async_trait]
impl OutputAdmissionPort for RecordingOutputAdmission {
    async fn admit_output_operation(
        &self,
        admission: OutputOperationAdmission,
    ) -> ServiceResult<()> {
        self.admissions.lock().unwrap().push(admission);
        Ok(())
    }

    async fn register_operation_runs(
        &self,
        operation_id: &str,
        run_ids: Vec<String>,
    ) -> ServiceResult<()> {
        self.registered_runs
            .lock()
            .unwrap()
            .push((operation_id.to_string(), run_ids));
        Ok(())
    }

    async fn release_operation_runs(
        &self,
        operation_id: &str,
        run_ids: Vec<String>,
    ) -> ServiceResult<()> {
        self.released_runs
            .lock()
            .unwrap()
            .push((operation_id.to_string(), run_ids));
        Ok(())
    }
}

pub fn delta_event(run_id: &str, bot_id: &str, session: Option<&str>) -> BotEventCommand {
    BotEventCommand {
        bot_id: bot_id.into(),
        run_id: run_id.into(),
        group_id: "group-1".into(),
        bcs_session_id: session.map(str::to_string),
        state: ChatEventState::Delta,
        event_type: "chat".into(),
        event_payload: serde_json::json!({"delta_text": "answer"}),
    }
}


#[path = "session.rs"]
mod session_support;

/// One managed driver plus an unmanaged observer. Tests keep the concrete
/// managed service (or a fault/pause decorator) so lifecycle actions remain
/// explicit; this helper only shares the identical Session/flow wiring.
pub async fn managed_output_flow(
    support: &super::support::FlowTestSupport,
    repo: std::sync::Arc<bcs_message_store::MemoryMessageRepo>,
    managed: std::sync::Arc<dyn bcs_service_api::ManagedMessageDeliveryService>,
    admission: std::sync::Arc<RecordingOutputAdmission>,
    capacity: u32,
) -> bcs_message_flow::BcsMessageFlow {
    use bcs_service_api::GroupCoreService;
    use std::sync::Arc;
    let session = session_support::test_session(
        "group-1:ffffffff", "group-1",
        support.group.get("group-1").await.expect("group").participants,
    );
    bcs_message_flow::BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_managed_deliveries(managed)
    .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), capacity)]))
    .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session)))
    .with_output_admission(Some(admission))
}
