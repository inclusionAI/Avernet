use super::*;
use bcs_domain::group_context::ScopeLevel;
use bcs_service_api::application::v1::{
    AddGroupContext, AuthenticatedBotIdentity, GetGroupContext, GroupContextAck,
    GroupContextApplicationService, GroupContextGetResponse, GroupContextItemPreset,
    GroupContextListResponse, ListGroupContexts, SharedGroupContextItem, SystemSetGroupContext,
    UpdateGroupContext,
};

struct ContextService;

fn check_group(group: &str) -> Result<(), ApplicationError> {
    match group {
        "group-1" | "read-only" | "authority-unavailable" => Ok(()),
        "missing" => Err(ApplicationError::not_found(
            "group_not_found",
            "Group not found",
        )),
        _ => panic!("unexpected group: {group}"),
    }
}

#[async_trait]
impl GroupContextApplicationService for ContextService {
    async fn shared_info(
        &self,
        group: &str,
        session: Option<&str>,
        view_bot: Option<&str>,
        caller: &AuthenticatedCaller,
    ) -> Result<Vec<SharedGroupContextItem>, ApplicationError> {
        check_group(group)?;
        assert_eq!(session, Some("session-1"));
        assert_eq!(view_bot, Some("bot-1"));
        assert_eq!(caller_user_id(caller), "staff-1");
        Ok(vec![SharedGroupContextItem {
            name: "Timeline".into(),
            scope_level: ScopeLevel::Session,
            scope: "session-1".into(),
            content: "Milestone".into(),
            creator: "human_staff-1".into(),
            gmt_modified: "2026-11-16T00:00:00Z".parse().unwrap(),
        }])
    }

    async fn list_presets(
        &self,
        group: &str,
        caller: &AuthenticatedCaller,
        session: Option<&str>,
    ) -> Result<Vec<GroupContextItemPreset>, ApplicationError> {
        check_group(group)?;
        assert_eq!(caller_user_id(caller), "staff-1");
        assert_eq!(session, (group == "group-1").then_some("session-1"));
        Ok(vec![GroupContextItemPreset {
            name: "Timeline".into(),
            description: "Milestones".into(),
            creator: "human_staff-1".into(),
            modifier: "human_staff-1".into(),
            gmt_create: "2026-11-16T00:00:00Z".parse().unwrap(),
            gmt_modified: "2026-11-16T00:00:00Z".parse().unwrap(),
        }])
    }

    async fn can_manage_presets(&self, group: &str, actor: &str) -> Result<bool, ApplicationError> {
        assert_eq!(actor, "human_staff-1");
        match group {
            "read-only" => Ok(false),
            "authority-unavailable" => Err(ApplicationError::internal("Authority unavailable")),
            _ => panic!("unexpected management lookup for {group}"),
        }
    }

    async fn add_preset(
        &self,
        group: &str,
        actor: &str,
        name: String,
        description: String,
    ) -> Result<(), ApplicationError> {
        check_group(group)?;
        assert_eq!(actor, "human_staff-1");
        assert_eq!(name, "Timeline");
        assert!(description.is_empty());
        Ok(())
    }

    async fn update_preset(
        &self,
        group: &str,
        actor: &str,
        name: String,
        new_name: Option<String>,
        description: Option<String>,
    ) -> Result<(), ApplicationError> {
        check_group(group)?;
        assert_eq!(actor, "human_staff-1");
        assert_eq!(name, "Timeline");
        assert_eq!(new_name.as_deref(), Some("Plan"));
        assert_eq!(description.as_deref(), Some("Revised"));
        Ok(())
    }

    async fn delete_preset(
        &self,
        group: &str,
        actor: &str,
        name: String,
    ) -> Result<(), ApplicationError> {
        check_group(group)?;
        assert_eq!(actor, "human_staff-1");
        assert_eq!(name, "Plan");
        Ok(())
    }

    async fn add(&self, _: AddGroupContext) -> Result<GroupContextAck, ApplicationError> {
        unreachable!("unexpected legacy add")
    }

    async fn update(&self, _: UpdateGroupContext) -> Result<GroupContextAck, ApplicationError> {
        unreachable!("unexpected legacy update")
    }

