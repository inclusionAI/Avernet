use bcs_service_api::{
    BotDynamicStatus, BotRuntimeConnectCommand, BotRuntimeConnectionService,
    BotRuntimeDisconnectCommand, BotRuntimeStatusCommand, ServiceError,
    WorkbenchChatAuthorizationCommand, WorkbenchConnectCommand, WorkbenchSessionService,
    WorkbenchUseCaseError,
};
use bcs_test_support::{NoopBotRuntimeConnectionService, NoopWorkbenchSessionService};

#[tokio::test]
async fn noop_bot_runtime_service_fails_closed() {
    let service = NoopBotRuntimeConnectionService;

    let connect = service
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("bot-1".to_string()),
            protocol_version: Some(1),
            client_kind: None,
        })
        .await;
    assert!(connect.is_err());

    let status = service
        .update_runtime_status(BotRuntimeStatusCommand {
            caller_actor_id: Some("bot-1".to_string()),
            bot_id: "bot-1".to_string(),
            status: BotDynamicStatus::default(),
        })
        .await;
    assert!(status.is_err());

    let disconnect = service
        .disconnect_streaming(BotRuntimeDisconnectCommand {
            bot_id: "bot-1".to_string(),
        })
        .await;
    assert!(disconnect.is_err());
}

#[tokio::test]
async fn noop_workbench_session_service_fails_closed() {
    let service = NoopWorkbenchSessionService;

    let connect = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_1".to_string()),
            group_id: "group-1".to_string(),
            session_id: None,
        })
        .await;
    assert!(connect.is_err());

    let authorize = service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_1".to_string()),
            group_id: "group-1".to_string(),
            from_actor_id: "human_1".to_string(),
            session_id: None,
        })
        .await;
    assert!(authorize.is_err());
}

#[test]
fn workbench_error_mapping_is_flat_at_contract_boundary() {
    let missing = WorkbenchUseCaseError::from_service_error(ServiceError::GroupNotFound(
        "group-1".to_string(),
    ));
    assert!(matches!(
        &missing,
        WorkbenchUseCaseError::GroupNotFound(group_id) if group_id == "group-1"
    ));
    assert_eq!(missing.code(), "group_not_found");
    assert_eq!(missing.message(), "Group not found: group-1");

    let unauthorized = WorkbenchUseCaseError::from_service_error(ServiceError::Unauthorized(
        "missing cookie".to_string(),
    ));
    assert!(matches!(&unauthorized, WorkbenchUseCaseError::Unauthorized));
    assert_eq!(unauthorized.code(), "unauthorized");
}
