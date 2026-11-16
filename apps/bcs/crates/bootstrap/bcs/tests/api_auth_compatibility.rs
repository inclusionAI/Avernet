//! The Task 13 compatibility parameter table (N1–N5) — one counting HTTP
//! test per row, driven through the REAL chain fixture (Task 12 assembly
//! over the real store, Stateful wrap, real routers; fakes only at the
//! registry/business/provider seams and the faulting DbPlugin) plus the
//! REAL production constructor for the N4 resigner arm.
//!
//! Rows (verbatim from the brief):
//! - N1: signed Bot deleted/disabled/Actor-mismatch → 401, business==0,
//!   NO OAuth fallback; registry fault → 502 `bot_identity_unavailable`;
//!   strict session DB fault → 503 `auth_dependency_unavailable`.
//! - N2: native Human's Bot/Actor/Gateway rejections + expected-user
//!   mismatch keep their original codes, with no-store and business==0;
//!   Draft public GET and Bot Self / Agent Self keep their dedicated
//!   protocols, untouched by the new composite.
//! - N3: Bot Authority keeps ITS application admission (no wrongly
//!   applied ordinary Human gate) while both sides actually use the new
//!   verifier; public-groups stays 401 without credentials and the
//!   ordinary invite gate stays `invite_code_required`, non-bypassable.
//! - N4: an OAuth-only chain reads ZERO gateway secrets; enabling
//!   friend-auth-sync keeps the LEGACY resigner secret contract (missing
//!   → startup FAILURE).
//! - N5: one valid GitHub cookie through the four-provider chain spends
//!   exactly ONE strict SELECT, with no per-provider duplicate queries.

use std::collections::BTreeMap;
use std::sync::atomic::Ordering;

use axum::http::StatusCode;
use serde_json::Value;

#[allow(dead_code)] // shared MockBot/e2e helpers; this suite uses create_test_config only
mod helpers;

#[path = "support/auth_db.rs"]
mod auth_db;
#[path = "support/api_fakes.rs"]
mod api_fakes;
#[path = "support/api_chain.rs"]
mod api_chain;

use api_chain::{
    ApiChainFixture, FixtureSpec, GATEWAY_SIGNING_KEY_REF, RegistryScript, drive,
    gateway_bot_principal_token, session_cookie_only,
};
use auth_db::NamedOp;

const GROUPS: &str = "/openapi/v1/collaboration/groups";
const PUBLIC_GROUPS: &str = "/openapi/v1/collaboration/public-groups";
const AUTHORITY_MANAGERS: &str = "/api/v1/collaboration/bots/bot-1/managers";
const USER_GROUPS: &str = "/collaboration/user/groups";
const DRAFT_READ: &str = "/api/v1/collaboration/drafts/task13-draft/revisions/1";
const BOT_SELF_ME: &str = "/api/v1/collaboration/bots/me";
const AGENT_SELF_ME: &str = "/api/v1/collaboration/agents/me";

fn error_code(body: &Value) -> String {
    body["data"]["error_code"].as_str().unwrap_or_default().to_string()
}

fn expected_headers() -> Vec<(&'static str, String)> {
    vec![
        ("sec-fetch-site", "same-origin".to_string()),
        ("x-bcs-expected-user", "staff-13".to_string()),
    ]
}

// ---------------------------------------------------------------------------
// N1
// ---------------------------------------------------------------------------

