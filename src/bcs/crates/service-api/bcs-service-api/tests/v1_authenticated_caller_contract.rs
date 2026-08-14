use bcs_service_api::application::v1::{
    AuthenticatedAccessKeyIdentity, AuthenticatedAppIdentity, AuthenticatedBotIdentity,
    AuthenticatedCaller, AuthenticatedUserIdentity, IdentityPolicy, Principal, require_human,
    select_principal,
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

fn caller(user_id: Option<&str>, bot: Option<(&str, &str)>) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: user_id.map(|id| AuthenticatedUserIdentity {
            id: id.into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        }),
        bot: bot.map(|(bot_uuid, owner_id)| AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: owner_id.into(),
            app_id: 7,
            agent_code: "agent".into(),
        }),
        app: None,
        access_key: None,
    }
}

#[test]
fn identity_policy_defaults_to_human_only() {
    assert_eq!(IdentityPolicy::default(), IdentityPolicy::HumanOnly);

    let error = select_principal(&caller(None, Some(("bot-1", "user-1"))), Default::default())
        .expect_err("default policy rejects Bot-only callers");
    assert_eq!(error.code(), "forbidden");
}

#[test]
fn identity_policy_selects_the_requested_identity_kind() {
    let human = select_principal(
        &caller(Some("user-1"), None),
        IdentityPolicy::HumanOnly,
    )
    .expect("Human policy");
    assert_eq!(human.actor_id(), "human_user-1");

    let bot = select_principal(
        &caller(None, Some(("bot-1", "user-1"))),
        IdentityPolicy::BotOnly,
    )
    .expect("Bot policy");
    assert_eq!(bot.actor_id(), "bot-1");
}

#[test]
fn human_or_owned_bot_is_bot_first_when_both_are_consistent() {
    let selected = select_principal(
        &caller(Some("user-1"), Some(("bot-1", "user-1"))),
        IdentityPolicy::HumanOrOwnedBot,
    )
    .expect("owned Bot may act with its User");

    assert!(matches!(selected, Principal::Bot(_)));
    assert_eq!(selected.actor_id(), "bot-1");
}

#[test]
fn human_or_owned_bot_rejects_a_mismatched_user_and_bot() {
    let error = select_principal(
        &caller(Some("user-1"), Some(("bot-1", "user-2"))),
        IdentityPolicy::HumanOrOwnedBot,
    )
    .expect_err("User may not impersonate another owner's Bot");

    assert_eq!(error.code(), "forbidden");
}

#[test]
fn app_only_does_not_become_a_file_actor() {
    let mut app_only = caller(None, None);
    app_only.app = Some(AuthenticatedAppIdentity {
        app_id: 7,
        app_name: "Contract App".into(),
        owners: "owner".into(),
        app_type: "THIRD_PARTY".into(),
    });

    let error = select_principal(&app_only, IdentityPolicy::HumanOrOwnedBot)
        .expect_err("App is not a collaboration actor");
    assert_eq!(error.code(), "forbidden");
}

#[test]
fn an_extra_app_does_not_override_a_valid_bot_actor() {
    let mut caller = caller(None, Some(("bot-1", "user-1")));
    caller.app = Some(AuthenticatedAppIdentity {
        app_id: 7,
        app_name: "Contract App".into(),
        owners: "owner".into(),
        app_type: "THIRD_PARTY".into(),
    });

    let selected = select_principal(&caller, IdentityPolicy::HumanOrOwnedBot)
        .expect("Bot remains the effective actor");
    assert_eq!(selected.actor_id(), "bot-1");
}
