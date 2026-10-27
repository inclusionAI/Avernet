//! Shared runtime PermissionProfile state on the same
//! [`MemoryBotRepo`] Arc (bot-authority plan 4.3a).
//!
//! The one `MemoryBotRepo` carries profile records, the
//! `(env, bot_id)` active-default index and a u64 ID
//! allocator (starting at 1). The wildcard/revision/
//! digest rules MIRROR the SQL store's
//! `ensure_default_profile` (D12 rule 2: an existing
//! default is never overwritten or re-revisioned).
//!
//! Atomicity: the trusted-registration composite stages
//! the profile inside a CLONE of this state and swaps the
//! clone inside its single write-guard span
//! (Task-2/3 pattern) — a failed registration publishes
//! neither a profile nor an allocator update, and the
//! profile port is never called under a state lock.

use std::collections::{BTreeMap, HashMap};

use async_trait::async_trait;
use bcs_domain::edge_permission::{PermissionProfile, ProfileStatus};
use bcs_service_api::port::repo::PermissionProfileRepoPort;
use bcs_service_api::ServiceResult;
use tokio::sync::RwLock;

use bcs_config::resolve_env_str as resolve_env;

use super::MemoryBotRepo;
use crate::ownership as crate_ownership;

/// Wildcard-allow rules of the seeded default profile —
/// the same bytes the SQL store seeds (`D12 rule 2`
/// parity).
pub(crate) const WILDCARD_ALLOW: &str =
    bcs_edge_permission_store::lifecycle::TRUSTED_DEFAULT_RULES;

/// One shared profile state bucket. `next_id` starts at 1
/// and grows once per committed record.
#[derive(Debug, Clone)]
pub struct MemoryProfileState {
    records: BTreeMap<u64, PermissionProfile>,
    /// `(env, bot_id)` → profile id of the active default.
    active_default: HashMap<(String, String), u64>,
    next_id: u64,
}

impl Default for MemoryProfileState {
    fn default() -> Self {
        Self {
            records: BTreeMap::new(),
            active_default: HashMap::new(),
            next_id: 1,
        }
    }
}

impl MemoryProfileState {
    /// FIXTURE surface: point the active-default index at
    /// a missing record so the composite's staged profile
    /// step deterministically fails on a corrupt state.
    pub fn corrupt_active_default(
        &mut self,
        env: &str,
        bot_id: &str,
        fake_id: u64,
    ) {
        self.active_default
            .insert((env.to_string(), bot_id.to_string()), fake_id);
    }

    /// FIXTURE surface: clear corrupt index entries.
    pub fn repair(&mut self) {
        self.active_default.retain(|_key, id| {
            self.records.contains_key(id)
        });
    }

    /// FIXTURE surface: number of records for one
    /// (env, bot).
    pub fn records_for_bot(&self, env: &str, bot_id: &str) -> usize {
        self.records
            .values()
            .filter(|record| {
                record.env == env && record.bot_id == bot_id
            })
            .count()
    }

    /// FIXTURE surface: total record count.
    pub fn record_count(&self) -> usize {
        self.records.len()
    }

    /// FIXTURE surface: next allocated id (unchanged by a
    /// failed registration).
    pub fn next_id_for_check(&self) -> u64 {
        self.next_id
    }

    /// The active default profile id, if any.
    fn active_default_id(&self, bot_id: &str, env: &str) -> Option<u64> {
        self.active_default
            .get(&(env.to_string(), bot_id.to_string()))
            .copied()
    }

    /// The default profile record; `Err` when the index
    /// references a missing record (corrupt state — the
    /// composite treats that as a staging failure and
    /// commits nothing).
    fn default_record(
        &self,
        bot_id: &str,
        env: &str,
    ) -> Option<PermissionProfile> {
        let id = self.active_default_id(bot_id, env)?;
        self.records.get(&id).cloned()
    }
}