/// N1 row 1: a signed Gateway Bot whose persisted state is gone (deleted,
/// disabled, or its Actor no longer matches the registration) is still
/// rejected with the ORIGINAL 401 — and the presence of a VALID OAuth
/// session cookie in the same request must NOT trigger a fallback to the
/// later source; the business service is never entered.
#[tokio::test]
async fn n1_rejected_gateway_bots_stay_401_without_an_oauth_fallback() {
    let fixture = ApiChainFixture::assemble(FixtureSpec::standard()).await;
    // A VALID github session rides along in every rejected request; a
    // faulty fallback to the later source would turn these 401s into 200s.
    let seeded = fixture.seed_session("n1-gateway-bot-user").await;
    let bot_headers = |token: &str, secret: bool| {
        let mut headers: Vec<(&'static str, String)> = Vec::new();
        headers.push(("x-avernet-principal", String::from(token)));
        if secret {
            headers.push(("cookie", format!("bcs_session={}", seeded.token)));
        }
        headers
    };

    for (script, case) in [
        (RegistryScript::Deleted, "deleted"),
        (RegistryScript::Disabled, "disabled"),
        (RegistryScript::ActorMismatch, "actor mismatch"),
    ] {
        fixture.registry.set(script);
        let listing = drive(
            &fixture.v1_router,
            "GET",
            GROUPS,
            &bot_headers(&gateway_bot_principal_token("n1-bot"), true),
        )
        .await;
        assert_eq!(
            listing.status,
            StatusCode::UNAUTHORIZED,
            "{case}: the stateful rejection keeps the original code"
        );
        assert_eq!(error_code(&listing.body), "unauthenticated");
        assert_eq!(fixture.group.calls("list_groups"), 0, "{case}: business never entered");
        assert!(
            fixture.registry.validate_calls.load(Ordering::SeqCst) > 0,
            "{case}: the Stateful revalidation actually ran"
        );
        fixture.db.reset_counts();
    }

    // Positive control: the same request with a CURRENT registration
    // reaches the business service through the gateway source.
    fixture.registry.set(RegistryScript::Ok);
    let register_calls = fixture.registry.validate_calls.load(Ordering::SeqCst);
    let before = fixture.group.calls("list_groups");
    let healthy = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &bot_headers(&gateway_bot_principal_token("n1-bot"), true),
    )
    .await;
    assert_eq!(healthy.status, StatusCode::OK, "{:?}", healthy.body);
    assert_eq!(fixture.group.calls("list_groups"), before + 1);
    assert!(
        fixture.registry.validate_calls.load(Ordering::SeqCst) > register_calls,
        "the control revalidates against the registry again"
    );
}

/// N1 row 2: the two store fault classes keep EXACTLY their HTTP
/// categories — the Stateful registry's persisted-state lookup failing
/// stays 502 `bot_identity_unavailable`; the strict session DB failing
/// stays 503 `auth_dependency_unavailable`; neither reaches the business.
#[tokio::test]
async fn n1_registry_and_session_store_faults_keep_502_and_503_exactly() {
    let fixture = ApiChainFixture::assemble(FixtureSpec::standard()).await;

    // Stateful registry failure → 502 bot_identity_unavailable.
    fixture.registry.set(RegistryScript::StoreFailure);
    let registry_fault = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &[(
            "x-avernet-principal",
            gateway_bot_principal_token("n1-registry-bot"),
        )],
    )
    .await;
    assert_eq!(registry_fault.status, StatusCode::BAD_GATEWAY);
    assert_eq!(registry_fault.body["code"], 50_200);
    assert_eq!(error_code(&registry_fault.body), "bot_identity_unavailable");
    assert_eq!(fixture.group.calls("list_groups"), 0);

    // Strict session DB failure → 503 auth_dependency_unavailable.
    let seeded = fixture.seed_session("n1-session-fault-user").await;
    fixture.db.control.set_faults(&[NamedOp::StrictSelect]);
    let session_fault = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &session_cookie_only(&seeded.token),
    )
    .await;
    assert_eq!(
        session_fault.status,
        StatusCode::SERVICE_UNAVAILABLE,
        "{:?}",
        session_fault.body
    );
    assert_eq!(error_code(&session_fault.body), "auth_dependency_unavailable");
    assert_eq!(fixture.group.calls("list_groups"), 0);
    fixture.db.control.clear_faults();
}

// ---------------------------------------------------------------------------
// N2
// ---------------------------------------------------------------------------

