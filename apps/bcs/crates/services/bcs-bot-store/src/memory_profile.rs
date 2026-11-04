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
use std::io::Write;
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

    /// Atomically replace a complete record. Callers hold `record_write_lock`
    /// from before reading their snapshot through memory publication.
    pub(super) async fn write_stored_profile(
        &self,
        bot_id: &str,
        stored: &StoredBotProfile,
    ) -> ServiceResult<()> {
        let path = self.bot_info_path(bot_id);
        let dir = path.parent().ok_or_else(|| {
            ServiceError::InternalError(format!("Invalid path for bot: {bot_id}"))
        })?.to_path_buf();
        let content = serde_json::to_vec_pretty(stored)?;
        // The worker prepares only a private file. If the caller is cancelled,
        // dropping its result cleans up the temporary file without a late write
        // to bot.json after the caller has released its serialization guard.
        let pending = tokio::task::spawn_blocking(move || -> std::io::Result<_> {
            std::fs::create_dir_all(&dir)?;
            let mut pending = tempfile::NamedTempFile::new_in(dir)?;
            pending.write_all(&content)?;
            pending.as_file().sync_all()?;
            Ok(pending)
        }).await.map_err(|_| ServiceError::InternalError(
            "Bot profile persistence task failed".into(),
        ))??;
        // Publish synchronously while the calling future holds the guard;
        // readers see either complete old JSON or complete new JSON.
        pending.persist(&path).map_err(|error| error.error)?;
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
    /// profile fields and the avatar transition, and only THEN does the
    /// live registry value (capabilities, binding index, audit) publish.
    /// The runtime registry identity (`agent_code`/`agent_token`) is
    /// registry-owned, never part of a profile write: the stored record and
    /// the in-memory publication both keep its current values — replacing
    /// them with an identity-less capabilities snapshot would silently skip
    /// the Bot's outbound interceptor chain (PR-3488 review).
    pub(super) async fn update_profile_existing_impl(
        &self,
        bot_id: &str,
        update: ExistingProfileUpdate,
    ) -> ServiceResult<ProfileWrite> {
        let _write_guard = self.record_write_lock.lock().await;
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
        // Identity preservation: same precedence as the owner — the live
        // registry value wins, the stored record is the fallback for a
        // file-backed identity without a live entry.
        let agent_code = snapshot
            .as_ref()
            .and_then(|snapshot| snapshot.capabilities.agent_code.clone())
            .or_else(|| {
                stored_existing
                    .as_ref()
                    .and_then(|stored| stored.record.agent_code.clone())
            });
        let agent_token = snapshot
            .as_ref()
            .and_then(|snapshot| snapshot.capabilities.agent_token.clone())
            .or_else(|| {
                stored_existing
                    .as_ref()
                    .and_then(|stored| stored.record.agent_token.clone())
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
        let mut record = self
            .capabilities_record(bot_id, &update.capabilities, created_by)
            .await?;
        record.agent_code = agent_code;
        record.agent_token = agent_token;
        let stored = StoredBotProfile { record, avatar_url };

        // 先完成可失败的文件写入，再发布内存值。
        self.write_stored_profile(bot_id, &stored).await?;

        self.sync_binding_channel_index(bot_id, &update.capabilities)
            .await;
        {
            let mut bots = self.bots.write().await;
            if let Some(existing) = bots.get_mut(bot_id) {
                existing.last_heartbeat = Instant::now();
                // Profile fields publish wholesale like the durable record;
                // the runtime identity keeps its current values.
                existing.capabilities.name = update.capabilities.name.clone();
                existing.capabilities.summary = update.capabilities.summary.clone();
                existing.capabilities.domains = update.capabilities.domains.clone();
                existing.capabilities.skills = update.capabilities.skills.clone();
                existing.capabilities.scopes = update.capabilities.scopes.clone();
                existing.capabilities.binding_channels =
                    update.capabilities.binding_channels.clone();
                existing.capabilities.visibility = update.capabilities.visibility.clone();
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
        let _write_guard = self.record_write_lock.lock().await;
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
            // A Human may have a record written through ordinary profile/token
            // APIs. Exclude it before opening that file. After restart/deletion,
            // match load_agent_bots' reserved human_ namespace fallback because
            // legacy files do not carry actor_kind; live registry kind wins.
            let is_human = snapshot.as_ref().map_or_else(
                || bot_id.starts_with("human_"),
                |snapshot| snapshot.actor_kind == bcs_service_api::ActorKind::Human,
            );
            if is_human {
                continue;
            }
            let registry_bot = snapshot.is_some();
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cancelled_preparation_never_publishes_a_late_record() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bot/bot.json");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let original = br#"{"bot_id":"bot","name":"Before","registered_at":1}"#;
        std::fs::write(&path, original).unwrap();
        let mut stored: StoredBotProfile = serde_json::from_slice(original).unwrap();
        stored.avatar_url = Some("https://example.test/cancelled.png".into());
        let repo = MemoryBotRepo::with_base_dir(dir.path().into());
        let runtime = tokio::runtime::Builder::new_current_thread()
            .max_blocking_threads(1).enable_all().build().unwrap();

        runtime.block_on(async {
            // Occupy the only blocking worker so preparation queues predictably.
            let (started_tx, started_rx) = std::sync::mpsc::channel();
            let (release_tx, release_rx) = std::sync::mpsc::channel();
            let blocker = tokio::task::spawn_blocking(move || {
                started_tx.send(()).unwrap();
                release_rx.recv().unwrap();
            });
            started_rx.recv().unwrap();
            let _guard = repo.record_write_lock.lock().await;
            let mut write = tokio_test::task::spawn(repo.write_stored_profile("bot", &stored));
            tokio_test::assert_pending!(write.poll());
            drop(write);
            release_tx.send(()).unwrap();
            blocker.await.unwrap();
        });
        // Runtime shutdown waits for any queued blocking preparation to finish.
        drop(runtime);
        assert_eq!(std::fs::read(&path).unwrap(), original);
        assert_eq!(std::fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);
    }
}
