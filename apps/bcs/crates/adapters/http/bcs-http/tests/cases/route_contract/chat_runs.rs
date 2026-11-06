use super::*;

#[derive(Default)]
struct RecordingA2aChat {
    get_runs: Mutex<Vec<(String, String)>>,
    cancel_runs: Mutex<Vec<(String, String)>>,
    run_state: Mutex<Option<String>>,
}

impl RecordingA2aChat {
    async fn set_run_state(&self, state: &str) {
        *self.run_state.lock().await = Some(state.to_string());
    }
}

#[async_trait::async_trait]
impl A2aChatRunService for RecordingA2aChat {
    async fn start_async_chat(
        &self,
        _cmd: AsyncA2aChatCommand,
    ) -> ServiceResult<AsyncA2aChatAccepted> {
        unreachable!("start_async_chat is not used by route contract tests")
    }

    async fn get_run(&self, cmd: ChatRunQueryCommand) -> ServiceResult<A2aRunStatus> {
        A2aChatService::get_run(self, cmd.caller, &cmd.run_id).await
    }

    async fn cancel_run(&self, cmd: ChatRunCancelCommand) -> ServiceResult<A2aRunStatus> {
        A2aChatService::cancel_run(self, cmd.caller, &cmd.run_id).await
    }
}

#[async_trait::async_trait]
impl A2aChatService for RecordingA2aChat {
    async fn chat(&self, _cmd: A2aChatCommand) -> ServiceResult<A2aChatOutcome> {
        unreachable!("chat is not used by route contract tests")
    }

    async fn get_run(&self, caller: CallerContext, run_id: &str) -> ServiceResult<A2aRunStatus> {
        let caller_id = match caller {
            CallerContext::Bot(bot) => bot.bot_uuid,
            other => format!("{other:?}"),
        };
        self.get_runs
            .lock()
            .await
            .push((caller_id, run_id.to_string()));
        let state = self
            .run_state
            .lock()
            .await
            .clone()
            .unwrap_or_else(|| "completed".to_string());
        let is_terminal = state == "completed";
        Ok(A2aRunStatus {
            run_id: run_id.to_string(),
            status: state.clone(),
            response: Some(serde_json::json!({
                "state": state,
                "content": "done",
                "version": 7,
                "is_terminal": is_terminal
            })),
        })
    }

    async fn wait_run(
        &self,
        _caller: CallerContext,
        _run_id: &str,
        _since_version: u64,
        _wait_ms: u64,
    ) -> ServiceResult<A2aRunStatus> {
        unreachable!("wait_run is not used by this test")
    }

    async fn record_run_event(&self, _run_id: &str, _event_json: &str) -> ServiceResult<bool> {
        unreachable!("record_run_event is not used by route contract tests")
    }

    async fn fail_run_if_open(&self, _run_id: &str, _error: &str) -> ServiceResult<bool> {
        unreachable!("fail_run_if_open is not used by route contract tests")
    }

    async fn cancel_run(&self, caller: CallerContext, run_id: &str) -> ServiceResult<A2aRunStatus> {
        let caller_id = match caller {
            CallerContext::Bot(bot) => bot.bot_uuid,
            other => format!("{other:?}"),
        };
        self.cancel_runs
            .lock()
            .await
            .push((caller_id, run_id.to_string()));
        Ok(A2aRunStatus {
            run_id: run_id.to_string(),
            status: "cancelled".to_string(),
            response: Some(serde_json::json!({
                "state": "cancelled",
                "content": "partial",
                "cancelled": true,
                "version": 3
            })),
        })
    }

    async fn cleanup_expired(
        &self,
        _now_ms: u64,
        _retention_ms: u64,
    ) -> ServiceResult<(Vec<String>, Vec<String>)> {
        unreachable!("cleanup_expired is not used by route contract tests")
    }
}

#[derive(Default)]
struct RecordingChatRunCleanup {
    unregistered: Mutex<Vec<String>>,
}

#[async_trait::async_trait]
impl ChatRunCleanupPort for RecordingChatRunCleanup {
    async fn unregister(&self, run_id: &str) {
        self.unregistered.lock().await.push(run_id.to_string());
    }
}
#[tokio::test]
async fn chat_run_route_uses_a2a_service_and_bot_token_identity() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("caller-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "caller-bot".to_string())
        .await;
    let a2a_chat = Arc::new(RecordingA2aChat::default());
    let services = Services::builder()
        .registry(registry)
        .a2a_chat(a2a_chat.clone())
        .a2a_chat_runs(a2a_chat.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/chat/runs/run-1")
                .header("authorization", "Bearer token-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["run_id"], "run-1");
    assert_eq!(json["state"], "completed");
    assert_eq!(json["response"]["content"], "done");

    let calls = a2a_chat.get_runs.lock().await;
    assert_eq!(
        calls.as_slice(),
        &[("caller-bot".to_string(), "run-1".to_string())]
    );
}

#[tokio::test]
async fn chat_run_route_downgrades_submitted_for_legacy_chat_version() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("caller-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "caller-bot".to_string())
        .await;
    let a2a_chat = Arc::new(RecordingA2aChat::default());
    a2a_chat.set_run_state("submitted").await;
    let services = Services::builder()
        .registry(registry)
        .a2a_chat(a2a_chat.clone())
        .a2a_chat_runs(a2a_chat)
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/chat/runs/run-1")
                .header("authorization", "Bearer token-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["state"], "running");
    assert_eq!(json["is_terminal"], false);
}

#[tokio::test]
async fn chat_run_route_returns_submitted_for_chat_version_2() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("caller-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "caller-bot".to_string())
        .await;
    let a2a_chat = Arc::new(RecordingA2aChat::default());
    a2a_chat.set_run_state("submitted").await;
    let services = Services::builder()
        .registry(registry)
        .a2a_chat(a2a_chat.clone())
        .a2a_chat_runs(a2a_chat)
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/chat/runs/run-1")
                .header("authorization", "Bearer token-1")
                .header("X-BCS-CHAT-VERSION", "2")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["state"], "submitted");
    assert_eq!(json["is_terminal"], false);
}
#[tokio::test]
async fn cancel_chat_run_route_uses_a2a_run_service() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("caller-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "caller-bot".to_string())
        .await;
    let a2a_chat = Arc::new(RecordingA2aChat::default());
    let cleanup = Arc::new(RecordingChatRunCleanup::default());
    let services = Services::builder()
        .registry(registry)
        .a2a_chat(a2a_chat.clone())
        .a2a_chat_runs(a2a_chat.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services).with_chat_run_cleanup(cleanup.clone()));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/chat/runs/run-2/cancel")
                .header("authorization", "Bearer token-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["run_id"], "run-2");
    assert_eq!(json["cancelled"], true);
    assert_eq!(json["state"], "cancelled");
    assert_eq!(json["response"]["content"], "partial");

    let calls = a2a_chat.cancel_runs.lock().await;
    assert_eq!(
        calls.as_slice(),
        &[("caller-bot".to_string(), "run-2".to_string())]
    );
    let cleanup_calls = cleanup.unregistered.lock().await;
    assert!(cleanup_calls.is_empty());
}