/// N2 row 1: the native Human router keeps every original rejection —
/// Bot/Actor/Gateway credentials are still refused, expected-user
/// mismatch keeps `user_context_changed` — with `no-store` on every
/// response and the group business service never entered.
#[tokio::test]
async fn n2_native_human_rejections_and_expected_user_mismatch_are_unchanged() {
    let mut spec = FixtureSpec::standard();
    spec.mock_human = Some("staff-13");
    let fixture = ApiChainFixture::assemble(spec).await;

    // Every hostile-credential POST carries the required browser context
    // headers so the rejection is observable at the verification layer.
    let substitute_cases: [(&'static str, &'static str, String); 4] = [
        (
            "gateway principal header",
            "x-avernet-principal",
            api_chain::gateway_user_principal_token("attacker"),
        ),
        (
            "authorization bearer",
            "authorization",
            "Bearer someone-elses-token".to_string(),
        ),
        ("actor header", "x-bcn-actor-bot-id", "bot-77".to_string()),
        ("bot token header", "x-bcs-bot-token", "tok-77".to_string()),
    ];
    for (case, header, credential) in substitute_cases {
        let mut headers = expected_headers();
        headers.push((header, credential));
        let driven = drive(&fixture.human_router, "POST", USER_GROUPS, &headers).await;
        assert_eq!(
            driven.status,
            StatusCode::UNAUTHORIZED,
            "{case}: the substituted credential keeps the original rejection"
        );
        assert_eq!(error_code(&driven.body), "unauthenticated");
        assert_eq!(fixture.group.calls("create"), 0, "{case}: business never entered");
        assert_eq!(
            driven.cache_control.as_deref(),
            Some("no-store"),
            "{case}: browser responses stay no-store"
        );
    }

    // expected-user mismatch: the verified Human ≠ the pinned expected
    // user keeps `user_context_changed`, business never entered.
    let mismatch = drive(
        &fixture.human_router,
        "POST",
        USER_GROUPS,
        &[
            ("sec-fetch-site", "same-origin".to_string()),
            ("x-bcs-expected-user", "someone-else".to_string()),
        ],
    )
    .await;
    assert_eq!(mismatch.status, StatusCode::FORBIDDEN);
    assert_eq!(error_code(&mismatch.body), "user_context_changed");
    assert_eq!(fixture.group.calls("create"), 0);
    assert_eq!(mismatch.cache_control.as_deref(), Some("no-store"));

    // Positive control: with the browser context intact the SAME router
    // serves the verified Human — the native chain really authenticates.
    let me = drive(&fixture.human_router, "GET", "/collaboration/user/me", &[]).await;
    assert_eq!(me.status, StatusCode::OK, "{:?}", me.body);
    assert_eq!(me.body["data"]["user_id"], "staff-13");
    assert_eq!(
        fixture
            .group
            .calls("create"),
        0,
        "the rejected-substitution family never reached the group business"
    );
}

/// N2 row 2: the dedicated-credential surfaces keep their own protocols —
/// the Draft public fixed GET and the Bot Self / Agent Self bearer routes
/// run their applications even when the request ALSO carries the
/// credentials the new chain guards, and the strict session store is
/// never consulted (no composite involvement).
#[tokio::test]
async fn n2_draft_and_self_credential_surfaces_keep_their_own_protocols() {
    let mut spec = FixtureSpec::standard();
    spec.drafts = true;
    spec.bot_self = true;
    let fixture = ApiChainFixture::assemble(spec).await;

    // Hostile residue the protected boundary would have 401'd on: a bogus
    // gateway header and a bogus session cookie both ride along.
    let hostile = [
        ("x-avernet-principal", "definitely-not-a-valid-token".to_string()),
        ("cookie", "bcs_session=garbage-value".to_string()),
    ];

    fixture.db.reset_counts();
    let draft = drive(&fixture.v1_router, "GET", DRAFT_READ, &hostile).await;
    assert_eq!(
        draft.status,
        StatusCode::NOT_FOUND,
        "the draft route's own protocol answers: {:?}",
        draft.body
    );
    assert_eq!(error_code(&draft.body), "DRAFT_REFERENCE_UNAVAILABLE");
    assert_eq!(fixture.surface.calls("draft.read"), 1, "the draft application ran");
    assert_eq!(
        draft.cache_control.as_deref(),
        Some("no-store"),
        "the draft surface keeps its no-store"
    );

    // Bot Self: the dedicated bearer + Actor protocol serves its own
    // application result; the composite never saw the request.
    let self_headers = [
        ("authorization", "Bearer task13-agent-token".to_string()),
        hostile[0].clone(),
    ];
    let bot_self = drive(&fixture.v1_router, "GET", BOT_SELF_ME, &self_headers).await;
    assert_eq!(bot_self.status, StatusCode::OK, "{:?}", bot_self.body);
    assert_eq!(bot_self.body["data"]["identity"]["agent_code"], "task13-agent");
    assert_eq!(fixture.surface.calls("bot_self.get_me"), 1);
    assert_eq!(bot_self.cache_control.as_deref(), Some("no-store"));

    // Agent Self: the same dedicated protocol.
    let agent_headers = [(
        "authorization",
        "Bearer task13-agent-token".to_string(),
    )];
    let agent_self = drive(&fixture.v1_router, "GET", AGENT_SELF_ME, &agent_headers).await;
    assert_eq!(agent_self.status, StatusCode::OK, "{:?}", agent_self.body);
    assert_eq!(agent_self.body["data"]["agent_code"], "task13-agent");
    assert_eq!(agent_self.body["data"]["bots"], Value::Array(Vec::new()));
    assert_eq!(fixture.surface.calls("bot_self.get_agent_me"), 1);

    // The strict session store was never consulted for any of these
    // dedicated-protocol surfaces.
    assert_eq!(
        fixture.db.control.strict_selects.load(Ordering::SeqCst),
        0,
        "the new composite/strict engine was never invoked"
    );
}

