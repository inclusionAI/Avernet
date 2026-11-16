use super::*;

async fn decision(app: &axum::Router, id: &str, action: &str) -> axum::response::Response {
    app.clone().oneshot(
        Request::builder()
            .method("POST")
            .uri(format!("/collaboration/friend-connections/requests/{id}/{action}"))
            .header("authorization", "Bearer caller-token")
            .body(Body::empty()).unwrap(),
    ).await.unwrap()
}

#[tokio::test]
async fn friend_connection_decisions_follow_current_receiver_authority() {
    for action in ["accept", "reject"] {
        for role in ["owner", "manager"] {
            let connect = Arc::new(RecordingConnectService::default());
            connect.request_lookup.lock().await.insert("1".into(), pending_request("1", "sender-bot", "receiver-bot"));
            let authority = bcs_test_support::lazy_authority_chain().hook;
            if role == "owner" {
                authority.seed_owner("receiver-bot", "10001");
            } else {
                authority.seed_owner("receiver-bot", "other-owner");
                authority.seed_manager("receiver-bot", "10001");
            }
            let temp_dir = TempDir::new().unwrap();
            let app = build_app_with_authority(
                &temp_dir, connect.clone(), Arc::new(RecordingBotQueryService::default()),
                Some(Arc::new(StaticUserIdentityPort::human("10001"))), None, Some(authority.clone()),
            ).await;

            assert_eq!(decision(&app, "1", action).await.status(), StatusCode::OK, "{role} can {action}");
            if action == "accept" {
                assert_eq!(*connect.approve_commands.lock().await, vec![("1".into(), "receiver-bot".into())]);
            } else {
                assert_eq!(*connect.reject_commands.lock().await, vec![("1".into(), "receiver-bot".into(), None)]);
            }

            if role == "owner" {
                authority.seed_owner("receiver-bot", "new-owner");
            } else {
                authority.revoke_manager("receiver-bot", "10001");
            }
            // Controlling the requester never substitutes for receiver authority.
            authority.seed_owner("sender-bot", "10001");
            assert_eq!(decision(&app, "1", action).await.status(), StatusCode::FORBIDDEN, "revoked {role} cannot {action}");
            assert_eq!(connect.approve_commands.lock().await.len() + connect.reject_commands.lock().await.len(), 1);
        }
    }
}

#[tokio::test]
async fn friend_connection_decisions_fail_closed_on_authority_read_error() {
    let connect = Arc::new(RecordingConnectService::default());
    connect.request_lookup.lock().await.insert("1".into(), pending_request("1", "sender-bot", "receiver-bot"));
    let temp_dir = TempDir::new().unwrap();
    let app = build_app_with_authority(
        &temp_dir, connect.clone(), Arc::new(RecordingBotQueryService::default()),
        Some(Arc::new(StaticUserIdentityPort::human("10001"))), None,
        Some(Arc::new(bcs_test_support::FailingAuthorityHook)),
    ).await;
    for action in ["accept", "reject"] {
        assert_eq!(decision(&app, "1", action).await.status(), StatusCode::INTERNAL_SERVER_ERROR);
    }
    assert!(connect.approve_commands.lock().await.is_empty());
    assert!(connect.reject_commands.lock().await.is_empty());
}

#[tokio::test]
async fn friend_connection_decisions_authenticate_before_request_lookup() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir, connect.clone(), Arc::new(RecordingBotQueryService::default()),
        None, Some(("caller-token", "receiver-bot")),
    ).await;
    for action in ["accept", "reject"] {
        let response = app.clone().oneshot(
            Request::builder().method("POST")
                .uri(format!("/collaboration/friend-connections/requests/missing/{action}"))
                .body(Body::empty()).unwrap(),
        ).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(decision(&app, "missing", action).await.status(), StatusCode::NOT_FOUND);
    }
    assert!(connect.approve_commands.lock().await.is_empty());
    assert!(connect.reject_commands.lock().await.is_empty());
}
