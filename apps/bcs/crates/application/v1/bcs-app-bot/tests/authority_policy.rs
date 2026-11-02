//! Task 5.1: the trusted-App authority policy and the
//! `BotAuthorityService` Human/App identity selection
//! (spec §5.1/§5.2/§6.1, §3.1).
//!
//! Gateway signature-verification failure cases (expired /
//! tampered / wrong-audience JWTs) are exercised in
//! `bcs-api-http/tests/bot_authority_routes.rs` against the
//! real `GatewayPrincipalTokenVerifier`; verified subscribers
//! reach this policy as `AuthenticatedCaller` projections of
//! the real gateway principal set (`api-contracts/v1/
//! gateway-principal/principal-set.json`).

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_app_bot::authority::BotAuthorityServiceImpl;
use bcs_app_bot::authority_policy::{
    AuthorityAction, AuthorityAppScopeRule, AuthorityServicePolicy, TenantScope,
};
use bcs_service_api::application::v1::bot_authority::{
    ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN, ERROR_BOT_TRANSFER_NOT_FOUND,
    BotAuthorityService,
};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedAppIdentity, AuthenticatedBotIdentity,
    AuthenticatedCaller, AuthenticatedAccessKeyIdentity, AuthenticatedUserIdentity,
    InviteCodeService,
};
use bcs_service_api::core::bot_authority::BotAuthorityCoreService;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, BotAccessRelation, BotOwnershipView,
    BotRoleQuery, ManagerEdit, ManagerListQuery, ManagerPage, ManagerSetChange,
    ManagerSync, OwnershipChange, OwnershipSync, TransferAction, TransferCommand,
    TransferKey, TransferListQuery, TransferOutcome, TransferPage, TransferView,
    TransferStatus,
};

const ENV: &str = "local";

/// The fixture App's shared wire name (spec §3.1: the rule
/// matches the verified principal's `app_name`, byte-exact).
const APP_NAME: &str = "sync-app";

// ---------------------------------------------------------------------------
// identity fixtures (verified-principal projections)
// ---------------------------------------------------------------------------

fn human_caller(user_id: &str, tenant: Option<&str>) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: tenant.map(str::to_string),
        user: Some(AuthenticatedUserIdentity {
            id: user_id.to_string(),
            username: user_id.to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn app_caller(app_id: i64, tenant: Option<&str>) -> AuthenticatedCaller {
    named_app_caller(APP_NAME, app_id, tenant)
}

fn named_app_caller(
    app_name: &str,
    app_id: i64,
    tenant: Option<&str>,
) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: tenant.map(str::to_string),
        user: None,
        bot: None,
        app: Some(AuthenticatedAppIdentity {
            app_id,
            app_name: app_name.to_string(),
            owners: "owner".to_string(),
            app_type: "THIRD_PARTY".to_string(),
        }),
        access_key: None,
    }
}

/// User+App simultaneous: the dual-credential projection the
/// gateway can produce for a delegation principal set.
fn user_plus_app_caller(user_id: &str, app_id: i64) -> AuthenticatedCaller {
    let mut caller = human_caller(user_id, Some("tenant-a"));
    caller.app = Some(AuthenticatedAppIdentity {
        app_id,
        app_name: APP_NAME.to_string(),
        owners: "owner".to_string(),
        app_type: "THIRD_PARTY".to_string(),
    });
    caller
}

/// Provider-style credential: only a Bot principal (no Human,
/// no trusted App).
fn bot_only_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".to_string()),
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: "bot-1".to_string(),
            owner_id: "user-1".to_string(),
            app_id: 7,
            agent_code: "agent-1".to_string(),
        }),
        app: None,
        access_key: None,
    }
}

/// Access-key-only credential (non-Human, non-App).
fn access_key_only_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".to_string()),
        user: None,
        bot: None,
        app: None,
        access_key: Some(AuthenticatedAccessKeyIdentity {
            access_key: "ak-test-1".to_string(),
            expire_at: time::OffsetDateTime::UNIX_EPOCH,
        }),
    }
}

// ---------------------------------------------------------------------------
// policy construction helpers
// ---------------------------------------------------------------------------

fn rule(
    app: &str,
    tenant: TenantScope,
    actions: &[AuthorityAction],
) -> AuthorityAppScopeRule {
    AuthorityAppScopeRule::from_config_strings(
        app,
        tenant_text(&tenant),
        &actions
            .iter()
            .map(|action| action.as_str().to_string())
            .collect::<Vec<_>>(),
    )
    .expect("valid rule fixture")
}

fn tenant_text(tenant: &TenantScope) -> &str {
    match tenant {
        TenantScope::Exact(value) => value,
        TenantScope::Absent => "absent",
    }
}

fn sync_policy(rules: Vec<AuthorityAppScopeRule>) -> AuthorityServicePolicy {
    AuthorityServicePolicy::new(ENV, rules).expect("valid policy fixture")
}

fn full_sync_rule() -> AuthorityAppScopeRule {
    rule(
        APP_NAME,
        TenantScope::Exact("tenant-a".to_string()),
        &[AuthorityAction::ManagersSync],
    )
}

fn transfer_read_rule() -> AuthorityAppScopeRule {
    rule(
        APP_NAME,
        TenantScope::Exact("tenant-a".to_string()),
        &[
            AuthorityAction::OwnershipRead,
            AuthorityAction::OwnershipAccept,
            AuthorityAction::OwnershipReject,
        ],
    )
}

