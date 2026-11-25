//! Replay tokens of `ev` positions. A token is opaque outside this crate.

use bcs_dist_model::{BackendId, LogGeneration, ReplayToken, Sid, TaskCursor, Tid};
use bcs_store_api::stream::StreamId;

/// Generation of `ev` positions. Stream ids never restart, so this changes only
/// if a future rebuild procedure recreates a stream.
pub const LOG_GENERATION: &str = "1";

/// The position before the first entry of a stream.
pub(crate) const START: StreamId = StreamId { major: 0, minor: 0 };

pub(crate) fn cursor(backend: &BackendId, sid: &Sid, tid: &Tid, id: StreamId) -> TaskCursor {
    TaskCursor {
        sid: sid.clone(),
        tid: tid.clone(),
        token: ReplayToken {
            backend: backend.clone(),
            generation: LogGeneration(LOG_GENERATION.to_owned()),
            value: format!("{}-{}", id.major, id.minor),
        },
    }
}

/// `None` when the token comes from another backend or generation, or is not a
/// position: the caller answers with a gap (snapshot first).
pub(crate) fn parse(backend: &BackendId, token: &ReplayToken) -> Option<StreamId> {
    if &token.backend != backend || token.generation.0 != LOG_GENERATION {
        return None;
    }
    let (major, minor) = token.value.split_once('-')?;
    Some(StreamId {
        major: major.parse().ok()?,
        minor: minor.parse().ok()?,
    })
}
