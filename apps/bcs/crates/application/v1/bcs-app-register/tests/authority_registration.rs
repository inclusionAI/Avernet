//! Task 4.1 — complete entry failure cases for the REAL
//! RegisterService chains against the SQL store (bot-authority
//! spec §10.1/§10.2).
//!
//! Both token versions run the production chain: legacy v1
//! Human token → BotRegistryCoreService → the Bot repo's
//! composite create; v2 Provider token →
//! ProviderRegistrationCoreService → the BotProvider repo's
//! composite create. The core is NEVER mocked: failures are
//! injected at the composed-transaction statement level (the
//! `ChainFailDb` wrapper), and after every injection the
//! assertions touch the SAME database the chain committed to:
//! no half registration (no Bot, no Human row, no owner edge,
//! no default profile, no initialize audit, no binding
//! projection), no usable token and no early runtime
//! activation.
//!
//! MySQL note: the composites share their statement style with
//! the manager/transfer transactions (dialect switches are in
//! the composed statements); these cases run on SQLite
//! locally, the MySQL dialect is exercised by the migration /
//! singlebox gates (plan §6).

use bcs_service_api::application::v1::{
    ApplicationError, RegisterBot, RegisterService,
};
use bcs_service_api::core::provider_registration::ProviderRegistrationMode;
use bcs_service_api::core::BotRegistryCoreService;
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_db_api::DbPlugin;
use bcs_service_api::types::bot_authority::BotAccessRelation;
use bcs_test_support::contract::bot_registration_chain::{
    assert_no_half_registration, committed_profile, mint_expired_v1_token,
    mint_v1_token, mint_v2_token, owner_relation, registration_chain,
    RegistrationChain, OWNER, REGISTER_SECRET,
};

/// The default database layout arms a fresh mutation per
/// command — the audit table's UNIQUE (env, mutation_id,
/// sequence) behaves exactly like the real surface.
fn v1_command(token: String) -> RegisterBot {
    RegisterBot {
        mode: None,
        provider_bot_ref: None,
        webhook_url: None,
        token,
        bot_name: "Trusted Bot".to_string(),
    }
}

fn v2_command(token: String) -> RegisterBot {
    RegisterBot {
        mode: Some(ProviderRegistrationMode::Plugin),
        provider_bot_ref: Some("ref-1".to_string()),
        webhook_url: None,
        token,
        bot_name: "Provider Bot".to_string(),
    }
}

async fn failed_v1_at(chain: &RegistrationChain, pattern: &'static str) {
    chain.db.arm(pattern);
    let error = chain
        .register_service
        .register_bot(v1_command(mint_v1_token(OWNER, REGISTER_SECRET)))
        .await
        .expect_err("the failure must NOT be swallowed");
    // Non-success, no credential content, no SQL text leakage.
    assert!(
        matches!(
            error,
            ApplicationError::Internal(_)
        ),
        "persistence failures stay internal: {error}"
    );
    assert!(!error.to_string().contains("injected"));
    assert_no_half_registration(chain, "bot_v1-failed").await;
    // No committed bot at all: the ONE registration attempt
    // left nothing behind, so the owner registry is intact.
    assert!(chain.bot_repo.list_active().await.is_empty());
    chain.db.disarm_all();
}

#[tokio::test]
async fn v1_owner_write_failure_leaves_no_half_registration() {
    let chain = registration_chain().await;
    failed_v1_at(&chain, "edge_grants").await;
}

#[tokio::test]
async fn v1_profile_write_failure_leaves_no_half_registration() {
    let chain = registration_chain().await;
    failed_v1_at(&chain, "permission_profiles").await;
}

#[tokio::test]
async fn v1_audit_write_failure_leaves_no_half_registration() {
    let chain = registration_chain().await;
    failed_v1_at(&chain, "bot_authority_changes").await;
}

#[tokio::test]
async fn v1_human_actor_failure_leaves_no_half_registration() {
    let chain = registration_chain().await;
    failed_v1_at(&chain, "actor_kind, name, bot_info, session_token")
        .await;
}

#[tokio::test]
async fn v1_bot_row_failure_leaves_no_half_registration() {
    let chain = registration_chain().await;
    failed_v1_at(&chain, "INSERT INTO bcs_bots").await;
}

