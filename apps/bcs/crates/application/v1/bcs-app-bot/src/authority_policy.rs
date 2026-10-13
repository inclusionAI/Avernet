//! The trusted-App authority policy (spec §3.1 / §6.1).
//!
//! One bounded cell: an explicit allow-list of App scope
//! rules (`app_id + env + tenant + bot_ids + actions +
//! target_owner_actor_ids`) resolved from configuration at
//! startup. The policy never reads raw configuration or
//! environment; the composition root injects the resolved
//! rules and the bound env.
//!
//! Hard semantics (spec §3.1):
//! - env/tenant/ids compare case-exactly; there are no
//!   wildcards.
//! - `TenantScope::Absent` matches ONLY a caller without a
//!   tenant; a tenant-ful caller matches only the exact
//!   tenant.
//! - Empty rule sets, empty `bot_ids` and empty `actions`
//!   deny everything.
//! - Rules granting transfer read/accept/reject must carry a
//!   non-empty target set; sync-only rules must keep it empty
//!   (sync ignores the target collection).
//! - Unknown actions, wildcard strings, duplicate rule keys
//!   (same `app_id + env + tenant`) and field-shape errors
//!   fail configuration resolution (a hard startup error).
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

/// One grantable trusted-App action. Only the four frozen
/// spellings parse; everything else is a configuration
/// error (spec §3.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum AuthorityAction {
    /// `managers:sync` — full manager-set replace.
    ManagersSync,
    /// `ownership:read` — transfer lookup for a scoped target.
    OwnershipRead,
    /// `ownership:accept` — transfer acceptance for a scoped
    /// target.
    OwnershipAccept,
    /// `ownership:reject` — transfer rejection for a scoped
    /// target.
    OwnershipReject,
}

impl AuthorityAction {
    pub const ALL: [AuthorityAction; 4] = [
        AuthorityAction::ManagersSync,
        AuthorityAction::OwnershipRead,
        AuthorityAction::OwnershipAccept,
        AuthorityAction::OwnershipReject,
    ];