fn ownership_sync_rule() -> AuthorityAppScopeRule {
    rule(
        APP_NAME,
        TenantScope::Exact("tenant-a".to_string()),
        &[AuthorityAction::OwnershipSync],
    )
}

/// The adapter's dead placeholder mutation: the facade always
/// rebuilds the mutation from the VERIFIED identity, so the
/// harness commands carry this through untouched.
fn unused_placeholder_mutation() -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: String::new(),
        actor: AuthorityActor::System { name: "unused".to_string() },
    }
}

// ---------------------------------------------------------------------------
// AuthorizationTestingCore: a strict stub that records commands so
// policy tests can observe exactly what the facade submitted.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct RecordingCore {
    sync_bot_ids: std::sync::Mutex<Vec<String>>,
    ownership_sync_commands: std::sync::Mutex<Vec<(String, AuthorityActor)>>,
    transfer_commands: std::sync::Mutex<Vec<(TransferAction, String, String)>>,
    transfer_row: std::sync::Mutex<Option<TransferView>>,
}

fn canned_transfer_view() -> TransferView {
    TransferView {
        bot_id: "bot-1".to_string(),
        target_owner_actor_id: "human_bob".to_string(),
        from_owner_actor_id: "human_alice".to_string(),
        status: TransferStatus::Pending,
        requested_at: 1,
        expires_at: 1,
        updated_at: 1,
        decided_at: None,
        decided_by_kind: None,
        decided_by_id: None,
        reason: None,
        result_ownership_version: None,
        bot_name_snapshot: None,
    }
}

#[async_trait]
impl BotAuthorityCoreService for RecordingCore {
    async fn role(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<Option<BotAccessRelation>> {
        Ok(None)
    }

    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> bcs_service_api::ServiceResult<Vec<Option<BotAccessRelation>>> {
        Ok(queries.iter().map(|_| None).collect())
    }

    async fn ownership(
        &self,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<BotOwnershipView> {
        Ok(BotOwnershipView {
            bot_id: "bot-1".to_string(),
            owner_user_id: "alice".to_string(),
            owner_actor_id: "human_alice".to_string(),
        })
    }

    async fn managers(
        &self,
        _query: ManagerListQuery,
    ) -> bcs_service_api::ServiceResult<ManagerPage> {
        Ok(ManagerPage {
            owner: BotOwnershipView {
                bot_id: "bot-1".to_string(),
                owner_user_id: "alice".to_string(),
                owner_actor_id: "human_alice".to_string(),
            },
            items: vec![],
            total: 0,
        })
    }

    async fn transfer(
        &self,
        _key: &TransferKey,
    ) -> bcs_service_api::ServiceResult<Option<TransferView>> {
        Ok(self.transfer_row.lock().expect("row lock").clone())
    }

    async fn transfers(
        &self,
        _query: TransferListQuery,
    ) -> bcs_service_api::ServiceResult<TransferPage> {
        Ok(TransferPage {
            items: vec![],
            total: 0,
        })
    }

    async fn edit_manager(
        &self,
        _command: ManagerEdit,
    ) -> bcs_service_api::ServiceResult<ManagerSetChange> {
        Ok(ManagerSetChange {
            added: 1,
            removed: 0,
            total: 1,
        })
    }

    async fn sync_managers(
        &self,
        command: ManagerSync,
    ) -> bcs_service_api::ServiceResult<ManagerSetChange> {
        self.sync_bot_ids
            .lock()
            .expect("sync lock")
            .push(command.bot_id);
        Ok(ManagerSetChange {
            added: 0,
            removed: 0,
            total: 0,
        })
    }

    async fn sync_ownership(
        &self,
        command: OwnershipSync,
    ) -> bcs_service_api::ServiceResult<OwnershipChange> {
        self.ownership_sync_commands
            .lock()
            .expect("ownership sync lock")
            .push((command.bot_id.clone(), command.mutation.actor.clone()));
        Ok(OwnershipChange {
            bot_id: command.bot_id,
            owner_user_id: Some("alice".to_string()),
            previous_owner_user_id: Some("bob".to_string()),
            changed: true,
        })
    }

    async fn change_transfer(
        &self,
        command: TransferCommand,
    ) -> bcs_service_api::ServiceResult<TransferOutcome> {
        self.transfer_commands
            .lock()
            .expect("transfer lock")
            .push((
                command.action,
                command.key.bot_id.clone(),
                command.key.target_owner_actor_id.clone(),
            ));
        Ok(TransferOutcome::Applied {
            transfer: canned_transfer_view(),
            change: bcs_service_api::types::bot_authority::TransferChange::Updated,
        })
    }

    async fn can_manage(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        Ok(true)
    }

    async fn require_owner(&self, _: &str, _: &str) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }

    async fn require_manage(&self, _: &str, _: &str) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }
}

struct NoopHook;

#[async_trait]
impl BotAuthorityPort for NoopHook {
    async fn role(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<Option<BotAccessRelation>> {
        Ok(Some(BotAccessRelation::Owner))
    }

    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> bcs_service_api::ServiceResult<Vec<Option<BotAccessRelation>>> {
        Ok(queries.iter().map(|_| Some(BotAccessRelation::Owner)).collect())
    }

    async fn can_manage(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        Ok(true)
    }

    async fn require_owner(&self, _: &str, _: &str) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }

    async fn require_manage(&self, _: &str, _: &str) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }
}

