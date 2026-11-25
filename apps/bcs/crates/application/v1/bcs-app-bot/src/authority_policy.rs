//! The trusted-App authority policy (spec §3.1 / §6.1).
//!
//! One bounded cell: an explicit allow-list of App scope rules
//! (`app + tenant + actions`) resolved from configuration at
//! startup. Rules bind to the BCS instance's own env at
//! composition time (config loading is already env-partitioned
//! via `bcs-config-{env}.toml`), so no rule carries a per-rule
//! env, a numeric app id, an explicit bot list, or a transfer
//! target set.
//!
//! Hard semantics (spec §3.1):
//! - app/tenant compare case-exactly; there are no wildcards.
//! - `TenantScope::Absent` matches ONLY a caller without a
//!   tenant; a tenant-ful caller matches only the exact tenant.
//! - Empty rule sets and empty `actions` deny everything.
//! - Duplicate rule keys (same `app + tenant`), unknown action
//!   spellings, wildcard strings, and field-shape errors fail
//!   configuration resolution (a hard startup error).
//! - A rule scopes to ALL bots on this BCS instance, including
//!   bots registered later (still bounded by the sync snapshot
//!   size cap and the full audit trail); transfer actions need
//!   no pre-declared target set — initiation stays Human-only
//!   and downstream state-machine guards apply.
//! - The `absent` tenant spelling is reserved.

use std::collections::BTreeSet;
use std::fmt;

use bcs_service_api::application::v1::bot_authority::
    ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN;
use bcs_service_api::application::v1::{ApplicationError, AuthenticatedCaller};
use bcs_service_api::types::bot_authority::AuthorityActor;

/// The reserved tenant spelling that matches ONLY callers
/// without a tenant (spec §3.1).
pub const ABSENT_TENANT_TEXT: &str = "absent";

/// One grantable trusted-App action. Only the five frozen
/// spellings parse; everything else is a configuration
/// error (spec §3.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum AuthorityAction {
    /// `managers:sync` — full manager-set replace.
    ManagersSync,
    /// `ownership:read` — transfer lookup.
    OwnershipRead,
    /// `ownership:accept` — transfer acceptance.
    OwnershipAccept,
    /// `ownership:reject` — transfer rejection.
    OwnershipReject,
    /// `ownership:sync` — trusted owner replace.
    OwnershipSync,
}

impl AuthorityAction {
    pub const ALL: [AuthorityAction; 5] = [
        AuthorityAction::ManagersSync,
        AuthorityAction::OwnershipRead,
        AuthorityAction::OwnershipAccept,
        AuthorityAction::OwnershipReject,
        AuthorityAction::OwnershipSync,
    ];

    /// The frozen configuration spelling (case-exact).
    pub fn as_str(&self) -> &'static str {
        match self {
            AuthorityAction::ManagersSync => "managers:sync",
            AuthorityAction::OwnershipRead => "ownership:read",
            AuthorityAction::OwnershipAccept => "ownership:accept",
            AuthorityAction::OwnershipReject => "ownership:reject",
            AuthorityAction::OwnershipSync => "ownership:sync",
        }
    }

    /// Strict parse of one configuration action spelling:
    /// `None` for anything else (unknown action strings fail
    /// configuration loading).
    pub fn parse(text: &str) -> Option<Self> {
        AuthorityAction::ALL.iter().copied().find(|action| {
            action.as_str() == text
        })
    }
}

/// Tenant scope of one rule: an exact tenant spelling or the
/// reserved `absent` marker for tenantless callers.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum TenantScope {
    Exact(String),
    Absent,
}

impl TenantScope {
    /// Strict construction from the configuration text.
    pub fn from_config_text(text: &str) -> Result<Self, AuthorityScopeConfigError> {
        check_plain_field("tenant", text)?;
        if text == ABSENT_TENANT_TEXT {
            return Ok(TenantScope::Absent);
        }
        Ok(TenantScope::Exact(text.to_string()))
    }

    /// Byte-exact tenant match; `Absent` matches ONLY the
    /// tenantless caller (spec §3.1).
    pub fn matches(&self, caller_tenant: Option<&str>) -> bool {
        match self {
            TenantScope::Exact(exact) => caller_tenant == Some(exact.as_str()),
            TenantScope::Absent => caller_tenant.is_none(),
        }
    }
}

/// One resolved allow rule (`app + tenant + actions`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityAppScopeRule {
    pub app: String,
    pub tenant: TenantScope,
    pub actions: BTreeSet<AuthorityAction>,
}

impl AuthorityAppScopeRule {
    /// Strict rule construction from configuration strings.
    /// Unknown action spellings, wildcard/blank strings, and
    /// duplicate actions are field-shape errors: configuration
    /// loading fails hard (spec §3.1).
    pub fn from_config_strings(
        app: &str,
        tenant: &str,
        actions: &[String],
    ) -> Result<Self, AuthorityScopeConfigError> {
        check_plain_field("app", app)?;
        let tenant = TenantScope::from_config_text(tenant)?;

        let mut parsed_actions = BTreeSet::new();
        for text in actions {
            let Some(action) = AuthorityAction::parse(text) else {
                return Err(AuthorityScopeConfigError::new(format!(
                    "bot_authority.trusted_apps: unknown action \
                     '{text}'; allowed actions are {}",
                    AuthorityAction::ALL
                        .iter()
                        .map(|action| action.as_str())
                        .collect::<Vec<_>>()
                        .join(", "),
                )));
            };
            if !parsed_actions.insert(action) {
                return Err(AuthorityScopeConfigError::new(format!(
                    "bot_authority.trusted_apps: duplicate action \
                     '{text}' in one rule"
                )));
            }
        }

        Ok(Self {
            app: app.to_string(),
            tenant,
            actions: parsed_actions,
        })
    }
}

