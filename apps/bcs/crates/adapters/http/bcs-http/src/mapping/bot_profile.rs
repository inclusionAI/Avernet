//! Wire→application conversion for bot profile avatar writes (bot-avatar
//! plan Task 5).

use bcs_protocol as wire;
use bcs_service_api::types::bot_profile::AvatarChange;

/// Convert the presence-aware wire `avatar_url` field into the service-api
/// avatar transition: an omitted field keeps the stored avatar, the empty
/// string clears it, and any other value replaces it. The conversion lives
/// at the delivery adapter boundary so service-api never depends on the
/// protocol crate.
pub fn to_core_avatar(avatar_url: wire::AvatarField) -> AvatarChange {
    match avatar_url.0 {
        None => AvatarChange::Keep,
        Some(value) if value.is_empty() => AvatarChange::Clear,
        Some(value) => AvatarChange::Set(value),
    }
}
