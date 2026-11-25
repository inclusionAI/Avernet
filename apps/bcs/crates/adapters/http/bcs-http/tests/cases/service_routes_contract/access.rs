use super::*;

#[tokio::test]
async fn service_invocation_without_api_key_uses_bot_identity() {
    let tmp = TempDir::new().unwrap();
    let bot_registry = Arc::new(BotCore::with_base_dir(tmp.path().to_path_buf()));
    bot_registry
        .store_token_mapping("bot-token".to_string(), "bot-manager".to_string())
        .await;

    let group_store = Arc::new(GroupStore::new());
    let group = Group::new(
        "group-1",
        "bot-manager",
        vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
    );
    group_store.upsert(group).await.unwrap();

    let sessions = Arc::new(RecordingSessionManagement::default());
    let mut services = Services::noop();
    services.registry = bot_registry;
    services.group = group_store;
    services.session_management = sessions.clone();

    let app = build_router(
        HttpAppState::new(services).with_service_api_keys(Arc::new(ApiKeyRegistry::new(vec![
            ApiKeyEntry {
                name: "svc-a".to_string(),
                sha256: sha256_hex("secret"),
                bound_groups: vec!["group-1".to_string()],
            },
        ]))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/services/group-1/sessions")
                .header("content-type", "application/json")
                .header("Authorization", "Bearer bot-token")
                .body(Body::from(json!({"input": {"task": "audit"}}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let commands = sessions.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0].params.caller_principal.as_deref(),
        Some("bot:bot-manager")
    );
    assert_eq!(commands[0].params.created_by.as_deref(), Some("bot:bot-manager"));
}

#[tokio::test]
async fn service_invocation_without_api_key_or_bot_identity_is_rejected() {
    let app = build_router(HttpAppState::new(Services::noop()));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/services/group-1/sessions")
                .header("content-type", "application/json")
                .body(Body::from(json!({"input": {"task": "audit"}}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body: serde_json::Value = serde_json::from_slice(
        &to_bytes(response.into_body(), usize::MAX).await.unwrap(),
    )
    .unwrap();
    assert_eq!(body["error"], "missing_bot_identity");
}
// ----- get_service_session caller_principal isolation -----
//
// Legacy server.rs:13648-13658 enforces that only the original caller can
// read invocation details: `sess.caller_principal` must match
// `caller.caller_principal`, otherwise 403. Two distinct svc-keys both
// bound to the same group must NOT be able to read each other's sessions.

#[derive(Default)]
struct StaticSessionStore {
    session: Mutex<Option<Session>>,
}

#[async_trait::async_trait]
impl SessionManagementService for StaticSessionStore {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!("not used by these tests")
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self.session.lock().await.as_ref().filter(|s| s.id == sid).cloned())
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self
            .session
            .lock()
            .await
            .as_ref()
            .map(|s| s.id == session_id && s.group_id == group_id)
            .unwrap_or(false))
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}

#[tokio::test]
async fn get_service_session_rejects_cross_caller_principal_access() {
    use bcs_service_api::ParticipantRole;
    let group_store = Arc::new(GroupStore::new());
    let group = Group::new(
        "group-1",
        "bot-manager",
        vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
    );
    group_store.upsert(group).await.unwrap();

    // Two svc-keys, both bound to "group-1".
    let raw_a = "key-a";
    let raw_b = "key-b";
    let hash_a = sha256_hex(raw_a);
    let hash_b = sha256_hex(raw_b);

    // A session created by caller A.
    let session = Session {
        id: "group-1:abcdef12".to_string(),
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::ServiceInvocation,
        participants: vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        activation_count: 1,
        message_visibility_version: 1,
        caller_principal: Some(caller_principal_for(&hash_a)),
        created_by: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        collected_at: None,
    };

    let sessions = Arc::new(StaticSessionStore {
        session: Mutex::new(Some(session)),
    });

    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = sessions.clone();

    let app = build_router(
        HttpAppState::new(services).with_service_api_keys(Arc::new(ApiKeyRegistry::new(vec![
            ApiKeyEntry {
                name: "svc-a".to_string(),
                sha256: hash_a.clone(),
                bound_groups: vec!["group-1".to_string()],
            },
            ApiKeyEntry {
                name: "svc-b".to_string(),
                sha256: hash_b.clone(),
                bound_groups: vec!["group-1".to_string()],
            },
        ]))),
    );

    // Caller B reads A's session → must be 403, not 200.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/services/group-1/sessions/group-1:abcdef12")
                .header("X-BCS-Service-Key", raw_b)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // Caller A reads its own session → 200.
    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/services/group-1/sessions/group-1:abcdef12")
                .header("X-BCS-Service-Key", raw_a)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
}