struct AllowInviteCode;

#[async_trait]
impl InviteCodeService for AllowInviteCode {
    async fn init_invite_codes(
        &self,
        _command: bcs_service_api::application::v1::InitInviteCodes,
    ) -> Result<bcs_service_api::application::v1::InitInviteCodesResult, ApplicationError> {
        Err(ApplicationError::internal("unused in authority tests"))
    }

    async fn claim_public_invite_code(
        &self,
        _command: bcs_service_api::application::v1::ClaimPublicInviteCode,
    ) -> Result<bcs_service_api::application::v1::ClaimPublicInviteCodeResult, ApplicationError>
    {
        Err(ApplicationError::internal("unused in authority tests"))
    }

    async fn bind_invite_code(
        &self,
        _command: bcs_service_api::application::v1::BindInviteCode,
    ) -> Result<bcs_service_api::application::v1::BindInviteCodeResult, ApplicationError> {
        Err(ApplicationError::internal("unused in authority tests"))
    }

    async fn get_my_invite_code_binding(
        &self,
        _command: bcs_service_api::application::v1::GetMyInviteCodeBinding,
    ) -> Result<bcs_service_api::application::v1::InviteCodeBindingView, ApplicationError> {
        Err(ApplicationError::internal("unused in authority tests"))
    }

    async fn ensure_invite_code_access(
        &self,
        _caller: &AuthenticatedCaller,
    ) -> Result<(), ApplicationError> {
        Ok(())
    }
}

struct RecordingInviteGate {
    human_checks: std::sync::Mutex<Vec<String>>,
}

#[async_trait]
impl InviteCodeService for RecordingInviteGate {
    async fn init_invite_codes(
        &self,
        _command: bcs_service_api::application::v1::InitInviteCodes,
    ) -> Result<bcs_service_api::application::v1::InitInviteCodesResult, ApplicationError> {
        Err(ApplicationError::internal("unused in authority tests"))
    }

    async fn claim_public_invite_code(
        &self,
        _command: bcs_service_api::application::v1::ClaimPublicInviteCode,
    ) -> Result<bcs_service_api::application::v1::ClaimPublicInviteCodeResult, ApplicationError>
    {
        Err(ApplicationError::internal("unused in authority tests"))
    }

    async fn bind_invite_code(
        &self,
        _command: bcs_service_api::application::v1::BindInviteCode,
    ) -> Result<bcs_service_api::application::v1::BindInviteCodeResult, ApplicationError> {
        Err(ApplicationError::internal("unused in authority tests"))
    }

    async fn get_my_invite_code_binding(
        &self,
        _command: bcs_service_api::application::v1::GetMyInviteCodeBinding,
    ) -> Result<bcs_service_api::application::v1::InviteCodeBindingView, ApplicationError> {
        Err(ApplicationError::internal("unused in authority tests"))
    }

    async fn ensure_invite_code_access(
        &self,
        caller: &AuthenticatedCaller,
    ) -> Result<(), ApplicationError> {
        if let Some(user) = caller.user.as_ref() {
            self.human_checks
                .lock()
                .expect("gate lock")
                .push(user.id.clone());
        }
        Err(ApplicationError::invite_code_required(
            "human callers must bind an invite code",
        ))
    }
}

fn service(
    core: Arc<RecordingCore>,
    policy: AuthorityServicePolicy,
    invite: Arc<dyn InviteCodeService>,
) -> BotAuthorityServiceImpl {
    BotAuthorityServiceImpl::new(
        Arc::new(NoopHook),
        core as Arc<dyn BotAuthorityCoreService>,
        invite,
        true,
        policy,
        ENV.to_string(),
    )
}

fn service_with_gate_disabled(
    core: Arc<RecordingCore>,
    policy: AuthorityServicePolicy,
) -> BotAuthorityServiceImpl {
    BotAuthorityServiceImpl::new(
        Arc::new(NoopHook),
        core as Arc<dyn BotAuthorityCoreService>,
        Arc::new(RecordingInviteGate {
            human_checks: std::sync::Mutex::new(Vec::new()),
        }),
        false,
        policy,
        ENV.to_string(),
    )
}

fn operator_forbidden_code(error: &ApplicationError) -> bool {
    matches!(error, ApplicationError::ForbiddenCode { code, .. }
        if code == ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN)
}

#[tokio::test]
async fn app_in_scope_is_authorized_as_service_actor() {
    let policy = sync_policy(vec![full_sync_rule()]);
    let caller = app_caller(7, Some("tenant-a"));

    let actor = policy
        .authorize(
            &caller,
            AuthorityAction::ManagersSync,
            "bot-1")
        .expect("in-scope app");

    assert_eq!(
        actor,
        AuthorityActor::Service { app_id: 7 },
        "a trusted App acts under its real service id"
    );
}

