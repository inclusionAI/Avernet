//! Bot profile (capabilities + avatar) persistence for `MemoryBotRepo`
//! (bot-avatar plan Task 3).
//!
//! The avatar lives in the SAME persisted bot record (`<bot>/bot.json`) as a
//! sibling of the capability fields, never inside `BotCapabilities` and
//! never in a separate avatar cache. The persisted snapshot is
//! [`StoredBotProfile`]: it flattens the legacy record fields so files
//! written before the avatar field existed still load (missing field → no
//! avatar), and re-serializing a record preserves its stored avatar.
//!
//! Write ordering follows the repo's strict creation discipline: the
//! fallible record file write happens FIRST; the in-memory registry value
//! (capabilities, binding index, audit) publishes only afterwards, so a
//! failing durable write never replaces the live one. Tombstoned identities
//! refuse every write (SQL `is_deleted` parity) but their retained records
//! still serve tombstone display reads.

use std::collections::HashSet;
use std::io::ErrorKind;
use std::path::Path;
use std::time::Instant;

use serde::{Deserialize, Serialize};
use tokio::fs;
use tracing::info;

use bcs_service_api::types::bot_profile::{
    AvatarChange, BotAvatarMap, ExistingProfileUpdate, ProfileWrite,
};
use bcs_service_api::{BotCapabilities, BotRepoPort, ServiceError, ServiceResult};

use super::{MemoryBotRepo, PersistedCapabilities, unix_millis};

/// Maximum number of distinct ids one batched avatar read accepts (same
/// bound as the SQL store side).
const AVATAR_BATCH_LIMIT: usize = 256;

/// The persisted bot record: the legacy capability/identity fields plus the
/// avatar as a sibling field. Flattened so historical files without the
/// avatar field deserialize to `avatar_url: None`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct StoredBotProfile {
    #[serde(flatten)]
    pub(super) record: PersistedCapabilities,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) avatar_url: Option<String>,
}

/// Live registry projection used by the profile writes and reads.
/// Tombstone and file-backed identities have no snapshot.
struct MemoryBotSnapshot {
    capabilities: BotCapabilities,
    created_by: Option<String>,
    actor_kind: bcs_service_api::ActorKind,
}