/// A retry that succeeds after a failed registration commits
/// exactly the intended state (the owner's identity row was
/// never fabricated by the failed attempt).
#[tokio::test]
async fn v1_retry_after_failure_commits_intended_state() {
    let chain = registration_chain().await;
    failed_v1_at(&chain, "permission_profiles").await;
    let registration = chain
        .register_service
        .register_bot(v1_command(mint_v1_token(OWNER, REGISTER_SECRET)))
        .await
        .expect("retry commits");
    assert!(!registration.bot_token.is_empty());
    assert_eq!(
        owner_relation(&chain, &registration.bot_uuid).await,
        Some(BotAccessRelation::Owner)
    );
    let profile = committed_profile(&chain, &registration.bot_uuid)
        .await
        .expect("default profile");
    assert_eq!(profile.revision, 1);
    let bot = chain
        .bot_repo
        .get(&registration.bot_uuid)
        .await
        .expect("bot persisted");
    assert_eq!(bot.created_by.as_deref(), Some(OWNER));
    assert!(
        !chain.bot_repo.is_connected(&registration.bot_uuid).await,
        "no early runtime activation"
    );
}

#[tokio::test]
async fn v1_registration_creates_the_owner_actor_row() {
    // Spec §10.2 literal (ruling T4-1): the trusted
    // registration ENSURES the owner's Human ACTOR row in
    // the SAME transaction so the Task-2/3 human-existence
    // guards accept a freshly registered owner; the OAuth
    // identity table is never written.
    let chain = registration_chain().await;
    let registration = chain
        .register_service
        .register_bot(v1_command(mint_v1_token(OWNER, REGISTER_SECRET)))
        .await
        .expect("token-verified human registers");
    assert_eq!(
        owner_relation(&chain, &registration.bot_uuid).await,
        Some(BotAccessRelation::Owner)
    );
    let rows = chain
        .db
        .query(bcs_db_api::DbStatement::with_params(
            "SELECT actor_kind FROM bcs_bots WHERE bot_uuid = ?",
            vec![format!("human_{OWNER}").into()],
        ))
        .await
        .expect("actor rows");
    assert_eq!(rows.len(), 1, "exactly one Human actor row for the owner");
    let actor_kind: String =
        bcs_db_api::db_get_column(&rows[0], "actor_kind").unwrap();
    assert_eq!(actor_kind, "human");
}

#[tokio::test]
async fn v1_expired_token_never_reaches_the_store() {
    let chain = registration_chain().await;
    let error = chain
        .register_service
        .register_bot(v1_command(mint_expired_v1_token(
            OWNER,
            REGISTER_SECRET,
        )))
        .await
        .expect_err("expired token");
    assert!(matches!(error, ApplicationError::Unauthenticated));
    assert_no_half_registration(&chain, "bot_expired").await;
}

// ---------------------------------------------------------------------------
// v2 Provider chain.
// ---------------------------------------------------------------------------

