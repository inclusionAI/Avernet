//! Batched owner projection behind `BotAuthorityRepoPort::ownerships_for`.
//!
//! Split out of `reads.rs` at the mechanical boundary the
//! `transfers.rs`/`transfers_support.rs` precedent set: the strict
//! read impl trait stays in `reads.rs`; this file carries ONE
//! inherent helper with the chunked SQL/mapping spine so the trait
//! file keeps its 1,000-line budget. Semantics and strictness are
//! identical to the single `ownership` read — corrupt owner state
//! remains a batch error, storage failures never become `None`.

use std::collections::HashMap;

use bcs_db_api::{DbStatement, DbValue};
use bcs_service_api::ServiceResult;
use bcs_service_api::types::bot_authority::{BotOwnershipView, human_actor_id};

use super::{DbBotAuthorityStore, owner_integrity_error};
use super::reads::chunked;

impl DbBotAuthorityStore {
    /// The full `ownerships_for` spine: same length and order as
    /// `bot_ids`; per-item `None` for missing/deleted/version-0 ids
    /// (legacy creator projection when the migration-window shim is
    /// enabled); integrity `Err` when an initialized bot has no
    /// or several approved owner edges. Bounded collection queries
    /// (≤ CHUNK ids per statement), zero statements on empty input.
    pub(super) async fn ownership_projection(
        &self,
        bot_ids: &[String],
    ) -> ServiceResult<Vec<Option<BotOwnershipView>>> {
        if bot_ids.is_empty() {
            return Ok(Vec::new());
        }
        let id_refs: Vec<&str> = bot_ids.iter().map(String::as_str).collect();

        // Chunked `(bot_id → ownership_version)`; absent rows are
        // missing/deleted bots and stay a per-item `None`.
        let mut states: HashMap<String, u64> = HashMap::new();
        let mut owners: HashMap<String, Vec<String>> = HashMap::new();
        for chunk in chunked(&id_refs) {
            for (bot_id, version) in self.bot_states_in(&chunk).await? {
                states.insert(bot_id, version);
            }
            for (bot_id, owner) in self.owner_actors_in(&chunk).await? {
                owners.entry(bot_id).or_default().push(owner);
            }
        }

        // Migration-window shim parity with `roles_for`: only
        // version-0 bots consult the legacy creator fact, in one
        // chunked read per chunk (never per-bot).
        let mut legacy_creators: HashMap<String, String> = HashMap::new();
        if self.legacy_created_by_fallback {
            let legacy_bots: Vec<_> = id_refs
                .iter()
                .copied()
                .filter(|bot| states.get(*bot) == Some(&0))
                .collect();
            for chunk in chunked(&legacy_bots) {
                for (bot_id, creator) in self.legacy_creators_in(&chunk).await? {
                    if let Some(creator) = creator {
                        legacy_creators.insert(bot_id, creator);
                    }
                }
            }
        }

        let mut results = Vec::with_capacity(bot_ids.len());
        for bot_id in bot_ids {
            let Some(version) = states.get(bot_id).copied() else {
                results.push(None); // missing / deleted bot projects no owner
                continue;
            };
            if version == 0 {
                results.push(legacy_creators.get(bot_id).map(|creator| BotOwnershipView {
                    bot_id: bot_id.clone(),
                    owner_user_id: creator.clone(),
                    owner_actor_id: human_actor_id(creator),
                }));
                continue;
            }
            match owners.get(bot_id).map_or(0, Vec::len) {
                0 => {
                    return Err(owner_integrity_error(format!(
                        "bot {bot_id} has no approved owner edge"
                    )));
                }
                1 => {
                    let owner = owners.get(bot_id).and_then(|v| v.first()).expect("one owner").clone();
                    results.push(Some(BotOwnershipView {
                        bot_id: bot_id.clone(),
                        owner_user_id: self.human_user_id(&owner)?,
                        owner_actor_id: owner,
                    }));
                }
                count => {
                    return Err(owner_integrity_error(format!(
                        "bot {bot_id} has {count} approved owner edges"
                    )));
                }
            }
        }
        Ok(results)
    }

    /// Approved owner `(to_id, from_id)` pairs for a chunk of bots.
    /// Duplicate owners of ONE bot surface as multiple rows and are
    /// the caller's integrity decision, same as `owner_counts_in`.
    async fn owner_actors_in(&self, bots: &[&str]) -> ServiceResult<Vec<(String, String)>> {
        let mut params = vec![self.env_param()];
        for bot in bots {
            params.push(DbValue::from(*bot));
        }
        let rows = self
            .query(
                "authority_owner_actors_chunk",
                DbStatement::with_params(
                    format!(
                        "SELECT to_id, from_id FROM edge_grants \
                         WHERE env = ? AND grant_kind = 'owner' AND grant_ref_id = 0 \
                           AND rules IS NULL AND status = 'approved' AND {}",
                        self.to_id_in(bots.len())
                    )
                    .as_str(),
                    params,
                ),
            )
            .await?;
        rows.iter()
            .map(|row| {
                Ok((
                    crate::required_string(row, "to_id")?,
                    crate::required_string(row, "from_id")?,
                ))
            })
            .collect()
    }
}
