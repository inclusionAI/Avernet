use bcs_auth_api::AuthPrincipal;
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};

/// Per-connection authentication snapshot resolved once at the WS upgrade
/// boundary (spec §10.1.1): the display actor id plus the independent
/// runtime-only caller ingress from the SAME authentication run. The ingress
/// is never serde-serialized, never logged by its redacted Debug and never
/// persisted. The WS channel sets NO identity expiry (operations decision,
/// plan §15.97 — aligned with the ordinary workbench mode): the login must
/// be alive at the upgrade moment; afterwards the identity lives for the
/// connection. Deeper material (handoff slots, subjects, proofs, dispatch
/// deadlines) keeps its own short-lived windows untouched.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WorkbenchConnectionAuth {
    UserBound {
        actor_id: Option<String>,
        caller_ingress: Option<AuthenticatedHumanCallerIngress>,
    },
    SessionBound {
        tenant: Option<String>,
        actor_id: String,
        group_id: String,
        session_id: String,
        caller_ingress: Option<AuthenticatedHumanCallerIngress>,
    },
}

impl WorkbenchConnectionAuth {
    pub fn actor_id(&self) -> Option<&str> {
        match self {
            Self::UserBound { actor_id, .. } => actor_id.as_deref(),
            Self::SessionBound { actor_id, .. } => Some(actor_id),
        }
    }

    /// Upgrade-time ingress snapshot. There is no window re-check on the WS
    /// channel (§15.97 deleted it): carrying the material past any frame
    /// time IS the contract.
    pub fn caller_ingress(&self) -> Option<&AuthenticatedHumanCallerIngress> {
        match self {
            Self::UserBound { caller_ingress, .. } => caller_ingress.as_ref(),
            Self::SessionBound { caller_ingress, .. } => caller_ingress.as_ref(),
        }
    }
}

/// Strict WS-upgrade caller-identity extraction (spec §10.1.1/§10.13, the
/// `/ws` counterpart of the HTTP route extractor): builds the runtime-only
/// ingress from the same authenticated principal the upgrade used for its
/// display binding. Machine credentials (bot/owner principals), principals
/// without a trusted session window and logins already dead at the upgrade
/// moment yield `None` — the adapter never synthesizes an identity from
/// display data. Past the boundary the WS channel carries NO expiry
/// (operations decision, plan §15.97): the invariant-value window is a
/// deliberate "never", kept meaningful by the deeper short-lived layers.
pub fn ws_upgrade_caller_ingress(
    principal: &AuthPrincipal,
    now_ms: i64,
) -> Option<AuthenticatedHumanCallerIngress> {
    // Machine credentials/bot principals are never human callers.
    if principal.bot_uuid.is_some() || principal.owner_id.is_some() {
        return None;
    }
    let staff_no = principal.user_id.as_deref().filter(|id| !id.is_empty())?;
    // Upgrade-time anchor: the login window must be alive RIGHT NOW. Its
    // distance-to-expiry is not carried past this boundary (§15.97).
    let login_expires_at_ms = principal.human_expires_at_ms? as i64;
    if login_expires_at_ms <= now_ms {
        return None;
    }
    AuthenticatedHumanCallerIngress::new(
        bcs_service_api::application::principal::HumanActor {
            actor_id: format!("human_{staff_no}"),
            staff_no: staff_no.to_string(),
        },
        HumanCallerAuthSource::WsAuth,
        now_ms,
        i64::MAX,
        bcs_config::resolve_env_str(),
    )
    .ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW_MS: i64 = 1_780_000_000_000;

    fn human(staff_no: Option<&str>, expires: Option<u64>) -> AuthPrincipal {
        let mut principal = AuthPrincipal::default();
        principal.user_id = staff_no.map(str::to_string);
        principal.human_expires_at_ms = expires;
        principal
    }

    #[test]
    fn upgrades_extract_ingress_only_from_windowed_human_principals() {
        let ingress =
            ws_upgrade_caller_ingress(&human(Some("100001"), Some((NOW_MS + 60_000) as u64)), NOW_MS)
                .expect("windowed human principal yields an ingress");
        assert_eq!(ingress.actor().actor_id, "human_100001");
        assert_eq!(ingress.auth_source(), HumanCallerAuthSource::WsAuth);
        assert_eq!(ingress.authenticated_at_ms(), NOW_MS);
        // §15.97: the login window gates the upgrade only; the WS channel
        // carries no expiry afterwards — the invariant window is a
        // deliberate "never".
        assert_eq!(ingress.expires_at_ms(), i64::MAX);
        assert!(!ingress.env().is_empty());

        // Machine principals, display-only ids and dead-at-upgrade windows
        // never synthesize a caller identity.
        let mut bot = human(Some("100001"), Some((NOW_MS + 60_000) as u64));
        bot.bot_uuid = Some("bot-1".into());
        assert!(ws_upgrade_caller_ingress(&bot, NOW_MS).is_none());
        let mut owner = human(Some("100001"), Some((NOW_MS + 60_000) as u64));
        owner.owner_id = Some("owner-1".into());
        assert!(ws_upgrade_caller_ingress(&owner, NOW_MS).is_none());
        assert!(ws_upgrade_caller_ingress(&human(None, Some(NOW_MS as u64 + 1)), NOW_MS).is_none());
        assert!(ws_upgrade_caller_ingress(&human(Some("100001"), None), NOW_MS).is_none());
        assert!(ws_upgrade_caller_ingress(&human(Some(""), Some(NOW_MS as u64 + 1)), NOW_MS).is_none());
        assert!(ws_upgrade_caller_ingress(&human(Some("100001"), Some(NOW_MS as u64)), NOW_MS).is_none());
    }

    #[test]
    fn the_connection_identity_has_no_window_recheck() {
        // A 1-second login window still validates at upgrade, and the
        // carried identity has no expiry: any frame time reads it back.
        let ingress =
            ws_upgrade_caller_ingress(&human(Some("100001"), Some((NOW_MS + 1_000) as u64)), NOW_MS)
                .unwrap();
        let auth = WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".into()),
            caller_ingress: Some(ingress),
        };
        assert!(auth.caller_ingress().is_some_and(|ingress| !ingress.is_expired(NOW_MS + 600_000)));
        assert!(auth.caller_ingress().is_some_and(|ingress| !ingress.is_expired(NOW_MS + 31 * 24 * 3600 * 1000)));
        let anonymous = WorkbenchConnectionAuth::UserBound {
            actor_id: None,
            caller_ingress: None,
        };
        assert!(anonymous.caller_ingress().is_none());
    }
}