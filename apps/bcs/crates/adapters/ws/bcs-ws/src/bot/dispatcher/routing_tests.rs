use super::*;
use bcs_service_api::ActorKind;

fn selector(kind: &str, value: Option<&str>) -> RouteSelectorWire {
    RouteSelectorWire { selector_type: kind.into(), value: value.map(str::to_string) }
}

fn participants() -> Vec<Participant> {
    [ParticipantRole::Driver, ParticipantRole::Consultant, ParticipantRole::Manager,
        ParticipantRole::Worker, ParticipantRole::Observer].into_iter().enumerate().map(|(i, role)| {
            let mut p = Participant::bot(format!("bot-{i}"), role);
            p.bot_name = Some(if i < 2 { "duplicate".into() } else { format!("Name-{i}") }); p
        }).chain(std::iter::once({
            let mut p = Participant::bot("human", ParticipantRole::Observer); p.actor_kind = ActorKind::Human;
            p.bot_name = Some("Name-3".into()); p
        })).collect()
}

struct RouteGroups;

#[async_trait::async_trait]
impl bcs_service_api::GroupDispatchContextPort for RouteGroups {
    async fn participants(&self, group: &str) -> Option<Vec<Participant>> {
        (group == "route-group").then(participants)
    }
}

#[tokio::test]
async fn coverage_route_handler_validates_registration_group_and_session_before_resolving() {
    use bcs_service_api::{NewSessionParams, SessionRepoPort};
    use bcs_test_support::*;
    let sessions = Arc::new(bcs_session_store::MemorySessionRepo::new());
    let state = Arc::new(BotDispatchState {
        expected_actor_id: None, bot_runtime: Arc::new(NoopBotRuntimeConnectionService),
        message_flow: Arc::new(NoopMessageFlowService), collaboration_runtime: Arc::new(NoopCollaborationRuntimeService),
        bot_run_context: Arc::new(NoopBotRunContextPort), bot_connections: Arc::new(crate::bot::BotConnectionRegistry::new()),
        run_channels: Arc::new(crate::shared::RunChannelManager::new()), task_callback: None,
        session_management: Arc::new(bcs_session::SessionManagementServiceImpl::new(sessions.clone(), Arc::new(bcs_group_store::MemoryGroupRepo::new()))),
        group_dispatch: Arc::new(RouteGroups), system_message: None,
        coordination_processed: Default::default(), agent_credential_backfill: None, interactions: Arc::new(NoopInteractionService),
    });
    let params = serde_json::json!({"group_id":"route-group", "selectors":[{"type":"bot","value":"bot-0"}]});
    let (tx, mut rx) = mpsc::channel(8);
    handle_route_resolve(&state, "unregistered", params.clone(), &tx, &None).await.unwrap();
    let denied: Value = serde_json::from_str(&rx.recv().await.unwrap()).unwrap();
    assert_eq!(denied["error"]["code"], "not_registered");
    let registered = Some("bot-0".into());
    assert!(matches!(handle_route_resolve(&state, "invalid", serde_json::json!({}), &tx, &registered).await, Err(BotWsDispatchError::InvalidFrameFormat(_))));
    assert!(rx.try_recv().is_err());
    let mut missing_group = params.clone(); missing_group["group_id"] = "absent".into();
    handle_route_resolve(&state, "group", missing_group, &tx, &registered).await.unwrap();
    let denied: Value = serde_json::from_str(&rx.recv().await.unwrap()).unwrap();
    assert_eq!(denied["error"]["code"], "group_not_found");
    let mut scoped = params.clone(); scoped["session_id"] = "route-group:00000001".into();
    handle_route_resolve(&state, "missing-session", scoped.clone(), &tx, &registered).await.unwrap();
    let denied: Value = serde_json::from_str(&rx.recv().await.unwrap()).unwrap();
    assert_eq!(denied["error"]["code"], "session_not_found");
    sessions.create("route-group", NewSessionParams { id: Some("route-group:00000001".into()),
        participants: vec![Participant::bot("session-only", ParticipantRole::Worker)], ..Default::default() }).await.unwrap();
    scoped["selectors"] = serde_json::json!([{"type":"bot","value":"session-only"}]);
    handle_route_resolve(&state, "scoped", scoped, &tx, &registered).await.unwrap();
    let resolved: Value = serde_json::from_str(&rx.recv().await.unwrap()).unwrap();
    assert_eq!(resolved["id"], "scoped"); assert_eq!(resolved["payload"]["ok"], true);
    assert_eq!(resolved["payload"]["resolved"][0]["value"], "session-only", "Session participants override the Group seed");
    handle_route_resolve(&state, "unscoped", params.clone(), &tx, &registered).await.unwrap();
    let resolved: Value = serde_json::from_str(&rx.recv().await.unwrap()).unwrap();
    assert_eq!(resolved["payload"]["resolved"][0]["value"], "bot-0");
    drop(rx);
    assert!(matches!(handle_route_resolve(&state, "closed", params, &tx, &registered).await, Err(BotWsDispatchError::WsProtocolError(_))));
}

