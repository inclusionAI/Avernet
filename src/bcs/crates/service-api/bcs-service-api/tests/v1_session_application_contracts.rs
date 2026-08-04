use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ActorKind, AddSessionParticipant, ApplicationError, AuthenticatedCaller,
    AuthenticatedUserIdentity, BotParticipantMode, CompleteSession, CreateSession,
    CreateSessionOutcome, DeleteResult, DeleteSession,
    DeleteSessionParticipant, GetSession, ListSessionMessages, ListSessions, MessageSenderKind,
    Page, ParticipantMode, SessionCompletionResult, SessionDetail, SessionMessage,
    SessionMessageKind, SessionMessagePage, SessionMessageService, SessionParticipant,
    SessionParticipantInput, SessionService, SessionStatus, UpdateSession, UpdateSessionParticipant,
};

fn human_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: "tenant-a".into(),
        user: Some(AuthenticatedUserIdentity {
            id: "staff-1".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

struct NoopSessionService;

#[async_trait]
impl SessionService for NoopSessionService {
    async fn create(&self, _command: CreateSession) -> Result<CreateSessionOutcome, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn list(&self, _command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        Ok(Page::empty(0, 20))
    }

    async fn get(&self, _query: GetSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn update(&self, _command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn delete(&self, _command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        Ok(DeleteResult { deleted: false })
    }

    async fn complete(
        &self,
        _command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn add_participant(
        &self,
        _command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn update_participant(
        &self,
        _command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn delete_participant(
        &self,
        _command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }
}

struct NoopSessionMessageService;

#[async_trait]
impl SessionMessageService for NoopSessionMessageService {
    async fn list(
        &self,
        _query: ListSessionMessages,
    ) -> Result<SessionMessagePage, ApplicationError> {
        Ok(SessionMessagePage {
            messages: Vec::new(),
            next_cursor: None,
            has_more: false,
        })
    }
}

// Re-exported for the list return type assertion below.
use bcs_service_api::application::v1::SessionSummary;

#[test]
fn session_service_is_object_safe() {
    fn accepts_service(_: &dyn SessionService) {}
    fn accepts_message_service(_: &dyn SessionMessageService) {}
    accepts_service(&NoopSessionService);
    accepts_message_service(&NoopSessionMessageService);
}

#[test]
fn session_commands_carry_caller_and_no_raw_credentials() {
    let caller = human_caller();
    let create = CreateSession {
        caller: caller.clone(),
        group_id: "g1".into(),
        driver_bot_uuid: "bot-1".into(),
        title: Some("plan".into()),
        input: None,
        participants: vec![SessionParticipantInput {
            bot_uuid: "bot-2".into(),
            mode: Some(BotParticipantMode::Auto),
        }],
    };
    let list = ListSessions {
        caller: caller.clone(),
        group_id: "g1".into(),
        view_bot_id: Some("bot-1".into()),
        offset: 0,
        limit: 25,
        status: Some(SessionStatus::Running),
    };
    let get = GetSession {
        caller: caller.clone(),
        session_id: "s1".into(),
    };
    let update = UpdateSession {
        caller: caller.clone(),
        session_id: "s1".into(),
        title: Some("renamed".into()),
    };
    let delete = DeleteSession {
        caller: caller.clone(),
        session_id: "s1".into(),
    };
    let complete = CompleteSession {
        caller: caller.clone(),
        session_id: "s1".into(),
    };
    let add = AddSessionParticipant {
        caller: caller.clone(),
        session_id: "s1".into(),
        bot_uuid: "bot-3".into(),
        mode: Some(BotParticipantMode::Muted),
    };
    let update_p = UpdateSessionParticipant {
        caller: caller.clone(),
        session_id: "s1".into(),
        bot_uuid: "bot-3".into(),
        mode: BotParticipantMode::Auto,
    };
    let remove_p = DeleteSessionParticipant {
        caller,
        session_id: "s1".into(),
        bot_uuid: "bot-3".into(),
    };
    for cmd in [
        &create.caller,
        &list.caller,
        &get.caller,
        &update.caller,
        &delete.caller,
        &complete.caller,
        &add.caller,
        &update_p.caller,
        &remove_p.caller,
    ] {
        let s = format!("{cmd:?}");
        assert!(!s.contains("Cookie") && !s.contains("Bearer") && !s.contains("sender"));
    }
    assert_eq!(create.driver_bot_uuid, "bot-1");
    assert_eq!(create.participants.len(), 1);
    assert_eq!(list.status, Some(SessionStatus::Running));
}

#[test]
fn session_message_uses_id_not_message_id() {
    let message = SessionMessage {
        id: "m1".into(),
        session_seq: 3,
        sender_id: "bot-1".into(),
        sender_type: MessageSenderKind::Bot,
        kind: SessionMessageKind::Text,
        content: "hello".into(),
        created_at: 99,
    };
    let json = serde_json::to_value(&message).expect("serialize SessionMessage");
    assert_eq!(json["id"], "m1");
    assert_eq!(json["session_seq"], 3);
    assert_eq!(json["sender_type"], "bot");
    assert_eq!(json["kind"], "text");
    assert!(json.get("message_id").is_none());
}

#[test]
fn bot_participant_mode_is_narrower_than_domain_participant_mode() {
    let auto = serde_json::to_value(BotParticipantMode::Auto).expect("serialize Auto");
    let muted = serde_json::to_value(BotParticipantMode::Muted).expect("serialize Muted");
    assert_eq!(auto, "auto");
    assert_eq!(muted, "muted");
    // Round-trip the entire vocabulary to prove V1 only has two variants.
    for raw in ["\"auto\"", "\"muted\""] {
        let parsed: BotParticipantMode =
            serde_json::from_str(raw).expect("deserialize BotParticipantMode");
        assert_eq!(serde_json::to_string(&parsed).unwrap(), raw);
    }
    assert!(serde_json::from_str::<BotParticipantMode>("\"present\"").is_err());
    assert!(serde_json::from_str::<BotParticipantMode>("\"absent\"").is_err());
}

#[test]
fn session_participant_serializes_bot_and_human_actors() {
    // Vey7i: the V1 `SessionParticipant` contract admits both Bot and Human
    // actors. A Bot participant serializes with `actor_kind: "bot"` and the
    // Bot-valid `mode: "auto"`; a Human participant (inserted by the legacy
    // invitation-accept path) serializes with `actor_kind: "human"` and the
    // Human-valid `mode: "present"`.
    let bot = SessionParticipant {
        actor_id: "bot-1".into(),
        actor_kind: ActorKind::Bot,
        name: Some("Zhang San".into()),
        role: bcs_service_api::application::v1::ParticipantRole::Driver,
        mode: ParticipantMode::Auto,
        joined_at: Some(42),
    };
    let bot_json = serde_json::to_value(&bot).expect("serialize Bot SessionParticipant");
    assert_eq!(bot_json["actor_id"], "bot-1");
    assert_eq!(bot_json["actor_kind"], "bot");
    assert_eq!(bot_json["role"], "driver");
    assert_eq!(bot_json["mode"], "auto");
    assert_eq!(bot_json["joined_at"], 42);

    let human = SessionParticipant {
        actor_id: "human_staff-1".into(),
        actor_kind: ActorKind::Human,
        name: Some("Alice".into()),
        role: bcs_service_api::application::v1::ParticipantRole::Consultant,
        mode: ParticipantMode::Present,
        joined_at: None,
    };
    let human_json = serde_json::to_value(&human).expect("serialize Human SessionParticipant");
    assert_eq!(human_json["actor_id"], "human_staff-1");
    assert_eq!(human_json["actor_kind"], "human");
    assert_eq!(human_json["role"], "consultant");
    assert_eq!(human_json["mode"], "present");
    assert!(human_json.get("joined_at").is_none());
}

#[test]
fn authenticated_caller_can_be_carried_in_session_command() {
    let command = ListSessionMessages {
        caller: human_caller(),
        session_id: "s1".into(),
        before: None,
        limit: 10,
        view_bot_id: None,
    };
    assert_eq!(command.caller.user.expect("User").id, "staff-1");
    assert_eq!(command.session_id, "s1");
}
