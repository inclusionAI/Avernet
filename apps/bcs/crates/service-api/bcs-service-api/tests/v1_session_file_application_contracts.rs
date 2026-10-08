use bcs_service_api::application::v1::{
    AuthenticatedCaller, AuthenticatedUserIdentity, ListSessionFiles, PrepareSessionFile,
    SessionFileActorKind, SessionFileApplicationService, SessionFileStatus, SessionFileView,
};

fn caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: None,
        user: Some(AuthenticatedUserIdentity {
            id: "user-1".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

#[test]
fn session_file_service_is_object_safe() {
    fn accepts_trait_object(_service: &dyn SessionFileApplicationService) {}
    let _ = accepts_trait_object;
}

#[test]
fn commands_carry_authenticated_caller_and_transport_neutral_inputs() {
    let prepare = PrepareSessionFile {
        caller: caller(),
        session_id: "session-1".into(),
        file_name: "report.txt".into(),
        size: 42,
        mime_type: "text/plain".into(),
    };
    assert_eq!(prepare.session_id, "session-1");
    assert_eq!(prepare.file_name, "report.txt");
    assert_eq!(prepare.size, 42);

    let list = ListSessionFiles {
        caller: caller(),
        session_id: "session-1".into(),
        prefix: Some("report".into()),
        status: Some(SessionFileStatus::Ready),
        limit: 25,
        offset: 5,
    };
    assert_eq!(list.limit, 25);
    assert_eq!(list.offset, 5);
}

#[test]
fn file_views_use_v1_snake_case_enums_and_omit_storage_handles() {
    assert_eq!(
        serde_json::to_string(&SessionFileStatus::Pending).expect("status"),
        "\"pending\""
    );
    assert_eq!(
        serde_json::to_string(&SessionFileActorKind::Bot).expect("actor kind"),
        "\"bot\""
    );

    let view = SessionFileView {
        file_id: "file-1".into(),
        session_id: "session-1".into(),
        file_name: "report.txt".into(),
        mime_type: "text/plain".into(),
        size: 42,
        sha256: None,
        owner: bcs_service_api::application::v1::SessionFileActor {
            actor_kind: SessionFileActorKind::Human,
            actor_id: "human_user-1".into(),
        },
        storage_backend: "local".into(),
        status: SessionFileStatus::Ready,
        created_at: 10,
        updated_at: 11,
    };
    let value = serde_json::to_value(view).expect("file view");

    assert_eq!(value["status"], "ready");
    assert_eq!(value["owner"]["actor_kind"], "human");
    assert!(value.get("object_handle").is_none());
}
