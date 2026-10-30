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

/// Why an upgrade-time caller-identity extraction refused a principal. The
/// reason rides into the upgrade logs verbatim (INFO on grant, WARN on
/// refusal) so a missing caller identity is always diagnosable from the
/// server log without re-running the handshake.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerIngressSkipReason {
    /// Machine credential principals (bot/owner) are never human callers.
    MachineCredential,
    /// The principal carries no usable user_id: display-only stays
    /// display-only — the adapter never synthesizes an identity.
    MissingUserId,
    /// Runtime-only material validation refused the envelope (practically
    /// unreachable; the constructor's own fail-closed backstop).
    InvalidMaterial,
}

impl CallerIngressSkipReason {
    /// Stable reason code for logs: grep `caller_identity_skip`.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::MachineCredential => "machine_credential",
            Self::MissingUserId => "missing_user_id",
            Self::InvalidMaterial => "invalid_material",
        }
    }
}

/// Strict WS-upgrade caller-identity extraction (spec §10.1.1/§10.13, the
/// `/ws` counterpart of the HTTP route extractor): builds the runtime-only
/// ingress from the same authenticated principal the upgrade used for its
/// display binding. A human principal with a non-empty user_id upgrades into
/// a full caller identity; machine credentials (bot/owner principals) and
/// identity-less principals are refused — never synthesized from display
/// data.
///
/// Deployment decision 2026-10-24 (operator choice): the upgrade boundary no
/// longer requires a live login window (`human_expires_at_ms`). Internal
/// office identity artifacts may carry no expirable evidence, and the caller
/// seam chose availability over the upgrade-time liveness proof. Login
/// liveness is owned by the authenticated session lifecycle: the ingress
/// carries NO expiry (plan §15.97 + the 2026-10-25 incident follow-up that
/// removed identity windows end to end), and the window value, when the
/// principal provides one, is logged for diagnostics only.
pub fn ws_upgrade_caller_ingress(
    principal: &AuthPrincipal,
    now_ms: i64,
) -> Result<AuthenticatedHumanCallerIngress, CallerIngressSkipReason> {
    // Machine credentials/bot principals are never human callers.
    if principal.bot_uuid.is_some() || principal.owner_id.is_some() {
        return Err(CallerIngressSkipReason::MachineCredential);
    }
    let staff_no = principal
        .user_id
        .as_deref()
        .filter(|id| !id.is_empty())
        .ok_or(CallerIngressSkipReason::MissingUserId)?;
    AuthenticatedHumanCallerIngress::new(
        bcs_service_api::application::principal::HumanActor {
            actor_id: format!("human_{staff_no}"),
            staff_no: staff_no.to_string(),
        },
        HumanCallerAuthSource::WsAuth,
        now_ms,
        bcs_config::resolve_env_str(),
    )
    .map_err(|_| CallerIngressSkipReason::InvalidMaterial)
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
    fn upgrades_extract_ingress_from_human_principals_window_or_not() {
        let ingress =
            ws_upgrade_caller_ingress(&human(Some("100001"), Some((NOW_MS + 60_000) as u64)), NOW_MS)
                .expect("human principal yields an ingress");
        assert_eq!(ingress.actor().actor_id, "human_100001");
        assert_eq!(ingress.auth_source(), HumanCallerAuthSource::WsAuth);
        assert_eq!(ingress.authenticated_at_ms(), NOW_MS);
        assert!(!ingress.env().is_empty());

        // Deployment decision 2026-10-24: a login window is no longer an
        // upgrade-keystone — window-less (e.g. internal identity artifacts
        // with no exp claim) and already-dead windows still upgrade. Deep
        // materials keep their own short-lived caps.
        for expires in [None, Some(NOW_MS as u64), Some((NOW_MS - 1) as u64)] {
            assert!(
                ws_upgrade_caller_ingress(&human(Some("100001"), expires), NOW_MS).is_ok(),
                "windowless/dead windows upgrade (expires={expires:?})"
            );
        }

        // Machine credentials and display-only ids never synthesize a caller
        // identity; refusals carry a stable reason for the upgrade logs.
        let mut bot = human(Some("100001"), Some((NOW_MS + 60_000) as u64));
        bot.bot_uuid = Some("bot-1".into());
        assert_eq!(
            ws_upgrade_caller_ingress(&bot, NOW_MS).unwrap_err(),
            CallerIngressSkipReason::MachineCredential
        );
        let mut owner = human(Some("100001"), Some((NOW_MS + 60_000) as u64));
        owner.owner_id = Some("owner-1".into());
        assert_eq!(
            ws_upgrade_caller_ingress(&owner, NOW_MS).unwrap_err(),
            CallerIngressSkipReason::MachineCredential
        );
        assert_eq!(
            ws_upgrade_caller_ingress(&human(None, Some(NOW_MS as u64 + 1)), NOW_MS).unwrap_err(),
            CallerIngressSkipReason::MissingUserId
        );
        assert_eq!(
            ws_upgrade_caller_ingress(&human(Some(""), Some(NOW_MS as u64 + 1)), NOW_MS).unwrap_err(),
            CallerIngressSkipReason::MissingUserId
        );
    }

    #[test]
    fn the_connection_identity_has_no_window_recheck() {
        // The carried identity has no expiry: any frame time reads it back.
        let ingress =
            ws_upgrade_caller_ingress(&human(Some("100001"), None), NOW_MS)
                .unwrap();
        let auth = WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".into()),
            caller_ingress: Some(ingress),
        };
        let _ = auth.caller_ingress();
        let anonymous = WorkbenchConnectionAuth::UserBound {
            actor_id: None,
            caller_ingress: None,
        };
        assert!(anonymous.caller_ingress().is_none());
    }
}