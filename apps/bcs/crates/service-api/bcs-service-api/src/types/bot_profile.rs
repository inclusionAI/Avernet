//! Bot profile contract types (bot-avatar plan).
//!
//! The profile of a Bot is the display-facing part of its persisted record:
//! the storage capabilities plus the dedicated avatar field. These values
//! live in the bot record itself (the `bcs_bots` row or the local store's
//! persisted record) as a sibling of the capabilities, never inside the
//! serializable [`BotCapabilities`] wire contract and never in a separate
//! avatar cache.

use std::collections::HashMap;

use crate::BotCapabilities;

/// Requested avatar transition for an existing-only Bot profile write.
///
/// `Keep` is the no-op member: it must never appear in a storage `SET`
/// clause or in a persisted record rewrite, so a capabilities update can
/// never silently clear or replace the stored avatar.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub enum AvatarChange {
    /// Leave the stored avatar exactly as it is.
    #[default]
    Keep,
    /// Clear the stored avatar (persist SQL NULL).
    Clear,
    /// Overwrite the stored avatar with the given URL.
    Set(String),
}

/// One existing-only Bot profile write.
///
/// `capabilities` is the complete storage projection and its profile fields
/// (name/summary/domains/skills/scopes/binding_channels/visibility) are
/// written wholesale (empty fields clear, unlike the legacy registration
/// merge); `avatar` applies to the record's dedicated avatar field in the
/// same write. The write targets an already-existing live record: a missing
/// or soft-deleted target is classified as [`ProfileWrite::Missing`] and
/// never creates or resurrects a row. Existing owner and token fields are
/// never part of this write. The runtime registry identity
/// (`agent_code`/`agent_token`) is never written by a profile write either:
/// implementations preserve the live record's current identity, because an
/// empty-identity publication would silently disable the outbound security
/// interceptor chain for that Bot.
#[derive(Debug, Clone)]
pub struct ExistingProfileUpdate {
    pub capabilities: BotCapabilities,
    pub avatar: AvatarChange,
}

/// Result of an existing-only profile write.
///
/// `Updated` means a live record was confirmed and the write committed for
/// it — including the case where the record already carried every requested
/// value and the backend therefore reports zero changed rows. `Missing`
/// means no live record matched and nothing was written.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProfileWrite {
    /// A live record was confirmed and the write committed.
    Updated,
    /// No live record matched; nothing was written.
    Missing,
}

/// Batched avatar read result: the stored avatar URL per requested Bot id.
///
/// A present key with `None` means the live or retained (tombstoned)
/// record exists but has no avatar. An absent key means the id resolved to
/// no record under the repository's current environment isolation, or the
/// row belongs to a Human actor, which this Bot display read excludes.
pub type BotAvatarMap = HashMap<String, Option<String>>;