#[test]
fn coverage_route_selectors_reject_ambiguity_and_never_resolve_human_participants() {
    let participants = participants();
    for selectors in [vec![], vec![selector("bot", None)], vec![selector("name", Some("  "))], vec![selector("unsupported", Some("value"))]] {
        let result = resolve_route_selectors(&participants, &selectors);
        assert!(!result.ok); assert_eq!(result.error.as_deref(), Some("INVALID_ROUTE_SELECTOR"));
        assert!(result.resolved.is_empty()); assert_eq!(result.candidates.len(), 5);
    }
    let ambiguous = resolve_route_selectors(&participants, &[selector("name", Some("duplicate"))]);
    assert_eq!(ambiguous.error.as_deref(), Some("AMBIGUOUS_ROUTE_TARGET"));
    assert_eq!(ambiguous.candidates.iter().map(|p| p.bot_uuid.as_str()).collect::<Vec<_>>(), ["bot-0", "bot-1"]);
    let human = resolve_route_selectors(&participants, &[selector("bot", Some("human"))]);
    assert_eq!(human.error.as_deref(), Some("UNKNOWN_ROUTE_TARGET"));
    assert!(human.candidates.iter().all(|p| p.bot_uuid != "human"));
    let result = resolve_route_selectors(&participants, &[selector("name", Some("Name-3")), selector("bot", Some(" bot-3 "))]);
    assert!(result.ok); assert_eq!(result.resolved.len(), 1, "name and bot identity deduplicate");
    assert_eq!(result.resolved[0].value, "bot-3"); assert_eq!(result.resolved[0].role, "worker");
}

#[test]
fn coverage_route_suggestions_match_case_insensitively_and_keep_role_metadata() {
    let participants = participants();
    let result = resolve_route_selectors(&participants, &[selector("name", Some("NAME-4"))]);
    assert_eq!(result.error.as_deref(), Some("UNKNOWN_ROUTE_TARGET"));
    assert_eq!(result.candidates.len(), 1); assert_eq!(result.candidates[0].bot_uuid, "bot-4");
    let fallback = route_candidates(&participants, Some("nothing matches"));
    assert_eq!(fallback.iter().map(|p| p.role.as_str()).collect::<Vec<_>>(), ["driver", "consultant", "manager", "worker", "observer"]);
    let resolved = resolve_route_selectors(&participants, &(0..5).map(|i| selector("bot", Some(&format!("bot-{i}")))).collect::<Vec<_>>());
    assert!(resolved.ok); assert_eq!(resolved.resolved.len(), 5);
    let json = serde_json::to_value(resolved).unwrap();
    assert!(json.get("error").is_none()); assert!(json.get("candidates").is_none());
    assert_eq!(json["resolved"][2]["type"], "bot");
}