async fn enable_gateway(chain: &RegistrationChain) {
    // Downlink enabled + credential so the Gateway mode passes
    // the existing Provider admission handling.
    let env = bcs_config::resolve_env_str();
    chain
        .db
        .execute(bcs_db_api::DbStatement::with_params(
            "UPDATE bcs_providers SET config = ? WHERE provider_id = 'provider-a'",
            vec![r#"{"downlink":{"enabled":true,"auth_mode":"static_bearer"}}"#.into()],
        ))
        .await
        .expect("enable downlink");
    chain
        .db
        .execute(bcs_db_api::DbStatement::with_params(
            "INSERT OR IGNORE INTO bcs_provider_credentials              (provider_id, env, credential_kind, secret_value, disabled)              VALUES ('provider-a', ?, 'downlink_bcs_to_provider', 'gateway-secret', 0)",
            vec![env.as_str().into()],
        ))
        .await
        .expect("seed downlink credential");
}

fn v2_gateway_command(token: String) -> RegisterBot {
    RegisterBot {
        mode: Some(ProviderRegistrationMode::Gateway),
        provider_bot_ref: Some("ref-gw".to_string()),
        webhook_url: Some("https://provider.example.test/callback".to_string()),
        token,
        bot_name: "Gateway Bot".to_string(),
    }
}

async fn failed_v2_at(
    chain: &RegistrationChain,
    command: RegisterBot,
    pattern: &'static str,
) -> ApplicationError {
    chain.db.arm(pattern);
    let error = chain
        .register_service
        .register_bot(command)
        .await
        .expect_err("v2 failure must NOT be swallowed");
    assert!(
        matches!(error, ApplicationError::Internal(_)),
        "persistence failures stay internal: {error}"
    );
    assert_no_half_registration(chain, "bot_v2-failed").await;
    assert!(chain.bot_repo.list_active().await.is_empty());
    chain.db.disarm_all();
    error
}

#[tokio::test]
async fn v2_owner_write_failure_leaves_no_half_registration() {
    let chain = registration_chain().await;
    failed_v2_at(
        &chain,
        v2_command(mint_v2_token(OWNER, "provider-a", REGISTER_SECRET)),
        "edge_grants",
    )
    .await;
}

#[tokio::test]
async fn v2_profile_and_audit_failures_leave_no_half_registration() {
    let chain = registration_chain().await;
    failed_v2_at(
        &chain,
        v2_command(mint_v2_token(OWNER, "provider-a", REGISTER_SECRET)),
        "permission_profiles",
    )
    .await;
    failed_v2_at(
        &chain,
        v2_command(mint_v2_token(OWNER, "provider-a", REGISTER_SECRET)),
        "bot_authority_changes",
    )
    .await;
}

#[tokio::test]
async fn v2_binding_write_failure_leaves_no_half_registration() {
    let chain = registration_chain().await;
    enable_gateway(&chain).await;
    failed_v2_at(
        &chain,
        v2_gateway_command(mint_v2_token(
            OWNER,
            "provider-a",
            REGISTER_SECRET,
        )),
        "bcs_provider_bot_bindings",
    )
    .await;
    // The Provider/ref stays available for an honest retry.
    let registration = chain
        .register_service
        .register_bot(v2_gateway_command(mint_v2_token(
            OWNER,
            "provider-a",
            REGISTER_SECRET,
        )))
        .await
        .expect("retry commits the gateway bot");
    let binding = chain
        .provider_repo
        .get_connection_mode(&registration.bot_uuid)
        .await
        .expect("read")
        .expect("bot");
    assert_eq!(
        binding,
        bcs_service_api::bot_provider::BotConnectionMode::Gateway
    );
}

/// A v2 registration with an existing successful state keeps
/// the Provider/ref conflict ( tombstones included) — the
/// committed first registration stays untouched.
#[tokio::test]
async fn v2_duplicate_ref_conflicts_without_touching_the_winner() {
    let chain = registration_chain().await;
    let first = chain
        .register_service
        .register_bot(v2_command(mint_v2_token(
            OWNER,
            "provider-a",
            REGISTER_SECRET,
        )))
        .await
        .expect("first");
    let audit_before = audit_count(&chain).await;
    let error = chain
        .register_service
        .register_bot(v2_command(mint_v2_token(
            OWNER,
            "provider-a",
            REGISTER_SECRET,
        )))
        .await
        .expect_err("duplicate Provider/ref");
    assert!(matches!(error, ApplicationError::Conflict { .. }));
    assert_eq!(audit_before, audit_count(&chain).await, "no new audit");
    // The winner keeps owner + version + its profile.
    assert_eq!(
        owner_relation(&chain, &first.bot_uuid).await,
        Some(BotAccessRelation::Owner)
    );
    assert!(committed_profile(&chain, &first.bot_uuid).await.is_some());
}

/// The audit rows carry the verified Human initiator only,
/// never a Provider-only identity and never a credential.
#[tokio::test]
async fn committed_registrations_audit_the_real_initiator_without_credentials() {
    let chain = registration_chain().await;
    let registration = chain
        .register_service
        .register_bot(v2_command(mint_v2_token(
            OWNER,
            "provider-a",
            REGISTER_SECRET,
        )))
        .await
        .expect("committed");
    let rows = chain
        .db
        .query(bcs_db_api::DbStatement::with_params(
            "SELECT actor_kind, actor_id, subject_id, action, after_state \
             FROM bot_authority_changes WHERE bot_id = ?",
            vec![registration.bot_uuid.clone().into()],
        ))
        .await
        .expect("audit rows");
    let actor_kind: String =
        bcs_db_api::db_get_column(&rows[0], "actor_kind").unwrap();
    let actor_id: String =
        bcs_db_api::db_get_column(&rows[0], "actor_id").unwrap();
    assert_eq!(actor_kind, "human", "the real initiator is a Human");
    assert_eq!(actor_id, format!("human_{OWNER}"));
    let dump = format!("{:?}", rows);
    assert!(!dump.contains("bot-prov"), "no token leaks: {dump}");
    let _ = registration;
}

async fn audit_count(chain: &RegistrationChain) -> i64 {
    let rows = chain
        .db
        .query(bcs_db_api::DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bot_authority_changes",
            vec![],
        ))
        .await
        .expect("audit count");
    bcs_db_api::db_get_column::<i64>(&rows[0], "c").expect("int")
}
