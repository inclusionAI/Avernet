use super::*;

async fn friends_app(
    authority: Arc<LazySeedAuthorityHook>,
    staff_no: &str,
) -> axum::Router {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    // Existence facts for the three probe targets
    // (the edge state lives ONLY in the authority
    // chain — no `created_by` rows are seeded).
    for bot_id in ["owned-bot", "unowned-bot", "receiver-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    drop(temp_dir);

    let mut services = Services::noop();
    services.bot_query = Arc::new(AcceptingBotQuery);
    services.friend_use_cases = Arc::new(friends_fake::RecordingFriendService::new());
    services.bot_authority_hook = authority;
    let identity: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
        staff_no: Some(staff_no.to_string()),
    });
    build_router(HttpAppState::new(services).with_user_identity(identity))
}

/// Existence-only bot lookup: the route's
/// `BotNotFound` probe must succeed on registered
/// ids so the AUTHORITY decision is what is
/// measured.
struct AcceptingBotQuery;

#[async_trait]
impl bcs_service_api::application::BotQueryService for AcceptingBotQuery {
    async fn list_bots(
        &self,
        _command: bcs_service_api::application::BotListCommand,
    ) -> Result<
        bcs_service_api::application::BotListResult,
        bcs_service_api::BotUseCaseError,
    > {
        Err(bcs_service_api::BotUseCaseError::Service(
            bcs_service_api::ServiceError::InternalError(
                "unused".to_string(),
            ),
        ))
    }

    async fn get_visibility(
        &self,
        _command: bcs_service_api::application::BotVisibilityQueryCommand,
    ) -> Result<
        bcs_service_api::application::BotVisibilityQueryResult,
        bcs_service_api::BotUseCaseError,
    > {
        Err(bcs_service_api::BotUseCaseError::Service(
            bcs_service_api::ServiceError::InternalError(
                "unused".to_string(),
            ),
        ))
    }

    async fn get_bot(
        &self,
        command: bcs_service_api::application::BotDetailCommand,
    ) -> Result<
        bcs_service_api::application::BotDetailResult,
        bcs_service_api::BotUseCaseError,
    > {
        Ok(bcs_service_api::application::BotDetailResult {
            agent_card: None,
            bot_uuid: command.bot_id.clone(),
            capabilities: BotCapabilities::default(),
            status: bcs_service_api::ActorStatus::Online,
            visibility: "public".to_string(),
            owner_actor_id: None,
            created_by: None,
            actor_kind: ActorKind::Bot,
            env: None,
            dynamic_status: Default::default(),
        })
    }
}

async fn friend_request(app: &axum::Router, from_bot: &str) -> axum::response::Response {
    app.clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/friends/request")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_bot": from_bot,
                        "to_bot": "target-bot",
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap()
}

#[tokio::test]
async fn friend_route_act_as_bot_follows_the_current_edge() {
    let authority = trusted_chain(&[("unowned-bot", "someone-else")]);

    // A (current owner): `from_bot` act-as passes
    // through the live edge; nothing about
    // `created_by` is consulted.
    let authority_owned = trusted_chain(&[("owned-bot", "100")]);
    let app = friends_app(authority_owned.clone(), "100").await;
    let resp = friend_request(&app, "owned-bot").await;
    assert_eq!(resp.status(), StatusCode::CREATED);

    // A→B handover: A's edge is replaced by B's;
    // the same staff is fallback-free denied even
    // though `created_by`-style rows would have
    // matched.
    authority_owned.seed_owner("owned-bot", "200");
    let app = friends_app(authority_owned.clone(), "100").await;
    let resp = friend_request(&app, "owned-bot").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    let app_b = friends_app(authority_owned.clone(), "200").await;
    let resp = friend_request(&app_b, "owned-bot").await;
    assert_eq!(resp.status(), StatusCode::CREATED);

    // X (unrelated) and the legacy `created_by:
    // None` auto-claim: both denied.
    let app_stranger = friends_app(authority_owned.clone(), "900").await;
    let resp = friend_request(&app_stranger, "unowned-bot").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);

    // M polarity: a CURRENT manager acts as the Bot.
    authority_owned.seed_manager("owned-bot", "300");
    let app_m = friends_app(authority_owned.clone(), "300").await;
    let resp = friend_request(&app_m, "owned-bot").await;
    assert_eq!(resp.status(), StatusCode::CREATED);

    // Manager withdrawal: fallback-free deny.
    authority_owned.revoke_manager("owned-bot", "300");
    let app_m = friends_app(authority_owned.clone(), "300").await;
    let resp = friend_request(&app_m, "owned-bot").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);

    // Never let the unused chain flag warning.
    drop(authority);
}

#[tokio::test]
async fn friend_request_receiver_follows_the_current_edge() {
    let authority = trusted_chain(&[]);

    // The receiver Bot (`receiver-bot`, resolved
    // from the recorded request) is accepted only
    // by its CURRENT controller.
    let app_owned = {
        authority.seed_owner("receiver-bot", "100");
        friends_app(authority.clone(), "100").await
    };
    let resp = app_owned
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/friends/requests/req-1/accept")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);

    // After the A→B handover the same staff is
    // fallback-free denied.
    authority.seed_owner("receiver-bot", "200");
    authority.revoke_owner("receiver-bot", "100");
    let app_revoked = friends_app(authority.clone(), "100").await;
    let resp = app_revoked
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/friends/requests/req-1/accept")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);

    // B accepts.
    let app_b = friends_app(authority.clone(), "200").await;
    let resp = app_b
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/friends/requests/req-1/accept")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    drop(authority);
}

#[tokio::test]
async fn friend_list_route_follows_the_current_edge() {
    let authority = trusted_chain(&[]);

    // `/bots/{id}/friends`: CURRENT control leg.
    authority.seed_owner("owned-bot", "100");
    let app = friends_app(authority.clone(), "100").await;
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/bots/owned-bot/friends")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);

    authority.seed_owner("owned-bot", "200");
    authority.revoke_owner("owned-bot", "100");
    let app = friends_app(authority.clone(), "100").await;
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/bots/owned-bot/friends")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    drop(authority);
}