    async fn list(
        &self,
        _: ListGroupContexts,
    ) -> Result<GroupContextListResponse, ApplicationError> {
        unreachable!("unexpected legacy list")
    }

    async fn get(&self, _: GetGroupContext) -> Result<GroupContextGetResponse, ApplicationError> {
        unreachable!("unexpected legacy get")
    }

    async fn systemset(
        &self,
        _: SystemSetGroupContext,
    ) -> Result<GroupContextAck, ApplicationError> {
        unreachable!("unexpected legacy systemset")
    }
}

fn context_router(caller: AuthenticatedCaller, with_service: bool) -> axum::Router {
    let mut state = ApiState::new(
        Arc::new(FakeGroupService::default()),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier { caller }),
    );
    if with_service {
        state.group_context = Some(Arc::new(ContextService));
    }
    router(state)
}

async fn post(
    app: &axum::Router,
    operation: &str,
    query: &str,
    body: Value,
) -> (StatusCode, Value) {
    let uri = format!("/openapi/v1/collaboration/groupcontext/{operation}?{query}");
    let response = app
        .clone()
        .oneshot(authenticated_request("POST", &uri, body))
        .await
        .unwrap();
    (response.status(), response_json(response).await)
}

#[tokio::test]
async fn shared_info_and_preset_lists_return_openapi_envelopes() {
    let app = context_router(caller(), true);
    let (status, body) = post(
        &app,
        "shared-info",
        "groupId=group-1&sessionId=session-1&view_bot_id=bot-1",
        json!({}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["request_id"], "request-123");
    assert_eq!(body["data"]["items"][0]["content"], "Milestone");

    let (status, body) = post(
        &app,
        "list-preset",
        "groupId=group-1&sessionId=session-1",
        json!({}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["presets"][0]["name"], "Timeline");
    assert!(body["data"].get("can_manage").is_none());

    let (status, body) = post(&app, "manage/list-preset", "groupId=read-only", json!({})).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["presets"][0]["description"], "Milestones");
    assert_eq!(body["data"]["can_manage"], false);
}

#[tokio::test]
async fn preset_writes_validate_json_and_forward_the_human_actor() {
    let app = context_router(caller(), true);
    for (operation, payload) in [
        ("manage/add-preset", json!({"name": "Timeline"})),
        (
            "manage/update-preset",
            json!({"name": "Timeline", "new_name": "Plan", "description": "Revised"}),
        ),
        ("manage/delete-preset", json!({"name": "Plan"})),
    ] {
        let (status, body) = post(&app, operation, "groupId=group-1", json!({})).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{operation}");
        assert_eq!(body["data"]["error_code"], "invalid_request");

        let (status, body) = post(&app, operation, "groupId=group-1", payload.clone()).await;
        assert_eq!(status, StatusCode::OK, "{operation}");
        assert_eq!(body["data"], json!({}));

        let (status, body) = post(&app, operation, "groupId=missing", payload).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{operation}");
        assert_eq!(body["data"]["error_code"], "group_not_found");
    }
}

#[tokio::test]
async fn reads_preserve_application_errors_and_reject_non_human_callers() {
    let app = context_router(caller(), true);
    for operation in ["shared-info", "list-preset", "manage/list-preset"] {
        let (status, body) = post(&app, operation, "groupId=missing", json!({})).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{operation}");
        assert_eq!(body["data"]["error_code"], "group_not_found");
    }

    let (status, body) = post(
        &app,
        "manage/list-preset",
        "groupId=authority-unavailable",
        json!({}),
    )
    .await;
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(body["request_id"], "request-123");

    let (status, _) = post(
        &context_router(caller(), false),
        "shared-info",
        "groupId=group-1",
        json!({}),
    )
    .await;
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);

    let mut bot = caller();
    bot.user = None;
    bot.bot = Some(AuthenticatedBotIdentity {
        bot_uuid: "bot-1".into(),
        owner_id: "staff-1".into(),
        app_id: 1,
        agent_code: "agent".into(),
    });
    let (status, body) = post(
        &context_router(bot, true),
        "shared-info",
        "groupId=group-1",
        json!({}),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["data"]["error_code"], "permission_denied");
}