// ---------------------------------------------------------------------------
// N3
// ---------------------------------------------------------------------------

/// N3 row 1: the authority surface runs on the NEW verifier (the gateway
/// Bot and the cookie-authed Human both pass it) but keeps ITS OWN
/// application admission — the ordinary Human invite gate is NOT bolted
/// onto these routes: a Bot caller is refused by the application's
/// Human-rule, an un-invited Human is refused by the application's own
/// invite consult, and an INVITED Human proceeds to the application's
/// ownership admission.
#[tokio::test]
async fn n3_authority_routes_run_on_the_new_verifier_with_application_admission_only() {
    // The UN-INVITED fixture: its invite stub denies Human callers for
    // BOTH the ordinary middleware gate and the authority application.
    let mut spec = FixtureSpec::standard();
    spec.authority = true;
    spec.authority_invite_allow = false;
    let denied = ApiChainFixture::assemble(spec).await;
    let human = denied.seed_session("n3-denied-user").await;
    let invite = denied
        .invite
        .clone()
        .expect("the authority mount wires the invite stub");
    let seed_repo = &denied
        .authority
        .as_ref()
        .expect("the authority mount wires the authority fixture")
        .bot_repo;
    bcs_test_support::bot_authority::seed_bot(seed_repo, "bot-1", "alice", &[]).await;

    // A legit Bot passes the NEW verifier (and the Stateful gate) and
    // reaches the AUTHORITY application, which refuses non-Human callers
    // with ITS own code — NOT the invite gate's: if the ordinary Human
    // gate were wrongly bolted onto these routes, this response would be
    // `invite_code_required` instead.
    let bot_probe = drive(
        &denied.v1_router,
        "GET",
        AUTHORITY_MANAGERS,
        &[(
            "x-avernet-principal",
            gateway_bot_principal_token("n3-authority-bot"),
        )],
    )
    .await;
    assert_eq!(bot_probe.status, StatusCode::FORBIDDEN, "{:?}", bot_probe.body);
    assert_eq!(
        error_code(&bot_probe.body),
        "bot_authority_operator_forbidden",
        "the application's own admission refusal, never the invite gate's"
    );
    assert!(
        invite.human_checks.lock().expect("checks").is_empty(),
        "no Human invite consult happened for the Bot caller"
    );

    // An UN-INVITED Human passes the verifier and reaches the
    // application, which performs ITS OWN invite consult.
    let uninvited = drive(
        &denied.v1_router,
        "GET",
        AUTHORITY_MANAGERS,
        &session_cookie_only(&human.token),
    )
    .await;
    assert_eq!(uninvited.status, StatusCode::FORBIDDEN, "{:?}", uninvited.body);
    assert_eq!(error_code(&uninvited.body), "invite_code_required");
    assert_eq!(
        invite.human_checks.lock().expect("checks").len(),
        1,
        "the application's own invite admission ran for the Human"
    );

    // An INVITED Human (a second fixture whose stub admits) proceeds
    // past the invite consult into the application's OWNERSHIP
    // admission — the seeded owner is a different Human, so the
    // application refuses with its business code, never the gate's.
    let mut invited_spec = FixtureSpec::standard();
    invited_spec.authority = true;
    invited_spec.authority_invite_allow = true;
    let allowed = ApiChainFixture::assemble(invited_spec).await;
    let invited_human = allowed.seed_session("n3-invited-user").await;
    let invited_repo = &allowed
        .authority
        .as_ref()
        .expect("the authority mount wires the authority fixture")
        .bot_repo;
    bcs_test_support::bot_authority::seed_bot(invited_repo, "bot-1", "alice", &[]).await;
    let invited = drive(
        &allowed.v1_router,
        "GET",
        AUTHORITY_MANAGERS,
        &session_cookie_only(&invited_human.token),
    )
    .await;
    assert_eq!(
        invited.status,
        StatusCode::FORBIDDEN,
        "the application's ownership admission decides: {:?}",
        invited.body
    );
    assert_ne!(error_code(&invited.body), "invite_code_required");
    assert_eq!(
        allowed
            .invite
            .as_ref()
            .expect("invite stub mounted")
            .human_checks
            .lock()
            .expect("checks")
            .len(),
        1,
        "the invited Human passed THROUGH the application's consult"
    );
    assert_eq!(denied.group.calls("list_groups"), 0);
}