#[tokio::test]
async fn missing_tenant_matches_only_explicit_absent_rule() {
    let absent_rule = rule(
        APP_NAME,
        TenantScope::Absent,
        &[AuthorityAction::ManagersSync],
    );
    let policy = sync_policy(vec![absent_rule]);

    // A tenantless caller matches ONLY the explicit `absent` rule.
    let caller = app_caller(7, None);
    policy
        .authorize(&caller, AuthorityAction::ManagersSync, "bot-1")
        .expect("explicit absent rule covers the tenantless caller");

    // A tenant-bearing caller must NOT match the absent rule.
    let tenant_caller = app_caller(7, Some("tenant-a"));
    let error = policy
        .authorize(&tenant_caller, AuthorityAction::ManagersSync, "bot-1")
        .expect_err("absent rule must not cover a tenant");
    assert!(operator_forbidden_code(&error));

    // An exact-tenant rule never matches a tenantless caller.
    let exact_policy = sync_policy(vec![full_sync_rule()]);
    let error = exact_policy
        .authorize(&caller, AuthorityAction::ManagersSync, "bot-1")
        .expect_err("exact-tenant rule excludes the tenantless caller");
    assert!(operator_forbidden_code(&error));
}

#[tokio::test]
async fn ids_are_case_exact() {
    let policy = sync_policy(vec![full_sync_rule()]);

    // App name casing is exact (spec §3.1: byte-exact match on
    // the verified principal's `app_name`).
    let error = policy
        .authorize(
            &named_app_caller("SYNC-APP", 7, Some("tenant-a")),
            AuthorityAction::ManagersSync,
            "bot-1")
        .expect_err("app name comparison is byte-exact");
    assert!(operator_forbidden_code(&error));

    // Tenant casing is exact.
    let error = policy
        .authorize(
            &app_caller(7, Some("TENANT-A")),
            AuthorityAction::ManagersSync,
            "bot-1")
        .expect_err("tenant comparison is byte-exact");
    assert!(operator_forbidden_code(&error));
}

#[tokio::test]
async fn out_of_scope_app_and_action_deny() {
    let sync_scope = sync_policy(vec![full_sync_rule()]);
    let caller = app_caller(7, Some("tenant-a"));

    // An unconfigured app name never matches.
    assert!(operator_forbidden_code(
        &sync_scope
            .authorize(
                &named_app_caller("other-app", 7, Some("tenant-a")),
                AuthorityAction::ManagersSync,
                "bot-1")
            .expect_err("unknown app name")
    ));
    // sync rule cannot transfer: the action is not in the rule.
    assert!(operator_forbidden_code(
        &sync_scope
            .authorize(
                &caller,
                AuthorityAction::OwnershipRead,
                "bot-1")
            .expect_err("sync-only rule lacks ownership:read")
    ));
}

#[tokio::test]
async fn empty_rule_sets_deny_everything() {
    // No rules at all: the default empty scope denies all.
    let empty_policy = sync_policy(vec![]);
    let caller = app_caller(7, Some("tenant-a"));
    assert!(operator_forbidden_code(
        &empty_policy
            .authorize(&caller, AuthorityAction::ManagersSync, "bot-1")
            .expect_err("empty policy denies sync")
    ));

    // A rule with an empty action set denies everything.
    let empty_actions = rule(
        APP_NAME,
        TenantScope::Exact("tenant-a".to_string()),
        &[],
    );
    let policy = sync_policy(vec![empty_actions]);
    assert!(operator_forbidden_code(
        &policy
            .authorize(&caller, AuthorityAction::ManagersSync, "bot-1")
            .expect_err("empty action set denies")
    ));
}

#[tokio::test]
async fn ordinary_app_outside_the_configured_scope_is_rejected() {
    let policy = sync_policy(vec![full_sync_rule()]);
    let caller = named_app_caller("other-app", 8, Some("tenant-a"));
    let error = policy
        .authorize(&caller, AuthorityAction::ManagersSync, "bot-1")
        .expect_err("unconfigured app name must not pass");
    assert!(operator_forbidden_code(&error));
}

#[tokio::test]
async fn human_caller_cannot_use_the_app_scope() {
    let policy = sync_policy(vec![full_sync_rule()]);
    // A plain Human caller is not App identity.
    let error = policy
        .authorize(
            &human_caller("alice", Some("tenant-a")),
            AuthorityAction::ManagersSync,
            "bot-1")
        .expect_err("Human identity must not consume the App scope");
    assert!(operator_forbidden_code(&error));
}

#[tokio::test]
async fn config_level_rejections_fail_startup_construction() {
    // Unknown action string.
    let error = AuthorityAppScopeRule::from_config_strings(
        APP_NAME,
        "tenant-a",
        &["managers:delete".to_string()],
    )
    .expect_err("unknown action must fail configuration loading");
    assert!(error.to_string().contains("unknown action"));

    // Case-exact action spelling is also rejected.
    assert!(
        AuthorityAppScopeRule::from_config_strings(
            APP_NAME,
            "tenant-a",
            &["managers:SYNC".to_string()],
        )
        .is_err(),
        "action spelling is case-exact"
    );

    // Wildcard strings anywhere are rejected.
    for wildcard in ["*.x".to_string(), "sync-?".to_string(), "100%".to_string()] {
        assert!(
            AuthorityAppScopeRule::from_config_strings(
                &wildcard,
                "tenant-a",
                &["managers:sync".to_string()],
            )
            .is_err(),
            "wildcard app name must fail configuration loading"
        );
    }
    assert!(
        AuthorityAppScopeRule::from_config_strings(
            APP_NAME,
            "tenant-*",
            &["managers:sync".to_string()],
        )
        .is_err(),
        "wildcard tenant must fail configuration loading"
    );

    // A duplicate action spelling in one rule is a shape error.
    assert!(
        AuthorityAppScopeRule::from_config_strings(
            APP_NAME,
            "tenant-a",
            &["managers:sync".to_string(), "managers:sync".to_string()],
        )
        .is_err(),
        "duplicate action in one rule must fail configuration loading"
    );

    // An empty/whitespace app or tenant is a shape error.
    assert!(
        AuthorityAppScopeRule::from_config_strings(
            "",
            "tenant-a",
            &["managers:sync".to_string()],
        )
        .is_err(),
        "empty app name must fail configuration loading"
    );
    assert!(
        AuthorityAppScopeRule::from_config_strings(
            APP_NAME,
            " padded ",
            &["managers:sync".to_string()],
        )
        .is_err(),
        "whitespace-padded tenant must fail configuration loading"
    );

    // Duplicate rule keys (same app/tenant) fail the
    // policy construction: one rule per key, no
    // order-based merging (spec §3.1).
    let error = AuthorityServicePolicy::new(
        ENV,
        vec![full_sync_rule(), full_sync_rule()],
    )
    .expect_err("duplicate rule keys must fail construction");
    assert!(error.to_string().contains("duplicate rule key"));
}

