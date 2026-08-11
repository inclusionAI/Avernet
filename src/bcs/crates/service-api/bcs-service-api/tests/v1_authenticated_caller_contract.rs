use bcs_service_api::application::v1::{
    AuthenticatedAccessKeyIdentity, AuthenticatedAppIdentity, AuthenticatedBotIdentity,
    AuthenticatedCaller, AuthenticatedUserIdentity, Principal, require_human,
};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};

#[test]
fn authenticated_caller_preserves_all_identity_kinds_without_selecting_an_actor() {
    let expire_at = match OffsetDateTime::parse("2030-01-01T00:00:00Z", &Rfc3339) {
        Ok(value) => value,
        Err(_) => panic!("valid contract timestamp"),
    };
    let caller = AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
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

    assert_eq!(caller.tenant.as_deref(), Some("tenant-a"));
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

#[test]
fn require_human_projects_only_the_authenticated_user() {
    let caller = AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: "staff-1".into(),
            username: "alice".into(),
            display_name: Some("Alice".into()),
            full_name: Some("Alice Example".into()),
        }),
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: "bot-extra".into(),
            owner_id: "someone-else".into(),
            app_id: 7,
            agent_code: "agent-extra".into(),
        }),
        app: None,
        access_key: None,
    };

    let principal = require_human(&caller).expect("caller has User");
    assert_eq!(principal.actor_id(), "human_staff-1");
    assert_eq!(principal.tenant(), Some("tenant-a"));
    assert!(principal.scopes().is_empty());
    assert!(matches!(principal, Principal::Human(_)));
}

#[test]
fn require_human_preserves_an_absent_user_tenant() {
    let caller = AuthenticatedCaller {
        tenant: None,
        user: Some(AuthenticatedUserIdentity {
            id: "staff-without-tenant".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    };

    let principal = require_human(&caller).expect("tenantless caller has a User");
    assert_eq!(principal.actor_id(), "human_staff-without-tenant");
    assert_eq!(principal.tenant(), None);
}

#[test]
fn require_human_rejects_a_valid_caller_without_user() {
    let caller = AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: "bot-only".into(),
            owner_id: "staff-1".into(),
            app_id: 7,
            agent_code: "agent-only".into(),
        }),
        app: None,
        access_key: None,
    };

    let error = require_human(&caller).expect_err("Bot-only caller is not Human");
    assert_eq!(error.code(), "forbidden");
}
