//! Identity matrix tests for `resolve_authorized_principal`
//! (bot-authority plan Task 6.2 RED at the resolver level, spec §3.3).
//!
//! The resolver must return ONLY a principal selection. It never
//! fabricates resource roles, never falls back from a Human
//! rejection to a Bot/App identity, and never consults the
//! authority hook for pure-Bot credentials.

use std::sync::atomic::{AtomicBool, Ordering};

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    IdentityPolicy, Principal, resolve_authorized_principal,
};
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::types::bot_authority::{BotAccessRelation, BotRoleQuery};
use bcs_service_api::{ServiceError, ServiceResult};

const BOT: &str = "bot-x";
const OWNER: &str = "user-owner";
const MANAGER: &str = "user-manager";
const OUTSIDER: &str = "user-x";

/// Hook returning a canned `can_manage` verdict; counts every
/// consult so tests can prove which policy paths hit the hook.
#[derive(Default)]
struct HookStub {
    can_manage_verdict: bool,
    consults: AtomicBool,
    fail: bool,
}

impl HookStub {
    fn granting() -> Self {
        Self {
            can_manage_verdict: true,
            ..Self::default()
        }
    }

    fn denying() -> Self {
        Self::default()
    }

    fn consulted(&self) -> bool {
        self.consults.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl BotAuthorityPort for HookStub {
    async fn role(&self, _user_id: &str, _bot_id: &str) -> ServiceResult<Option<BotAccessRelation>> {
        Err(ServiceError::InternalError("role is not part of resolver contracts".into()))
    }

    async fn roles_for(&self, _queries: &[BotRoleQuery]) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        Err(ServiceError::InternalError("roles_for is not part of resolver contracts".into()))
    }

    async fn can_manage(&self, _user_id: &str, _bot_id: &str) -> ServiceResult<bool> {
        self.consults.store(true, Ordering::SeqCst);
        if self.fail {
            return Err(ServiceError::InternalError("authority storage down".into()));
        }
        Ok(self.can_manage_verdict)
    }

    async fn require_owner(&self, _user_id: &str, _bot_id: &str) -> ServiceResult<()> {
        Err(ServiceError::InternalError("require_owner unused".into()))
    }

    async fn require_manage(&self, _user_id: &str, _bot_id: &str) -> ServiceResult<()> {
        Err(ServiceError::InternalError("require_manage unused".into()))
    }
}

fn human_user(id: &str) -> AuthenticatedUserIdentity {
    AuthenticatedUserIdentity {
        id: id.to_string(),
        username: id.to_string(),
        display_name: None,
        full_name: None,
    }
}

fn caller(user: Option<&str>, bot: Option<&str>) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".to_string()),
        user: user.map(human_user),
        bot: bot.map(|bot_uuid| AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.to_string(),
            owner_id: user.unwrap_or("").to_string(),
            app_id: 7,
            agent_code: format!("agent-{bot_uuid}"),
        }),
        app: None,
        access_key: None,
    }
}

fn authorized_policy(bot_id: &str) -> IdentityPolicy {
    IdentityPolicy::HumanOrAuthorizedBot {
        bot_id: bot_id.to_string(),
    }
}

fn assert_bot_principal(principal: Principal, bot_id: &str) {
    match principal {
        Principal::Bot(bot) => assert_eq!(bot.bot_uuid, bot_id),
        other => panic!("expected Bot principal, got {other:?}"),
    }
}

fn assert_human_principal(principal: Principal, user_id: &str) {
    match principal {
        Principal::Human(human) => assert_eq!(human.subject.id, user_id),
        other => panic!("expected Human principal, got {other:?}"),
    }
}

// -- A / M / X matrix ------------------------------------------------------

/// The Human owner A of bot X acting through a hybrid
/// User+Bot credential resolves to the Bot principal.
#[tokio::test]
async fn owner_hybrid_resolves_to_bot() {
    let hook = HookStub::granting();
    let caller = caller(Some(OWNER), Some(BOT));
    let principal = resolve_authorized_principal(&caller, authorized_policy(BOT), &hook)
        .await
        .expect("owner resolves");
    assert_bot_principal(principal, BOT);
    assert!(hook.consulted());
}

/// The manager M of bot X also resolves to the Bot principal.
#[tokio::test]
async fn manager_hybrid_resolves_to_bot() {
    let hook = HookStub::granting();
    let caller = caller(Some(MANAGER), Some(BOT));
    resolve_authorized_principal(&caller, authorized_policy(BOT), &hook)
        .await
        .expect("manager resolves");
}

/// A Human with no relation X is rejected with no fallback.
#[tokio::test]
async fn outsider_hybrid_is_rejected_without_fallback() {
    let hook = HookStub::denying();
    let caller = caller(Some(OUTSIDER), Some(BOT));
    let error = resolve_authorized_principal(&caller, authorized_policy(BOT), &hook)
        .await
        .err()
        .expect("outsider is denied");
    assert!(
        matches!(error, bcs_service_api::application::v1::ApplicationError::Forbidden(_))
    );
}