#[tokio::test]
async fn allowed_actions_are_exactly_the_five_frozen_strings() {
    assert_eq!(AuthorityAction::parse("managers:sync"), Some(AuthorityAction::ManagersSync));
    assert_eq!(AuthorityAction::parse("ownership:read"), Some(AuthorityAction::OwnershipRead));
    assert_eq!(AuthorityAction::parse("ownership:accept"), Some(AuthorityAction::OwnershipAccept));
    assert_eq!(
        AuthorityAction::parse("ownership:reject"),
        Some(AuthorityAction::OwnershipReject)
    );
    assert_eq!(
        AuthorityAction::parse("ownership:sync"),
        Some(AuthorityAction::OwnershipSync)
    );
    for unknown in ["managers:delete", "sync", "ownership:reject ", "ownership:sync ", "", "managers"] {
        assert_eq!(AuthorityAction::parse(unknown), None);
    }
}

#[tokio::test]
async fn ownership_sync_authorizes_independently_of_managers_sync() {
    // A rule granting ONLY managers:sync must not authorize
    // ownership:sync (设计 §8.3 权限隔离), and vice versa.
    let managers_only = sync_policy(vec![full_sync_rule()]);
    let caller = app_caller(7, Some("tenant-a"));
    assert!(operator_forbidden_code(
        &managers_only
            .authorize(
                &caller,
                AuthorityAction::OwnershipSync,
                "bot-1")
            .expect_err("managers-only rule lacks ownership:sync")
    ));

    // The mirror: a rule granting only ownership:sync
    // authorizes ownership:sync and nothing else.
    let ownership_only = sync_policy(vec![rule(
        APP_NAME,
        TenantScope::Exact("tenant-a".to_string()),
        &[AuthorityAction::OwnershipSync],
    )]);
    let actor = ownership_only
        .authorize(
            &caller,
            AuthorityAction::OwnershipSync,
            "bot-1")
        .expect("ownership:sync rule authorizes the action");
    assert_eq!(
        actor,
        AuthorityActor::Service { app_id: 7 },
        "a trusted App acts under its real service id"
    );
    assert!(operator_forbidden_code(
        &ownership_only
            .authorize(
                &caller,
                AuthorityAction::ManagersSync,
                "bot-1")
            .expect_err("ownership-only rule lacks managers:sync")
    ));
}

// ---------------------------------------------------------------------------
// facade-level identity selection (the App/Human split per route kind)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn sync_rejects_a_plain_human_and_selects_the_app_for_dual_credentials() {
    let core = Arc::new(RecordingCore::default());
    let service = service(
        core.clone(),
        sync_policy(vec![full_sync_rule()]),
        Arc::new(AllowInviteCode),
    );

    // A plain Human credential (no App identity anywhere)
    // can never run sync.
    let error = service
        .sync_managers(
            &human_caller("alice", Some("tenant-a")),
            ManagerSync {
                bot_id: "bot-1".to_string(),
                user_ids: vec![],
                mutation: AuthorityMutation {
                    mutation_id: String::new(),
                    actor: AuthorityActor::System { name: "unused".to_string() },
                },
            },
        )
        .await
        .expect_err("Human-only sync attempt");
    assert!(operator_forbidden_code(&error));
    assert!(core.sync_bot_ids.lock().expect("lock").is_empty());

    // A bot-only credential is likewise rejected.
    let error = service
        .sync_managers(
            &bot_only_caller(),
            ManagerSync {
                bot_id: "bot-1".to_string(),
                user_ids: vec![],
                mutation: AuthorityMutation {
                    mutation_id: String::new(),
                    actor: AuthorityActor::System { name: "unused".to_string() },
                },
            },
        )
        .await
        .expect_err("bot-only sync attempt");
    assert!(operator_forbidden_code(&error));
    assert!(core.sync_bot_ids.lock().expect("lock").is_empty());

    // spec §6.1: sync EXPLICITLY selects the App identity —
    // a User+App dual credential IS processed as the App.
    service
        .sync_managers(
            &user_plus_app_caller("alice", 7),
            ManagerSync {
                bot_id: "bot-1".to_string(),
                user_ids: vec!["bob".to_string()],
                mutation: AuthorityMutation {
                    mutation_id: String::new(),
                    actor: AuthorityActor::System { name: "unused".to_string() },
                },
            },
        )
        .await
        .expect("dual credential syncs explicitly as the App");
    assert_eq!(
        core.sync_bot_ids.lock().expect("lock").as_slice(),
        ["bot-1".to_string()]
    );
}