impl MemoryBotRepo {
    /// Read the persisted record of one bot. A missing file is `None`;
    /// unreadable or malformed files, and records carrying another
    /// identity, are closed errors.
    pub(super) async fn read_stored_profile(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Option<StoredBotProfile>> {
        let path = self.bot_info_path(bot_id);
        let content = match fs::read_to_string(&path).await {
            Ok(content) => content,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        let stored: StoredBotProfile = serde_json::from_str(&content).map_err(|_| {
            ServiceError::InternalError("invalid persisted Bot record".into())
        })?;
        if stored.record.bot_id != bot_id {
            return Err(ServiceError::InternalError(
                "persisted Bot record identity mismatch".into(),
            ));
        }
        Ok(Some(stored))
    }

    /// Build a capability record with the existing token preserved and the
    /// given resolved creator; internal attributes come from the live
    /// registry entry when present (legacy local-save semantics).
    async fn capabilities_record(
        &self,
        bot_id: &str,
        caps: &BotCapabilities,
        created_by: Option<String>,
    ) -> ServiceResult<PersistedCapabilities> {
        let existing_token = self.load_token(bot_id).await;
        let (user_visibility, friend_ext, friend_check_in_strategy) = {
            let bots = self.bots.read().await;
            bots.get(bot_id)
                .map(|bot| {
                    (
                        bot.user_visibility,
                        bot.friend_ext.clone(),
                        bot.friend_check_in_strategy,
                    )
                })
                .unwrap_or_default()
        };
        Ok(PersistedCapabilities {
            bot_id: bot_id.to_string(),
            name: caps.name.clone(),
            summary: caps.summary.clone(),
            domains: caps.domains.clone(),
            skills: caps.skills.clone(),
            scopes: caps.scopes.clone(),
            binding_channels: caps.binding_channels.clone(),
            token: existing_token,
            registered_at: unix_millis(),
            hidden: false,
            created_by,
            visibility: if caps.visibility.is_empty() {
                None
            } else {
                Some(caps.visibility.clone())
            },
            agent_code: caps.agent_code.clone(),
            agent_token: caps.agent_token.clone(),
            user_visibility,
            friend_ext,
            friend_check_in_strategy,
        })
    }

    /// Fallible record write (same file layout as the ordinary save path).
    async fn write_stored_profile(
        &self,
        bot_id: &str,
        stored: &StoredBotProfile,
    ) -> ServiceResult<()> {
        let path = self.bot_info_path(bot_id);
        let dir = path.parent().ok_or_else(|| {
            ServiceError::InternalError(format!("Invalid path for bot: {bot_id}"))
        })?;
        fs::create_dir_all(dir).await?;
        let content = serde_json::to_string_pretty(stored)?;
        fs::write(&path, content).await?;
        info!(bot_id = %bot_id, path = ?path, "Saved bot profile to disk");
        Ok(())
    }

    /// The ordinary capabilities save: write the record with the given
    /// capabilities while preserving everything outside them — token,
    /// creator and the stored avatar (`AvatarChange::Keep` never rewrites
    /// the avatar field).
    pub(super) async fn save_profile_to_disk(
        &self,
        bot_id: &str,
        caps: &BotCapabilities,
        avatar: &AvatarChange,
    ) -> ServiceResult<()> {
        let created_by = {
            let bots = self.bots.read().await;
            bots.get(bot_id).and_then(|b| b.created_by.clone())
        };
        let avatar_url = match avatar {
            AvatarChange::Keep => self
                .read_stored_profile(bot_id)
                .await?
                .and_then(|stored| stored.avatar_url),
            AvatarChange::Clear => None,
            AvatarChange::Set(url) => Some(url.clone()),
        };
        let stored = StoredBotProfile {
            record: self.capabilities_record(bot_id, caps, created_by).await?,
            avatar_url,
        };
        self.write_stored_profile(bot_id, &stored).await
    }

    /// Existing-only profile write. Tombstoned or unknown targets are
    /// `Missing`; otherwise the record is written with the wholesale
    /// capabilities and the avatar transition, and only THEN does the live
    /// registry value (capabilities, binding index, audit) publish.
    pub(super) async fn update_profile_existing_impl(
        &self,
        bot_id: &str,
        update: ExistingProfileUpdate,
    ) -> ServiceResult<ProfileWrite> {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return Ok(ProfileWrite::Missing);
        }
        let snapshot = self.memory_bot_snapshot(bot_id).await;
        let stored_existing = self.read_stored_profile(bot_id).await?;
        if snapshot.is_none() && stored_existing.is_none() {
            return Ok(ProfileWrite::Missing);
        }

        // Owner preservation: the live registry value wins; a file-backed
        // identity without a live creator keeps its stored creator.
        let created_by = snapshot
            .as_ref()
            .and_then(|snapshot| snapshot.created_by.clone())
            .or_else(|| {
                stored_existing
                    .as_ref()
                    .and_then(|stored| stored.record.created_by.clone())
            });
        // `Keep` resolves against the current record, so a capabilities
        // replacement never clears the stored avatar.
        let avatar_url = match &update.avatar {
            AvatarChange::Keep => stored_existing
                .as_ref()
                .and_then(|stored| stored.avatar_url.clone()),
            AvatarChange::Clear => None,
            AvatarChange::Set(url) => Some(url.clone()),
        };
        let stored = StoredBotProfile {
            record: self
                .capabilities_record(bot_id, &update.capabilities, created_by)
                .await?,
            avatar_url,
        };

        // 先完成可失败的文件写入，再发布内存值。
        self.write_stored_profile(bot_id, &stored).await?;

        self.sync_binding_channel_index(bot_id, &update.capabilities)
            .await;
        {
            let mut bots = self.bots.write().await;
            if let Some(existing) = bots.get_mut(bot_id) {
                existing.last_heartbeat = Instant::now();
                existing.capabilities = update.capabilities.clone();
            }
        }
        let now = unix_millis();
        let mut audit = self.control_plane_audit.write().await;
        let entry = audit.entry(bot_id.to_string()).or_insert((now, now));
        entry.1 = now;
        info!(bot_id = %bot_id, "update_profile_existing (memory): profile published");
        Ok(ProfileWrite::Updated)
    }

    /// Existing-only avatar write: one transition of the record's avatar
    /// field. `Keep` only confirms the live target and writes nothing. The
    /// avatar never mirrors into the registry entry, so the record write is
    /// the publication itself.
    pub(super) async fn update_avatar_existing_impl(
        &self,
        bot_id: &str,
        avatar: AvatarChange,
    ) -> ServiceResult<ProfileWrite> {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return Ok(ProfileWrite::Missing);
        }
        let snapshot = self.memory_bot_snapshot(bot_id).await;
        let stored_existing = self.read_stored_profile(bot_id).await?;
        if snapshot.is_none() && stored_existing.is_none() {
            return Ok(ProfileWrite::Missing);
        }
        let avatar_url = match avatar {
            AvatarChange::Keep => return Ok(ProfileWrite::Updated),
            AvatarChange::Clear => None,
            AvatarChange::Set(url) => Some(url),
        };
        // Live registry entry without a record yet: persist it with the
        // same assembly as the ordinary save, carrying the avatar.
        let mut stored = match (stored_existing, snapshot) {
            (Some(stored), _) => stored,
            (None, Some(snapshot)) => StoredBotProfile {
                record: self
                    .capabilities_record(bot_id, &snapshot.capabilities, snapshot.created_by)
                    .await?,
                avatar_url: None,
            },
            (None, None) => return Ok(ProfileWrite::Missing),
        };
        stored.avatar_url = avatar_url;
        self.write_stored_profile(bot_id, &stored).await?;
        Ok(ProfileWrite::Updated)
    }