/// A Bot credential with NO Human-bound user identity keeps its
/// pure-Bot contract: the authority hook is NOT consulted, so a
/// revoked management edge cannot break it (spec §3.3).
#[tokio::test]
async fn pure_bot_credential_skips_the_authority_hook() {
    let hook = HookStub::denying();
    let caller = caller(None, Some(BOT));
    let principal = resolve_authorized_principal(&caller, authorized_policy(BOT), &hook)
        .await
        .expect("pure bot resolves");
    assert_bot_principal(principal, BOT);
    assert!(!hook.consulted());
}

/// A Human-only credential keeps its Human self rules.
#[tokio::test]
async fn human_only_credential_keeps_human_principal() {
    let hook = HookStub::denying();
    let caller = caller(Some(OUTSIDER), None);
    let principal = resolve_authorized_principal(&caller, authorized_policy(BOT), &hook)
        .await
        .expect("human-only keeps self rules");
    assert_human_principal(principal, OUTSIDER);
    // The authority hook decides only the Human→Bot representation,
    // which never applies to a Human-only credential.
    assert!(!hook.consulted());
}

/// A revoked management edge rejects the hybrid principal: after
/// the edge is revoked, resolving again fails closed.
#[tokio::test]
async fn revoking_the_management_edge_rejects_hybrid_resolution() {
    let hook = HookStub::granting();
    let caller = caller(Some(MANAGER), Some(BOT));
    resolve_authorized_principal(&caller, authorized_policy(BOT), &hook)
        .await
        .expect("before revocation");
    let revoked = HookStub::denying();
    assert!(
        resolve_authorized_principal(&caller, authorized_policy(BOT), &revoked)
            .await
            .is_err()
    );
}

/// After an A→B transfer, A remains manager: A still resolves
/// (non-owner role is enough for the authorized-Bot policy because
/// `can_manage` covers owner + manager), and B — the new owner —
/// resolves too. The test models the edge facts through the hook
/// verdicts; edge storage semantics are pinned by the authority
/// store suites.
#[tokio::test]
async fn transfer_keeps_former_owner_resolvable_as_manager() {
    let as_manager = HookStub::granting();
    let caller_a = caller(Some(OWNER), Some(BOT));
    resolve_authorized_principal(&caller_a, authorized_policy(BOT), &as_manager)
        .await
        .expect("transferred-out former owner (still manager) resolves");
    let as_owner = HookStub::granting();
    let caller_b = caller(Some("user-b"), Some(BOT));
    resolve_authorized_principal(&caller_b, authorized_policy(BOT), &as_owner)
        .await
        .expect("new owner B resolves");
}

/// A hybrid credential whose Bot does not match the requested bot
/// id must not launder the Bot identity through the Human edge.
#[tokio::test]
async fn bot_id_mismatch_is_rejected() {
    let hook = HookStub::granting();
    let caller = caller(Some(OWNER), Some("bot-other"));
    assert!(
        resolve_authorized_principal(&caller, authorized_policy(BOT), &hook)
            .await
            .is_err()
    );
}

/// Authority read failures propagate as errors — never a deny that
/// looks like an authorization verdict and never an Ok.
#[tokio::test]
async fn authority_read_failure_is_an_error() {
    let hook = HookStub {
        fail: true,
        ..HookStub::default()
    };
    let caller = caller(Some(OWNER), Some(BOT));
    assert!(
        resolve_authorized_principal(&caller, authorized_policy(BOT), &hook)
            .await
            .is_err()
    );
}

/// No identity at all stays rejected.
#[tokio::test]
async fn credentialless_caller_is_rejected() {
    let hook = HookStub::granting();
    let caller = caller(None, None);
    assert!(
        resolve_authorized_principal(&caller, authorized_policy(BOT), &hook)
            .await
            .is_err()
    );
    assert!(!hook.consulted());
}

/// Existing policies keep their claim-local selection paths; the
/// hook is never consulted for them.
#[tokio::test]
async fn legacy_policies_do_not_consult_the_hook() {
    let hook = HookStub::granting();
    let human = caller(Some(OUTSIDER), None);
    assert!(
        resolve_authorized_principal(&human, IdentityPolicy::HumanOnly, &hook)
            .await
            .is_ok()
    );
    let bot = caller(None, Some(BOT));
    resolve_authorized_principal(&bot, IdentityPolicy::BotOnly, &hook)
        .await
        .expect("bot-only unchanged");
    let owned = caller(Some(OWNER), Some(BOT));
    resolve_authorized_principal(&owned, IdentityPolicy::HumanOrOwnedBot, &hook)
        .await
        .expect("claim-local ownership selection unchanged");
    assert!(!hook.consulted());
}

#[tokio::test]
async fn default_policy_is_still_human_only() {
    assert_eq!(IdentityPolicy::default(), IdentityPolicy::HumanOnly);
    assert_eq!(
        authorized_policy(BOT).authorized_bot_id(),
        Some(BOT)
    );
    assert_eq!(IdentityPolicy::HumanOnly.authorized_bot_id(), None);
}
