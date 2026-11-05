use super::*;

#[tokio::test]
async fn service_invocation_without_policy_uses_api_key_and_seeds_group_session_fields() {
    let group_store = Arc::new(GroupStore::new());
    let group = Group::new(
        "group-1",
        "bot-manager",
        vec![
            Participant::bot("bot-manager", ParticipantRole::Manager),
            Participant::bot("bot-worker", ParticipantRole::Worker),
        ],
    );
    group_store.upsert(group).await.unwrap();

    let sessions = Arc::new(RecordingSessionManagement::default());
    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = sessions.clone();

    let raw_key = "secret";
    let hash = sha256_hex(raw_key);
    let app = build_router(
        HttpAppState::new(services).with_service_api_keys(Arc::new(ApiKeyRegistry::new(vec![
            ApiKeyEntry {
                name: "svc-a".to_string(),
                sha256: hash.clone(),
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
                .header("X-BCS-Service-Key", raw_key)
                .body(Body::from(
                    json!({
                        "caller_id": "trace-1",
                        "session_title": "性能审计",
                        "input": {"task": "audit"},
                        "meta": {"k": "v"}
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let body: serde_json::Value = serde_json::from_slice(
        &to_bytes(response.into_body(), usize::MAX).await.unwrap(),
    )
    .unwrap();
    assert_eq!(body["session_id"], "group-1:abcdef12");

    let commands = sessions.commands.lock().await;
    assert_eq!(commands.len(), 1);
    let command = &commands[0];
    assert_eq!(command.group_id, "group-1");
    assert_eq!(command.session_id, None);
    assert_eq!(command.params.session_kind, SessionKind::ServiceInvocation);
    assert_eq!(command.params.participants.len(), 2);
    assert_eq!(command.params.group_version, Some(1));
    assert_eq!(command.params.caller_id.as_deref(), Some("trace-1"));
    assert_eq!(
        command.params.caller_principal.as_deref(),
        Some(caller_principal_for(&hash).as_str())
    );
    assert_eq!(command.params.created_by, command.params.caller_principal);
    assert_eq!(command.params.session_title.as_deref(), Some("性能审计"));
}
// ----- post_invocation reactivate semantics (fix #9) -----
//
// Legacy server.rs:12529-12535: reactivating a service-invocation session
// that is still Running returns 409 `session_is_running_cannot_invoke`,
// not 400. The use-case layer must surface this as Conflict, not
// InvalidParams, so the HTTP layer can map it to 409.

#[derive(Default)]
struct ReactivateConflictSessions {
    session: Mutex<Option<Session>>,
}

#[async_trait::async_trait]
impl SessionManagementService for ReactivateConflictSessions {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        // Simulate the real session core: reactivating a Running session
        // returns SessionUseCaseError::Conflict.
        if cmd.session_id.is_some() {
            return Err(SessionUseCaseError::Conflict(
                "session_is_running_cannot_invoke".to_string(),
            ));
        }
        unimplemented!("only reactivate path used by this test")
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self
            .session
            .lock()
            .await
            .as_ref()
            .filter(|s| s.id == sid)
            .cloned())
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
async fn post_invocation_running_session_returns_409() {
    use bcs_service_api::ParticipantRole;

    let group_store = Arc::new(GroupStore::new());
    let group = Group::new(
        "group-1",
        "bot-manager",
        vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
    );
    group_store.upsert(group).await.unwrap();

    // Pre-existing running service-invocation session.
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
        caller_principal: None,
        created_by: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        collected_at: None,
    };
    let sessions = Arc::new(ReactivateConflictSessions {
        session: Mutex::new(Some(session)),
    });

    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = sessions.clone();

    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/services/group-1/sessions")
                .header("content-type", "application/json")
                .header("X-BCS-Service-Key", "test-key")
                .body(Body::from(
                    json!({
                        "session_id": "group-1:abcdef12",
                        "input": {"task": "audit"},
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CONFLICT);
}
// ----- Bug fix #13: post_invocation must use belongs_to_group contract -----
//
// services.rs handlers must consult `SessionManagementService::belongs_to_group`,
// not `session.group_id == group_id`. Future stores might add env scoping
// or soft-delete filtering that the field comparison would silently bypass.
//
// We simulate this by returning a session whose `group_id` field matches the
// URL but whose `belongs_to_group` returns false. The handler must honor
// the contract method (404), not the field (200/202).

#[derive(Default)]
struct ContractMismatchSessions {
    session: Mutex<Option<Session>>,
}

#[async_trait::async_trait]
impl SessionManagementService for ContractMismatchSessions {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!()
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self
            .session
            .lock()
            .await
            .as_ref()
            .filter(|s| s.id == sid)
            .cloned())
    }

    async fn belongs_to_group(
        &self,
        _session_id: &str,
        _group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        // Force the contract to disagree with `session.group_id`.
        Ok(false)
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
async fn post_invocation_honors_belongs_to_group_contract_over_field_match() {
    use bcs_service_api::ParticipantRole;
    let group_store = Arc::new(GroupStore::new());
    let group = Group::new(
        "group-1",
        "bot-manager",
        vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
    );
    group_store.upsert(group).await.unwrap();

    let session = Session {
        id: "group-1:abcdef12".to_string(),
        // group_id field intentionally matches URL — but belongs_to_group
        // contract method returns false. The handler must trust the method.
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Completed,
        session_kind: SessionKind::ServiceInvocation,
        participants: vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        activation_count: 1,
        message_visibility_version: 1,
        caller_principal: None,
        created_by: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: 1,
        updated_at: 1,
        completed_at: Some(2),
        meta: None,
        collected_at: None,
    };
    let sessions = Arc::new(ContractMismatchSessions {
        session: Mutex::new(Some(session)),
    });

    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = sessions.clone();

    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/services/group-1/sessions")
                .header("content-type", "application/json")
                .header("X-BCS-Service-Key", "test-key")
                .body(Body::from(
                    json!({
                        "session_id": "group-1:abcdef12",
                        "input": {},
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(
        response.status(),
        StatusCode::NOT_FOUND,
        "post_invocation must use belongs_to_group contract, not session.group_id field match"
    );
}
