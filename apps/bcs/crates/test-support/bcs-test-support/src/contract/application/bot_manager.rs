//! `BotManagerService` shared conformance harness (plan Task 18 R25,
//! plan 公共命名: `bot_manager_service_contract_tests`).
//!
//! The driver constructs the PRODUCTION manager facade over the shared
//! recording doubles — [`RecordingBotAuthorityHook`] +
//! [`CountingBotAuthorityCore`] — and this suite pins the store-independent
//! application contract:
//!
//! - the lane is Human-only: a Bot-only caller is Forbidden on every use
//!   case and the hook is never consulted for it;
//! - the centralized hook is asked FIRST on every use case: with a
//!   DENYING hook the answer is Forbidden and the authority core is never
//!   read (zero reads/writes) — an unwired authority store can never leak
//!   manager facts;
//! - the deeper owner/manager/team semantics (owner-target 409, team
//!   sources untouched, positional pages) live in the production crate's
//!   own suites (plan Task 13) over the real stores, keeping this shared
//!   harness driver-substitutable without recreating them.

use std::sync::Arc;

use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    BotManagerService, GrantBotManager, ListBotManagers, RevokeBotManager,
};

use crate::{CountingBotAuthorityCore, RecordingBotAuthorityHook};

/// Driver-supplied wiring bundle: the production facade over the shared
/// recording doubles.
pub struct BotManagerServiceHarness {
    /// The driver's `BotManagerServiceImpl` (or substitutable equivalent).
    pub service: Arc<dyn BotManagerService>,
    /// The recording hook the driver constructed the facade with.
    pub hook: Arc<RecordingBotAuthorityHook>,
    /// The counting authority core the driver constructed the facade with.
    pub core: Arc<CountingBotAuthorityCore>,
}

fn human_caller(user_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("contract-tenant".into()),
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

fn bot_only_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: None,
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: "contract-bot".into(),
            owner_id: "contract-owner".into(),
            app_id: 1,
            agent_code: "contract-agent".into(),
        }),
        app: None,
        access_key: None,
    }
}

fn is_forbidden(error: &ApplicationError) -> bool {
    matches!(error, ApplicationError::Forbidden(_) | ApplicationError::ForbiddenCode { .. })
}

/// The shared `BotManagerService` conformance suite (async: the use cases
/// are async trait methods).
pub async fn bot_manager_service_contract_tests(h: &BotManagerServiceHarness) {
    let bot_id = "manager-contract-bot";
    let user_id = "manager-contract-user";

    // Human-only: a Bot-only caller is Forbidden on every use case, and
    // the hook is NOT consulted (calls stay at zero — identity precedes
    // authorization).
    let hook_before = h.hook.calls();
    for error in [
        h.service
            .list_managers(ListBotManagers {
                caller: bot_only_caller(),
                bot_id: bot_id.into(),
                offset: 0,
                limit: 20,
            })
            .await
            .expect_err("bot-only list must be refused"),
        h.service
            .grant_manager(GrantBotManager {
                caller: bot_only_caller(),
                bot_id: bot_id.into(),
                user_id: user_id.into(),
            })
            .await
            .expect_err("bot-only grant must be refused"),
        h.service
            .revoke_manager(RevokeBotManager {
                caller: bot_only_caller(),
                bot_id: bot_id.into(),
                user_id: user_id.into(),
            })
            .await
            .expect_err("bot-only revoke must be refused"),
    ] {
        assert!(is_forbidden(&error), "Bot-only callers stay Forbidden: {error:?}");
    }
    assert_eq!(
        h.hook.calls(),
        hook_before,
        "no authority question is asked for an unauthenticated-Human caller"
    );

    // Hook-FIRST: with a denying hook every use case surfaces Forbidden
    // and the authority core is never read (the facade may not probe the
    // store before the centralized hook answers).
    let reads_before = h.core.reads();
    let writes_before = h.core.writes();
    let hook_before = h.hook.calls();
    for error in [
        h.service
            .list_managers(ListBotManagers {
                caller: human_caller(user_id),
                bot_id: bot_id.into(),
                offset: 0,
                limit: 20,
            })
            .await
            .expect_err("denying hook list must be refused"),
        h.service
            .grant_manager(GrantBotManager {
                caller: human_caller(user_id),
                bot_id: bot_id.into(),
                user_id: user_id.into(),
            })
            .await
            .expect_err("denying hook grant must be refused"),
        h.service
            .revoke_manager(RevokeBotManager {
                caller: human_caller(user_id),
                bot_id: bot_id.into(),
                user_id: user_id.into(),
            })
            .await
            .expect_err("denying hook revoke must be refused"),
    ] {
        assert!(is_forbidden(&error), "denying hook answers Forbidden: {error:?}");
    }
    assert_eq!(
        h.hook.calls(),
        hook_before + 3,
        "exactly one hook question per use case (three use cases asked)"
    );
    // Three use cases above consulted the hook once each; no core reads
    // or writes happened for any of them.
    assert_eq!(
        h.core.reads(),
        reads_before,
        "a denied Human never triggers an authority read"
    );
    assert_eq!(
        h.core.writes(),
        writes_before,
        "a denied Human never triggers an authority write"
    );
}