use bcs_service_api::{
    ActorKind, ActorStatus, BotSendResult, ChatEventRouting, DeliveryType, Group, GroupKind,
    Participant, ParticipantMode, ParticipantRole, RouteAndSendResult, RouteParticipantOverlay,
    RoutingCoreService, RoutingDecision, RoutingTarget, StructuredRoutingError,
};
use bcs_test_support::{NoopBotRegistryCoreService, NoopRoutingCoreService};

fn sample_group() -> Group {
    Group::new(
        "group-1",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("helper", ParticipantRole::Consultant),
        ],
    )
}

#[derive(Default)]
struct LegacyRoutingCoreService;

#[async_trait::async_trait]
impl RoutingCoreService for LegacyRoutingCoreService {
    async fn route(
        &self,
        _group: &Group,
        message: &str,
        _sender_bot_id: Option<&str>,
    ) -> RoutingDecision {
        RoutingDecision {
            targets: vec![RoutingTarget {
                bot_uuid: "driver".to_string(),
                url: String::new(),
                is_driver: true,
                delivery_type: DeliveryType::Send,
            }],
            mentions: vec!["driver".to_string()],
            cleaned_message: message.to_string(),
            hidden_mentions: vec![],
        }
    }

    async fn send_to_bot(
        &self,
        _target: &RoutingTarget,
        _message: &str,
        _from_bot_id: Option<&str>,
        _group_id: Option<&str>,
    ) -> BotSendResult {
        BotSendResult {
            bot_uuid: String::new(),
            content: String::new(),
            success: false,
            error: Some("not used".to_string()),
        }
    }

    async fn route_and_send(
        &self,
        _group: &Group,
        _message: &str,
        _from: Option<&str>,
    ) -> RouteAndSendResult {
        RouteAndSendResult {
            results: Vec::new(),
            mentions: Vec::new(),
        }
    }
}

#[tokio::test]
async fn noop_routing_ignores_overlay_and_returns_empty_decisions() {
    let service = NoopRoutingCoreService::default();
    let group = sample_group();
    let overlay = [RouteParticipantOverlay {
        bot_uuid: "driver".to_string(),
        bot_name: Some("Driver".to_string()),
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::Muted),
        status: ActorStatus::Hidden,
        is_driver: true,
    }];

    let route = service.route(&group, "@helper hello", Some("driver")).await;
    assert!(route.targets.is_empty());
    assert!(route.mentions.is_empty());
    assert_eq!(route.cleaned_message, "@helper hello");

    let overlay_route = service
        .route_with_overlay(&group, "@helper hello", Some("driver"), &overlay)
        .await;
    assert!(overlay_route.targets.is_empty());
    assert!(overlay_route.mentions.is_empty());
    assert_eq!(overlay_route.cleaned_message, "@helper hello");

    let route_and_send = service
        .route_and_send(&group, "hello", Some("driver"))
        .await;
    assert!(route_and_send.results.is_empty());
    assert!(route_and_send.mentions.is_empty());
}

#[tokio::test]
async fn default_dm_routing_fails_closed_instead_of_using_normal_route() {
    let service = LegacyRoutingCoreService::default();
    let mut group = sample_group();
    group.group_kind = GroupKind::Dm;

    let decision = service
        .route_dm_with_overlay(&group, "hello", "human_alice", &[])
        .await;

    assert!(decision.targets.is_empty());
    assert!(decision.mentions.is_empty());
    assert_eq!(decision.cleaned_message, "hello");
}

#[tokio::test]
async fn noop_routing_send_and_structured_route_fail_closed() {
    let service = NoopRoutingCoreService::default();
    let group = sample_group();
    let registry = NoopBotRegistryCoreService::default();
    let target = RoutingTarget {
        bot_uuid: "helper".to_string(),
        url: "http://helper.local".to_string(),
        is_driver: false,
        delivery_type: DeliveryType::Send,
    };
    let routing = ChatEventRouting {
        responders: vec![],
        mode: None,
        reason: "contract-test".to_string(),
        include_self: None,
        dedupe_key: None,
    };

    let send = service
        .send_to_bot(&target, "hello", Some("driver"), Some("group-1"))
        .await;
    assert_eq!(send.bot_uuid, "");
    assert_eq!(send.content, "");
    assert!(!send.success);
    assert_eq!(send.error.as_deref(), Some("Noop implementation"));

    let err = service
        .route_structured(&group, &routing, "driver", &registry)
        .await
        .unwrap_err();
    assert!(matches!(err, StructuredRoutingError::NoTargetMatched));
}
