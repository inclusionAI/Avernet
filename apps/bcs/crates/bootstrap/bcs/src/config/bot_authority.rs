//! Trusted-App authority scope configuration
//! (bot-authority spec §3.1, plan Task 5).
//!
//! Explicit allow rules per trusted app:
//! `app + tenant + actions`. Rules bind to the BCS instance's
//! own env at composition time — config loading is already
//! env-partitioned (`SERVER_ENV` selects `bcs-config-{env}.toml`),
//! so no rule carries a duplicate per-rule `env`. app/tenant
//! compare case-exactly with NO wildcards; a missing tenant only
//! matches a rule with the explicit `tenant = "absent"`.
//! Configuration fails hard on unknown actions, wildcard strings,
//! duplicate rule keys (same `app + tenant`), and field-shape
//! errors; `deny_unknown_fields` rejects any legacy field
//! (`env`/`app_id`/`bot_ids`/`target_owner_actor_ids`) loudly
//! instead of half-applying it. The default empty rule set
//! denies every trusted app everything.

use serde::{Deserialize, Serialize};

use bcs_app_bot::authority_policy::{
    AuthorityAppScopeRule, AuthorityServicePolicy,
};

/// Configuration section `[bot_authority]`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BotAuthorityConfig {
    /// One allow rule per trusted app/tenant key.
    /// Defaults to none: every trusted app is denied
    /// (deny-everything default per spec §3.1).
    #[serde(default)]
    pub trusted_apps: Vec<TrustedAppScopeRuleConfig>,
    /// Legacy `created_by` read fallback for the ownership
    /// migration window: a version-0 Bot answers strict role
    /// reads with the legacy created_by semantics (shadowed
    /// arbiter of LAST resort after the approved-edge and
    /// ownership-version arms), so an environment can ship the
    /// authority code BEFORE its data is backfilled. Absent
    /// field defaults to ON — flip it off to restore strict
    /// reads; the whole shim is sunset after migration.
    #[serde(default = "legacy_created_by_fallback_default")]
    pub legacy_created_by_fallback: bool,
}

fn legacy_created_by_fallback_default() -> bool {
    true
}

impl Default for BotAuthorityConfig {
    fn default() -> Self {
        Self {
            trusted_apps: Vec::new(),
            legacy_created_by_fallback: true,
        }
    }
}

/// One trusted-App allow rule.
///
/// `tenant` is REQUIRED and takes a byte-exact tenant
/// spelling, or the reserved value `"absent"` (which
/// matches ONLY callers without a tenant). Missing the
/// field is a load error, not a wildcard.
///
/// A rule scopes to ALL bots on this BCS instance,
/// including bots registered later (still bounded by the
/// sync snapshot size cap and the full audit trail).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrustedAppScopeRuleConfig {
    /// Byte-exact match on the Gateway-verified App name
    /// (the wire principal's `app_name`).
    pub app: String,
    /// Byte-exact tenant, or the reserved `"absent"`.
    pub tenant: String,
    /// Allowed actions from exactly:
    /// `managers:sync | ownership:read | ownership:accept |
    /// ownership:reject | ownership:sync`. An empty
    /// set denies everything; unknown spellings fail
    /// configuration loading.
    #[serde(default)]
    pub actions: Vec<String>,
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

    fn rules(&self) -> Result<Vec<AuthorityAppScopeRule>, String> {
        let mut rules =
            Vec::with_capacity(self.trusted_apps.len());
        for (index, rule) in self.trusted_apps.iter().enumerate() {
            let resolved = AuthorityAppScopeRule::from_config_strings(
                &rule.app,
                &rule.tenant,
                &rule.actions,
            )
            .map_err(|error| {
                format!("trusted_apps[{index}]: {error}")
            })?;
            rules.push(resolved);
        }
        Ok(rules)
    }
}
