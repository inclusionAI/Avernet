//! Existing-only Bot profile writes and the participant-page avatar read
//! for `BotCore` (bot-avatar plan Task 4).
//!
//! The profile write protects Provider parent identity before delegating to
//! the store's composite existing-only contract. `avatar_urls` accepts a
//! whole participant result page — the core owns
//! deduplication, store-sized chunking and map merging, while visibility
//! rules (environment isolation, Human exclusion, tombstone display) remain
//! the repository read's contract. The query budget of one store batch read
//! per 256 distinct ids is asserted in `tests/bot_profile.rs`.

use super::*;

/// Maximum number of distinct ids submitted to one repository avatar batch
/// read. This mirrors the store's `AVATAR_BATCH_LIMIT` (the store rejects
/// larger deduplicated pages), so one core chunk is exactly one DB query.
const AVATAR_QUERY_CHUNK: usize = 256;

impl BotCore {
    /// Existing-only profile write: preserve Provider parent identity before
    /// the composite store write (capabilities replacement + avatar transition).
    pub(super) async fn update_profile_existing_impl(
        &self,
        bot_id: &str,
        mut update: bcs_service_api::types::bot_profile::ExistingProfileUpdate,
    ) -> ServiceResult<bcs_service_api::types::bot_profile::ProfileWrite> {
        update.capabilities = match self.protect_provider_identity(bot_id, update.capabilities).await {
            Ok(capabilities) => capabilities,
            Err(error @ ServiceError::Unauthorized(_)) => {
                // The shared guard rejects unavailable affiliated Bots. This
                // existing-only API classifies absent/deleted targets as Missing,
                // including deletion during identity validation. Strict reads
                // preserve storage failures; live targets retain the denial.
                if self.repo.try_get(bot_id).await?.is_none() {
                    return Ok(bcs_service_api::types::bot_profile::ProfileWrite::Missing);
                }
                return Err(error);
            }
            Err(error) => return Err(error),
        };
        self.repo.update_profile_existing(bot_id, update).await
    }

    /// Existing-only avatar write: delegate the store's avatar transition
    /// (`Keep` never reaches a storage SET clause).
    pub(super) async fn update_avatar_existing_impl(
        &self,
        bot_id: &str,
        avatar: bcs_service_api::types::bot_profile::AvatarChange,
    ) -> ServiceResult<bcs_service_api::types::bot_profile::ProfileWrite> {
        self.repo.update_avatar_existing(bot_id, avatar).await
    }

    /// Batched avatar URLs for a whole participant page: deduplicate the
    /// submitted ids, read them through the bounded repository batches and
    /// merge the per-batch maps. Only the page's own ids are submitted —
    /// there is no full-table read and no authorization here: environment
    /// isolation, Human exclusion and tombstone display come from the
    /// repository read, and its failures propagate.
    pub(super) async fn avatar_urls_impl(
        &self,
        bot_ids: &[String],
    ) -> ServiceResult<bcs_service_api::types::bot_profile::BotAvatarMap> {
        let unique = bot_ids.iter().cloned().collect::<std::collections::BTreeSet<_>>();
        let ids = unique.into_iter().collect::<Vec<_>>();
        let mut output = bcs_service_api::types::bot_profile::BotAvatarMap::new();
        for batch in ids.chunks(AVATAR_QUERY_CHUNK) {
            output.extend(self.repo.read_avatar_batch(batch).await?);
        }
        Ok(output)
    }
}
