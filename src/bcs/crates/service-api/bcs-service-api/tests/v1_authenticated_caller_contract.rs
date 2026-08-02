use bcs_service_api::application::v1::{
    AuthenticatedAccessKeyIdentity, AuthenticatedAppIdentity, AuthenticatedBotIdentity,
    AuthenticatedCaller, AuthenticatedUserIdentity,
};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};

#[test]
fn authenticated_caller_preserves_all_identity_kinds_without_selecting_an_actor() {
    let expire_at =
        OffsetDateTime::parse("2030-01-01T00:00:00Z", &Rfc3339).expect("valid contract timestamp");
    let caller = AuthenticatedCaller {
        tenant: "tenant-a".into(),
        user: Some(AuthenticatedUserIdentity {
            id: "user-1".into(),
            username: "alice".into(),
            display_name: Some("Alice".into()),
            full_name: None,
        }),
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: "bot-1".into(),
            owner_id: "user-1".into(),
            app_id: 7,
            agent_code: "agent-1".into(),
        }),
        app: Some(AuthenticatedAppIdentity {
            app_id: 7,
            app_name: "Contract App".into(),
            owners: "contract-owner".into(),
            app_type: "THIRD_PARTY".into(),
        }),
        access_key: Some(AuthenticatedAccessKeyIdentity {
            access_key: "ak-test-1".into(),
            expire_at,
        }),
    };

    assert_eq!(caller.tenant, "tenant-a");
    assert_eq!(
        caller.user.as_ref().map(|value| value.id.as_str()),
        Some("user-1")
    );
    assert_eq!(
        caller.bot.as_ref().map(|value| value.bot_uuid.as_str()),
        Some("bot-1")
    );
    assert_eq!(caller.app.as_ref().map(|value| value.app_id), Some(7));
    assert_eq!(
        caller
            .access_key
            .as_ref()
            .map(|value| value.access_key.as_str()),
        Some("ak-test-1"),
    );
}