    /// The frozen configuration spelling (case-exact).
    pub fn as_str(&self) -> &'static str {
        match self {
            AuthorityAction::ManagersSync => "managers:sync",
            AuthorityAction::OwnershipRead => "ownership:read",
            AuthorityAction::OwnershipAccept => "ownership:accept",
            AuthorityAction::OwnershipReject => "ownership:reject",
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

    /// Whether the action addresses one transfer target and
    /// therefore requires the rule's target set.
    pub fn requires_target(self) -> bool {
        matches!(
            self,
            AuthorityAction::OwnershipRead
                | AuthorityAction::OwnershipAccept
                | AuthorityAction::OwnershipReject
        )
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

/// One resolved allow rule. Sets are ordered for stable
/// diagnostics; matching is membership only.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityAppScopeRule {
    pub app_id: i64,
    pub env: String,
    pub tenant: TenantScope,
    pub bot_ids: BTreeSet<String>,
    pub actions: BTreeSet<AuthorityAction>,
    pub target_owner_actor_ids: BTreeSet<String>,
}

impl AuthorityAppScopeRule {
    /// Strict rule construction from configuration strings.
    /// Unknown action spellings, wildcard/blank strings,
    /// duplicate entries, and the sync/target shape
    /// combinations are field-shape errors: configuration
    /// loading fails hard (spec §3.1).
    pub fn from_config_strings(
        app_id: i64,
        env: &str,
        tenant: &str,
        bot_ids: &[String],
        actions: &[String],
        target_owner_actor_ids: &[String],
    ) -> Result<Self, AuthorityScopeConfigError> {
        check_plain_field("env", env)?;
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

        let bot_ids = plain_string_set("bot_ids", bot_ids)?;
        let target_owner_actor_ids =
            plain_string_set("target_owner_actor_ids", target_owner_actor_ids)?;

        // Target-shape combination (spec §3.1): rules containing
        // transfer read/accept/reject require a non-empty target
        // set; sync-only rules keep it empty (sync ignores the
        // target collection).
        let transfers = parsed_actions
            .iter()
            .any(|action| action.requires_target());
        if transfers {
            if target_owner_actor_ids.is_empty() {
                return Err(AuthorityScopeConfigError::new(
                    "bot_authority.trusted_apps: a rule granting \
                     ownership:read/accept/reject needs a non-empty \
                     target_owner_actor_ids set",
                ));
            }
        } else if !target_owner_actor_ids.is_empty() {
            return Err(AuthorityScopeConfigError::new(
                "bot_authority.trusted_apps: a sync-only rule must keep \
                 target_owner_actor_ids empty",
            ));
        }

        Ok(Self {
            app_id,
            env: env.to_string(),
            tenant,
            bot_ids,
            actions: parsed_actions,
            target_owner_actor_ids,
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
    /// The env this BCS instance serves. Rules granted for
    /// other environments stay inert.
    env: String,
    rules: Vec<AuthorityAppScopeRule>,
}

impl AuthorityServicePolicy {
    /// The shared rule-set checks (spec §3.1): one rule
    /// per `app_id/env/tenant` key without order-based
    /// merging; no blank ids. Env-independent, so
    /// configuration loading can run it before the
    /// composition root binds the instance env.
    pub fn validate_rules(
        rules: &[AuthorityAppScopeRule],
    ) -> Result<(), AuthorityScopeConfigError> {
        let mut keys: BTreeSet<(i64, String, TenantScope)> =
            BTreeSet::new();
        for rule in rules {
            if rule.bot_ids.iter().any(String::is_empty) {
                return Err(AuthorityScopeConfigError::new(
                    "bot_authority.trusted_apps: bot ids \
                     must not be blank",
                ));
            }
            let key = (
                rule.app_id,
                rule.env.clone(),
                rule.tenant.clone(),
            );
            if !keys.insert(key) {
                return Err(AuthorityScopeConfigError::new(format!(
                    "bot_authority.trusted_apps: duplicate rule \
                     key for app {} env '{}' (at most one rule \
                     per app/env/tenant)",
                    rule.app_id, rule.env,
                )));
            }
        }
        Ok(())
    }

    /// Resolve a policy from validated rules; one rule per
    /// `(app_id, env, tenant)` key without order-based
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
    /// the scope for `action` on `bot_id` — and, for transfer
    /// actions, on `target_owner_actor_id`.
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
        target_owner_actor_id: Option<&str>,
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
            app.app_id,
            bot_id,
            target_owner_actor_id,
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
            app.app_id,
            action.as_str(),
            tenant_clause = match caller.tenant.as_deref() {
                Some(tenant) => format!("and tenant '{tenant}'"),
                None => "without a tenant".to_string(),
            },
        )))
    }

    fn matched_rule(
        &self,
        app_id: i64,
        bot_id: &str,
        target_owner_actor_id: Option<&str>,
        action: AuthorityAction,
        caller_tenant: Option<&str>,
    ) -> bool {
        self.rules.iter().any(|rule| {
            // A rule bound to another env stays inert.
            rule.app_id == app_id
                && rule.env == self.env
                && rule.tenant.matches(caller_tenant)
                && rule.bot_ids.contains(bot_id)
                && rule.actions.contains(&action)
                && match target_owner_actor_id {
                    // sync ignores the target collection;
                    // transfer actions must match it.
                    Some(target) if action.requires_target() => rule
                        .target_owner_actor_ids
                        .contains(target),
                    _ => !action.requires_target(),
                }
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

fn plain_string_set(
    field: &str,
    values: &[String],
) -> Result<BTreeSet<String>, AuthorityScopeConfigError> {
    let mut set = BTreeSet::new();
    for value in values {
        check_plain_field(field, value)?;
        if !set.insert(value.clone()) {
            return Err(AuthorityScopeConfigError::new(format!(
                "bot_authority.trusted_apps: duplicate entry '{value}' \
                 in {field}"
            )));
        }
    }
    Ok(set)
}