impl MemoryBotRepo {
    /// The shared atomic profile-state boundary. Contract
    /// fixtures drive the SAME fields the production
    /// composites and the runtime port use; this accessor
    /// is the fixture surface, not an application API.
    pub fn profile_state(&self) -> &RwLock<MemoryProfileState> {
        &self.profiles
    }
}

/// Stage (temporary) default-profile insertion on a CLONE of
/// the state — used by the registration composite inside its
/// single write-guard span. Reuses an existing default (D12
/// rule 2), allocates from the same allocator otherwise, and
/// fails on a corrupt index without publishing anything.
pub(super) fn stage_default_profile(
    staged: &mut MemoryProfileState,
    bot_id: &str,
    env: &str,
) -> ServiceResult<()> {
    if let Some(id) = staged.active_default_id(bot_id, env) {
        if staged.records.contains_key(&id) {
            return Ok(()); // reuse; never overwrite
        }
        return Err(crate_ownership::initialization_invalid_public(
            "profile state corrupted: active-default index \
             references a missing record",
        ));
    }
    let id = staged.next_id;
    staged
        .records
        .insert(id, default_profile_record(bot_id, env, id));
    staged
        .active_default
        .insert((env.to_string(), bot_id.to_string()), id);
    staged.next_id = id + 1;
    Ok(())
}

fn default_profile_record(
    bot_id: &str,
    env: &str,
    id: u64,
) -> PermissionProfile {
    PermissionProfile {
        permission_profile_id: id,
        bot_id: bot_id.to_string(),
        env: env.to_string(),
        name: bcs_edge_permission_store::lifecycle::TRUSTED_DEFAULT_PROFILE_NAME
            .to_string(),
        description: None,
        rules_template: serde_json::from_str(WILDCARD_ALLOW)
            .expect("wildcard rules JSON is fixed"),
        revision: 1,
        digest: bcs_edge_permission_store::lifecycle::default_rules_digest(),
        is_default: true,
        status: ProfileStatus::Active,
        created_by: "system".to_string(),
        updated_by: None,
    }
}

#[async_trait]
impl PermissionProfileRepoPort for MemoryBotRepo {
    /// Idempotent: a committed default is reused with its
    /// original id, revision, digest and rules (D12 rule 2).
    async fn ensure_default_profile(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<u64> {
        let mut state = self.profiles.write().await;
        if let Some(existing) = state.default_record(bot_id, env) {
            return Ok(existing.permission_profile_id);
        }
        let id = state.next_id;
        state.records.insert(id, default_profile_record(bot_id, env, id));
        state
            .active_default
            .insert((env.to_string(), bot_id.to_string()), id);
        state.next_id = id + 1;
        Ok(id)
    }

    async fn get_active_default(
        &self,
        bot_id: &str,
        env: &str,
    ) -> Option<PermissionProfile> {
        let state = self.profiles.read().await;
        state.default_record(bot_id, env)
    }

    /// Bump rules/revision/digest in the SAME state; the
    /// profile id never changes (D12 rule 2).
    async fn upsert_revision(
        &self,
        profile: PermissionProfile,
    ) -> ServiceResult<()> {
        let mut state = self.profiles.write().await;
        let Some(existing) = state
            .records
            .get_mut(&profile.permission_profile_id)
        else {
            return Err(bcs_service_api::ServiceError::InvalidOperation {
                message: format!(
                    "unknown permission profile id {}",
                    profile.permission_profile_id
                ),
                request_id: None,
            });
        };
        existing.rules_template = profile.rules_template;
        existing.revision = profile.revision;
        existing.digest = profile.digest;
        existing.updated_by = profile.updated_by;
        Ok(())
    }
}

/// Runtime-consumable default read without the port trait
/// machinery (single-record projection used by tests).
#[allow(dead_code)]
pub(crate) async fn active_default(
    repo: &MemoryBotRepo,
    bot_id: &str,
) -> Option<PermissionProfile> {
    let env = resolve_env();
    repo.get_active_default(bot_id, &env).await
}
