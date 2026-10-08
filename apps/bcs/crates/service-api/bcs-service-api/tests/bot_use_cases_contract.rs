use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotConnectCommand, BotDetailCommand, BotDetailResult,
    BotDiscoveryCommand, BotDiscoveryService, BotDynamicStatus, BotLeaveCommand, BotLeaveResult,
    BotListCommand, BotListEntry, BotListResult, BotManagementService, BotPagedListCommand,
    BotQueryByIdsCommand, BotQueryService, BotStatusUpdateCommand, BotStatusUpdateResult,
    BotUseCaseError, BotVisibilityCommand, BotVisibilityQueryCommand, BotVisibilityQueryResult,
    ConnectError, DynamicStatusResponse, MyBotsCommand, ServiceError,
};
use bcs_test_support::{NoopBotDiscoveryService, NoopBotManagementService, NoopBotQueryService};

#[test]
fn bot_list_command_carries_paging_and_scope() {
    let command = BotListCommand {
        caller_actor_id: Some("human_alice".to_string()),
        offset: 25,
        limit: 50,
        onboarded: Some(true),
    };

    assert_eq!(command.caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(command.offset, 25);
    assert_eq!(command.limit, 50);
    assert_eq!(command.onboarded, Some(true));
}

#[test]
fn bot_connect_command_matches_current_route_contract() {
    let command = BotConnectCommand {
        caller_actor_id: Some("human_alice".to_string()),
        token: Some("session-token".to_string()),
        bot_id: Some("bot-1".to_string()),
        protocol_version: Some(2),
    };

    assert_eq!(command.caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(command.token.as_deref(), Some("session-token"));
    assert_eq!(command.bot_id.as_deref(), Some("bot-1"));
    assert_eq!(command.protocol_version, Some(2));
}

#[test]
fn bot_status_update_command_uses_dynamic_status() {
    let command = BotStatusUpdateCommand {
        caller_actor_id: Some("human_alice".to_string()),
        bot_id: "bot-1".to_string(),
        status: BotDynamicStatus {
            status: "idle".to_string(),
            dynamic_summary: Some("ready".to_string()),
            load: Some(0.1),
            updated_at: Some(42),
        },
    };

    assert_eq!(command.bot_id, "bot-1");
    assert_eq!(command.status.status, "idle");
    assert_eq!(command.status.dynamic_summary.as_deref(), Some("ready"));

    let result = BotStatusUpdateResult {
        updated: false,
        bot_uuid: "bot-1".to_string(),
        status: command.status.clone(),
    };
    assert!(!result.updated);
    assert_eq!(result.bot_uuid, "bot-1");
}

#[test]
fn bot_visibility_and_leave_commands_carry_actor_boundary() {
    let query = BotVisibilityQueryCommand {
        caller_actor_id: Some("human_alice".to_string()),
        bot_id: "bot-1".to_string(),
    };
    let query_result = BotVisibilityQueryResult {
        bot_uuid: "bot-1".to_string(),
        visibility: "protected".to_string(),
    };
    let leave = BotLeaveCommand {
        caller_actor_id: Some("bot-1".to_string()),
        human_actor_id: Some("human_alice".to_string()),
        bot_id: "bot-1".to_string(),
    };
    let leave_result = BotLeaveResult {
        left: true,
        bot_uuid: "bot-1".to_string(),
    };

    assert_eq!(query.caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(query.bot_id, "bot-1");
    assert_eq!(query_result.visibility, "protected");
    assert_eq!(leave.caller_actor_id.as_deref(), Some("bot-1"));
    assert_eq!(leave.human_actor_id.as_deref(), Some("human_alice"));
    assert!(leave_result.left);
}

#[test]
fn bot_dtos_carry_route_compatibility_fields() {
    let capabilities = BotCapabilities {
        name: Some("Route Bot".to_string()),
        summary: Some("Preserves route shape".to_string()),
        visibility: "public".to_string(),
        ..Default::default()
    };
    let list_entry = BotListEntry {
        bot_uuid: "bot-1".to_string(),
        name: capabilities.name.clone(),
        summary: capabilities.summary.clone(),
        capabilities: capabilities.clone(),
        status: ActorStatus::Online,
        visibility: "public".to_string(),
        owner_actor_id: Some("human_alice".to_string()),
        created_by: Some("alice".to_string()),
    };
    let detail = BotDetailResult {
        bot_uuid: "bot-1".to_string(),
        capabilities: capabilities.clone(),
        status: ActorStatus::Hidden,
        visibility: "public".to_string(),
        owner_actor_id: Some("human_alice".to_string()),
        created_by: Some("alice".to_string()),
        actor_kind: ActorKind::Bot,
        env: Some("dev".to_string()),
        dynamic_status: DynamicStatusResponse {
            status: "offline".to_string(),
        },
    };

    assert_eq!(list_entry.capabilities.name.as_deref(), Some("Route Bot"));
    assert_eq!(list_entry.created_by.as_deref(), Some("alice"));
    assert_eq!(detail.actor_kind, ActorKind::Bot);
    assert_eq!(detail.env.as_deref(), Some("dev"));
    assert_eq!(detail.dynamic_status.status, "offline");
}

#[test]
fn bot_use_case_errors_preserve_connect_error_classes() {
    let conflict = BotUseCaseError::Connect(ConnectError::AlreadyRegistered("bot-1".to_string()));
    let invalid = BotUseCaseError::InvalidBotId("human_ prefix is reserved".to_string());

    assert!(matches!(
        conflict,
        BotUseCaseError::Connect(ConnectError::AlreadyRegistered(id)) if id == "bot-1"
    ));
    assert!(matches!(
        invalid,
        BotUseCaseError::InvalidBotId(message) if message.contains("reserved")
    ));
}

#[tokio::test]
async fn noop_bot_query_service_fails_closed() {
    let service = NoopBotQueryService;

    let result = service
        .list_bots(BotListCommand {
            caller_actor_id: None,
            offset: 10,
            limit: 20,
            onboarded: None,
        })
        .await;
    assert_not_configured(result, "bot query service is not configured");

    let detail = service
        .get_bot(BotDetailCommand {
            caller_actor_id: None,
            bot_id: "bot-missing".to_string(),
        })
        .await;
    assert_not_configured(detail, "bot query service is not configured");

    let visibility = service
        .get_visibility(BotVisibilityQueryCommand {
            caller_actor_id: None,
            bot_id: "bot-missing".to_string(),
        })
        .await;
    assert_not_configured(visibility, "bot query service is not configured");

    let paged = service
        .list_bots_paged(BotPagedListCommand::default())
        .await;
    assert_not_configured(paged, "bot query service is not configured");

    let mine = service
        .list_my_bots(MyBotsCommand {
            staff_no: "alice".to_string(),
            offset: 0,
            limit: 10,
            active_only: false,
        })
        .await;
    assert_not_configured(mine, "bot query service is not configured");

    let by_ids = service
        .query_bots_by_ids(BotQueryByIdsCommand {
            bot_ids: vec!["bot-missing".to_string()],
        })
        .await;
    assert_not_configured(by_ids, "bot query service is not configured");
}

#[tokio::test]
async fn noop_bot_discovery_service_reports_discovery_boundary() {
    let service = NoopBotDiscoveryService;

    let result = service.discover_bots(BotDiscoveryCommand::default()).await;
    assert_not_configured(result, "bot discovery service is not configured");
}

#[tokio::test]
async fn added_bot_query_methods_fail_closed_for_legacy_implementations() {
    let service = LegacyBotQueryService;

    let paged = service
        .list_bots_paged(BotPagedListCommand::default())
        .await;
    assert_not_configured(paged, "bot query service is not configured");

    let mine = service
        .list_my_bots(MyBotsCommand {
            staff_no: "alice".to_string(),
            offset: 0,
            limit: 10,
            active_only: false,
        })
        .await;
    assert_not_configured(mine, "bot query service is not configured");

    let by_ids = service
        .query_bots_by_ids(BotQueryByIdsCommand {
            bot_ids: vec!["bot-missing".to_string()],
        })
        .await;
    assert_not_configured(by_ids, "bot query service is not configured");
}

#[tokio::test]
async fn noop_bot_management_service_fails_closed() {
    let service = NoopBotManagementService;

    let connect = service
        .connect_bot(BotConnectCommand {
            caller_actor_id: None,
            token: Some("session-token".to_string()),
            bot_id: Some("bot-1".to_string()),
            protocol_version: Some(2),
        })
        .await;
    assert_not_configured(connect, "bot management service is not configured");

    let status = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: None,
            bot_id: "bot-1".to_string(),
            status: BotDynamicStatus {
                status: "idle".to_string(),
                ..Default::default()
            },
        })
        .await;
    assert_not_configured(status, "bot management service is not configured");

    let visibility = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: None,
            bot_id: "bot-1".to_string(),
            visibility: "private".to_string(),
        })
        .await;
    assert_not_configured(visibility, "bot management service is not configured");

    let leave = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: None,
            human_actor_id: None,
            bot_id: "bot-1".to_string(),
        })
        .await;
    assert_not_configured(leave, "bot management service is not configured");
}

fn assert_not_configured<T>(result: Result<T, BotUseCaseError>, expected_message: &str) {
    assert!(matches!(
        result,
        Err(BotUseCaseError::Service(ServiceError::InvalidOperation {
            message,
            request_id: None,
        })) if message == expected_message
    ));
}

struct LegacyBotQueryService;

#[async_trait]
impl BotQueryService for LegacyBotQueryService {
    async fn list_bots(&self, _command: BotListCommand) -> Result<BotListResult, BotUseCaseError> {
        Err(ServiceError::InternalError("not used".to_string()).into())
    }

    async fn get_bot(
        &self,
        _command: BotDetailCommand,
    ) -> Result<BotDetailResult, BotUseCaseError> {
        Err(ServiceError::InternalError("not used".to_string()).into())
    }

    async fn get_visibility(
        &self,
        _command: BotVisibilityQueryCommand,
    ) -> Result<BotVisibilityQueryResult, BotUseCaseError> {
        Err(ServiceError::InternalError("not used".to_string()).into())
    }
}