/// N3 row 2: `public-groups` still requires credentials (401 without
/// any), and the ORDINARY routes keep the `invite_code_required` gate for
/// un-invited Humans — which no credential (not even a signed Gateway
/// Bot) can bypass; the business services run exactly when the gate
/// allows.
#[tokio::test]
async fn n3_public_groups_needs_credentials_and_the_invite_gate_cannot_be_bypassed() {
    // The UN-INVITED fixture: the middleware invite gate denies.
    let mut spec = FixtureSpec::standard();
    spec.authority = true;
    spec.authority_invite_allow = false;
    let denied = ApiChainFixture::assemble(spec).await;
    let human = denied.seed_session("n3-invite-user").await;

    // public-groups WITHOUT credentials: the boundary's 401.
    let anonymous = drive(&denied.v1_router, "GET", PUBLIC_GROUPS, &[]).await;
    assert_eq!(anonymous.status, StatusCode::UNAUTHORIZED);
    assert_eq!(error_code(&anonymous.body), "unauthenticated");
    assert_eq!(denied.group.calls("list_public_groups"), 0);

    // public-groups WITH a valid cookie but an un-invited Human: the
    // ordinary middleware invite gate refuses BEFORE the business.
    let gated = drive(
        &denied.v1_router,
        "GET",
        PUBLIC_GROUPS,
        &session_cookie_only(&human.token),
    )
    .await;
    assert_eq!(gated.status, StatusCode::FORBIDDEN, "{:?}", gated.body);
    assert_eq!(error_code(&gated.body), "invite_code_required");
    assert_eq!(denied.group.calls("list_public_groups"), 0);

    // A signed Gateway Bot does NOT bypass the ordinary gate.
    let bot_attempt = drive(
        &denied.v1_router,
        "GET",
        GROUPS,
        &[(
            "x-avernet-principal",
            gateway_bot_principal_token("n3-invite-bot"),
        )],
    )
    .await;
    assert_eq!(bot_attempt.status, StatusCode::FORBIDDEN, "{:?}", bot_attempt.body);
    assert_eq!(error_code(&bot_attempt.body), "invite_code_required");
    assert_eq!(denied.group.calls("list_groups"), 0);

    // The INVITED fixture: the SAME requests run the business services,
    // proving the earlier refusals were the invite policy alone.
    let mut invited_spec = FixtureSpec::standard();
    invited_spec.authority = true;
    invited_spec.authority_invite_allow = true;
    let allowed = ApiChainFixture::assemble(invited_spec).await;
    let invited_human = allowed.seed_session("n3-invite-allowed-user").await;
    let admitted_public = drive(
        &allowed.v1_router,
        "GET",
        PUBLIC_GROUPS,
        &session_cookie_only(&invited_human.token),
    )
    .await;
    assert_eq!(admitted_public.status, StatusCode::OK);
    assert_eq!(allowed.group.calls("list_public_groups"), 1);
    let admitted_groups = drive(
        &allowed.v1_router,
        "GET",
        GROUPS,
        &session_cookie_only(&invited_human.token),
    )
    .await;
    assert_eq!(admitted_groups.status, StatusCode::OK);
    assert_eq!(allowed.group.calls("list_groups"), 1);
}

// ---------------------------------------------------------------------------
// N4
// ---------------------------------------------------------------------------