/// Configuration-resolution failure. Bootstrap maps this to
/// a hard startup error (spec §3.1: configuration loading
/// fails; it never silently narrows rights).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityScopeConfigError {
    pub message: String,
}

impl AuthorityScopeConfigError {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for AuthorityScopeConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "bot authority app-scope config is invalid: {}", self.message)
    }
}

impl std::error::Error for AuthorityScopeConfigError {}

/// The resolved, immutable trusted-App scope policy.
#[derive(Debug, Clone)]
pub struct AuthorityServicePolicy {
    /// The env this BCS instance serves (diagnostics only).
    env: String,
    rules: Vec<AuthorityAppScopeRule>,
}

impl AuthorityServicePolicy {
    /// The shared rule-set checks (spec §3.1): one rule
    /// per `(app, tenant)` key without order-based merging.
    /// Env-independent, so configuration loading can run it
    /// before the composition root binds the instance env.
    pub fn validate_rules(
        rules: &[AuthorityAppScopeRule],
    ) -> Result<(), AuthorityScopeConfigError> {
        let mut keys: BTreeSet<(&str, TenantScope)> = BTreeSet::new();
        for rule in rules {
            let key = (rule.app.as_str(), rule.tenant.clone());
            if !keys.insert(key) {
                return Err(AuthorityScopeConfigError::new(format!(
                    "bot_authority.trusted_apps: duplicate rule \
                     key for app '{}' (at most one rule per \
                     app/tenant)",
                    rule.app,
                )));
            }
        }
        Ok(())
    }

    /// Resolve a policy from validated rules; one rule per
    /// `(app, tenant)` key without order-based
    /// merging (spec §3.1). Duplicate keys and blank envs are
    /// configuration errors.
    pub fn new(
        env: impl Into<String>,
        rules: Vec<AuthorityAppScopeRule>,
    ) -> Result<Self, AuthorityScopeConfigError> {
        let env: String = env.into();
        if env.trim().is_empty() {
            return Err(AuthorityScopeConfigError::new(
                "bot authority policy requires a non-empty bound env",
            ));
        }
        Self::validate_rules(&rules)?;
        Ok(Self { env, rules })
    }

    /// The bound env (diagnostics only).
    pub fn env(&self) -> &str {
        &self.env
    }

    /// The resolved rules (diagnostics only).
    pub fn rules(&self) -> &[AuthorityAppScopeRule] {
        &self.rules
    }

    /// Decide whether the verified caller is a trusted App with
    /// the scope for `action` on `bot_id`. Transfer actions no
    /// longer match a pre-declared target set (review ruling:
    /// target sets were deleted with the fields); bot_id stays
    /// for diagnostics only.
    ///
    /// This resolves the APP identity when the facade selects
    /// it: for `managers:sync` (spec §6.1 — sync 始终显式选择
    /// App 身份, even when the same credential also carries a
    /// Human identity) and for the App branch of the
    /// dual-admission routes (that branch is only reached when
    /// the caller carries NO Human identity, so a Human
    /// rejection never falls back to App escalation — the
    /// Human-first rule is enforced by the facade, not here).
    /// A credential with no App identity (plain Human, Bot,
    /// AccessKey) is always denied here.
    pub fn authorize(
        &self,
        caller: &AuthenticatedCaller,
        action: AuthorityAction,
        bot_id: &str,
    ) -> Result<AuthorityActor, ApplicationError> {
        let Some(app) = caller.app.as_ref() else {
            return Err(operator_forbidden(
                "authority actions require a verified Gateway \
                 App credential; Provider/Bot/AccessKey \
                 credentials stay outside this surface",
            ));
        };
        let bound_env = self.env.as_str();
        if self.matched_rule(
            &app.app_name,
            action,
            caller.tenant.as_deref(),
        ) {
            return Ok(AuthorityActor::Service {
                app_id: app.app_id,
            });
        }
        Err(operator_forbidden(&format!(
            "trusted app {} does not authorize the '{}' \
             action for bot '{bot_id}' on env '{bound_env}' \
             {tenant_clause}",
            app.app_name,
            action.as_str(),
            tenant_clause = match caller.tenant.as_deref() {
                Some(tenant) => format!("and tenant '{tenant}'"),
                None => "without a tenant".to_string(),
            },
        )))
    }

    fn matched_rule(
        &self,
        app_name: &str,
        action: AuthorityAction,
        caller_tenant: Option<&str>,
    ) -> bool {
        self.rules.iter().any(|rule| {
            rule.app == app_name
                && rule.tenant.matches(caller_tenant)
                && rule.actions.contains(&action)
        })
    }
}


fn operator_forbidden(detail: &str) -> ApplicationError {
    ApplicationError::forbidden_code(
        ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN,
        detail.to_string(),
    )
}

/// Reject blank, whitespace-padded, or wildcard-bearing
/// configuration strings (spec §3.1: no wildcards, field-shape
/// errors fail loading).
fn check_plain_field(field: &str, value: &str) -> Result<(), AuthorityScopeConfigError> {
    if value.is_empty()
        || value.trim() != value
        || value.contains('*')
        || value.contains('?')
        || value.contains('%')
    {
        return Err(AuthorityScopeConfigError::new(format!(
            "bot_authority.trusted_apps: {field} '{value}' is not a \
             plain exact value (no blanks, wildcards, or padding)"
        )));
    }
    Ok(())
}