    /// Batched avatar read. Live registry identities and file-backed
    /// records (including tombstones — display reads retain them) each
    /// contribute their stored avatar; Human registry actors are excluded;
    /// unknown identities are absent from the map. Read failures of an
    /// existing record are errors, never a silent "no avatar".
    pub(super) async fn read_avatar_batch_impl(
        &self,
        bot_ids: &[String],
    ) -> ServiceResult<BotAvatarMap> {
        let mut distinct: Vec<String> = Vec::with_capacity(bot_ids.len());
        let mut seen: HashSet<&str> = HashSet::with_capacity(bot_ids.len());
        for bot_id in bot_ids {
            if seen.insert(bot_id.as_str()) {
                distinct.push(bot_id.clone());
            }
        }
        if distinct.is_empty() {
            return Ok(BotAvatarMap::new());
        }
        if distinct.len() > AVATAR_BATCH_LIMIT {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "bot avatar batch read accepts at most {AVATAR_BATCH_LIMIT} distinct bot ids"
                ),
                request_id: None,
            });
        }
        let mut avatars = BotAvatarMap::with_capacity(distinct.len());
        for bot_id in &distinct {
            let snapshot = self.memory_bot_snapshot(bot_id).await;
            let registry_bot = snapshot
                .as_ref()
                .is_some_and(|snapshot| snapshot.actor_kind != bcs_service_api::ActorKind::Human);
            let stored = self.read_stored_profile(bot_id).await?;
            match (registry_bot, stored) {
                (true, stored) => {
                    avatars.insert(bot_id.clone(), stored.and_then(|stored| stored.avatar_url));
                }
                (false, Some(stored)) => {
                    avatars.insert(bot_id.clone(), stored.avatar_url);
                }
                (false, None) => {}
            }
        }
        Ok(avatars)
    }

    async fn memory_bot_snapshot(&self, bot_id: &str) -> Option<MemoryBotSnapshot> {
        let bots = self.bots.read().await;
        bots.get(bot_id).map(|bot| MemoryBotSnapshot {
            capabilities: bot.capabilities.clone(),
            created_by: bot.created_by.clone(),
            actor_kind: bot.actor_kind.clone(),
        })
    }
}

/// Read only the stored avatar from a record file, tolerating absent or
/// unreadable files: avatar preservation must never block an ordinary
/// attribute write that today also tolerates unreadable records.
pub(super) async fn stored_avatar_at(path: &Path) -> Option<String> {
    let content = fs::read_to_string(path).await.ok()?;
    serde_json::from_str::<StoredBotProfile>(&content)
        .ok()
        .and_then(|stored| stored.avatar_url)
}