/// N4 row 1 + row 2 (fixture arm): an OAuth-ONLY chain (no gateway
/// source) serves real requests end-to-end and resolves ZERO gateway
/// secrets; enabling friend-auth-sync keeps the resigner on the LEGACY
/// `[gateway_principal]` secret contract, so a missing referenced secret
/// is a startup FAILURE (production constructor), while providing it
/// lets the server start.
#[tokio::test]
#[serial_test::serial]
async fn n4_oauth_only_chain_reads_no_gateway_secret_and_the_resigner_keeps_its_contract() {
    // ---- Fixture arm: OAuth-only explicit chain over the real store.
    let fixture = ApiChainFixture::assemble(FixtureSpec::oauth_only()).await;
    let seeded = fixture.seed_session("n4-oauth-only-user").await;
    let requests_before = fixture.secrets.requests().len();
    let served = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &session_cookie_only(&seeded.token),
    )
    .await;
    assert_eq!(served.status, StatusCode::OK, "{:?}", served.body);
    assert_eq!(fixture.group.calls("list_groups"), 1);
    assert_eq!(
        fixture.secrets.count(GATEWAY_SIGNING_KEY_REF),
        0,
        "an OAuth-only chain NEVER resolves the gateway signing secret"
    );
    let later = fixture.secrets.requests()[requests_before..].to_vec();
    assert!(
        later.iter().all(|name| name != GATEWAY_SIGNING_KEY_REF),
        "the served request read no gateway material: {later:?}"
    );

    // ---- Production-constructor arms: the friend-auth-sync resigner
    // keeps its OWN [gateway_principal] secret resolution.
    let bots_dir = tempfile::TempDir::new().expect("bots dir");
    let data_dir = tempfile::TempDir::new().expect("data dir");

    // Arm 1 — the resigner's referenced secret is MISSING: startup must
    // FAIL loudly (never silently proceed without the resigner). The
    // OAuth-only [api.auth] chain's own secrets ARE provided, so the
    // failure is attributable to the resigner contract alone.
    // Env carries only test fixture material; the serial annotation and
    // the single-threaded entrances here keep the env mutation sound
    // (no server has started or snapshotted the environment yet).
    unsafe {
        for name in [
            "BCS_TASK13_SECRET_TASK13_SESSION_SIGNING_KEY",
            "BCS_TASK13_SECRET_TASK13_GITHUB_CLIENT_SECRET",
        ] {
            std::env::set_var(name, "task13-env-secret-material");
        }
        std::env::remove_var("BCS_TASK13_SECRET_TASK13_LEGACY_RESIGNER_SECRET");
    }
    let failure = bcs::BcsServer::new_with_storage(production_config(&bots_dir, &data_dir)).await;
    let error = match failure {
        Err(error) => error,
        Ok(_) => panic!("a missing friend-auth-sync resigner secret must fail startup"),
    };
    assert!(
        error
            .to_string()
            .contains("Gateway Principal signing key secret 'task13-legacy-resigner-secret' is required"),
        "the startup failure names the unresolved resigner secret, got: {error}"
    );

    // Arm 2 — providing the legacy secret lets the SAME config start:
    // the resigner read happens from its OWN contract (independent of the
    // OAuth-only chain) and does not fail the boot.
    unsafe {
        std::env::set_var(
            "BCS_TASK13_SECRET_TASK13_LEGACY_RESIGNER_SECRET",
            "task13-resigner-material",
        );
    }
    let started = bcs::BcsServer::new_with_storage(production_config(&bots_dir, &data_dir)).await;
    assert!(started.is_ok(), "startup with the resigner secret present: {:?}", started.err());

    unsafe {
        for name in [
            "BCS_TASK13_SECRET_TASK13_SESSION_SIGNING_KEY",
            "BCS_TASK13_SECRET_TASK13_GITHUB_CLIENT_SECRET",
            "BCS_TASK13_SECRET_TASK13_LEGACY_RESIGNER_SECRET",
        ] {
            std::env::remove_var(name);
        }
    }
}

