use super::super::{
    Arc,
    BcsMessageFlow,
    BlockReason,
    BlockingMessageRepo,
    BotDeliveryTarget,
    BotRegistryCoreService,
    ChatResponseMode,
    GroupCoreService,
    GroupStrategy,
    InterceptorDecision,
    MemoryBotRunContextStore,
    MessageInterceptor,
    OutboundMessage,
    Participant,
    ParticipantRole,
    ProviderStreamGrayList,
    RecordingMessageRepo,
    new_task_entry,
    support
};

pub(in super::super) struct BlockingInterceptor;

#[async_trait::async_trait]
impl MessageInterceptor for BlockingInterceptor {
    async fn on_outbound(&self, _msg: &mut OutboundMessage) -> InterceptorDecision {
        InterceptorDecision::Block(BlockReason {
            interceptor_id: "test-block".to_string(),
            code: "blocked".to_string(),
            message: "task dispatch blocked by test".to_string(),
            user_visible: true,
        })
    }
}

pub(in super::super) async fn manager_worker_flow_with_repo() -> (
    support::FlowTestSupport,
    Arc<RecordingMessageRepo>,
    BcsMessageFlow,
) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    let mut manager = Participant::bot("bot-manager", ParticipantRole::Manager);
    manager.tags = vec!["draft".to_string()];
    let mut worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    worker.tags = vec!["online".to_string()];
    group.participants = vec![manager, worker];
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());
    (support, repo, flow)
}

pub(in super::super) async fn configure_provider_v2_target(
    support: &support::FlowTestSupport,
    bot_id: &str,
    created_by: &str,
) {
    support
        .registry
        .save_created_by(bot_id, created_by, true)
        .await
        .unwrap();
    let mut provider_target = support::FakeRegistryService::provider_target(bot_id);
    if let BotDeliveryTarget::HttpProvider {
        protocol_version, ..
    } = &mut provider_target
    {
        *protocol_version = "2.0".to_string();
    }
    support
        .registry
        .set_delivery_target(bot_id, provider_target)
        .await;
}

pub(in super::super) fn manager_worker_flow_with_provider_stream(
    support: &support::FlowTestSupport,
    repo: Arc<RecordingMessageRepo>,
) -> BcsMessageFlow {
    BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new(vec![
        "gray-user".to_string(),
    ])))
}

pub(in super::super) const CONFIGURED_TASK_RUN_TIMEOUT_MS: u64 = 7_200_000;

pub(in super::super) async fn manager_worker_flow_with_blocking_repo() -> (
    support::FlowTestSupport,
    Arc<BlockingMessageRepo>,
    Arc<MemoryBotRunContextStore>,
    BcsMessageFlow,
) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(BlockingMessageRepo::new());
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_provider_chat_run_timeout_ms(CONFIGURED_TASK_RUN_TIMEOUT_MS)
    .with_message_repo(repo.clone())
    .with_bot_run_context(run_context.clone());
    (support, repo, run_context, flow)
}

pub(in super::super) async fn register_manager_worker_task(
    flow: &BcsMessageFlow,
    task_id: &str,
    response_mode: ChatResponseMode,
) {
    flow.task_store
        .register(new_task_entry(
            task_id.to_string(),
            "group-1".to_string(),
            Some("group-1:abcdef12".to_string()),
            "bot-manager".to_string(),
            "bot-worker".to_string(),
            Some("Worker".to_string()),
            0,
            response_mode,
        ))
        .await;
}