#[tokio::test]
async fn in_scope_app_sync_submits_service_actor() {
    let core = Arc::new(RecordingCore::default());
    let service = service(
        core.clone(),
        sync_policy(vec![full_sync_rule()]),
        Arc::new(AllowInviteCode),
    );

    service
        .sync_managers(
            &app_caller(7, Some("tenant-a")),
            ManagerSync {
                bot_id: "bot-1".to_string(),
                user_ids: vec!["bob".to_string(), "bob".to_string()],
                mutation: AuthorityMutation {
                    mutation_id: String::new(),
                    actor: AuthorityActor::System { name: "unused".to_string() },
                },
            },
        )
        .await
        .expect("in-scope sync");
    assert_eq!(
        core.sync_bot_ids.lock().expect("lock").as_slice(),
        ["bot-1".to_string()]
    );
}

#[tokio::test]
async fn sync_only_credential_cannot_accept_or_reject() {
    let core = Arc::new(RecordingCore::default());
    let service = service(
        core.clone(),
        sync_policy(vec![full_sync_rule()]),
        Arc::new(AllowInviteCode),
    );
    let caller = app_caller(7, Some("tenant-a"));

    for action in [TransferAction::Accept, TransferAction::Reject] {
        let error = service
            .change_transfer(
                &caller,
                TransferCommand {
                    key: TransferKey {
                        bot_id: "bot-1".to_string(),
                        target_owner_actor_id: "human_bob".to_string(),
                    },
                    action,
                    mutation: AuthorityMutation {
                        mutation_id: String::new(),
                        actor: AuthorityActor::System { name: "unused".to_string() },
                    },
                },
            )
            .await
            .expect_err("sync-only scope must not decide transfers");
        assert!(operator_forbidden_code(&error));
    }
    assert!(core.transfer_commands.lock().expect("lock").is_empty());
}

#[tokio::test]
async fn provider_style_credentials_gain_no_authority_actions() {
    let core = Arc::new(RecordingCore::default());
    // One combined rule per app/tenant: sync + all transfer
    // actions (spec §3.1 simplified shape).
    let combined_rule = rule(
        APP_NAME,
        TenantScope::Exact("tenant-a".to_string()),
        &[
            AuthorityAction::ManagersSync,
            AuthorityAction::OwnershipRead,
            AuthorityAction::OwnershipAccept,
            AuthorityAction::OwnershipReject,
        ],
    );
    let service = service(
        core.clone(),
        sync_policy(vec![combined_rule]),
        Arc::new(AllowInviteCode),
    );

    for caller in [bot_only_caller(), access_key_only_caller()] {
        // Provider-style credentials stay retirement-only: no sync.
        let error = service
            .sync_managers(
                &caller,
                ManagerSync {
                    bot_id: "bot-1".to_string(),
                    user_ids: vec![],
                    mutation: AuthorityMutation {
                        mutation_id: String::new(),
                        actor: AuthorityActor::System { name: "unused".to_string() },
                    },
                },
            )
            .await
            .expect_err("non-Human non-App credential must not sync");
        assert!(operator_forbidden_code(&error));

        // No transfer decisions either.
        let error = service
            .change_transfer(
                &caller,
                TransferCommand {
                    key: TransferKey {
                        bot_id: "bot-1".to_string(),
                        target_owner_actor_id: "human_bob".to_string(),
                    },
                    action: TransferAction::Accept,
                    mutation: AuthorityMutation {
                        mutation_id: String::new(),
                        actor: AuthorityActor::System { name: "unused".to_string() },
                    },
                },
            )
            .await
            .expect_err("non-Human non-App credential must not decide transfers");
        assert!(operator_forbidden_code(&error));
    }
    assert!(core.sync_bot_ids.lock().expect("lock").is_empty());
    assert!(core.transfer_commands.lock().expect("lock").is_empty());
}