fn production_config(bots_dir: &tempfile::TempDir, data_dir: &tempfile::TempDir) -> bcs::BcsConfig {
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.database.sqlite.path = data_dir
        .path()
        .join("task13-n4.sqlite")
        .to_string_lossy()
        .to_string();
    config.secret = bcs_config_api::SecretConfig {
        provider: "env".to_string(),
        providers: BTreeMap::from([(
            "env".to_string(),
            BTreeMap::from([(
                "prefix".to_string(),
                serde_json::Value::String("BCS_TASK13_SECRET_".to_string()),
            )]),
        )]),
    };
    config.api.auth = Some(toml::from_str(
        r#"
chain = ["github"]
public_base_url = "http://127.0.0.1:8000/openapi/v1/auth"
session_signing_key_secret = "task13-session-signing-key"
success_redirect_path = "/"
trusted_browser_origins = ["http://127.0.0.1:8000"]

[github]
client_id = "github-task13"
client_secret_secret = "task13-github-client-secret"
"#,
    )
    .expect("production N4 [api.auth] parses"));
    config.gateway_principal = bcs::GatewayPrincipalConfig {
        signing_key_secret: Some("task13-legacy-resigner-secret".to_string()),
        ..Default::default()
    };
    // The friend-auth-sync resigner is built exactly when this is set.
    config.friend_work_order_base_url = Some("http://127.0.0.1:9/friend-work-order".to_string());
    config
}

// ---------------------------------------------------------------------------
// N5
// ---------------------------------------------------------------------------

/// N5: one valid GitHub cookie through the REAL four-OAuth-provider chain
/// (alipay → github → google → wechat): the strict engine runs ONCE —
/// exactly one strict SELECT for the whole request — and the later
/// provider positions replay the request-local cache instead of issuing
/// per-provider duplicate queries.
#[tokio::test]
async fn n5_four_provider_chain_spends_exactly_one_strict_select() {
    let alipay_keys = alipay_fixture_keys();
    let fixture =
        ApiChainFixture::assemble(FixtureSpec::four_oauth_providers(alipay_keys)).await;
    let seeded = fixture.seed_session("n5-github-cookie-user").await;

    fixture.db.reset_counts();
    let served = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &session_cookie_only(&seeded.token),
    )
    .await;
    assert_eq!(served.status, StatusCode::OK, "{:?}", served.body);
    assert_eq!(fixture.group.calls("list_groups"), 1, "the request reached the business");
    assert_eq!(
        fixture.db.control.strict_selects.load(Ordering::SeqCst),
        1,
        "the four-position chain spent exactly ONE strict SELECT for the request"
    );
    assert_eq!(
        fixture.db.control.rows_returned.load(Ordering::SeqCst),
        1,
        "the single strict SELECT returned the one current session row"
    );
    assert_eq!(
        fixture.db.control.transactions.load(Ordering::SeqCst),
        0,
        "the verify path never opens a transaction"
    );

    // The same session stays credentials-invalid per-occasion after
    // revoke: exactly one SELECT each — no per-provider duplication in
    // either direction.
    let logout = drive(
        &fixture.v1_router,
        "POST",
        "/openapi/v1/auth/logout",
        &api_chain::session_headers(&seeded.token),
    )
    .await;
    assert_eq!(logout.status, StatusCode::OK);
    fixture.db.reset_counts();
    let revoked = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &session_cookie_only(&seeded.token),
    )
    .await;
    assert_eq!(revoked.status, StatusCode::UNAUTHORIZED);
    assert_eq!(fixture.group.calls("list_groups"), 1, "unchanged totals");
    assert_eq!(
        fixture.db.control.strict_selects.load(Ordering::SeqCst),
        1,
        "the revoked-credential verdict also spends exactly one SELECT"
    );
    assert_eq!(fixture.group.calls("list_groups"), 1);
}

fn alipay_fixture_keys() -> (String, String) {
    use rsa::pkcs8::{EncodePrivateKey, EncodePublicKey, LineEnding};
    use rsa::RsaPrivateKey;
    let mut rng = rand::rngs::OsRng;
    let private_key = RsaPrivateKey::new(&mut rng, 2048).expect("task13 alipay fixture key");
    let public_key = rsa::RsaPublicKey::from(&private_key);
    let private_pem = private_key
        .to_pkcs8_pem(LineEnding::LF)
        .expect("task13 alipay private PEM");
    let public_pem = public_key
        .to_public_key_pem(LineEnding::LF)
        .expect("task13 alipay public PEM");
    (private_pem.to_string(), public_pem)
}
