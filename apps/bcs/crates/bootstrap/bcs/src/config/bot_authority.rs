//! Trusted-App authority scope configuration
//! (bot-authority spec §3.1, plan Task 5).
//!
//! Explicit allow rules per trusted app:
//! `app_id + env + tenant + bot_ids + actions +
//! target_owner_actor_ids`. env/tenant/ids are
//! case-exact with NO wildcards; a missing tenant only
//! matches a rule with the explicit `tenant = "absent"`.
//! Configuration fails hard on unknown actions, wildcard
//! strings, duplicate rule keys (same
//! `app_id/env/tenant`), and field-shape errors. The
//! default empty rule set denies every trusted app
//! everything.

use serde::{Deserialize, Serialize};

use bcs_app_bot::authority_policy::{
    AuthorityAppScopeRule, AuthorityServicePolicy,
};

/// Configuration section `[bot_authority]`.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct BotAuthorityConfig {
    /// One allow rule per trusted app/env/tenant
    /// key. Defaults to none: every trusted app is
    /// denied (deny-everything default per spec §3.1).
    #[serde(default)]
    pub trusted_apps: Vec<TrustedAppScopeRuleConfig>,
}

/// One trusted-App allow rule.
///
/// `tenant` is REQUIRED and takes a byte-exact tenant
/// spelling, or the reserved value `"absent"` (which
/// matches ONLY callers without a tenant). Missing the
/// field is a load error, not a wildcard.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrustedAppScopeRuleConfig {
    /// The Gateway-verified App id.
    pub app_id: i64,
    /// Byte-exact environment this rule applies to; rules
    /// for other environments stay inert here.
    pub env: String,
    /// Byte-exact tenant, or the reserved `"absent"`.
    pub tenant: String,
    /// Bot ids this rule covers (case-exact). An empty
    /// set denies everything.
    #[serde(default)]
    pub bot_ids: Vec<String>,
    /// Allowed actions from exactly:
    /// `managers:sync | ownership:read |
    /// ownership:accept | ownership:reject`. An empty
    /// set denies everything; unknown spellings fail
    /// configuration loading.
    #[serde(default)]
    pub actions: Vec<String>,
    /// Human actor ids (`human_<user_id>`) the rule may
    /// read/decide transfers for. REQUIRED non-empty
    /// when the rule grants any
    /// `ownership:read|accept|reject`; REQUIRED empty
    /// for sync-only rules.
    #[serde(default)]
    pub target_owner_actor_ids: Vec<String>,
}

impl BotAuthorityConfig {
    /// Validate the section (spec §3.1):
    /// unknown actions, wildcard strings, duplicate
    /// rule keys, and shape errors are hard failures.
    pub fn validate(&self) -> Result<(), String> {
        let rules = self.rules()?;
        AuthorityServicePolicy::validate_rules(&rules)
            .map_err(|error| error.to_string())
    }

    /// Resolve the configured rules into the
    /// application's validated scope policy; called once
    /// at composition time on the instance's own
    /// environment. This method performs NO raw env
    /// reads: the env comes from the wiring caller.
    pub fn resolve(
        &self,
        env: &str,
    ) -> Result<AuthorityServicePolicy, String> {
        let rules = self.rules()?;
        AuthorityServicePolicy::new(env, rules)
            .map_err(|error| error.to_string())
    }

    fn rules(
        &self,
    ) -> Result<Vec<AuthorityAppScopeRule>, String> {
        let mut rules =
            Vec::with_capacity(self.trusted_apps.len());
        for (index, rule) in self.trusted_apps.iter().enumerate() {
            let resolved = AuthorityAppScopeRule::from_config_strings(
                rule.app_id,
                &rule.env,
                &rule.tenant,
                &rule.bot_ids,
                &rule.actions,
                &rule.target_owner_actor_ids,
            )
            .map_err(|error| {
                format!("trusted_apps[{index}]: {error}")
            })?;
            rules.push(resolved);
        }
        Ok(rules)
    }
}
