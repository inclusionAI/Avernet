//! Rule 25 driver: the `BotAuthorityService` facade
//! conformance harness, driven here where the impl
//! lives ([`BotAuthorityServiceImpl`], in
//! `bcs-app-bot`).
//!
//! Two REAL pieces:
//! 1. a HERE-composed `BotAuthorityServiceImpl`
//!    (the same constructor the composition root
//!    calls) serving a real manager read;
//! 2. the FULL shared facade harness
//!    (`bot_authority_service_contract_tests`),
//!    whose composed fixture uses the SAME
//!    constructor — its assertions execute in
//!    this run.

use std::sync::Arc;

use bcs_app_bot::authority_policy::AuthorityServicePolicy;
use bcs_app_bot::BotAuthorityServiceImpl;
use bcs_edge_permission::BotAuthorityCoreServiceImpl;
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::application::v1::bot_authority::ManagerListQuery;
use bcs_service_api::application::v1::identity::{
    AuthenticatedCaller, AuthenticatedUserIdentity,
};
use bcs_service_api::application::v1::bot_authority::BotAuthorityService;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_test_support::bot_authority::{authority_service_fixture, seed_bot};
use bcs_test_support::contract::bot_authority_service::bot_authority_service_contract_tests;

fn human(user_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: user_id.into(),
            username: user_id.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

/// The HERE-composed facade answers real reads
/// through the real core/store chain.
#[tokio::test]
async fn bot_authority_facade_composes_here() {
    let repo = Arc::new(MemoryBotRepo::new());
    let env = bcs_config::resolve_env_str();
    let rule = bcs_app_bot::authority_policy::AuthorityAppScopeRule::from_config_strings(
        bcs_test_support::AUTHORITY_FIXTURE_APP_NAME,
        "tenant-a",
        &["managers:sync".to_string()],
    )
    .expect("valid scope rule");
    let service = BotAuthorityServiceImpl::new(
        Arc::new(BotAuthorityCoreServiceImpl::new(
            repo.clone() as Arc<dyn BotAuthorityRepoPort>,
            env.clone(),
        )),
        Arc::new(BotAuthorityCoreServiceImpl::new(
            repo.clone() as Arc<dyn BotAuthorityRepoPort>,
            env.clone(),
        )),
        Arc::new(bcs_test_support::bot_authority::InviteGateStub {
            allow: true,
            human_checks: std::sync::Mutex::new(Vec::new()),
        }),
        true,
        AuthorityServicePolicy::new(&env, vec![rule])
            .expect("fixture policy"),
        env,
    );
    seed_bot(&repo, "bot_facade", "facade_owner", &[]).await;
    let page = service
        .managers(&human("facade_owner"), ManagerListQuery {
            bot_id: "bot_facade".into(),
            offset: 0,
            limit: 50,
        })
        .await
        .expect("composed facade read");
    assert_eq!(page.owner.owner_user_id, "facade_owner");
}

/// The FULL shared facade harness: admission
/// without App fallback, trusted-App scope
/// denials, owner-target protection, the transfer
/// round trip and the revoked-edge reads all
/// EXECUTE here.
#[tokio::test]
async fn bot_authority_service_contract_composed() {
    let fixture = authority_service_fixture(true).await;
    // The fixture's facade is the SAME constructor
    // shape exercised above; the harness runs its
    // full semantic set.
    let _ = &fixture.service;
    bot_authority_service_contract_tests().await;
}
