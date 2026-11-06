use super::*;

fn static_bot_auth_chain(bot_uuid: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        bot_uuid: Some(bot_uuid.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(principal))]))
}
#[derive(Default)]
struct RecordingGroupQuery {
    list_calls: Mutex<Vec<GroupListCommand>>,
    detail_calls: Mutex<Vec<GroupDetailCommand>>,
    bot_group_calls: Mutex<Vec<BotGroupListCommand>>,
}
#[async_trait::async_trait]
impl GroupQueryService for RecordingGroupQuery {
    async fn list_groups(
        &self,
        cmd: GroupListCommand,
    ) -> Result<GroupListResult, bcs_service_api::GroupUseCaseError> {
        self.list_calls.lock().await.push(cmd.clone());
        Ok(GroupListResult {
            items: vec![group_list_entry("group-1")],
            total: 1,
            offset: cmd.offset,
            limit: cmd.limit,
        })
    }

    async fn get_group(
        &self,
        cmd: GroupDetailCommand,
    ) -> Result<GroupDetailResult, bcs_service_api::GroupUseCaseError> {
        self.detail_calls.lock().await.push(cmd.clone());
        Ok(GroupDetailResult {
            group_id: cmd.group_id,
            label: Some("Queried group".to_string()),
            status: GroupStatus::Active,
            driver_bot_id: "driver-bot".to_string(),
            originator: None,
            context: Some("Shared context".to_string()),
            opening_message: None,
            participants: vec![participant_view("driver-bot", "driver")],
            message_count: 0,
            service_group_uuid: None,
            service_mode: None,
            group_kind: Default::default(),
            dm_pair_key: None,
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 20,
            chat_url: None,
            context_injected: 0,
            latest_running_session_id: None,
            initial_run: None,
            visibility: "private".to_string(),
            human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
        })
    }

    async fn list_bot_groups(
        &self,
        cmd: BotGroupListCommand,
    ) -> Result<GroupListResult, bcs_service_api::GroupUseCaseError> {
        self.bot_group_calls.lock().await.push(cmd.clone());
        Ok(GroupListResult {
            items: vec![group_list_entry("bot-group-1")],
            total: 1,
            offset: cmd.offset,
            limit: cmd.limit,
        })
    }

}
#[tokio::test]
async fn group_query_routes_delegate_to_group_query_service() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder()
        .group_query(query.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let list_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/groups?group_kind=all&offset=2&limit=3")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(list_response.status(), StatusCode::OK);
    let body = to_bytes(list_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["items"][0]["id"], "group-1");
    assert_eq!(json["items"][0]["human_mention_notify_mode"], "all");
    assert_eq!(json["total"], 1);
    assert_eq!(json["offset"], 2);
    assert_eq!(json["limit"], 3);
    assert_eq!(json["group_kind"], "all");

    let detail_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/groups/group-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(detail_response.status(), StatusCode::OK);
    let body = to_bytes(detail_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "group-1");
    assert_eq!(json["driver_bot"], "driver-bot");
    assert_eq!(json["message_count"], 0);

    let bot_groups_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/bots/driver-bot/groups?group_kind=normal&offset=4&limit=5&q=05")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(bot_groups_response.status(), StatusCode::OK);
    let body = to_bytes(bot_groups_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["bot_uuid"], "driver-bot");
    assert_eq!(json["items"][0]["group_id"], "bot-group-1");
    assert_eq!(json["items"][0]["human_mention_notify_mode"], "all");
    assert_eq!(json["total"], 1);

    let list_calls = query.list_calls.lock().await;
    assert_eq!(list_calls.len(), 1);
    assert_eq!(list_calls[0].group_kind, None);
    assert_eq!(list_calls[0].offset, 2);
    assert_eq!(list_calls[0].limit, 3);
    drop(list_calls);

    let detail_calls = query.detail_calls.lock().await;
    assert_eq!(detail_calls.len(), 1);
    assert_eq!(detail_calls[0].group_id, "group-1");
    drop(detail_calls);

    let bot_group_calls = query.bot_group_calls.lock().await;
    assert_eq!(bot_group_calls.len(), 1);
    assert_eq!(bot_group_calls[0].bot_id, "driver-bot");
    assert_eq!(bot_group_calls[0].group_kind, Some(Default::default()));
    assert_eq!(bot_group_calls[0].q.as_deref(), Some("05"));
    assert_eq!(bot_group_calls[0].offset, 4);
    assert_eq!(bot_group_calls[0].limit, 5);
    drop(bot_group_calls);

}
#[tokio::test]
async fn my_groups_resolves_bot_principal_and_preserves_query() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder()
        .group_query(query.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services)
            .with_auth_chain(static_bot_auth_chain("bot-current"), AuthConfig::default())
            .with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/my?group_kind=normal&offset=4&limit=5&q=05&include_session_groups=true")
                .header("authorization", "Bearer human-oauth-token")
                .header("X-BCS-Bot-Token", "valid-bot-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["actor_id"], "bot-current");
    assert!(json.get("bot_uuid").is_none());
    assert_eq!(json["items"][0]["group_id"], "bot-group-1");

    let calls = query.bot_group_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].bot_id, "bot-current");
    assert_eq!(calls[0].group_kind, Some(Default::default()));
    assert_eq!(calls[0].q.as_deref(), Some("05"));
    assert_eq!(calls[0].offset, 4);
    assert_eq!(calls[0].limit, 5);
}

#[tokio::test]
async fn my_groups_resolves_human_identity() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder()
        .group_query(query.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/my?include_session_groups=false")
                .header("authorization", "Bearer human-oauth-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["actor_id"], "human_alice");

    let calls = query.bot_group_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].bot_id, "human_alice");
}

#[tokio::test]
async fn my_groups_rejects_explicit_invalid_bot_token_instead_of_falling_back_to_human() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder()
        .group_query(query.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/my")
                .header("authorization", "Bearer human-oauth-token")
                .header("X-BCS-Bot-Token", "stale-bot-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(query.bot_group_calls.lock().await.is_empty());
}

#[tokio::test]
async fn my_groups_rejects_explicit_bot_token_when_container_validation_fails() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder()
        .group_query(query.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services)
            .with_auth_chain(static_bot_auth_chain("bot-current"), AuthConfig::default())
            .with_user_identity(Arc::new(ChainUserIdentityPort::new(chain)))
            .with_strict_container_validation(true),
    );

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/my")
                .header("authorization", "Bearer human-oauth-token")
                .header("X-BCS-Bot-Token", "valid-bot-token")
                .header("x-agentclaw-bolt-id", "different-container")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(query.bot_group_calls.lock().await.is_empty());
}

#[tokio::test]
async fn my_groups_rejects_empty_bot_actor_identity() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder().group_query(query).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services)
            .with_auth_chain(static_bot_auth_chain(""), AuthConfig::default())
            .with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/my")
                .header("authorization", "Bearer human-oauth-token")
                .header("X-BCS-Bot-Token", "valid-bot-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn my_groups_rejects_anonymous_without_shadowing_group_detail() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder().group_query(query).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let my_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/groups/my?include_session_groups=false")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(my_response.status(), StatusCode::UNAUTHORIZED);

    let detail_response = app
        .oneshot(
            Request::builder()
                .uri("/groups/group-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(detail_response.status(), StatusCode::OK);
}