#[tokio::test]
async fn ownership_sync_is_trusted_app_only() {
    let core = Arc::new(RecordingCore::default());
    // Built first: the `service` binding below would shadow the
    // `service()` constructor for a second in-place call.
    let managers_only = service(
        core.clone(),
        sync_policy(vec![full_sync_rule()]),
        Arc::new(AllowInviteCode),
    );
    let service = service(
        core.clone(),
        sync_policy(vec![ownership_sync_rule()]),
        Arc::new(AllowInviteCode),
    );

    // A Human-only credential is denied and never falls back to
    // the App scope (设计 §2).
    let error = service
        .sync_ownership(
            &human_caller("alice", Some("tenant-a")),
            OwnershipSync {
                bot_id: "bot-1".to_string(),
                owner_user_id: "bob".to_string(),
                mutation: unused_placeholder_mutation(),
            },
        )
        .await
        .expect_err("Human-only denied");
    assert!(operator_forbidden_code(&error));
    assert!(core.ownership_sync_commands.lock().expect("lock").is_empty());

    // The scoped App passes, and the facade built the mutation
    // from the VERIFIED App identity (the audit keeps the app id).
    let result = service
        .sync_ownership(
            &app_caller(7, Some("tenant-a")),
            OwnershipSync {
                bot_id: "bot-1".to_string(),
                owner_user_id: "bob".to_string(),
                mutation: unused_placeholder_mutation(),
            },
        )
        .await
        .expect("scoped app syncs");
    assert!(result.changed);
    let recorded = core.ownership_sync_commands.lock().expect("lock").clone();
    assert_eq!(recorded.len(), 1);
    assert_eq!(recorded[0].1, AuthorityActor::Service { app_id: 7 });

    // A dual Human+App credential is processed as the App (sync
    // 始终显式选择 App 身份).
    let result = service
        .sync_ownership(
            &user_plus_app_caller("mallory", 7),
            OwnershipSync {
                bot_id: "bot-2".to_string(),
                owner_user_id: "bob".to_string(),
                mutation: unused_placeholder_mutation(),
            },
        )
        .await
        .expect("dual identity processes as App");
    assert!(result.changed);
    let recorded = core.ownership_sync_commands.lock().expect("lock").clone();
    assert_eq!(recorded.len(), 2);
    assert_eq!(recorded[1].1, AuthorityActor::Service { app_id: 7 });

    // An App with ONLY the managers:sync scope cannot sync
    // ownership (设计 §8.3 权限隔离).
    let error = managers_only
        .sync_ownership(
            &app_caller(7, Some("tenant-a")),
            OwnershipSync {
                bot_id: "bot-1".to_string(),
                owner_user_id: "bob".to_string(),
                mutation: unused_placeholder_mutation(),
            },
        )
        .await
        .expect_err("managers-only scope denies ownership sync");
    assert!(operator_forbidden_code(&error));

    // A tenant outside the rule is denied (byte-exact tenant).
    let error = service
        .sync_ownership(
            &app_caller(7, Some("tenant-b")),
            OwnershipSync {
                bot_id: "bot-1".to_string(),
                owner_user_id: "bob".to_string(),
                mutation: unused_placeholder_mutation(),
            },
        )
        .await
        .expect_err("tenant mismatch denied");
    assert!(operator_forbidden_code(&error));
    assert_eq!(core.ownership_sync_commands.lock().expect("lock").len(), 2);
}

#[tokio::test]
async fn authorized_app_reads_and_decides_transfers_through_the_service() {
    let core = Arc::new(RecordingCore::default());
    *core.transfer_row.lock().expect("lock") = Some(canned_transfer_view());
    let service = service(
        core.clone(),
        sync_policy(vec![transfer_read_rule()]),
        Arc::new(AllowInviteCode),
    );
    let caller = app_caller(7, Some("tenant-a"));
    let key = TransferKey {
        bot_id: "bot-1".to_string(),
        target_owner_actor_id: "human_bob".to_string(),
    };

    // Scoped read.
    let view = service
        .transfer(&caller, &key)
        .await
        .expect("scoped read");
    assert_eq!(
        view.as_ref().map(|v| v.target_owner_actor_id.as_str()),
        Some("human_bob")
    );

    // Scoped accept/reject reach the Core as Service actors.
    for action in [TransferAction::Accept, TransferAction::Reject] {
        service
            .change_transfer(
                &caller,
                TransferCommand {
                    key: TransferKey {
                        bot_id: "bot-1".to_string(),
                        target_owner_actor_id: "human_bob".to_string(),
                    },
                    action,
                    mutation: AuthorityMutation {
                        mutation_id: String::new(),
                        actor: AuthorityActor::System { name: "unused".to_string() },
                    },
                },
            )
            .await
            .unwrap_or_else(|_| panic!("{action:?} must pass for the scoped app"));
    }
    let commands = core.transfer_commands.lock().expect("lock");
    assert_eq!(commands.len(), 2, "exactly the two scoped decisions ran");
}

#[tokio::test]
async fn dual_route_transfer_get_serves_human_parties_and_denies_others() {
    let core = Arc::new(RecordingCore::default());
    *core.transfer_row.lock().expect("lock") = Some(canned_transfer_view());
    let service = service(
        core.clone(),
        sync_policy(vec![transfer_read_rule()]),
        Arc::new(AllowInviteCode),
    );
    let key = TransferKey {
        bot_id: "bot-1".to_string(),
        target_owner_actor_id: "human_bob".to_string(),
    };

    // The current-round initiator may read.
    service
        .transfer(&human_caller("alice", Some("tenant-a")), &key)
        .await
        .expect("initiator read");
    // The target Human may read.
    service
        .transfer(&human_caller("bob", Some("tenant-a")), &key)
        .await
        .expect("target read");
    // An unrelated Human is denied.
    let error = service
        .transfer(&human_caller("carol", Some("tenant-a")), &key)
        .await
        .expect_err("unrelated Human read");
    assert!(operator_forbidden_code(&error));

    // A missing row is a pinned 404, not a secret.
    *core.transfer_row.lock().expect("lock") = None;
    let error = service
        .transfer(&human_caller("bob", Some("tenant-a")), &key)
        .await
        .expect_err("missing transfer row");
    assert!(
        matches!(error, ApplicationError::NotFound { ref code, .. } if code == ERROR_BOT_TRANSFER_NOT_FOUND)
    );
}

#[tokio::test]
async fn human_only_routes_reject_app_and_non_human_identities() {
    let core = Arc::new(RecordingCore::default());
    let service = service(
        core.clone(),
        sync_policy(vec![transfer_read_rule()]),
        Arc::new(AllowInviteCode),
    );

    // Initiate/cancel/inbox and single-item edits are Human-rule.
    let app = app_caller(7, Some("tenant-a"));
    let error = service
        .edit_manager(
            &app,
            ManagerEdit {
                bot_id: "bot-1".to_string(),
                user_id: "bob".to_string(),
                action: bcs_service_api::types::bot_authority::ManagerEditAction::Grant,
                mutation: AuthorityMutation {
                    mutation_id: String::new(),
                    actor: AuthorityActor::System { name: "unused".to_string() },
                },
            },
        )
        .await
        .expect_err("App cannot single-edit managers");
    assert!(operator_forbidden_code(&error));

    let error = service
        .transfers(
            &app,
            TransferListQuery {
                user_id: "unused".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect_err("App cannot read the Human inbox");
    assert!(operator_forbidden_code(&error));

    // Manager/ownership reads stay Human-rule (owner/manager).
    let error = service
        .managers(
            &app,
            ManagerListQuery {
                bot_id: "bot-1".to_string(),
                offset: 0,
                limit: 50,
            },
        )
        .await
        .expect_err("App cannot read the manager page through the Human route");
    assert!(operator_forbidden_code(&error));
}

#[tokio::test]
async fn human_invite_gate_applies_to_human_path_only() {
    let core = Arc::new(RecordingCore::default());
    *core.transfer_row.lock().expect("lock") = Some(canned_transfer_view());
    let gate = Arc::new(RecordingInviteGate {
        human_checks: std::sync::Mutex::new(Vec::new()),
    });
    let service = service(
        core.clone(),
        sync_policy(vec![transfer_read_rule()]),
        gate.clone() as Arc<dyn InviteCodeService>,
    );

    // An un-invite-coded Human on a Human path is rejected.
    let key = TransferKey {
        bot_id: "bot-1".to_string(),
        target_owner_actor_id: "human_bob".to_string(),
    };
    let error = service
        .transfer(&human_caller("bob", Some("tenant-a")), &key)
        .await
        .expect_err("un-invite-coded Human");
    assert!(
        matches!(error, ApplicationError::ForbiddenCode { ref code, .. } if code == "invite_code_required")
    );
    assert_eq!(gate.human_checks.lock().expect("lock").len(), 1);

    // A trusted App on the SAME dual handler is never treated as an
    // un-invite-coded Human.
    service
        .transfer(&app_caller(7, Some("tenant-a")), &key)
        .await
        .expect("scoped app bypasses the Human invite gate");
    assert_eq!(gate.human_checks.lock().expect("lock").len(), 1);
}

#[tokio::test]
async fn disabled_invite_gate_keeps_human_paths_open_without_binding() {
    let core = Arc::new(RecordingCore::default());
    *core.transfer_row.lock().expect("lock") = Some(canned_transfer_view());
    let service = service_with_gate_disabled(
        core.clone(),
        sync_policy(vec![transfer_read_rule()]),
    );
    let key = TransferKey {
        bot_id: "bot-1".to_string(),
        target_owner_actor_id: "human_bob".to_string(),
    };
    service
        .transfer(&human_caller("bob", Some("tenant-a")), &key)
        .await
        .expect("invite gate disabled keeps the Human path open");
}

#[tokio::test]
async fn human_accept_and_reject_keep_the_invite_gate() {
    // Review fix: the dual-admission Human branch of the
    // decision routes must run the same invite-code gate as
    // every other Human path (spec §6.1) — an
    // un-invite-coded Human cannot accept/reject, and the
    // rejection never falls back to the App scope.
    let core = Arc::new(RecordingCore::default());
    *core.transfer_row.lock().expect("lock") = Some(canned_transfer_view());
    let gate = Arc::new(RecordingInviteGate {
        human_checks: std::sync::Mutex::new(Vec::new()),
    });
    // gate.allow == false: the Human path denies; the App
    // path is unaffected (the gate ignores non-Humans).
    let service = service(
        core.clone(),
        sync_policy(vec![transfer_read_rule()]),
        gate.clone() as Arc<dyn InviteCodeService>,
    );
    let key = TransferKey {
        bot_id: "bot-1".to_string(),
        target_owner_actor_id: "human_bob".to_string(),
    };

    for action in [TransferAction::Accept, TransferAction::Reject] {
        let error = service
            .change_transfer(
                &human_caller("bob", Some("tenant-a")),
                TransferCommand {
                    key: key.clone(),
                    action,
                    mutation: AuthorityMutation {
                        mutation_id: String::new(),
                        actor: AuthorityActor::System {
                            name: "unused".to_string(),
                        },
                    },
                },
            )
            .await
            .expect_err("un-invite-coded Human decision");
        assert!(
            matches!(error, ApplicationError::ForbiddenCode { ref code, .. }
                if code == "invite_code_required"),
            "unexpected error: {error:?}"
        );
    }
    // No decision reached the Core: the Human rejections are
    // final, before any App fallback could occur.
    assert!(core.transfer_commands.lock().expect("lock").is_empty());
    assert_eq!(gate.human_checks.lock().expect("lock").len(), 2);

    // The App branch of the SAME dual handler is unaffected
    // by the invite gate.
    service
        .change_transfer(
            &app_caller(7, Some("tenant-a")),
            TransferCommand {
                key: key.clone(),
                action: TransferAction::Accept,
                mutation: AuthorityMutation {
                    mutation_id: String::new(),
                    actor: AuthorityActor::System {
                        name: "unused".to_string(),
                    },
                },
            },
        )
        .await
        .expect("scoped app decides through the same handler");
    assert_eq!(gate.human_checks.lock().expect("lock").len(), 2);
}
